"""Data-quality gates for the MEPS adherence pipeline.

Each check returns a ``GateResult`` with a pass/fail flag and a plain-language
summary suitable for the Streamlit Gates tab. The same functions are exercised
by unit tests under ``tests/``.

Requirement → gate map
----------------------
1. Numerator ≤ denominator (else adherence inflates)
   → ``check_numerator_le_denominator`` on ``total_valid_days`` ≤ ``total_days_supply``
     (these are the exact columns used in ``meps_adherence_ratio``).
2. One-hot encoding is only 0/1 (else models train poorly)
   → ``check_one_hot_encoding`` on a matrix built from modeling categoricals;
     also requires each original column's dummies to sum to 1 per row.
3. No negatives for RXDAYSUP / ICD10CDX (unknown supply / disease)
   → ``check_no_negative_rx_or_icd`` (RXDAYSUP must be > 0; technical details
     show RXDAYSUP and ICD10CDX ranges).
4. All conditions are chronic (``is_chronic.xlsx``)
   → ``check_all_conditions_chronic`` — every unique ICD must have
     ``is_chronic == 1``.
5. Adherence ratio in [0, 100]
   → ``check_adherence_bounds`` on ``meps_adherence_ratio``.
6. No age < 0
   → ``check_age_non_negative`` on ``AGE`` / ``AGEyyX`` (``unknown`` allowed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

CHRONIC_ICD_FILE = "is_chronic.xlsx"

# Columns used when building a modeling-style one-hot matrix for gate #2.
# RXNAME is intentionally omitted here — it creates thousands of sparse columns
# and is one-hot'd at person level in the notebooks; ICD + demographics cover
# the 0/1 structural property the gate is meant to enforce.
ONE_HOT_SOURCE_COLUMNS = (
    "SEX",
    "ICD10CDX",
    "participation_type",
    "drug_condition_type",
    "INSCOV",
    "POVCAT",
)


@dataclass
class GateResult:
    """Outcome of one data-quality gate."""

    id: str
    title: str
    passed: bool
    summary: str
    detail: str = ""
    n_violations: int = 0
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "passed": self.passed,
            "summary": self.summary,
            "detail": self.detail,
            "n_violations": int(self.n_violations),
            "meta": self.meta,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _age_series(df: pd.DataFrame) -> pd.Series | None:
    """Return the first available age column (AGE or AGEyyX)."""
    if "AGE" in df.columns:
        return df["AGE"]
    age_cols = [c for c in df.columns if c.startswith("AGE") and c.endswith("X")]
    if not age_cols:
        return None
    return df[age_cols[0]]


def _is_negative_icd(series: pd.Series) -> pd.Series:
    """True where ICD10CDX looks like a negative / missing MEPS sentinel."""
    s = series.astype(str).str.strip()
    numeric = pd.to_numeric(series, errors="coerce")
    return s.str.startswith("-") | (numeric < 0)


def _one_hot_candidate_columns(df: pd.DataFrame) -> list[str]:
    """Resolve which categorical columns exist for one-hot checking."""
    candidates: list[str] = []
    for c in ONE_HOT_SOURCE_COLUMNS:
        if c in df.columns:
            candidates.append(c)
    for c in df.columns:
        if c.startswith("INSCOV") or c.startswith("POVCAT"):
            if c not in candidates:
                candidates.append(c)
    return candidates


def build_one_hot_matrix(
    df: pd.DataFrame,
    columns: list[str] | None = None,
) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """One-hot encode selected categorical columns (0/1 integers).

    Returns ``(encoded, groups)`` where ``groups`` maps each original column
    name to its dummy column names (used to verify a true one-hot: each row
    has exactly one 1 per original variable).

    Missing columns are skipped. Returns an empty frame and empty groups when
    nothing can be encoded.
    """
    if columns is None:
        columns = _one_hot_candidate_columns(df)

    present = [c for c in columns if c in df.columns]
    if not present:
        return pd.DataFrame(index=df.index), {}

    pieces: list[pd.DataFrame] = []
    groups: dict[str, list[str]] = {}
    for col in present:
        # dummy_na=True so missing values get their own indicator and every
        # row still has exactly one 1 for this variable (true one-hot).
        dummies = pd.get_dummies(
            df[[col]].astype("object"),
            prefix=col,
            dtype=int,
            dummy_na=True,
        )
        pieces.append(dummies)
        groups[col] = list(dummies.columns)

    encoded = pd.concat(pieces, axis=1)
    return encoded, groups


# ---------------------------------------------------------------------------
# Individual gates
# ---------------------------------------------------------------------------


def check_numerator_le_denominator(df: pd.DataFrame) -> GateResult:
    """Numerator (days covered) must not exceed denominator (eligible days).

    Pipeline formula::

        meps_adherence_ratio = total_valid_days / total_days_supply * 100

    so ``total_valid_days`` is the numerator and ``total_days_supply`` is the
    eligible-days denominator (despite the supply-like name).
    """
    title = "Days covered never exceed eligible days"
    num_col, den_col = "total_valid_days", "total_days_supply"

    if num_col not in df.columns or den_col not in df.columns:
        return GateResult(
            id="numerator_le_denominator",
            title=title,
            passed=False,
            summary=(
                "Could not run this check — the data is missing the columns that "
                "track days of medicine on hand vs. days the person was eligible."
            ),
            detail=f"Need numerator '{num_col}' and denominator '{den_col}'.",
        )

    num = pd.to_numeric(df[num_col], errors="coerce")
    den = pd.to_numeric(df[den_col], errors="coerce")
    comparable = num.notna() & den.notna()
    bad = comparable & (num > den)
    n_bad = int(bad.sum())
    passed = n_bad == 0

    if passed:
        summary = (
            "Pass — every person–drug row has days of medicine on hand that are "
            "less than or equal to the days they were eligible to be measured. "
            "That keeps adherence from being artificially inflated."
        )
    else:
        summary = (
            f"Fail — {n_bad:,} row(s) have more days of medicine on hand than "
            "eligible days. When the numerator is larger than the denominator, "
            "adherence scores get inflated above what is possible."
        )

    return GateResult(
        id="numerator_le_denominator",
        title=title,
        passed=passed,
        summary=summary,
        detail=(
            f"{n_bad} rows with numerator {num_col} > denominator {den_col} "
            f"(adherence = {num_col} / {den_col} × 100)"
        ),
        n_violations=n_bad,
        meta={"numerator": num_col, "denominator": den_col},
    )


def check_one_hot_encoding(
    encoded: pd.DataFrame,
    groups: dict[str, list[str]] | None = None,
) -> GateResult:
    """One-hot / dummy columns must contain only 0 and 1.

    When ``groups`` is provided (original column → dummy columns), also require
    that each row has exactly one ``1`` within each group — the defining
    property of a proper one-hot encoding.
    """
    title = "One-hot features are only 0s and 1s"

    if encoded is None or encoded.empty:
        return GateResult(
            id="one_hot_encoding",
            title=title,
            passed=False,
            summary=(
                "Could not run this check — there is no one-hot encoded feature "
                "matrix to inspect. Models need clean 0/1 indicators for categories."
            ),
            detail="Empty encoded frame.",
        )

    values = encoded.to_numpy(dtype=float, copy=False)
    finite = np.isfinite(values)
    ok_binary = finite & ((values == 0) | (values == 1))
    n_bad_binary = int((~ok_binary).sum())

    n_bad_rowsum = 0
    bad_group_notes: list[str] = []
    if groups:
        for src, cols in groups.items():
            present_cols = [c for c in cols if c in encoded.columns]
            if not present_cols:
                continue
            row_sums = encoded[present_cols].sum(axis=1)
            # Proper one-hot: exactly one category active per row.
            bad_rows = row_sums != 1
            n = int(bad_rows.sum())
            if n:
                n_bad_rowsum += n
                bad_group_notes.append(f"{src}: {n} rows do not sum to 1")

    n_bad = n_bad_binary + n_bad_rowsum
    passed = n_bad == 0
    encoded_from = ", ".join(groups.keys()) if groups else "provided matrix"

    if passed:
        summary = (
            f"Pass — all {encoded.shape[1]:,} one-hot columns contain only 0 or 1"
            + (
                f", and each of {len(groups)} category groups has exactly one "
                "active flag per row"
                if groups
                else ""
            )
            + f" (encoded: {encoded_from}). That is what models expect."
        )
    else:
        parts = []
        if n_bad_binary:
            parts.append(f"{n_bad_binary:,} cell(s) that are not 0 or 1")
        if n_bad_rowsum:
            parts.append(
                f"{n_bad_rowsum:,} row/group cases that are not a proper one-hot "
                f"({'; '.join(bad_group_notes)})"
            )
        summary = (
            f"Fail — found {'; and '.join(parts)}. If categories are not encoded "
            "as pure 0/1 one-hot flags, models will not train correctly."
        )

    return GateResult(
        id="one_hot_encoding",
        title=title,
        passed=passed,
        summary=summary,
        detail=(
            f"non-binary cells={n_bad_binary}; bad row-sums={n_bad_rowsum}; "
            f"shape={encoded.shape}; sources=[{encoded_from}]"
        ),
        n_violations=n_bad,
        meta={
            "n_columns": int(encoded.shape[1]),
            "n_rows": int(len(encoded)),
            "n_bad_binary": n_bad_binary,
            "n_bad_rowsum": n_bad_rowsum,
            "sources": list(groups.keys()) if groups else [],
        },
    )


def check_no_negative_rx_or_icd(df: pd.DataFrame) -> GateResult:
    """RXDAYSUP must be > 0; ICD10CDX must not be negative / unknown sentinels."""
    title = "No negative days-supply or disease codes"

    issues: list[str] = []
    n_bad = 0
    rx_range = "n/a"
    icd_range = "n/a"
    n_rx_checked = 0
    n_icd_checked = 0

    if "RXDAYSUP" in df.columns:
        rx = pd.to_numeric(df["RXDAYSUP"], errors="coerce")
        n_rx_checked = int(rx.notna().sum())
        # Require strictly positive days of supply (0 / negative = unknown / unusable).
        rx_bad = rx.isna() | (rx <= 0)
        n_rx = int(rx_bad.sum())
        n_bad += n_rx
        if n_rx_checked:
            rx_range = f"{float(rx.min()):g} to {float(rx.max()):g}"
        if n_rx:
            n_neg = int((rx.notna() & (rx < 0)).sum())
            n_zero = int((rx.notna() & (rx == 0)).sum())
            n_missing = int(rx.isna().sum())
            parts = []
            if n_neg:
                parts.append(f"{n_neg:,} negative")
            if n_zero:
                parts.append(f"{n_zero:,} zero")
            if n_missing:
                parts.append(f"{n_missing:,} missing")
            issues.append(
                f"{n_rx:,} RXDAYSUP value(s) not > 0 ({', '.join(parts) or 'invalid'})"
            )
    else:
        issues.append("RXDAYSUP column is missing")

    if "ICD10CDX" in df.columns:
        icd = df["ICD10CDX"]
        n_icd_checked = int(icd.notna().sum())
        icd_bad = icd.isna() | _is_negative_icd(icd)
        n_icd = int(icd_bad.sum())
        n_bad += n_icd
        # Disease-code range: sorted unique codes (first → last).
        valid_codes = (
            icd[~icd_bad]
            .astype(str)
            .str.strip()
            .replace("", pd.NA)
            .dropna()
            .unique()
            .tolist()
        )
        valid_codes_sorted = sorted(valid_codes)
        if valid_codes_sorted:
            icd_range = (
                f"{valid_codes_sorted[0]} to {valid_codes_sorted[-1]} "
                f"({len(valid_codes_sorted)} unique codes)"
            )
        elif n_icd_checked:
            icd_range = "no valid codes"
        if n_icd:
            issues.append(f"{n_icd:,} negative / unknown / missing ICD-10 code(s)")
    else:
        issues.append("ICD10CDX column is missing")

    missing_required = ("RXDAYSUP" not in df.columns) or ("ICD10CDX" not in df.columns)
    passed = (n_bad == 0) and not missing_required

    detail = (
        f"RXDAYSUP: require > 0; checked {n_rx_checked:,} non-null values; "
        f"range {rx_range}; "
        f"ICD10CDX: require non-negative real codes; checked {n_icd_checked:,} "
        f"non-null values; range {icd_range}"
    )
    if issues:
        detail = f"{detail}. Issues: {'; '.join(issues)}"

    if passed:
        summary = (
            "Pass — every medication days-supply is greater than 0, and every "
            "disease code looks like a real ICD-10 code (not a negative placeholder). "
            "Zero or negative supply / disease codes would mean those values are unknown."
        )
    else:
        joined = "; ".join(issues) if issues else "unknown problem"
        summary = (
            f"Fail — {joined}. Days of supply must be > 0, and disease codes must "
            "not be negative placeholders — otherwise the person's medication "
            "supply or diagnosis is unknown."
        )

    return GateResult(
        id="no_negative_rx_or_icd",
        title=title,
        passed=passed,
        summary=summary,
        detail=detail,
        n_violations=n_bad,
        meta={"rx_range": rx_range, "icd_range": icd_range},
    )


def check_all_conditions_chronic(
    df: pd.DataFrame,
    chronic_lookup: pd.DataFrame | None = None,
) -> GateResult:
    """Every distinct ICD condition must have ``is_chronic == 1``.

    Counts unique ``ICD10CDX`` codes. When ``is_chronic.xlsx`` is available it
    is the source of truth; a missing frame ``is_chronic`` is OK if the lookup
    value for that ICD is exactly 1.
    """
    title = "Every condition in the data is chronic"

    if df is None or df.empty:
        return GateResult(
            id="all_conditions_chronic",
            title=title,
            passed=False,
            summary="Could not run this check — the analysis table is empty.",
            detail="Empty frame.",
        )

    if "ICD10CDX" not in df.columns:
        return GateResult(
            id="all_conditions_chronic",
            title=title,
            passed=False,
            summary=(
                "Could not run this check — the disease-code column (ICD10CDX) "
                "is missing from the table."
            ),
            detail="Need ICD10CDX.",
        )

    if "is_chronic" in df.columns:
        cond = (
            df.groupby("ICD10CDX", as_index=False)
            .agg(frame_is_chronic=("is_chronic", "max"))
        )
        cond["frame_is_chronic"] = pd.to_numeric(cond["frame_is_chronic"], errors="coerce")
    else:
        cond = df[["ICD10CDX"]].drop_duplicates().copy()
        cond["frame_is_chronic"] = pd.NA

    n_conditions = int(len(cond))
    bad_codes: list[str] = []

    if chronic_lookup is not None:
        if not {"ICD10CDX", "is_chronic"}.issubset(chronic_lookup.columns):
            return GateResult(
                id="all_conditions_chronic",
                title=title,
                passed=False,
                summary=(
                    "Could not finish this check — is_chronic.xlsx is missing "
                    "the ICD10CDX / is_chronic columns."
                ),
                detail="is_chronic.xlsx schema incomplete.",
            )
        lookup = (
            chronic_lookup[["ICD10CDX", "is_chronic"]]
            .drop_duplicates("ICD10CDX")
            .rename(columns={"is_chronic": "lookup_is_chronic"})
            .copy()
        )
        lookup["lookup_is_chronic"] = pd.to_numeric(
            lookup["lookup_is_chronic"], errors="coerce"
        )
        cond = cond.merge(lookup, on="ICD10CDX", how="left")
        # Exactly 1 required on the allowlist.
        not_exactly_one = cond["lookup_is_chronic"].isna() | (cond["lookup_is_chronic"] != 1)
        frame_contradicts = cond["frame_is_chronic"].notna() & (cond["frame_is_chronic"] != 1)
        bad = not_exactly_one | frame_contradicts
        source = "is_chronic.xlsx (require is_chronic == 1)"
        flag_col = "lookup_is_chronic"
    else:
        bad = cond["frame_is_chronic"].isna() | (cond["frame_is_chronic"] != 1)
        source = "frame is_chronic only (is_chronic.xlsx not loaded; require == 1)"
        flag_col = "frame_is_chronic"

    n_bad = int(bad.sum())
    n_eq_one = int((~bad).sum())
    if n_bad:
        bad_codes = cond.loc[bad, "ICD10CDX"].astype(str).tolist()

    passed = n_bad == 0

    flag_vals = (
        cond.loc[~bad, flag_col].dropna().astype(int).unique().tolist()
        if flag_col in cond.columns
        else []
    )
    detail = (
        f"Unique ICD10CDX conditions: {n_conditions}; "
        f"with is_chronic == 1: {n_eq_one}; "
        f"not equal to 1: {n_bad}; "
        f"source: {source}"
    )
    if passed:
        detail += f"; all unique conditions have is_chronic == 1 (values seen: {flag_vals or [1]})"
    elif bad_codes:
        detail += f"; examples not == 1: {', '.join(bad_codes[:8])}"

    if passed:
        summary = (
            f"Pass — all {n_conditions:,} distinct disease codes have "
            "is_chronic = 1 on the allowlist."
        )
    else:
        summary = (
            f"Fail — {n_bad:,} of {n_conditions:,} distinct disease codes do not "
            "have is_chronic = 1"
            + (f" (examples: {', '.join(bad_codes[:5])})" if bad_codes else "")
            + ". This project should only keep long-term (chronic) conditions."
        )

    return GateResult(
        id="all_conditions_chronic",
        title=title,
        passed=passed,
        summary=summary,
        detail=detail,
        n_violations=n_bad,
        meta={
            "n_conditions": n_conditions,
            "n_bad_conditions": n_bad,
            "n_eq_one": n_eq_one,
            "require": "is_chronic == 1",
        },
    )


def check_adherence_bounds(df: pd.DataFrame) -> GateResult:
    """Adherence ratio must stay within 0–100%."""
    title = "Adherence stays between 0% and 100%"

    if "meps_adherence_ratio" not in df.columns:
        return GateResult(
            id="adherence_bounds",
            title=title,
            passed=False,
            summary=(
                "Could not run this check — the adherence percentage column "
                "is missing from the data."
            ),
            detail="Need 'meps_adherence_ratio'.",
        )

    ratio = pd.to_numeric(df["meps_adherence_ratio"], errors="coerce")
    valid = ratio.dropna()
    below = valid < 0
    above = valid > 100
    n_bad = int(below.sum() + above.sum())
    passed = n_bad == 0

    if passed:
        summary = (
            f"Pass — all {len(valid):,} adherence scores fall between 0% and 100%. "
            "Scores below 0 or above 100 would not make sense for a coverage percentage."
        )
    else:
        summary = (
            f"Fail — {int(below.sum()):,} score(s) below 0% and "
            f"{int(above.sum()):,} score(s) above 100%. Adherence is a percentage "
            "of days covered and must stay inside that range."
        )

    return GateResult(
        id="adherence_bounds",
        title=title,
        passed=passed,
        summary=summary,
        detail=f"below 0: {int(below.sum())}; above 100: {int(above.sum())}",
        n_violations=n_bad,
    )


def check_age_non_negative(df: pd.DataFrame) -> GateResult:
    """Numeric ages must not be negative (``unknown`` is allowed)."""
    title = "No negative ages"

    age = _age_series(df)
    if age is None:
        return GateResult(
            id="age_non_negative",
            title=title,
            passed=False,
            summary="Could not run this check — no age column was found in the data.",
            detail="Expected AGE or AGEyyX.",
        )

    as_str = age.astype(str).str.strip().str.lower()
    is_unknown = as_str.isin({"unknown", "nan", "none", ""})
    numeric = pd.to_numeric(age, errors="coerce")
    bad = (~is_unknown) & numeric.notna() & (numeric < 0)
    n_bad_rows = int(bad.sum())
    n_bad_people = n_bad_rows
    if "DUPERSID" in df.columns and n_bad_rows:
        n_bad_people = int(df.loc[bad, "DUPERSID"].nunique())
    passed = n_bad_rows == 0

    if passed:
        n_unknown = int(is_unknown.sum())
        summary = (
            "Pass — no person has a negative age. Missing ages labeled "
            f"'unknown' are allowed ({n_unknown:,} row(s))."
        )
        detail = (
            f"0 rows with numeric age < 0; "
            f"{n_unknown} row(s) labeled unknown; "
            f"{int(numeric.notna().sum())} numeric age values checked"
        )
    else:
        summary = (
            f"Fail — {n_bad_people:,} person(s) ({n_bad_rows:,} row(s)) have an "
            "age less than 0. A negative age is not valid (MEPS sometimes uses "
            "-1 to mean missing; those should be labeled 'unknown' instead of "
            "kept as a number)."
        )
        detail = f"{n_bad_rows} rows / {n_bad_people} persons with numeric age < 0"

    return GateResult(
        id="age_non_negative",
        title=title,
        passed=passed,
        summary=summary,
        detail=detail,
        n_violations=n_bad_rows,
        meta={"n_bad_rows": n_bad_rows, "n_bad_people": n_bad_people},
    )


# ---------------------------------------------------------------------------
# Lookup loader
# ---------------------------------------------------------------------------


def load_chronic_icd_lookup(meps_dir: str | Path | None = None) -> pd.DataFrame | None:
    """Load ``is_chronic.xlsx`` from the MEPS data directory when available."""
    candidates: list[Path] = []
    if meps_dir is not None:
        candidates.append(Path(meps_dir) / CHRONIC_ICD_FILE)
    here = Path(__file__).resolve().parent
    candidates.extend(
        [
            here.parent.parent.parent / "data" / "MEPS" / CHRONIC_ICD_FILE,
            here.parent.parent / "data" / "MEPS" / CHRONIC_ICD_FILE,
            here.parent / CHRONIC_ICD_FILE,
        ]
    )
    try:
        from clean_meps import resolve_meps_dir

        candidates.insert(0, resolve_meps_dir() / CHRONIC_ICD_FILE)
    except Exception:
        pass

    for path in candidates:
        if path.exists():
            try:
                return pd.read_excel(path, engine="calamine")
            except Exception:
                try:
                    return pd.read_excel(path)
                except Exception:
                    continue
    return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_all_gates(
    df: pd.DataFrame | None,
    *,
    chronic_lookup: pd.DataFrame | None = None,
    one_hot: pd.DataFrame | None = None,
    one_hot_groups: dict[str, list[str]] | None = None,
) -> list[GateResult]:
    """Run every gate against ``df`` (and optional lookup / one-hot matrix)."""
    if df is None:
        return [
            GateResult(
                id="data_available",
                title="Analysis data is available",
                passed=False,
                summary=(
                    "Fail — no analysis table is loaded, so the quality checks "
                    "cannot run. Refresh or rebuild the year data first."
                ),
                detail="df is None",
            )
        ]

    if one_hot is None:
        encoded, groups = build_one_hot_matrix(df)
    else:
        encoded, groups = one_hot, (one_hot_groups or {})

    return [
        check_numerator_le_denominator(df),
        check_one_hot_encoding(encoded, groups),
        check_no_negative_rx_or_icd(df),
        check_all_conditions_chronic(df, chronic_lookup),
        check_adherence_bounds(df),
        check_age_non_negative(df),
    ]
