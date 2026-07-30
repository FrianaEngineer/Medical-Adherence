"""MEPS Medical Adherence Streamlit app.

Run from this directory:

    streamlit run simple_app.py

Prebuild the all-years cache (recommended; do this in a terminal, not in the UI)::

    python clean_meps.py cache-all-years

Uses ``clean_meps`` for builds/exports and reads cached tables/graphs from
``../output/<year>/`` (and ``../output/all_years/``) when they already exist.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from clean_meps import (
    YEAR_FILES,
    build,
    export_merged_all_years,
    normalize_age_column,
    output_dirs,
    all_years_output_dirs,
    resolve_meps_dir,
    run_exports,
    write_log,
)

# Cache filenames (match clean_meps.ALL_YEARS_*); kept local so Streamlit
# hot-reload does not break if it briefly holds a stale clean_meps module.
ALL_YEARS_PARQUET = "new_grouped_merge_df_chronic_drugs.parquet"
ALL_YEARS_FILTER_OPTIONS = "filter_options.json"
BRIDGE_PARQUET = "patient_drug_condition_bridge.parquet"

YEARS = sorted(YEAR_FILES)
ALL_YEARS_LABEL = "All years"
YEAR_OPTIONS = [ALL_YEARS_LABEL, *YEARS]

# MEPS codebook labels
SEX_LABELS = {1: "Male", 2: "Female"}
POVCAT_LABELS = {
    1: "Poor / negative",
    2: "Near poor",
    3: "Low income",
    4: "Middle income",
    5: "High income",
}
# INSCOV{yy}: 1 any private, 2 public only → Insured; 3 (or 0 if remapped) → Uninsured
INSCOV_FILTER_LABELS = {
    "Insured": {1, 2},
    "Uninsured": {0, 3},
}

st.set_page_config(
    page_title="MEPS Medical Adherence",
    page_icon="💊",
    layout="wide",
    initial_sidebar_state="expanded",
)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def tables_dir(year: int) -> Path:
    return output_dirs(year)[0]


def graphs_dir(year: int) -> Path:
    return output_dirs(year)[1]


def table_path(year: int | str, name: str) -> Path:
    if year == "all_years" or year is None:
        return all_years_output_dirs()[0] / name
    return tables_dir(int(year)) / name


def graph_path(year: int, name: str) -> Path:
    return graphs_dir(year) / name


def age_col(year: int) -> str:
    return f"AGE{year % 100:02d}X"


def povcat_col(year: int) -> str:
    return f"POVCAT{year % 100:02d}"


def inscov_col(year: int) -> str:
    return f"INSCOV{year % 100:02d}"


def _harmonize_year_columns(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Rename AGE/POVCAT/INSCOV{yy} to shared names so years can be stacked."""
    out = df.copy()
    renames = {}
    for src, dest in (
        (age_col(year), "AGE"),
        (povcat_col(year), "POVCAT"),
        (inscov_col(year), "INSCOV"),
    ):
        if src in out.columns and dest not in out.columns:
            renames[src] = dest
    if renames:
        out = out.rename(columns=renames)
    out["YEAR"] = year
    return out


@st.cache_data(show_spinner=False)
def load_merged_frame() -> pd.DataFrame | None:
    """Load the prebuilt all-years parquet cache (from ``cache-all-years``)."""
    tables = all_years_output_dirs()[0]
    parquet = tables / ALL_YEARS_PARQUET
    xlsx = tables / "new_grouped_merge_df_chronic_drugs.xlsx"
    if parquet.exists():
        return pd.read_parquet(parquet)
    if xlsx.exists():
        # Legacy fallback if only the old Excel merge exists
        return pd.read_excel(xlsx)
    return None


@st.cache_data(show_spinner=False)
def load_all_years_filter_options() -> dict | None:
    """Precomputed sidebar options for the all-years view."""
    import json

    path = all_years_output_dirs()[0] / ALL_YEARS_FILTER_OPTIONS
    if not path.exists():
        return None
    return json.loads(path.read_text())


@st.cache_data(show_spinner=False)
def load_bridge_frame() -> pd.DataFrame | None:
    """Load the all-years patient-drug × condition bridge (one row per pair × chronic ICD)."""
    tables = all_years_output_dirs()[0]
    parquet = tables / BRIDGE_PARQUET
    if not parquet.exists():
        st.warning(
            "Bridge parquet not found — condition-level views will fall back to primary-ICD "
            "attribution. Rerun `python clean_meps.py cache-all-years` to enable full "
            "multi-condition attribution."
        )
        return None
    return pd.read_parquet(parquet)


@st.cache_data(show_spinner=False)
def load_year_bridge(year: int) -> pd.DataFrame | None:
    """Load the per-year patient-drug × condition bridge."""
    parquet = tables_dir(year) / BRIDGE_PARQUET
    if not parquet.exists():
        return None
    return pd.read_parquet(parquet)


def resolve_active_bridge(year_selection: int | str) -> pd.DataFrame | None:
    if year_selection == ALL_YEARS_LABEL:
        return load_bridge_frame()
    return load_year_bridge(int(year_selection))


def condition_view(pd_df: pd.DataFrame | None, bridge: pd.DataFrame | None) -> pd.DataFrame | None:
    """Expand patient_drug rows to one-row-per-linked-chronic-condition via the bridge.

    Filters must already be applied to ``pd_df``; the inner join propagates them.
    Falls back to ``pd_df`` unchanged (primary-ICD attribution) when the bridge is missing.
    """
    if bridge is None or pd_df is None or pd_df.empty:
        return pd_df
    drop_cols = [
        c
        for c in ("ICD10CDX", "ICD10CDX_LABEL", "primary_ICD10CDX", "primary_ICD10CDX_LABEL")
        if c in pd_df.columns
    ]
    keys = ["DUPERSID", "DRUGIDX"]
    if "YEAR" in pd_df.columns and "YEAR" in bridge.columns:
        keys.append("YEAR")
    return bridge.merge(pd_df.drop(columns=drop_cols), on=keys, how="inner")


