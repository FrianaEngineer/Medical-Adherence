"""Unit tests for the extracted pipeline stages in ``clean_meps``.

Covers the three pure functions pulled out of ``build()``:
    - link_rx_to_conditions
    - compute_reference_days
    - apply_adherence_math

These don't need real MEPS files - they use small hand-built frames that
exercise the invariants (CLNK multi-condition dedup, PSTATS eligibility
windows, PDC-style capping) that used to silently break in ``build()``.

Run from the app directory::

    python -m pytest tests/ -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from clean_meps import (
    apply_adherence_math,
    compute_reference_days,
    link_rx_to_conditions,
)


# ---------------------------------------------------------------------------
# link_rx_to_conditions
# ---------------------------------------------------------------------------

def _rx_row(dupersid, drugidx, rxrecidx, linkidx, days=30, name="DRUG_A"):
    return {
        "DUPERSID": dupersid, "DRUGIDX": drugidx, "RXRECIDX": rxrecidx,
        "LINKIDX": linkidx, "RXDAYSUP": days, "RXNAME": name,
        "RXDRGNAM": name, "RXBEGYRX": 2023, "RXBEGMM": 3,
    }


def _clnk_row(dupersid, evntidx, condidx, eventype=8):
    return {"DUPERSID": dupersid, "EVNTIDX": evntidx,
            "CONDIDX": condidx, "EVENTYPE": eventype}


def _cond_row(dupersid, condidx, icd, label="LBL", is_chronic=1):
    return {"DUPERSID": dupersid, "CONDIDX": condidx, "ICD10CDX": icd,
            "ICD10CDX_LABEL": label, "is_chronic": float(is_chronic)}


def test_multi_condition_fill_counts_days_once():
    """A fill linked to N chronic conditions must count RXDAYSUP once."""
    rx = pd.DataFrame([
        _rx_row("P1", "D1", "F1", "L1", days=30),  # 1 fill, 1 chronic cond
        _rx_row("P2", "D1", "F2", "L2", days=90),  # 1 fill, 2 chronic conds
    ])
    clnk = pd.DataFrame([
        _clnk_row("P1", "L1", "C1"),
        _clnk_row("P2", "L2", "C2"),
        _clnk_row("P2", "L2", "C3"),
    ])
    chronic = pd.DataFrame([
        _cond_row("P1", "C1", "E11"),
        _cond_row("P2", "C2", "I10"),
        _cond_row("P2", "C3", "E78"),
    ])

    merged, fills, bridge = link_rx_to_conditions(rx, clnk, chronic)

    # merged carries the multi-condition expansion (2 rows for P2)
    assert len(merged[merged["DUPERSID"] == "P2"]) == 2

    # fills is deduped per physical fill: RXDAYSUP sums correctly
    assert len(fills) == 2
    assert fills["RXDAYSUP"].sum() == 30 + 90, "RXDAYSUP double-counted"
    assert fills.duplicated(subset=["DUPERSID", "DRUGIDX", "RXRECIDX"]).sum() == 0

    # bridge preserves both ICDs for P2's one fill
    p2_bridge = bridge[bridge["DUPERSID"] == "P2"]
    assert set(p2_bridge["ICD10CDX"]) == {"I10", "E78"}
    assert bridge.duplicated(
        subset=["DUPERSID", "DRUGIDX", "ICD10CDX", "CONDIDX"]
    ).sum() == 0


def test_non_chronic_conditions_are_dropped():
    """Rx rows whose only CLNK link is to a non-chronic condition drop out.

    ``h249_chronic`` is prefiltered upstream, so we simulate by simply not
    including the non-chronic CONDIDX in the chronic frame.
    """
    rx = pd.DataFrame([
        _rx_row("P1", "D1", "F1", "L1"),   # links to chronic
        _rx_row("P3", "D1", "F3", "L3"),   # links to non-chronic (not in chronic frame)
    ])
    clnk = pd.DataFrame([
        _clnk_row("P1", "L1", "C1"),
        _clnk_row("P3", "L3", "C9"),  # C9 not in chronic list
    ])
    chronic = pd.DataFrame([_cond_row("P1", "C1", "E11")])

    _, fills, bridge = link_rx_to_conditions(rx, clnk, chronic)

    assert set(fills["DUPERSID"]) == {"P1"}
    assert set(bridge["DUPERSID"]) == {"P1"}


def test_eventtype_not_8_is_filtered():
    """Only EVENTYPE == 8 (prescription meds) survives the CLNK filter."""
    rx = pd.DataFrame([_rx_row("P1", "D1", "F1", "L1")])
    clnk = pd.DataFrame([
        _clnk_row("P1", "L1", "C1", eventype=1),   # office visit — dropped
        _clnk_row("P1", "L1", "C1", eventype=8),   # Rx — kept
    ])
    chronic = pd.DataFrame([_cond_row("P1", "C1", "E11")])

    _, fills, _ = link_rx_to_conditions(rx, clnk, chronic)
    assert len(fills) == 1


# ---------------------------------------------------------------------------
# compute_reference_days
# ---------------------------------------------------------------------------

def _person_row(dupersid, pstats, begrf, endrf):
    """pstats/begrf/endrf are 3-tuples for rounds 31, 42, 53."""
    out = {"DUPERSID": dupersid}
    for sfx, ps, beg, end in zip((31, 42, 53), pstats, begrf, endrf):
        out[f"PSTATS{sfx}"] = ps
        out[f"BEGRFM{sfx}"], out[f"BEGRFY{sfx}"] = beg
        out[f"ENDRFM{sfx}"], out[f"ENDRFY{sfx}"] = end
    return out


def test_full_year_all_rounds_11_gets_365_days():
    person = pd.DataFrame([_person_row(
        "P1", (11, 11, 11),
        [(1, 2023), (5, 2023), (9, 2023)],
        [(4, 2023), (8, 2023), (12, 2023)],
    )])
    ref = compute_reference_days(person, 2023)
    row = ref.iloc[0]
    assert row["total_days_supply"] == 365
    assert row["participation_type"] == "full_year"
    assert not row["r53_nonresponse"]


def test_pstats_12_full_year_uses_reference_window():
    """PSTATS=12 is FT military out-of-scope for national estimates, but
    Table 8 still assigns a full BEGRF/ENDRF window (same as code 11) and
    skips no instrument sections — denom must not be zeroed.
    """
    # Mirrors DUPERSID 2791970102 in h251 (2023): 12/12/12 with round dates
    # spanning the calendar year.
    person = pd.DataFrame([_person_row(
        "2791970102", (12, 12, 12),
        [(9, 2022), (2, 2023), (7, 2023)],
        [(2, 2023), (7, 2023), (12, 2023)],
    )])
    ref = compute_reference_days(person, 2023)
    row = ref.iloc[0]
    assert row["total_days_supply"] == 365
    assert row["participation_type"] == "full_year"
    assert row["coverage_notes"] == "full_year_all_rounds_12"


def test_pstats_12_mixed_with_11_still_counts():
    person = pd.DataFrame([_person_row(
        "P1", (12, 11, 11),
        [(1, 2023), (5, 2023), (9, 2023)],
        [(4, 2023), (8, 2023), (12, 2023)],
    )])
    ref = compute_reference_days(person, 2023)
    assert ref.iloc[0]["total_days_supply"] == 365


def test_gap_round_unions_begrf_endrf_windows():
    """Skipped middle round must not invent days between flanking windows."""
    person = pd.DataFrame([_person_row(
        "P1", (11, -1, 11),
        [(1, 2023), (-1, -1), (9, 2023)],
        [(4, 2023), (-1, -1), (12, 2023)],
    )])
    ref = compute_reference_days(person, 2023)
    # Jan1–Apr30 = 120; Sep1–Dec31 = 122
    assert ref.iloc[0]["total_days_supply"] == 242


def test_has_excluded_pstats_any_round():
    from clean_meps import has_excluded_pstats, EXCLUDED_PSTATS

    person = pd.DataFrame([
        _person_row("keep", (11, 11, 11),
                    [(1, 2023), (5, 2023), (9, 2023)],
                    [(4, 2023), (8, 2023), (12, 2023)]),
        _person_row("drop31", (11, 31, -1),
                    [(1, 2023), (5, 2023), (-1, -1)],
                    [(4, 2023), (6, 2023), (-1, -1)]),
        _person_row("drop12", (12, 12, 12),
                    [(1, 2023), (5, 2023), (9, 2023)],
                    [(4, 2023), (8, 2023), (12, 2023)]),
    ])
    mask = has_excluded_pstats(person)
    assert set(person.loc[mask, "DUPERSID"]) == {"drop31", "drop12"}
    assert 31 in EXCLUDED_PSTATS and 12 in EXCLUDED_PSTATS


def test_death_mid_year_gives_shortened_window():
    # PSTATS 31 == 31 means the person died during round 1 (per h251doc).
    person = pd.DataFrame([_person_row(
        "P1", (31, -1, -1),
        [(1, 2023), (-1, -1), (-1, -1)],
        [(6, 2023), (-1, -1), (-1, -1)],
    )])
    ref = compute_reference_days(person, 2023)
    row = ref.iloc[0]
    # window Jan 1 through Jun 30 = 181 days
    assert row["total_days_supply"] == 181
    assert row["participation_type"] == "ended_early_death"


def test_not_in_any_round_gives_zero_days():
    person = pd.DataFrame([_person_row(
        "P1", (-1, -1, -1),
        [(-1, -1), (-1, -1), (-1, -1)],
        [(-1, -1), (-1, -1), (-1, -1)],
    )])
    ref = compute_reference_days(person, 2023)
    row = ref.iloc[0]
    assert row["total_days_supply"] == 0
    assert row["participation_type"] == "not_in_any_round"


def test_r53_nonresponse_flag_set():
    person = pd.DataFrame([_person_row(
        "P1", (11, 11, -1),
        [(1, 2023), (5, 2023), (-1, -1)],
        [(4, 2023), (8, 2023), (-1, -1)],
    )])
    ref = compute_reference_days(person, 2023)
    assert ref.iloc[0]["r53_nonresponse"]


def test_dedups_person_input():
    """Person file with duplicate DUPERSID rows collapses to one."""
    row = _person_row(
        "P1", (11, 11, 11),
        [(1, 2023), (5, 2023), (9, 2023)],
        [(4, 2023), (8, 2023), (12, 2023)],
    )
    person = pd.DataFrame([row, row])
    ref = compute_reference_days(person, 2023)
    assert len(ref) == 1


# ---------------------------------------------------------------------------
# apply_adherence_math
# ---------------------------------------------------------------------------

def test_ratio_normal_case():
    df = pd.DataFrame({"RXDAYSUP": [100], "total_days_supply": [365]})
    out = apply_adherence_math(df)
    assert out.iloc[0]["total_valid_days"] == 100
    assert out.iloc[0]["meps_adherence_ratio"] == pytest.approx(100 / 365 * 100)


def test_numerator_capped_at_365():
    df = pd.DataFrame({"RXDAYSUP": [500], "total_days_supply": [365]})
    out = apply_adherence_math(df)
    assert out.iloc[0]["total_valid_days"] == 365
    assert out.iloc[0]["meps_adherence_ratio"] == 100.0


def test_numerator_capped_at_denominator():
    """Drug with shorter denominator: numerator can't exceed the window."""
    df = pd.DataFrame({"RXDAYSUP": [400], "total_days_supply": [200]})
    out = apply_adherence_math(df)
    assert out.iloc[0]["total_valid_days"] == 200
    assert out.iloc[0]["meps_adherence_ratio"] == 100.0


