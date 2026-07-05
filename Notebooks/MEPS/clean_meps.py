"""Build ``new_grouped_merge_df`` for any MEPS year (2020-2023).

Single public entry point:

    from clean_meps import build, write_log

    df, log = build(2023)         # or 2020, 2021, 2022
    write_log(log)                # appends a run block to decisions_log.md

``df`` matches the frame Friana's ``YYYY_clean.ipynb`` produces at the end of
cell 56: one row per (DUPERSID, DRUGIDX) after the CLNK join, the is_chronic
ICD filter, and the chronic-drug filter — with the PSTATS-based
reference-days denominator and MPR-style ``meps_adherence_ratio`` already
computed. ``log`` is a plain dict recording every stage's row/patient counts,
columns dropped, and decisions taken, ready to render in a Streamlit sidebar
or persist to ``decisions_log.md``.

Column set is the lean set the notebooks currently emit — no scope creep to
the Data Reference Guide's fuller Q3 feature list. Add those at the model
step, not here.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# Config: what file is what, per year
# ---------------------------------------------------------------------------

# AHRQ ships a fresh set of file numbers per year. Only h248a → h220a/h229a/
# h239a substitution; conditions and person-year files also renumber. Person
# file for 2020 is capitalized as ``H224.xlsx`` on AHRQ's release — matters
# on Linux (case-sensitive), harmless on macOS.
YEAR_FILES = {
    2020: {"rx": "h220a.xlsx", "clnk": "h220if1.xlsx",
           "cond": "h222.xlsx", "person": "H224.xlsx"},
    2021: {"rx": "h229a.xlsx", "clnk": "h229if1.xlsx",
           "cond": "h231.xlsx", "person": "h233.xlsx"},
    2022: {"rx": "h239a.xlsx", "clnk": "h239if1.xlsx",
           "cond": "h241.xlsx", "person": "h243.xlsx"},
    2023: {"rx": "h248a.xlsx", "clnk": "h248if1.xlsx",
           "cond": "h249.xlsx", "person": "h251.xlsx"},
}

# Cross-year lookup tables (same file used for all years).
CHRONIC_ICD_FILE = "is_chronic.xlsx"                          # Friana's DRAFT ICD list
CHRONIC_DRUG_FILE = "unique_rxname_chronic_labeled_revised.csv"  # Friana's labeled RXNAMEs

# PSTATS taxonomy per h251doc.pdf Tables 7-8. Stable across 2020-2023 (the
# 2020 file just has more -1 nonresponse because of COVID).
FULL_ROUND_STATUSES = {11, 13, 14, 22, 41, 42, 44, 51, 71}
STOP_STATUSES = {32, 33, 34, 35, 36}
NO_COVERAGE_STATUSES = {0, 12, 21, 24, 43, 62, 63, 64, 72, 73, 74, 81}


# ---------------------------------------------------------------------------
# Log data model
# ---------------------------------------------------------------------------

@dataclass
class StageEntry:
    stage: str
    rows_in: int
    rows_out: int
    patients_in: int | None = None
    patients_out: int | None = None
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "stage": self.stage,
            "rows_in": int(self.rows_in),
            "rows_out": int(self.rows_out),
            "rows_dropped": int(self.rows_in - self.rows_out),
            "patients_in": self.patients_in,
            "patients_out": self.patients_out,
            "patients_dropped": (
                None if self.patients_in is None or self.patients_out is None
                else int(self.patients_in - self.patients_out)
            ),
            "detail": self.detail,
        }


@dataclass
class RunLog:
    year: int
    meps_dir: str
    run_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    stages: list[StageEntry] = field(default_factory=list)
    columns_dropped: dict[str, list[str]] = field(default_factory=dict)
    decisions: list[str] = field(default_factory=list)
    final_row_count: int | None = None
    final_patient_count: int | None = None

    def add(self, stage: StageEntry) -> None:
        self.stages.append(stage)

    def decide(self, note: str) -> None:
        self.decisions.append(note)

    def to_dict(self) -> dict:
        return {
            "year": self.year,
            "run_at": self.run_at,
            "meps_dir": self.meps_dir,
            "stages": [s.to_dict() for s in self.stages],
            "columns_dropped": self.columns_dropped,
            "decisions": self.decisions,
            "final_row_count": self.final_row_count,
            "final_patient_count": self.final_patient_count,
        }


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def resolve_meps_dir(start: Path | None = None) -> Path:
    """Walk up from ``start`` (or CWD) looking for ``data/MEPS/excels`` or
    ``data/MEPS`` containing ``h248a.xlsx``. Works from repo root, from
    ``Notebooks/``, and from ``Notebooks/MEPS/``.
    """
    cwd = (start or Path.cwd()).resolve()
    candidates = []
    for base in [cwd, cwd.parent, cwd.parent.parent, cwd.parent.parent.parent]:
        candidates += [base / "data" / "MEPS" / "excels",
                       base / "data" / "MEPS"]
    for d in candidates:
        if d.exists() and any(d.glob("h248a.xlsx")):
            return d
    raise FileNotFoundError(
        "Could not find MEPS data directory. Looked under: "
        + ", ".join(str(c) for c in candidates)
    )


# ---------------------------------------------------------------------------
# PSTATS reference-days computation
# ---------------------------------------------------------------------------

def _month_start(year, month):
    if pd.isna(month) or pd.isna(year) or month <= 0 or year <= 0:
        return None
    return date(int(year), int(month), 1)


def _month_end(year, month):
    if pd.isna(month) or pd.isna(year) or month <= 0 or year <= 0:
        return None
    return date(int(year), int(month), calendar.monthrange(int(year), int(month))[1])


def _compute_reference_coverage(row, year: int):
    """Return (eligible_days, coverage_start, coverage_end, notes).

    Same algorithm as cell 55 in Friana's notebooks (patched version).
    ``PSTATS == -1`` means the person was not in that round (skip; no days
    added or removed).
    """
    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    ps_values = [int(row[f"PSTATS{sfx}"]) for sfx in (31, 42, 53)]
    if all(p == -1 for p in ps_values):
        return 0, None, None, "not_in_any_round"
    if all(p == 11 for p in ps_values):
        return 365, year_start, year_end, "full_year_all_rounds_11"

    cs, ce = None, None
    notes: list[str] = []

    for sfx in (31, 42, 53):
        ps = int(row[f"PSTATS{sfx}"])
        beg = _month_start(row[f"BEGRFY{sfx}"], row[f"BEGRFM{sfx}"])
        end = _month_end(row[f"ENDRFY{sfx}"], row[f"ENDRFM{sfx}"])

        if ps == -1:
            notes.append(f"R{sfx}:not_in_round")
            continue
        if ps in NO_COVERAGE_STATUSES:
            notes.append(f"R{sfx}:no_coverage({ps})")
            continue

        if cs is None:
            cs = max(beg, year_start) if beg else year_start

        if ps in FULL_ROUND_STATUSES:
            if end:
                ce = min(end, year_end)
            notes.append(f"R{sfx}:active({ps})")
        elif ps == 31:
            if end:
                ce = min(end, year_end)
            notes.append(f"R{sfx}:death")
            break
        elif ps in STOP_STATUSES:
            if end:
                ce = min(end, year_end)
            notes.append(f"R{sfx}:stop({ps})")
            break
        else:
            notes.append(f"R{sfx}:UNCLASSIFIED({ps})")

    if cs is None or ce is None or ce < cs:
        return 0, None, None, "no_coverage"
    return (ce - cs).days + 1, cs, ce, ";".join(notes)


# ---------------------------------------------------------------------------
# Column sets (year-parameterized)
# ---------------------------------------------------------------------------

def _rx_cols(year: int) -> list[str]:
    yy = f"{year % 100:02d}"
    return [
        "DUPERSID", "DRUGIDX", "LINKIDX", "RXRECIDX", "RXDAYSUP",
        "RXNAME", "RXBEGYRX", "RXNDC", "TC1", "TC1S1",
        f"RXXP{yy}X", f"RXSF{yy}X",
    ]


def _person_cols(year: int) -> list[str]:
    yy = f"{year % 100:02d}"
    return [
        "DUPERSID", f"AGE{yy}X", f"INSCOV{yy}", f"POVCAT{yy}",
        f"FAMINC{yy}", "RACEV2X",
        "PSTATS31", "PSTATS42", "PSTATS53",
        "BEGRFM31", "BEGRFY31", "BEGRFM42", "BEGRFY42", "BEGRFM53", "BEGRFY53",
        "ENDRFM31", "ENDRFY31", "ENDRFM42", "ENDRFY42", "ENDRFM53", "ENDRFY53",
    ]


COND_COLS = ["DUPERSID", "CONDIDX", "ICD10CDX", "AGEDIAG"]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def build(year: int, meps_dir: str | Path | None = None,
          drug_chronic_only: bool = True) -> tuple[pd.DataFrame, RunLog]:
    """Build ``new_grouped_merge_df`` for one year.

    Parameters
    ----------
    year
        One of 2020, 2021, 2022, 2023.
    meps_dir
        Directory with the MEPS excels. Auto-detected by ``resolve_meps_dir``
        if not passed.
    drug_chronic_only
        If True (default), filter to chronic drugs per Friana's labeled CSV.
        If False, keep all drugs and let the caller filter.

    Returns
    -------
    (df, log)
        ``df`` has one row per (DUPERSID, DRUGIDX) with adherence and
        reference-days columns. ``log`` records every stage's counts and
        decisions — feed it to ``write_log(log)`` to append to
        ``decisions_log.md``.
    """
    if year not in YEAR_FILES:
        raise ValueError(f"year must be one of {sorted(YEAR_FILES)}; got {year}")

    dir_ = Path(meps_dir) if meps_dir is not None else resolve_meps_dir()
    log = RunLog(year=year, meps_dir=str(dir_))
    files = YEAR_FILES[year]
    yy = f"{year % 100:02d}"
    xp_col = f"RXXP{yy}X"
    sf_col = f"RXSF{yy}X"

    log.decide(
        f"Denominator is PSTATS-derived reference days from {files['person']} "
        f"(BEGRF/ENDRF per round). Full-year respondents get 365; deceased "
        f"and moved-out get windowed."
    )
    log.decide(
        "PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; "
        "unknown codes marked 'UNCLASSIFIED' rather than silently included."
    )

    # -- 1. Load h248a, keep only lean columns --------------------------
    h248a = pd.read_excel(dir_ / files["rx"], engine="calamine",
                          usecols=_rx_cols(year))
    log.add(StageEntry("load_rx", rows_in=len(h248a), rows_out=len(h248a),
                       patients_in=h248a["DUPERSID"].nunique(),
                       patients_out=h248a["DUPERSID"].nunique(),
                       detail=f"file={files['rx']}"))

    # -- 2. RXDAYSUP filter: keep 1..989 (drops 999 'as needed' + negs) --
    before, before_p = len(h248a), h248a["DUPERSID"].nunique()
    rx = h248a[(h248a["RXDAYSUP"] > 0) & (h248a["RXDAYSUP"] < 990)]
    log.add(StageEntry("filter_RXDAYSUP_1_to_989",
                       rows_in=before, rows_out=len(rx),
                       patients_in=before_p, patients_out=rx["DUPERSID"].nunique(),
                       detail="Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and "
                              "negative sentinels (-1, -7, -8, -9, -15)."))
    log.decide(
        "RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not "
        "a day count. Treating it as adherence data would create fake 100% MPR."
    )

    # -- 3. Drop rows with unusable start-year info ---------------------
    before, before_p = len(rx), rx["DUPERSID"].nunique()
    rx = rx[rx["RXBEGYRX"] > 0]
    log.add(StageEntry("filter_RXBEGYRX_positive",
                       rows_in=before, rows_out=len(rx),
                       patients_in=before_p, patients_out=rx["DUPERSID"].nunique(),
                       detail="Excludes rows where the start-year variable is a "
                              "MEPS sentinel (-1, -7, -8)."))
    log.decide(
        "Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because "
        "the denominator branch can't route them. Loses coverage for meds "
        "with missing history; documented as a limitation."
    )

    # -- 4. Load h249 conditions + is_chronic ICD list ------------------
    h249 = pd.read_excel(dir_ / files["cond"], engine="calamine",
                         usecols=COND_COLS)
    is_chronic_icd = pd.read_excel(dir_ / CHRONIC_ICD_FILE, engine="calamine")
    log.decide(
        f"Chronic-condition filter uses {CHRONIC_ICD_FILE} (Friana's DRAFT "
        "ICD-10 3-digit allowlist). The Data Reference Guide flags this file "
        "as needing verification — treat downstream ICD-level counts with "
        "care until it's audited."
    )
    h249_ic = h249.merge(
        is_chronic_icd[["ICD10CDX", "ICD10CDX_LABEL", "is_chronic"]],
        on="ICD10CDX", how="left",
    )
    log.add(StageEntry("merge_chronic_icd_list",
                       rows_in=len(h249), rows_out=len(h249_ic),
                       detail="Added is_chronic + ICD10CDX_LABEL to h249 rows."))

    before, before_p = len(h249_ic), h249_ic["DUPERSID"].nunique()
    h249_chronic = h249_ic[h249_ic["is_chronic"] > 0].copy()
    log.add(StageEntry("filter_conditions_to_chronic",
                       rows_in=before, rows_out=len(h249_chronic),
                       patients_in=before_p,
                       patients_out=h249_chronic["DUPERSID"].nunique(),
                       detail="Keeps only conditions whose ICD-10 3-digit code "
                              "is in the chronic allowlist."))

    # -- 5. CLNK: link Rx events → conditions (EVENTYPE == 8) -----------
    clnk = pd.read_excel(dir_ / files["clnk"], engine="calamine")
    cond_df = clnk.merge(
        h249_chronic.drop(columns=["DUPERSID"]),  # keep clnk's DUPERSID
        on="CONDIDX", how="left",
    )
    before, before_p = len(cond_df), cond_df["DUPERSID"].nunique()
    cond_df = cond_df[cond_df["EVENTYPE"] == 8]
    log.add(StageEntry("filter_CLNK_EVENTYPE_8",
                       rows_in=before, rows_out=len(cond_df),
                       patients_in=before_p,
                       patients_out=cond_df["DUPERSID"].nunique(),
                       detail="Keeps only prescription-medicine event links "
                              "(EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, "
                              "8=PMED). Only 8 is relevant for Rx adherence."))

    # -- 6. Join Rx rows to CLNK-conditions, drop rows w/o chronic cond -
    keep_cond_cols = ["DUPERSID", "CONDIDX", "EVNTIDX", "ICD10CDX",
                      "ICD10CDX_LABEL", "is_chronic"]
    merged = rx.merge(
        cond_df[keep_cond_cols],
        left_on=["DUPERSID", "LINKIDX"], right_on=["DUPERSID", "EVNTIDX"],
        how="left",
    )
    before, before_p = len(merged), merged["DUPERSID"].nunique()
    merged = merged.dropna(subset=["is_chronic"])
    log.add(StageEntry("merge_rx_to_chronic_condition",
                       rows_in=before, rows_out=len(merged),
                       patients_in=before_p,
                       patients_out=merged["DUPERSID"].nunique(),
                       detail="LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops "
                              "Rx rows whose CLNK link isn't to a chronic "
                              "condition. This is where non-chronic-condition "
                              "fills leave the pipeline."))
    log.decide(
        "Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID "
        "alone (Cartesian explosion) and never LINKIDX alone (round-scoped). "
        "Documented in MEPS_SCHEMA_NOTES."
    )

    # -- 7. Groupby to person-drug grain -------------------------------
    gm = merged.groupby(["DUPERSID", "DRUGIDX"]).agg(
        RXDAYSUP=("RXDAYSUP", "sum"),
        RXXP=(xp_col, "mean"),
        RXSF=(sf_col, "mean"),
        RXNAME=("RXNAME", "first"),
        RXNDC=("RXNDC", "first"),
        RXBEGYRX=("RXBEGYRX", "first"),
        TC1=("TC1", "first"),
        TC1S1=("TC1S1", "first"),
        ICD10CDX=("ICD10CDX", "first"),
        ICD10CDX_LABEL=("ICD10CDX_LABEL", "first"),
    ).reset_index()
    gm = gm.rename(columns={"RXXP": xp_col, "RXSF": sf_col})
    log.add(StageEntry("groupby_person_drug",
                       rows_in=len(merged), rows_out=len(gm),
                       patients_in=merged["DUPERSID"].nunique(),
                       patients_out=gm["DUPERSID"].nunique(),
                       detail="One row per (DUPERSID, DRUGIDX). If a fill was "
                              "CLNK-linked to multiple chronic ICDs, only the "
                              "first-encountered ICD is kept (documented "
                              "attribution choice)."))
    log.decide(
        "Multi-condition fills attribute to the first ICD encountered in the "
        "join. Alternative: allocate days across linked conditions. Deferred "
        "until multi-attribution is a modeling requirement."
    )

    # -- 8. Optional: drug-chronic filter ------------------------------
    if drug_chronic_only:
        rxlabel = pd.read_csv(dir_ / CHRONIC_DRUG_FILE)
        rx_flag = rxlabel[["RXNAME", "is_chronic"]].rename(
            columns={"is_chronic": "is_chronic_drug"}
        )
        before, before_p = len(gm), gm["DUPERSID"].nunique()
        gm = gm.merge(rx_flag, on="RXNAME", how="left")
        gm = gm[gm["is_chronic_drug"] == 1].drop(columns=["is_chronic_drug"])
        log.add(StageEntry("filter_drug_chronic_only",
                           rows_in=before, rows_out=len(gm),
                           patients_in=before_p,
                           patients_out=gm["DUPERSID"].nunique(),
                           detail=f"Uses {CHRONIC_DRUG_FILE}. Drops drugs "
                                  "labeled 'flare_up' or 'non_chronic'. "
                                  "Keeps 'chronic'."))
        log.decide(
            "Drug-side chronic filter uses Friana's hand-labeled RXNAME list "
            "(1,158 unique drug names, 518 chronic, 373 flare_up, 267 "
            "non_chronic). Prevents acute meds (antibiotics, steroid bursts, "
            "topical FLUOROURACIL) from contaminating chronic-condition MPR."
        )

    # -- 9. Load h251, keep lean cols, merge on DUPERSID ---------------
    person = pd.read_excel(dir_ / files["person"], engine="calamine",
                           usecols=_person_cols(year))
    log.decide(
        f"Person-level columns pulled from {files['person']}: AGE{yy}X, "
        f"SEX-adjacent (RACEV2X), INSCOV{yy}, POVCAT{yy}, FAMINC{yy}, plus "
        "PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec "
        "features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included "
        "here — add at RF-modeling step."
    )
    before, before_p = len(gm), gm["DUPERSID"].nunique()
    gm = gm.merge(person, on="DUPERSID", how="left", validate="many_to_one")
    log.add(StageEntry("merge_person_demographics",
                       rows_in=before, rows_out=len(gm),
                       patients_in=before_p,
                       patients_out=gm["DUPERSID"].nunique(),
                       detail=f"LEFT JOIN person {files['person']} on DUPERSID; "
                              "validated many_to_one to catch dupe person rows."))

    # -- 10. PSTATS → reference_days_df --------------------------------
    person_demo = person.drop_duplicates("DUPERSID").copy()
    parts = person_demo.apply(
        lambda row: _compute_reference_coverage(row, year),
        axis=1, result_type="expand",
    )
    person_demo["total_days_supply"] = parts[0]
    person_demo[f"ref_start_{year}"] = parts[1]
    person_demo[f"ref_end_{year}"] = parts[2]
    person_demo["coverage_notes"] = parts[3]
    person_demo["r53_nonresponse"] = person_demo["PSTATS53"] == -1

    person_demo["participation_type"] = np.select(
        [
            person_demo["coverage_notes"].eq("full_year_all_rounds_11"),
            person_demo["coverage_notes"].eq("not_in_any_round"),
            person_demo["coverage_notes"].str.contains("death", na=False),
            person_demo["coverage_notes"].str.contains("stop", na=False),
            person_demo["coverage_notes"].str.contains("no_coverage", na=False),
            person_demo["total_days_supply"].eq(0),
        ],
        ["full_year", "not_in_any_round", "ended_early_death",
         "ended_early_left_ru", "partial_no_survey", "no_coverage"],
        default="partial_year",
    )

    reference_days_df = person_demo[
        ["DUPERSID", f"ref_start_{year}", f"ref_end_{year}",
         "total_days_supply", "participation_type", "coverage_notes",
         "r53_nonresponse"]
    ]
    log.add(StageEntry("compute_reference_days",
                       rows_in=len(person),
                       rows_out=len(reference_days_df),
                       patients_in=person["DUPERSID"].nunique(),
                       patients_out=reference_days_df["DUPERSID"].nunique(),
                       detail="PSTATS + BEGRF/ENDRF → per-person eligible "
                              f"days in {year}. Clamped to [Jan 1, Dec 31]."))
    n_r53 = int(reference_days_df["r53_nonresponse"].sum())
    log.decide(
        f"R5/3 nonresponders flagged (PSTATS53 == -1): {n_r53} persons. Their "
        "denominator ends at R4/2 interview date but their numerator can "
        "include RXDAYSUP that extends past it — MPR biased HIGH for this "
        "group. Kept in the frame; filter downstream if the analysis needs to."
    )

    # -- 11. Merge reference_days_df + compute adherence ----------------
    gm = gm.merge(reference_days_df, on="DUPERSID", how="left")

    gm["total_valid_days"] = np.minimum(gm["RXDAYSUP"], 365).astype(int)
    gm["total_valid_days"] = np.minimum(
        gm["total_valid_days"], gm["total_days_supply"],
    )
    gm["meps_adherence_ratio"] = np.where(
        gm["total_days_supply"].eq(0),
        np.nan,
        gm["total_valid_days"] / gm["total_days_supply"] * 100,
    )
    log.decide(
        "meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, "
        "eligible_days). This is PDC-style (100% ceiling), not MPR-style "
        "(uncapped). Documented; can be relaxed later if uncapped MPR is "
        "wanted."
    )

    # Drop round-detail cols the caller doesn't need
    drop_cols = [c for c in [
        "PSTATS31", "PSTATS42", "PSTATS53",
        "BEGRFM31", "BEGRFY31", "ENDRFM31", "ENDRFY31",
        "BEGRFM42", "BEGRFY42", "ENDRFM42", "ENDRFY42",
        "BEGRFM53", "BEGRFY53", "ENDRFM53", "ENDRFY53",
    ] if c in gm.columns]
    if drop_cols:
        gm = gm.drop(columns=drop_cols)
        log.columns_dropped["person_year_round_detail"] = drop_cols

    log.final_row_count = int(len(gm))
    log.final_patient_count = int(gm["DUPERSID"].nunique())

    return gm, log


# ---------------------------------------------------------------------------
# Log persistence
# ---------------------------------------------------------------------------

def write_log(log: RunLog | dict, path: str | Path | None = None) -> Path:
    """Append a human-readable run block to ``decisions_log.md``.

    ``path`` defaults to ``<this file's dir>/decisions_log.md``.
    """
    d = log if isinstance(log, dict) else log.to_dict()
    if path is None:
        path = Path(__file__).resolve().parent / "decisions_log.md"
    path = Path(path)

    existing = path.read_text() if path.exists() else ""
    has_our_header = "# Decisions Log" in existing

    md_lines: list[str] = []
    if not existing:
        # Brand-new file: write full header.
        md_lines += [
            "# Decisions Log",
            "",
            "Auto-appended by `clean_meps.build()`. Each block records one "
            "invocation of the pipeline — rows and patients lost at every "
            "stage, columns dropped, and decisions taken.",
            "",
        ]
    elif not has_our_header:
        # File exists with unrelated content (e.g. hand-written notes).
        # Preserve it; add a divider + section header before the first run block.
        md_lines += [
            "",
            "---",
            "",
            "# Decisions Log",
            "",
            "Auto-appended by `clean_meps.build()`. Each block records one "
            "invocation of the pipeline — rows and patients lost at every "
            "stage, columns dropped, and decisions taken.",
            "",
        ]

    md_lines += [
        f"## {d['year']} build — {d['run_at']}",
        "",
        f"**Source dir**: `{d['meps_dir']}`",
        f"**Final rows**: {d['final_row_count']:,}    "
        f"**Final unique patients**: {d['final_patient_count']:,}",
        "",
        "### Pipeline stages",
        "",
        "| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in d["stages"]:
        pi = s["patients_in"] if s["patients_in"] is not None else "—"
        po = s["patients_out"] if s["patients_out"] is not None else "—"
        pd_ = s["patients_dropped"] if s["patients_dropped"] is not None else "—"
        pi_s = f"{pi:,}" if isinstance(pi, int) else pi
        po_s = f"{po:,}" if isinstance(po, int) else po
        pd_s = f"{pd_:,}" if isinstance(pd_, int) else pd_
        md_lines.append(
            f"| `{s['stage']}` | {s['rows_in']:,} | {s['rows_out']:,} | "
            f"{s['rows_dropped']:,} | {pi_s} | {po_s} | {pd_s} |"
        )

    md_lines += ["", "### Detail per stage", ""]
    for s in d["stages"]:
        if s.get("detail"):
            md_lines.append(f"- **{s['stage']}**: {s['detail']}")

    if d["columns_dropped"]:
        md_lines += ["", "### Columns dropped", ""]
        for group, cols in d["columns_dropped"].items():
            md_lines.append(f"- **{group}**: `{', '.join(cols)}`")

    md_lines += ["", "### Decisions taken", ""]
    for note in d["decisions"]:
        md_lines.append(f"- {note}")

    md_lines += ["", "---", ""]

    # Always append — never overwrite. Header logic above decides whether to
    # prepend divider + header lines before the run block.
    with path.open("a") as f:
        f.write("\n".join(md_lines) + "\n")

    return path


# ---------------------------------------------------------------------------
# Legacy helper (kept for backwards compatibility with any code that imported
# clean_h248a from this module).
# ---------------------------------------------------------------------------

SENTINELS = [-1, -7, -8, -9, -15]


def clean_h248a(df: pd.DataFrame) -> pd.DataFrame:
    """Sentinel-mask h248a in place — pre-existing helper, unchanged.

    New code should call ``build(year)`` instead.
    """
    df = df.copy()
    if "DIABEQUIP" in df.columns:
        df = df[df["DIABEQUIP"] != 1].copy()
    for c in ("RXDAYSUP", "RXQUANTY", "RXSF23X", "RXMR23X"):
        if c in df.columns:
            df.loc[df[c].isin(SENTINELS), c] = np.nan
    if "RXDAYSUP" in df.columns:
        df.loc[df["RXDAYSUP"] == 999, "RXDAYSUP"] = np.nan
    if "RXDRGNAM" in df.columns:
        df = df[~df["RXDRGNAM"].astype(str).str.startswith("-")].copy()
    return df
