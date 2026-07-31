"""Unit tests for MEPS data-quality gates.

Run from the app directory::

    python -m pytest tests/ -v
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from gates import (
    build_one_hot_matrix,
    check_adherence_bounds,
    check_age_non_negative,
    check_all_conditions_chronic,
    check_no_negative_rx_or_icd,
    check_numerator_le_denominator,
    check_one_hot_encoding,
    run_all_gates,
)


# ---------------------------------------------------------------------------
# Numerator ≤ denominator
# ---------------------------------------------------------------------------


def test_numerator_le_denominator_passes_when_equal_or_smaller():
    df = pd.DataFrame(
        {
            "total_valid_days": [10, 30, 100, 0],
            "total_days_supply": [10, 60, 365, 1],
        }
    )
    result = check_numerator_le_denominator(df)
    assert result.passed
    assert result.n_violations == 0


def test_numerator_le_denominator_fails_when_numerator_bigger():
    df = pd.DataFrame(
        {
            "total_valid_days": [50, 200],
            "total_days_supply": [40, 100],
        }
    )
    result = check_numerator_le_denominator(df)
    assert not result.passed
    assert result.n_violations == 2
    assert "inflated" in result.summary.lower()


def test_numerator_le_denominator_missing_columns():
    result = check_numerator_le_denominator(pd.DataFrame({"x": [1]}))
    assert not result.passed


def test_numerator_le_denominator_matches_adherence_formula_columns():
    """Gate must use the same num/den columns as meps_adherence_ratio."""
    df = pd.DataFrame(
        {
            "total_valid_days": [50, 100, 200],
            "total_days_supply": [100, 100, 100],
            "meps_adherence_ratio": [50.0, 100.0, 200.0],  # last would be capped in pipeline
        }
    )
    # Row 2: numerator 200 > denominator 100 → gate catches inflation risk
    result = check_numerator_le_denominator(df)
    assert not result.passed
    assert result.n_violations == 1
    assert result.meta["numerator"] == "total_valid_days"
    assert result.meta["denominator"] == "total_days_supply"


# ---------------------------------------------------------------------------
# One-hot encoding is 0/1 only
# ---------------------------------------------------------------------------


def test_one_hot_encoding_passes_for_pure_zeros_and_ones():
    encoded = pd.DataFrame(
        {
            "SEX_1": [1, 0, 1],
            "SEX_2": [0, 1, 0],
            "ICD_E11": [1, 0, 1],
            "ICD_I10": [0, 1, 0],
        }
    )
    groups = {
        "SEX": ["SEX_1", "SEX_2"],
        "ICD": ["ICD_E11", "ICD_I10"],
    }
    result = check_one_hot_encoding(encoded, groups)
    assert result.passed
    assert result.n_violations == 0


def test_one_hot_encoding_fails_for_other_values():
    encoded = pd.DataFrame(
        {
            "SEX_1": [1, 0, 2],
            "SEX_2": [0, 1, -1],
            "ICD_E11": [1, 0.5, 0],
        }
    )
    result = check_one_hot_encoding(encoded)
    assert not result.passed
    assert result.n_violations >= 3


def test_one_hot_encoding_fails_when_row_not_exactly_one_hot():
    # Row 0 activates both SEX categories → not a valid one-hot.
    encoded = pd.DataFrame(
        {
            "SEX_1": [1, 0, 1],
            "SEX_2": [1, 1, 0],
        }
    )
    groups = {"SEX": ["SEX_1", "SEX_2"]}
    result = check_one_hot_encoding(encoded, groups)
    assert not result.passed
    assert result.meta.get("n_bad_rowsum", 0) >= 1


def test_one_hot_encoding_fails_on_empty():
    result = check_one_hot_encoding(pd.DataFrame())
    assert not result.passed


def test_build_one_hot_matrix_only_zeros_and_ones():
    df = pd.DataFrame(
        {
            "SEX": [1, 2, 1],
            "ICD10CDX": ["E11", "I10", "E11"],
        }
    )
    encoded, groups = build_one_hot_matrix(df, columns=["SEX", "ICD10CDX"])
    assert not encoded.empty
    assert set(groups) == {"SEX", "ICD10CDX"}
    assert set(np.unique(encoded.to_numpy())).issubset({0, 1})
    # Each original column's dummies sum to 1 per row
    for cols in groups.values():
        assert (encoded[cols].sum(axis=1) == 1).all()
    assert check_one_hot_encoding(encoded, groups).passed


# ---------------------------------------------------------------------------
# No negatives for RXDAYSUP / ICD10CDX
# ---------------------------------------------------------------------------


def test_no_negative_rx_or_icd_passes():
    df = pd.DataFrame(
        {
            "RXDAYSUP": [1, 30, 90, 365],
            "ICD10CDX": ["E11", "I10", "J45", "F32"],
        }
    )
    result = check_no_negative_rx_or_icd(df)
    assert result.passed
    assert "range" in result.detail
    assert "1 to 365" in result.detail
    assert "E11 to J45" in result.detail or "E11" in result.detail


def test_no_negative_rx_or_icd_fails_on_negative_supply():
    df = pd.DataFrame(
        {
            "RXDAYSUP": [30, -5, 10],
            "ICD10CDX": ["E11", "I10", "J45"],
        }
    )
    result = check_no_negative_rx_or_icd(df)
    assert not result.passed
    assert result.n_violations == 1
    assert "range" in result.detail


def test_no_negative_rx_or_icd_fails_on_zero_supply():
    df = pd.DataFrame(
        {
            "RXDAYSUP": [30, 0, 10],
            "ICD10CDX": ["E11", "I10", "J45"],
        }
    )
    result = check_no_negative_rx_or_icd(df)
    assert not result.passed
    assert "not > 0" in result.detail


def test_no_negative_rx_or_icd_fails_on_negative_icd_sentinel():
    df = pd.DataFrame(
        {
            "RXDAYSUP": [30, 60],
            "ICD10CDX": ["E11", "-15"],
        }
    )
    result = check_no_negative_rx_or_icd(df)
    assert not result.passed
    assert result.n_violations == 1


def test_no_negative_rx_or_icd_fails_on_numeric_negative_icd():
    df = pd.DataFrame(
        {
            "RXDAYSUP": [30],
            "ICD10CDX": [-1],
        }
    )
    result = check_no_negative_rx_or_icd(df)
    assert not result.passed


# ---------------------------------------------------------------------------
# All conditions chronic (is_chronic + lookup)
# ---------------------------------------------------------------------------


def test_all_conditions_chronic_passes_with_lookup():
    df = pd.DataFrame(
        {
            "ICD10CDX": ["E11", "I10", "J45"],
            "is_chronic": [1, 1, 1],
        }
    )
    lookup = pd.DataFrame(
        {
            "ICD10CDX": ["E11", "I10", "J45", "A08"],
            "is_chronic": [1, 1, 1, 0],
        }
    )
    result = check_all_conditions_chronic(df, lookup)
    assert result.passed
    assert result.meta.get("n_conditions") == 3
    assert result.detail
    assert "is_chronic == 1" in result.detail
    assert result.meta.get("require") == "is_chronic == 1"


def test_all_conditions_chronic_pass_summary_omits_row_disclaimer():
    df = pd.DataFrame({"ICD10CDX": ["E11"], "is_chronic": [1]})
    lookup = pd.DataFrame({"ICD10CDX": ["E11"], "is_chronic": [1]})
    result = check_all_conditions_chronic(df, lookup)
    assert result.passed
    assert "person–drug" not in result.summary
    assert result.detail


def test_all_conditions_chronic_passes_when_frame_flag_missing_but_lookup_ok():
    """All-years merge may leave is_chronic blank for older years."""
    df = pd.DataFrame(
        {
            "ICD10CDX": ["E11", "E11", "I10", "I10", "I10"],
            "is_chronic": [1, 1, pd.NA, pd.NA, pd.NA],
        }
    )
    lookup = pd.DataFrame(
        {"ICD10CDX": ["E11", "I10"], "is_chronic": [1, 1]}
    )
    result = check_all_conditions_chronic(df, lookup)
    assert result.passed
    assert result.n_violations == 0
    assert result.meta.get("n_conditions") == 2


def test_all_conditions_chronic_counts_unique_conditions_not_rows():
    df = pd.DataFrame(
        {
            "ICD10CDX": ["A08"] * 1000,
            "is_chronic": [0] * 1000,
        }
    )
    lookup = pd.DataFrame({"ICD10CDX": ["A08"], "is_chronic": [0]})
    result = check_all_conditions_chronic(df, lookup)
    assert not result.passed
    assert result.n_violations == 1
    assert "1 of 1" in result.summary


def test_all_conditions_chronic_fails_when_frame_flag_zero():
    df = pd.DataFrame(
        {
            "ICD10CDX": ["E11", "A08"],
            "is_chronic": [1, 0],
        }
    )
    result = check_all_conditions_chronic(df)
    assert not result.passed
    assert result.n_violations >= 1


def test_all_conditions_chronic_fails_when_lookup_says_not_chronic():
    df = pd.DataFrame(
        {
            "ICD10CDX": ["E11", "A08"],
            "is_chronic": [1, 1],  # frame claims chronic, lookup disagrees for A08
        }
    )
    lookup = pd.DataFrame(
        {
            "ICD10CDX": ["E11", "A08"],
            "is_chronic": [1, 0],
        }
    )
    result = check_all_conditions_chronic(df, lookup)
    assert not result.passed
    assert result.n_violations >= 1


# ---------------------------------------------------------------------------
# Adherence ratio in [0, 100]
# ---------------------------------------------------------------------------


def test_adherence_bounds_passes():
    df = pd.DataFrame({"meps_adherence_ratio": [0.0, 50.5, 100.0, np.nan]})
    result = check_adherence_bounds(df)
    assert result.passed


def test_adherence_bounds_fails_below_zero():
    df = pd.DataFrame({"meps_adherence_ratio": [-0.1, 50.0]})
    result = check_adherence_bounds(df)
    assert not result.passed
    assert result.n_violations == 1


def test_adherence_bounds_fails_above_100():
    df = pd.DataFrame({"meps_adherence_ratio": [80.0, 100.1, 150.0]})
    result = check_adherence_bounds(df)
    assert not result.passed
    assert result.n_violations == 2


# ---------------------------------------------------------------------------
# Age ≥ 0
# ---------------------------------------------------------------------------


def test_age_non_negative_passes_with_unknown():
    df = pd.DataFrame({"AGE23X": [0, 45, 90, "unknown"]})
    result = check_age_non_negative(df)
    assert result.passed


def test_age_non_negative_passes_shared_age_column():
    df = pd.DataFrame({"AGE": [12, 34, "unknown"]})
    result = check_age_non_negative(df)
    assert result.passed


def test_age_non_negative_fails_on_negative():
    df = pd.DataFrame({"AGE23X": [40, -1, 22]})
    result = check_age_non_negative(df)
    assert not result.passed
    assert result.n_violations == 1


def test_age_non_negative_missing_column():
    result = check_age_non_negative(pd.DataFrame({"SEX": [1, 2]}))
    assert not result.passed


# ---------------------------------------------------------------------------
# run_all_gates integration
# ---------------------------------------------------------------------------


def test_run_all_gates_all_pass_on_clean_frame():
    df = pd.DataFrame(
        {
            "DUPERSID": [1, 2, 3],
            "RXDAYSUP": [30, 60, 90],
            "ICD10CDX": ["E11", "I10", "J45"],
            "is_chronic": [1, 1, 1],
            "total_valid_days": [30, 60, 90],
            "total_days_supply": [365, 365, 180],
            "meps_adherence_ratio": [8.2, 16.4, 50.0],
            "AGE": [40, 55, "unknown"],
            "SEX": [1, 2, 1],
        }
    )
    lookup = pd.DataFrame(
        {"ICD10CDX": ["E11", "I10", "J45"], "is_chronic": [1, 1, 1]}
    )
    results = run_all_gates(df, chronic_lookup=lookup)
    assert len(results) == 6
    assert all(r.passed for r in results)
    assert all(r.detail for r in results)


def test_run_all_gates_none_df():
    results = run_all_gates(None)
    assert len(results) == 1
    assert not results[0].passed
    assert results[0].detail


@pytest.mark.parametrize(
    "maker,expect_pass",
    [
        (
            lambda: pd.DataFrame(
                {
                    "total_valid_days": [1],
                    "total_days_supply": [1],
                    "RXDAYSUP": [1],
                    "ICD10CDX": ["E11"],
                    "is_chronic": [1],
                    "meps_adherence_ratio": [100.0],
                    "AGE": [30],
                    "SEX": [1],
                }
            ),
            True,
        ),
        (
            lambda: pd.DataFrame(
                {
                    "total_valid_days": [10],
                    "total_days_supply": [5],  # numerator bigger
                    "RXDAYSUP": [10],
                    "ICD10CDX": ["E11"],
                    "is_chronic": [1],
                    "meps_adherence_ratio": [100.0],
                    "AGE": [30],
                    "SEX": [1],
                }
            ),
            False,
        ),
    ],
)
def test_run_all_gates_parametrized(maker, expect_pass):
    results = run_all_gates(
        maker(),
        chronic_lookup=pd.DataFrame({"ICD10CDX": ["E11"], "is_chronic": [1]}),
    )
    overall = all(r.passed for r in results)
    assert overall is expect_pass