def test_zero_denominator_gives_nan_ratio():
    df = pd.DataFrame({"RXDAYSUP": [100], "total_days_supply": [0]})
    out = apply_adherence_math(df)
    assert out.iloc[0]["total_valid_days"] == 0
    assert pd.isna(out.iloc[0]["meps_adherence_ratio"])


def test_zero_numerator_gives_zero_ratio():
    df = pd.DataFrame({"RXDAYSUP": [0], "total_days_supply": [365]})
    out = apply_adherence_math(df)
    assert out.iloc[0]["total_valid_days"] == 0
    assert out.iloc[0]["meps_adherence_ratio"] == 0.0


def test_custom_denom_column():
    df = pd.DataFrame({"RXDAYSUP": [100], "drug_start_days": [200]})
    out = apply_adherence_math(df, denom_col="drug_start_days")
    assert out.iloc[0]["total_valid_days"] == 100
    assert out.iloc[0]["meps_adherence_ratio"] == 50.0


def test_input_frame_not_mutated():
    df = pd.DataFrame({"RXDAYSUP": [100], "total_days_supply": [365]})
    before = df.copy()
    _ = apply_adherence_math(df)
    pd.testing.assert_frame_equal(df, before)


def test_invariant_numerator_le_denominator():
    """Property: after apply_adherence_math, num <= denom always holds.

    This is the gate #1 invariant (``check_numerator_le_denominator``)
    made unconditional by construction.
    """
    df = pd.DataFrame({
        "RXDAYSUP": [30, 100, 400, 999, 0, 200],
        "total_days_supply": [365, 200, 365, 365, 365, 100],
    })
    out = apply_adherence_math(df)
    assert (out["total_valid_days"] <= out["total_days_supply"]).all()