def resolve_active_frame(year_selection: int | str) -> tuple[pd.DataFrame | None, int | None, str]:
    """Return (frame, year_or_None_for_all, display_label)."""
    if year_selection == ALL_YEARS_LABEL:
        return load_merged_frame(), None, ALL_YEARS_LABEL
    y = int(year_selection)
    return prepare_frame(load_main_frame(y), y), y, str(y)


def sample_size_summary(
    df: pd.DataFrame,
    bridge: pd.DataFrame | None = None,
    *,
    min_n: int = 10,
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Patient counts for conditions and drug–condition pairs vs ``min_n``.

    Returns (condition_table, pair_table, summary_counts). Uses the bridge so a
    patient-drug pair contributes to every chronic condition it links to.
    """
    empty = pd.DataFrame()
    summary = {
        "n_conditions": 0,
        "conditions_lt": 0,
        "conditions_ge": 0,
        "n_pairs": 0,
        "pairs_lt": 0,
        "pairs_ge": 0,
        "min_n": min_n,
    }
    if df is None or df.empty or "ICD10CDX_LABEL" not in df.columns:
        return empty, empty, summary

    cv = condition_view(df, bridge)
    if cv is None or cv.empty or "ICD10CDX_LABEL" not in cv.columns:
        return empty, empty, summary

    cond = (
        cv.groupby("ICD10CDX_LABEL", as_index=False)
        .agg(**{"n patients": ("DUPERSID", "nunique")})
        .rename(columns={"ICD10CDX_LABEL": "condition name"})
        .sort_values("n patients", ascending=False)
    )
    summary["n_conditions"] = int(len(cond))
    summary["conditions_lt"] = int((cond["n patients"] < min_n).sum())
    summary["conditions_ge"] = int((cond["n patients"] >= min_n).sum())

    pairs = empty
    if "RXNAME" in cv.columns:
        pairs = (
            cv.groupby(["ICD10CDX_LABEL", "RXNAME"], as_index=False)
            .agg(**{"n patients": ("DUPERSID", "nunique")})
            .rename(columns={"ICD10CDX_LABEL": "condition name", "RXNAME": "drug name"})
            .sort_values("n patients", ascending=False)
        )
        summary["n_pairs"] = int(len(pairs))
        summary["pairs_lt"] = int((pairs["n patients"] < min_n).sum())
        summary["pairs_ge"] = int((pairs["n patients"] >= min_n).sum())

    return cond, pairs, summary


def exports_ready_selection(year_selection: int | str) -> bool:
    if year_selection == ALL_YEARS_LABEL:
        tables = all_years_output_dirs()[0]
        return (tables / ALL_YEARS_PARQUET).exists() or (
            tables / "new_grouped_merge_df_chronic_drugs.xlsx"
        ).exists()
    return exports_ready(int(year_selection))
@st.cache_data(show_spinner=False)
def load_table(year: int, name: str) -> pd.DataFrame | None:
    path = table_path(year, name)
    if not path.exists():
        return None
    if path.suffix.lower() == ".csv":
        return pd.read_csv(path)
    return pd.read_excel(path)


@st.cache_data(show_spinner=False)
def list_graphs(year: int) -> list[str]:
    gdir = graphs_dir(year)
    if not gdir.exists():
        return []
    return sorted(p.name for p in gdir.glob("*.png"))


@st.cache_data(show_spinner=False)
def list_tables(year: int) -> list[str]:
    tdir = tables_dir(year)
    if not tdir.exists():
        return []
    return sorted(p.name for p in tdir.iterdir() if p.suffix.lower() in {".xlsx", ".csv"})


@st.cache_data(show_spinner="Building adherence frame…")
def cached_build(
    year: int,
    drug_chronic_only: bool = True,
    pstats_denominator: bool = True,
) -> tuple[pd.DataFrame, dict]:
    df, log = build(
        year,
        drug_chronic_only=drug_chronic_only,
        pstats_denominator=pstats_denominator,
    )
    return df, log.to_dict()


@st.cache_data(show_spinner="Loading demographics…")
def load_sex_lookup(year: int) -> pd.DataFrame | None:
    """Join gender for older exports that predate the SEX column."""
    try:
        path = resolve_meps_dir() / YEAR_FILES[year]["person"]
        sex = pd.read_excel(path, engine="calamine", usecols=["DUPERSID", "SEX"])
        return sex.drop_duplicates("DUPERSID")
    except Exception:
        return None


def exports_ready(year: int) -> bool:
    return table_path(year, "new_grouped_merge_df_chronic_drugs.xlsx").exists()


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.1f}%"


def fmt_int(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{int(value):,}"


def load_main_frame(year: int) -> pd.DataFrame | None:
    return load_table(year, "new_grouped_merge_df_chronic_drugs.xlsx")


def enrich_demographics(df: pd.DataFrame, year: int) -> pd.DataFrame:
    """Ensure SEX is present; older exports may lack it. Normalize age -1 → unknown."""
    out = normalize_age_column(df, year)
    if "SEX" not in out.columns:
        sex = load_sex_lookup(year)
        if sex is not None:
            out = out.merge(sex, on="DUPERSID", how="left")
    return out


def prepare_frame(df: pd.DataFrame | None, year: int) -> pd.DataFrame | None:
    if df is None:
        return None
    return enrich_demographics(df, year)


def apply_filters(
    df: pd.DataFrame,
    year: int | None,
    *,
    genders: list[str],
    age_range: tuple[int, int],
    conditions: list[str],
    incomes: list[str],
    insurance: list[str],
) -> pd.DataFrame:
    """Filter person–drug rows by sidebar demographic controls.

    Empty gender, income, or insurance selection → no rows. Empty condition selection → all
    conditions. Age slider applies to numeric ages; rows with age ``unknown`` are kept.
    ``year`` may be ``None`` for the merged all-years frame (uses AGE / POVCAT / INSCOV).
    """
    out = df
    if year is None:
        a_col = "AGE" if "AGE" in out.columns else None
        p_col = "POVCAT" if "POVCAT" in out.columns else None
        i_col = "INSCOV" if "INSCOV" in out.columns else None
    else:
        a_col = age_col(year) if age_col(year) in out.columns else ("AGE" if "AGE" in out.columns else None)
        p_col = povcat_col(year) if povcat_col(year) in out.columns else ("POVCAT" if "POVCAT" in out.columns else None)
        i_col = inscov_col(year) if inscov_col(year) in out.columns else ("INSCOV" if "INSCOV" in out.columns else None)

    if genders and "SEX" in out.columns:
        wanted_sex = {code for code, label in SEX_LABELS.items() if label in genders}
        out = out[out["SEX"].isin(wanted_sex)]

    if a_col is not None and age_range is not None:
        lo, hi = age_range
        age_num = pd.to_numeric(out[a_col], errors="coerce")
        is_unknown = out[a_col].astype(str).str.lower().eq("unknown")
        out = out[((age_num >= lo) & (age_num <= hi)) | is_unknown]

    if conditions and "ICD10CDX_LABEL" in out.columns:
        out = out[out["ICD10CDX_LABEL"].isin(conditions)]

    if incomes and p_col is not None:
        wanted_inc = {code for code, label in POVCAT_LABELS.items() if label in incomes}
        out = out[out[p_col].isin(wanted_inc)]

    if insurance and i_col is not None:
        wanted_ins: set[int] = set()
        for label in insurance:
            wanted_ins |= INSCOV_FILTER_LABELS.get(label, set())
        out = out[pd.to_numeric(out[i_col], errors="coerce").isin(wanted_ins)]

    return out


def apply_adherence_view(
    df: pd.DataFrame,
    view: str,
    value_col: str = "mean adherence",
    *,
    min_n: int = 10,
) -> pd.DataFrame:
    """Return all / top-10 / bottom-10 rows by mean adherence.

    Top/bottom 10 only keep groups with ``n patients >= min_n`` when that
    column is present (skips sparse 1-patient perfect/zero scores).
    ``All`` is sorted by ``n patients`` descending when available.
    """
    ranked = df
    if view.startswith("Top 10") and "n patients" in ranked.columns:
        ranked = ranked[ranked["n patients"] >= min_n]
    if view.startswith("Top 10 most"):
        return (
            ranked.sort_values(value_col, ascending=False)
            .head(10)
            .reset_index(drop=True)
        )
    if view.startswith("Top 10 least"):
        return (
            ranked.sort_values(value_col, ascending=True)
            .head(10)
            .reset_index(drop=True)
        )
    # All — prefer largest samples first
    if "n patients" in ranked.columns:
        return ranked.sort_values("n patients", ascending=False).reset_index(drop=True)
    return ranked.sort_values(value_col, ascending=False).reset_index(drop=True)


def drug_level_adherence(df: pd.DataFrame, bridge: pd.DataFrame | None = None) -> pd.DataFrame:
    """Mean adherence by drug × condition. Routes through the bridge so a pair contributes
    to every chronic condition it links to; falls back to primary-ICD when bridge is None."""
    base = condition_view(df, bridge)
    out = (
        base.dropna(subset=["meps_adherence_ratio"])
        .groupby(["RXNAME", "ICD10CDX_LABEL"], as_index=False)
        .agg(**{
            "mean adherence": ("meps_adherence_ratio", "mean"),
            "n patients": ("DUPERSID", "nunique"),
        })
    )
    return out.rename(columns={"RXNAME": "drug name", "ICD10CDX_LABEL": "condition name"})


def condition_level_adherence(df: pd.DataFrame, bridge: pd.DataFrame | None = None) -> pd.DataFrame:
    """Mean adherence by condition, routed through the bridge to include secondary chronic ICDs."""
    base = condition_view(df, bridge)
    out = (
        base.dropna(subset=["meps_adherence_ratio"])
        .groupby(["ICD10CDX_LABEL"], as_index=False)
        .agg(**{
            "mean adherence": ("meps_adherence_ratio", "mean"),
            "n patients": ("DUPERSID", "nunique"),
        })
    )
    return out.rename(columns={"ICD10CDX_LABEL": "condition name"})


def assign_drug_category(df: pd.DataFrame) -> pd.DataFrame:
    """Prefer TC1 therapeutic class; fall back to TC1S1 subclass when TC1 is missing."""
    out = df.copy()
    has_tc1 = "TC1" in out.columns
    has_tc1s1 = "TC1S1" in out.columns
    if not has_tc1 and not has_tc1s1:
        out["drug category"] = pd.NA
        return out

    if has_tc1:
        tc1 = pd.to_numeric(out["TC1"], errors="coerce")
    else:
        tc1 = pd.Series(pd.NA, index=out.index, dtype="Float64")

    if has_tc1s1:
        tc1s1 = pd.to_numeric(out["TC1S1"], errors="coerce")
    else:
        tc1s1 = pd.Series(pd.NA, index=out.index, dtype="Float64")

    use_tc1 = tc1.notna()
    labels = pd.Series(pd.NA, index=out.index, dtype="object")
    labels.loc[use_tc1] = "TC1-" + tc1.loc[use_tc1].astype(int).astype(str)
    labels.loc[~use_tc1 & tc1s1.notna()] = (
        "TC1S1-" + tc1s1.loc[~use_tc1 & tc1s1.notna()].astype(int).astype(str)
    )
    out["drug category"] = labels
    return out


def drug_category_adherence(df: pd.DataFrame) -> pd.DataFrame:
    """Mean adherence by drug category (TC1, else TC1S1)."""
    tagged = assign_drug_category(df)
    out = (
        tagged.dropna(subset=["meps_adherence_ratio", "drug category"])
        .groupby(["drug category"], as_index=False)
        .agg(**{
            "mean adherence": ("meps_adherence_ratio", "mean"),
            "n patients": ("DUPERSID", "nunique"),
        })
    )
    return out


def adherence_histogram_figure(
    values: pd.Series,
    *,
    threshold: int,
    title: str,
    xlabel: str,
    ylabel: str,
):
    """Interactive histogram with bins colored by adherence threshold."""
    clipped = values.dropna().clip(0, 100)
    bins = list(range(0, 110, 10))
    labels = [f"{bins[i]}-{bins[i + 1]}" for i in range(len(bins) - 1)]
    mids = [bins[i] + 5 for i in range(len(bins) - 1)]
    binned = pd.cut(
        clipped,
        bins=bins,
        right=False,
        include_lowest=True,
        labels=labels,
    )
    count_series = binned.value_counts().reindex(labels, fill_value=0)

    colors = []
    for i in range(len(labels)):
        left, right = bins[i], bins[i + 1]
        if right <= threshold:
            colors.append("#d9534f")
        elif left >= threshold:
            colors.append("#5cb85c")
        else:
            colors.append("#f0ad4e")

    n_meet = int((clipped >= threshold).sum())
    fig = go.Figure(
        data=[
            go.Bar(
                x=mids,
                y=count_series.values,
                width=9,
                marker_color=colors,
                customdata=labels,
                hovertemplate="%{customdata}%: %{y}<extra></extra>",
            )
        ]
    )
    fig.add_vline(
        x=threshold,
        line_dash="dot",
        line_color="red",
        annotation_text=f"{threshold}%",
        annotation_position="top",
    )
    fig.update_layout(
        title=f"{title}<br><sup>{n_meet} of {len(clipped)} meet the {threshold}% threshold</sup>",
        xaxis_title=xlabel,
        yaxis_title=ylabel,
        xaxis=dict(tickmode="array", tickvals=mids, ticktext=labels),
        showlegend=False,
        margin=dict(t=80, b=60),
        height=420,
    )
    return fig


def top_bottom_bar_figures(
    df: pd.DataFrame,
    *,
    label_col: str,
    value_col: str,
    threshold: int,
    title: str,
    top_n: int = 10,
) -> tuple | None:
    """Two separate horizontal bar charts: top-N and bottom-N by mean adherence."""
    ranked = df.dropna(subset=[value_col]).sort_values(value_col, ascending=False)
    if ranked.empty:
        return None

    top = ranked.head(top_n).copy()
    bottom = ranked.tail(top_n).copy()

    def _one_chart(piece: pd.DataFrame, *, heading: str, color: str):
        plot_df = piece.sort_values(value_col, ascending=True).copy()
        plot_df["_label"] = plot_df[label_col].astype(str).str.slice(0, 48)
        fig = px.bar(
            plot_df,
            x=value_col,
            y="_label",
            orientation="h",
            title=heading,
            labels={value_col: "Mean adherence (%)", "_label": ""},
            hover_data={label_col: True, "_label": False},
        )
        fig.update_traces(marker_color=color)
        fig.update_layout(
            height=420,
            margin=dict(t=60, l=10, r=20, b=40),
            xaxis=dict(range=[0, 100]),
            showlegend=False,
        )
        return fig

    top_fig = _one_chart(
        top,
        heading=f"Top {top_n} — highest mean adherence",
        color="#5cb85c",
    )
    bottom_fig = _one_chart(
        bottom,
        heading=f"Bottom {top_n} — lowest mean adherence",
        color="#d9534f",
    )
    return top_fig, bottom_fig


@st.cache_data(show_spinner=False)
def year_findings(year: int, threshold: int) -> dict | None:
    """Summarize chronic-drug + PSTATS exports for the Home tab."""
    df = load_table(year, "new_grouped_merge_df_chronic_drugs.xlsx")
    if df is None or "meps_adherence_ratio" not in df.columns:
        return None

    ratio = df["meps_adherence_ratio"]
    bridge = load_year_bridge(year)
    # Condition-level frame: bridge-joined so a pair counts for every linked chronic ICD.
    cond_frame = condition_view(df, bridge)
    by_condition = (
        cond_frame.groupby(["ICD10CDX", "ICD10CDX_LABEL"], as_index=False)
        .agg(
            mean_adherence=("meps_adherence_ratio", "mean"),
            n_patients=("DUPERSID", "nunique"),
        )
        .sort_values("mean_adherence", ascending=False)
    )
    # Condition grain is dense enough to require n >= 30 for headline top/low;
    # threshold-meeting count uses the same floor so tiny conditions don't inflate it.
    reliable = by_condition[by_condition["n_patients"] >= 30]
    high = reliable[reliable["mean_adherence"] >= threshold]
    low = reliable.nsmallest(3, "mean_adherence")
    top = reliable.head(3)

    persons = df.drop_duplicates("DUPERSID")
    part_counts = (
        persons["participation_type"].value_counts().to_dict()
        if "participation_type" in persons.columns
        else {}
    )

    # Lowest drug × condition combination, restricted to combos with ≥ 10 patients
    # so a 1-patient noise row can't headline the summary. Uses the bridge so a pair
    # with multiple linked chronic ICDs contributes to every combo.
    worst = None
    combos = (
        cond_frame.dropna(subset=["meps_adherence_ratio"])
        .groupby(["ICD10CDX_LABEL", "RXNAME"], as_index=False)
        .agg(
            ratio=("meps_adherence_ratio", "mean"),
            n=("DUPERSID", "nunique"),
        )
    )
    eligible = combos[combos["n"] >= 10].sort_values("ratio")
    if len(eligible):
        row = eligible.iloc[0]
        worst = {
            "condition": str(row["ICD10CDX_LABEL"]),
            "drug": str(row["RXNAME"]),
            "ratio": float(row["ratio"]),
            "n": int(row["n"]),
        }

    return {
        "rows": int(len(df)),
        "patients": int(df["DUPERSID"].nunique()),
        "drugs": int(df["RXNAME"].nunique()),
        "conditions": int(cond_frame["ICD10CDX"].nunique()) if "ICD10CDX" in cond_frame.columns else int(df["ICD10CDX"].nunique()),
        "mean": float(ratio.mean()),
        "median": float(ratio.median()),
        "pct_ge_threshold": float((ratio >= threshold).mean() * 100),
        "pct_lt_10": float((ratio < 10).mean() * 100),
        "conditions_ge_threshold": int(len(high)),
        "conditions_total": int(len(reliable)),
        "conditions_total_all": int(len(by_condition)),
        "top_conditions": top.to_dict("records"),
        "low_conditions": low.to_dict("records"),
        "participation": part_counts,
        "worst": worst,
        "threshold": threshold,
    }


def render_year_findings(year: int, findings: dict) -> None:
    thr = findings["threshold"]
    st.markdown(f"#### {year}")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Patients", fmt_int(findings["patients"]))
    m2.metric("Person–drug pairs", fmt_int(findings["rows"]))
    m3.metric("Mean adherence", fmt_pct(findings["mean"]))
    m4.metric(f"Pairs ≥ {thr}%", fmt_pct(findings["pct_ge_threshold"]))

    st.markdown(
        f"""
- **Cohort**: chronic medications linked to chronic conditions, with eligible days based on
  survey participation for the year.
- **Scale**: {fmt_int(findings['patients'])} patients · {fmt_int(findings['drugs'])} unique drugs ·
  {fmt_int(findings['conditions'])} conditions.
- **Central tendency**: mean {findings['mean']:.1f}% · median {findings['median']:.1f}%.
- **Threshold**: {findings['pct_ge_threshold']:.1f}% of person–drug pairs meet the
  {thr}% bar; {findings['pct_lt_10']:.1f}% fall below 10%.
- **Conditions**: {findings['conditions_ge_threshold']} of
  {findings['conditions_total']} condition groups (with ≥ 30 patients) average ≥ {thr}%.
  Total distinct chronic conditions in the frame: {findings['conditions_total_all']}.
        """
    )

    top_bits = "; ".join(
        f"{r['ICD10CDX_LABEL']} ({r['mean_adherence']:.0f}%, n={r['n_patients']})"
        for r in findings["top_conditions"]
    )
    low_bits = "; ".join(
        f"{r['ICD10CDX_LABEL']} ({r['mean_adherence']:.0f}%, n={r['n_patients']})"
        for r in findings["low_conditions"]
    )
    st.markdown(f"- **Highest mean condition adherence** (≥ 30 patients): {top_bits}")
    st.markdown(f"- **Lowest mean condition adherence** (≥ 30 patients): {low_bits}")

    if findings.get("worst"):
        w = findings["worst"]
        st.markdown(
            f"- **Lowest condition × drug combination** (≥ 10 patients): "
            f"{w['condition']} × {w['drug']} ({w['ratio']:.1f}%, n={w['n']})."
        )

    if findings.get("participation"):
        full = findings["participation"].get("full_year", 0)
        death = findings["participation"].get("ended_early_death", 0)
        left = findings["participation"].get("ended_early_left_ru", 0)
        st.markdown(
            f"- **Participation**: {fmt_int(full)} full-year respondents; "
            f"{fmt_int(death)} ended early (death); {fmt_int(left)} left the household."
        )


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------


with st.sidebar:
    st.header("Controls")
    year_selection = st.selectbox(
        "MEPS year",
        YEAR_OPTIONS,
        index=YEAR_OPTIONS.index(2023) if 2023 in YEAR_OPTIONS else 0,
    )
    frame_preview, year, year_label = resolve_active_frame(year_selection)

    st.divider()
    st.subheader("Filters")

    # Filter option lists: prefer all-years JSON cache so sidebar stays fast
    _preview = frame_preview
    _ay_opts = load_all_years_filter_options() if year is None else None

    gender_options = list(SEX_LABELS.values())
    income_options = [POVCAT_LABELS[k] for k in sorted(POVCAT_LABELS)]
    insurance_options = list(INSCOV_FILTER_LABELS.keys())

    if _ay_opts is not None:
        age_min = int(_ay_opts.get("age_min", 0))
        age_max = int(_ay_opts.get("age_max", 85))
        if age_min >= age_max:
            age_max = age_min + 1
        condition_options = list(_ay_opts.get("conditions") or [])
    else:
        age_candidates = []
        if _preview is not None:
            for col in (
                (["AGE"] if year is None else [age_col(year), "AGE"])
            ):
                if col in _preview.columns:
                    age_candidates.append(pd.to_numeric(_preview[col], errors="coerce"))
        if age_candidates:
            age_vals = pd.concat(age_candidates).dropna()
            age_min, age_max = int(age_vals.min()), int(age_vals.max())
            if age_min >= age_max:
                age_max = age_min + 1
        else:
            age_min, age_max = 0, 85

        if _preview is not None and "ICD10CDX_LABEL" in _preview.columns:
            condition_options = sorted(
                _preview["ICD10CDX_LABEL"].dropna().astype(str).unique()
            )
        else:
            condition_options = []

    def _reset_filters() -> None:
        st.session_state.filt_threshold = 60
        st.session_state.filt_genders = gender_options.copy()
        st.session_state.filt_age_range = (age_min, age_max)
        st.session_state.filt_conditions = []
        st.session_state.filt_incomes = income_options.copy()
        st.session_state.filt_insurance = insurance_options.copy()

    if "filt_threshold" not in st.session_state:
        _reset_filters()
    # Migrate away from the old 3-way insurance labels if still in session
    if "filt_insurance" not in st.session_state or not set(
        st.session_state.get("filt_insurance", [])
    ).issubset(set(insurance_options)):
        st.session_state.filt_insurance = insurance_options.copy()

    # Keep age range valid when year / data bounds change
    cur_age = st.session_state.get("filt_age_range", (age_min, age_max))
    lo = min(max(cur_age[0], age_min), age_max)
    hi = max(min(cur_age[1], age_max), age_min)
    if lo > hi:
        lo, hi = age_min, age_max
    st.session_state.filt_age_range = (lo, hi)

    if st.button("Reset filters", use_container_width=True):
        _reset_filters()
        st.rerun()

    threshold = st.slider(
        "Adherence threshold (%)",
        min_value=0,
        max_value=100,
        step=5,
        key="filt_threshold",
        help="Exploratory cut for “meeting threshold” counts and tables — not a clinical guideline.",
    )

    genders = st.multiselect(
        "Gender", gender_options, key="filt_genders",
        help="Leave empty to include all genders.",
    )
    age_range = st.slider("Age", min_value=age_min, max_value=age_max, key="filt_age_range")
    conditions = st.multiselect(
        "Condition",
        condition_options,
        key="filt_conditions",
        help="Leave empty to include all conditions.",
    )
    incomes = st.multiselect(
        "Income", income_options, key="filt_incomes",
        help="Leave empty to include all income levels.",
    )
    insurance = st.multiselect(
        "Insurance",
        insurance_options,
        key="filt_insurance",
        help=(
            "Insured = INSCOV 1 (any private) or 2 (public only). "
            "Uninsured = INSCOV 3 (or 0 if remapped). Leave empty to include all."
        ),
    )

    st.divider()
    ready = exports_ready_selection(year_selection)
    st.write("Data:", "✅ ready" if ready else "⚠️ missing — run below")

    if year is None:
        st.caption(
            "All-years cache: run in a terminal (faster than Refresh):\n"
            "`python clean_meps.py cache-all-years`"
        )

    if st.button("Refresh year data", type="primary", use_container_width=True):
        with st.spinner(
            f"Refreshing {year_label} data… this can take several minutes"
        ):
            try:
                if year is None:
                    # Prefer terminal `cache-all-years`; UI path reuses year exports
                    for y in YEARS:
                        if not exports_ready(y):
                            run_exports(y)
                    export_merged_all_years()
                else:
                    run_exports(year)
                    export_merged_all_years()
                load_table.clear()
                list_graphs.clear()
                list_tables.clear()
                cached_build.clear()
                load_sex_lookup.clear()
                year_findings.clear()
                load_merged_frame.clear()
                load_all_years_filter_options.clear()
                load_bridge_frame.clear()
                load_year_bridge.clear()
                st.success(f"{year_label} data refreshed.")
                st.rerun()
            except Exception as exc:
                st.error(f"Refresh failed: {exc}")

    if st.button("Rebuild analysis frame", use_container_width=True):
        cached_build.clear()
        load_merged_frame.clear()
        load_all_years_filter_options.clear()
        load_bridge_frame.clear()
        load_year_bridge.clear()
        with st.spinner(f"Rebuilding {year_label} analysis frame…"):
            try:
                if year is None:
                    for y in YEARS:
                        df_live, log_dict = cached_build(
                            y,
                            drug_chronic_only=True,
                            pstats_denominator=True,
                        )
                        write_log(log_dict)
                    export_merged_all_years()
                    merged = load_merged_frame()
                    n_rows = 0 if merged is None else len(merged)
                    n_pat = 0 if merged is None else int(merged["DUPERSID"].nunique())
                else:
                    df_live, log_dict = cached_build(
                        year,
                        drug_chronic_only=True,
                        pstats_denominator=True,
                    )
                    write_log(log_dict)
                    export_merged_all_years()
                    n_rows = len(df_live)
                    n_pat = int(df_live["DUPERSID"].nunique())
                st.success(
                    f"Built {n_rows:,} rows / {n_pat:,} patients ({year_label})"
                )
                st.rerun()
            except Exception as exc:
                st.error(f"Rebuild failed: {exc}")


# ---------------------------------------------------------------------------
# Tabs
# ---------------------------------------------------------------------------


tab_home, tab_analysis, tab_method, tab_viz = st.tabs(
    ["Home", "Analysis", "Methodology", "Visualization"]
)


# ---- Home -----------------------------------------------------------------

with tab_home:
    st.title("MEPS Medical Adherence")
    st.write(
        "A multi-year look at how consistently people fill chronic medications, "
        "using the Agency for Healthcare Research and Quality (AHRQ) "
        "Medical Expenditure Panel Survey (MEPS) for 2020–2023."
    )

    st.subheader("What is medical adherence?")
    st.markdown(
        """
**Medication adherence** is the extent to which a person takes medication as prescribed —
the right drug, dose, and schedule over time. In claims and survey research it is usually
approximated from refill patterns (days of supply on hand) rather than observed pill-taking.
Poor adherence is linked to worse disease control, preventable hospitalizations, and higher
costs, especially for chronic conditions that require continuous therapy.
        """
    )

    st.subheader("What this project is about")
    st.markdown(
        """
In this project, I use nationally representative data from the Agency for Healthcare Research
and Quality (AHRQ) Medical Expenditure Panel Survey (MEPS) for 2020–2023 to study how
consistently people maintain medication coverage for chronic conditions. Because MEPS is a
survey rather than a pharmacy transaction log, it does not provide exact refill dates or a
ready-made adherence measure, so I created the measure through feature engineering. I linked
prescription records to chronic conditions, excluded medications used for acute conditions or
temporary flare-ups, removed records with missing or unusable values, and combined multiple
prescription, days-supply, and survey-participation columns to estimate each person’s total
medication coverage. I also used survey response information (PSTATS — person status /
participation codes) to determine whether a person participated for the full year or had a
shorter observation period, allowing me to calculate an appropriate number of eligible days.
For each person–drug pair, I calculated a Proportion of Days Covered (PDC)–style adherence
ratio by dividing the estimated total days supplied by the eligible observation days and
capping the result at 100%. I use a 60% threshold as an exploratory indicator of possible
non-adherence rather than as a clinical standard. In addition to medication coverage, I examine
factors that may influence adherence, including economic status, insurance coverage, medication
costs, age, sex, chronic-condition burden, and other patient characteristics. The goal of the
project is to identify conditions and drug groups with lower adherence, understand the factors
associated with those patterns, support future predictive modeling, and produce transparent,
reproducible, and year-comparable results.
        """
    )

    st.subheader("Findings by year")
    st.caption(
        f"Summaries use chronic medications with participation-based eligible days. "
        f"Threshold: **{threshold}%**. "
        "Demographic filters apply on Analysis / Visualization."
    )

    any_findings = False
    for y in YEARS:
        findings = year_findings(y, threshold)
        if findings is None:
            st.warning(f"{y}: data not found — use Refresh year data in the sidebar.")
            continue
        any_findings = True
        with st.expander(f"{y} summary", expanded=(y == year)):
            render_year_findings(y, findings)

    if any_findings:
        st.markdown("##### Cross-year pattern")
        rows = []
        for y in YEARS:
            f = year_findings(y, threshold)
            if f is None:
                continue
            rows.append(
                {
                    "Year": y,
                    "Patients": f["patients"],
                    "Person–drug pairs": f["rows"],
                    "Mean adherence %": round(f["mean"], 1),
                    "Median %": round(f["median"], 1),
                    f"% pairs ≥ {threshold}": round(f["pct_ge_threshold"], 1),
                    "Conditions ≥ threshold": (
                        f"{f['conditions_ge_threshold']}/{f['conditions_total']}"
                    ),
                }
            )
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
            st.markdown(
                f"""
Across 2020–2023, mean chronic person–drug adherence sits in the high-50s to ~60%.
Condition-level averages vary widely — sparse codes can look extreme; read with sample-size
caution. The sidebar threshold is currently **{threshold}%**.
                """
            )


# ---- Analysis -------------------------------------------------------------

with tab_analysis:
    st.header(f"Analysis · {year_label}")

    frame = frame_preview
    bridge = resolve_active_bridge(year_selection)

    if frame is None:
        st.warning("Data not found for this selection. Use Refresh year data in the sidebar.")
    else:
        n_before = len(frame)
        frame = apply_filters(
            frame,
            year,
            genders=genders,
            age_range=age_range,
            conditions=conditions,
            incomes=incomes,
            insurance=insurance,
        )
        st.caption(
            f"Filtered rows: {n_before:,} → {len(frame):,} · threshold {threshold}%"
        )
        if bridge is None:
            st.info(
                "Condition-level view uses primary ICD only — rerun the export to enable "
                "full multi-condition attribution."
            )

        # Sample-size split at 10 unique patients (after filters)
        cond_counts, _pair_counts, size_sum = sample_size_summary(frame, bridge, min_n=10)
        if size_sum["n_conditions"]:
            st.caption(
                f"Conditions: **{size_sum['conditions_lt']:,}** with &lt; 10 patients · "
                f"**{size_sum['conditions_ge']:,}** with ≥ 10 "
                f"(of {size_sum['n_conditions']:,}). "
                f"Drug–condition pairs: **{size_sum['pairs_lt']:,}** &lt; 10 · "
                f"**{size_sum['pairs_ge']:,}** ≥ 10 "
                f"(of {size_sum['n_pairs']:,})."
            )
            with st.expander("Conditions by patient count"):
                st.dataframe(cond_counts, use_container_width=True, hide_index=True)
        else:
            st.caption("No conditions available under the current filters.")

        if frame.empty:
            st.warning("No rows match the current filters.")
        else:
            view = st.radio(
                "View",
                ["All", "Top 10 most adherent", "Top 10 least adherent"],
                horizontal=True,
                key="analysis_view",
                help="Top 10 lists only include groups with ≥ 10 patients.",
            )
            if view.startswith("Top 10"):
                st.caption("Top / bottom 10 restricted to groups with ≥ 10 patients.")

            drug_tab, cond_tab = st.tabs(["Drug level", "Condition level"])

            with drug_tab:
                drug_df = apply_adherence_view(drug_level_adherence(frame, bridge), view)
                st.dataframe(drug_df, use_container_width=True, hide_index=True)
                st.caption(f"{len(drug_df):,} drug × condition rows")

            with cond_tab:
                cond_df = apply_adherence_view(condition_level_adherence(frame, bridge), view)
                st.dataframe(cond_df, use_container_width=True, hide_index=True)
                st.caption(f"{len(cond_df):,} condition rows")


# ---- Methodology ----------------------------------------------------------

with tab_method:
    st.header(f"Methodology · {year_label}")
    st.write(
        "This section summarizes how the adherence measure is built from the "
        "Medical Expenditure Panel Survey (MEPS) prescription, condition, and "
        "survey-participation information."
    )

    st.subheader("Pipeline overview")
    st.markdown(
        """
1. Load prescription fill records for the selected year
2. Keep fills with a usable days-supply value (drop “as needed” and missing codes)
3. Keep fills with a usable medication start year
4. Keep only prescription-medicine event links
5. Link fills to chronic conditions using a chronic-condition allowlist
6. Collapse to one row per person–drug pair
7. Keep chronic medications only (exclude acute / flare-up drugs)
8. Add demographics and compute each person’s eligible days from survey participation
   (PSTATS — person status / participation codes)
9. Compute a Proportion of Days Covered (PDC)–style adherence ratio:
   estimated days supplied ÷ eligible days × 100, capped at 100%
        """
    )

    st.subheader("Key definitions")
    st.markdown(
        f"""
- **Eligible days**: how long the person is observed in the survey year (full year = 365;
  shorter if participation ends early)
- **Adherence ratio**: coverage of medication supply over eligible days
- **Exploratory threshold**: currently **{threshold}%** in the sidebar (not a clinical standard)
- **Unit of analysis**: one person–drug pair after chronic-condition and chronic-drug filters
        """
    )

    st.subheader("Acronyms")
    st.markdown(
        """
| Acronym | Meaning |
|--------|---------|
| **AHRQ** | Agency for Healthcare Research and Quality |
| **MEPS** | Medical Expenditure Panel Survey (sponsored by AHRQ) |
| **PDC** | Proportion of Days Covered — share of eligible days with medication supply on hand |
| **PSTATS** | Person status codes in MEPS that record survey participation / disposition by round |
| **ICD-10** | International Classification of Diseases, 10th Revision (condition diagnosis codes) |
| **NDC** | National Drug Code (medication product identifier) |
        """
    )


# ---- Visualization --------------------------------------------------------

with tab_viz:
    st.header(f"Visualization · {year_label}")

    frame = frame_preview
    bridge = resolve_active_bridge(year_selection)
    if frame is None:
        st.warning("Data not found for this selection. Use Refresh year data in the sidebar.")
    else:
        n_before = len(frame)
        frame = apply_filters(
            frame,
            year,
            genders=genders,
            age_range=age_range,
            conditions=conditions,
            incomes=incomes,
            insurance=insurance,
        )
        st.caption(
            f"Charts update with sidebar filters · "
            f"rows {n_before:,} → {len(frame):,} · threshold {threshold}%"
        )

        if frame.empty:
            st.warning("No rows match the current filters.")
        else:
            level = st.radio(
                "Chart level",
                [
                    "Person–drug",
                    "Condition",
                    "Drug × condition",
                    "Drug category (TC1 / TC1S1)",
                ],
                horizontal=True,
                key="viz_level",
            )

            if level == "Person–drug":
                values = frame["meps_adherence_ratio"].dropna()
                ylabel = "Number of person–drug pairs"
                xlabel = "Adherence ratio (%)"
                title = "Distribution of person–drug adherence"
                rank_df = None
                label_col = None
            elif level == "Condition":
                by = condition_level_adherence(frame)
                values = by["mean adherence"]
                ylabel = "Number of conditions"
                xlabel = "Average adherence by condition (%)"
                title = "Distribution of condition-level average adherence"
                rank_df = by
                label_col = "condition name"
            elif level == "Drug × condition":
                by = drug_level_adherence(frame)
                values = by["mean adherence"]
                ylabel = "Number of drug × condition pairs"
                xlabel = "Average adherence by drug × condition (%)"
                title = "Distribution of drug × condition average adherence"
                rank_df = by.assign(
                    label=by["drug name"].astype(str) + " · " + by["condition name"].astype(str)
                )
                label_col = "label"
            else:
                by = drug_category_adherence(frame)
                values = by["mean adherence"]
                ylabel = "Number of drug categories"
                xlabel = "Average adherence by drug category (%)"
                title = "Distribution of drug-category average adherence (TC1, else TC1S1)"
                rank_df = by
                label_col = "drug category"
                n_tc1 = int(by["drug category"].astype(str).str.startswith("TC1-").sum()) if len(by) else 0
                n_tc1s1 = int(by["drug category"].astype(str).str.startswith("TC1S1-").sum()) if len(by) else 0
                st.caption(
                    f"Category rule: use **TC1** when present; otherwise **TC1S1**. "
                    f"This selection has {n_tc1:,} TC1 groups and {n_tc1s1:,} TC1S1 fallback groups."
                )

            fig = adherence_histogram_figure(
                values,
                threshold=threshold,
                title=title,
                xlabel=xlabel,
                ylabel=ylabel,
            )
            st.plotly_chart(fig, use_container_width=True)

            below = int((values < threshold).sum())
            st.caption(
                f"{len(values):,} groups · mean {values.mean():.1f}% · "
                f"below {threshold}%: {below:,} · at/above: {len(values) - below:,}"
            )

            if rank_df is not None and label_col is not None and len(rank_df):
                st.subheader("Top / bottom by mean adherence")
                min_n = 10
                if "n patients" in rank_df.columns:
                    rank_df = rank_df[rank_df["n patients"] >= min_n].copy()
                st.caption(
                    f"{title.replace('Distribution of ', '')} "
                    f"(threshold {threshold}% · only groups with ≥ {min_n} patients)"
                )
                if rank_df.empty:
                    st.info(f"No groups have ≥ {min_n} patients under the current filters.")
                else:
                    rank_figs = top_bottom_bar_figures(
                        rank_df,
                        label_col=label_col,
                        value_col="mean adherence",
                        threshold=threshold,
                        title=title.replace("Distribution of ", ""),
                    )
                    if rank_figs is not None:
                        top_fig, bottom_fig = rank_figs
                        st.plotly_chart(top_fig, use_container_width=True)
                        st.plotly_chart(bottom_fig, use_container_width=True)
