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
import gates as _gates_mod
from gates import load_chronic_icd_lookup, run_all_gates

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
def _cached_parquet(path_str: str, _mtime: float) -> pd.DataFrame:
    return pd.read_parquet(path_str)


@st.cache_data(show_spinner=False)
def _cached_excel(path_str: str, _mtime: float) -> pd.DataFrame:
    try:
        return pd.read_excel(path_str, engine="calamine")
    except Exception:
        return pd.read_excel(path_str)


def load_merged_frame() -> pd.DataFrame | None:
    """Load the prebuilt all-years parquet cache (from ``cache-all-years``)."""
    tables = all_years_output_dirs()[0]
    parquet = tables / ALL_YEARS_PARQUET
    xlsx = tables / "new_grouped_merge_df_chronic_drugs.xlsx"
    if parquet.exists():
        return _cached_parquet(str(parquet), parquet.stat().st_mtime)
    if xlsx.exists():
        return _cached_excel(str(xlsx), xlsx.stat().st_mtime)
    return None


@st.cache_data(show_spinner=False)
def load_all_years_filter_options() -> dict | None:
    """Precomputed sidebar options for the all-years view."""
    import json

    path = all_years_output_dirs()[0] / ALL_YEARS_FILTER_OPTIONS
    if not path.exists():
        return None
    return json.loads(path.read_text())


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
    return _cached_parquet(str(parquet), parquet.stat().st_mtime)


def load_year_bridge(year: int) -> pd.DataFrame | None:
    """Load the per-year patient-drug × condition bridge."""
    parquet = tables_dir(year) / BRIDGE_PARQUET
    if not parquet.exists():
        return None
    return _cached_parquet(str(parquet), parquet.stat().st_mtime)


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
    """True when the chronic-drug analysis frame exists (parquet preferred, xlsx ok)."""
    tables = tables_dir(year)
    return (tables / "new_grouped_merge_df_chronic_drugs.parquet").exists() or (
        tables / "new_grouped_merge_df_chronic_drugs.xlsx"
    ).exists()


def fmt_pct(value: float | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{value:.1f}%"


def fmt_int(value: float | int | None) -> str:
    if value is None or pd.isna(value):
        return "—"
    return f"{int(value):,}"


def load_main_frame(year: int) -> pd.DataFrame | None:
    """Load the chronic-drug person–drug frame for one year.

    Prefers parquet (what ``clean_meps`` / ``run_exports`` write for every year).
    Falls back to the Excel export when only that exists (older 2023-only workflow).
    """
    tables = tables_dir(year)
    parquet = tables / "new_grouped_merge_df_chronic_drugs.parquet"
    xlsx = tables / "new_grouped_merge_df_chronic_drugs.xlsx"
    if parquet.exists():
        return _cached_parquet(str(parquet), parquet.stat().st_mtime)
    if xlsx.exists():
        return _cached_excel(str(xlsx), xlsx.stat().st_mtime)
    return None


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


def condition_compare_figure(
    df: pd.DataFrame,
    *,
    threshold: int,
) -> go.Figure:
    """Horizontal bar chart of mean adherence for user-selected conditions.

    Each bar is labeled with the condition name (y-axis) and mean adherence
    (text on the bar) so several conditions can be compared on one graph.
    """
    plot_df = (
        df.dropna(subset=["mean adherence"])
        .sort_values("mean adherence", ascending=True)
        .copy()
    )
    plot_df["_label"] = plot_df["condition name"].astype(str)
    plot_df["_text"] = plot_df["mean adherence"].map(lambda v: f"{v:.1f}%")
    plot_df["_hover_n"] = plot_df["n patients"] if "n patients" in plot_df.columns else 0

    fig = px.bar(
        plot_df,
        x="mean adherence",
        y="_label",
        orientation="h",
        color="_label",
        text="_text",
        labels={"mean adherence": "Mean adherence (%)", "_label": ""},
        title="Mean adherence by selected condition",
    )
    fig.update_traces(
        textposition="outside",
        cliponaxis=False,
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Mean adherence: %{x:.1f}%<br>"
            "Patients: %{customdata[0]:,}<extra></extra>"
        ),
        customdata=plot_df[["_hover_n"]].to_numpy(),
        showlegend=True,
    )
    fig.add_vline(
        x=threshold,
        line_dash="dot",
        line_color="red",
        annotation_text=f"{threshold}%",
        annotation_position="top",
    )
    n = len(plot_df)
    fig.update_layout(
        height=max(380, 48 * n + 140),
        margin=dict(t=70, l=10, r=90, b=40),
        xaxis=dict(range=[0, 115], title="Mean adherence (%)"),
        yaxis=dict(title=""),
        legend_title_text="Condition",
        legend=dict(orientation="v", yanchor="top", y=1, xanchor="left", x=1.02),
    )
    return fig


@st.cache_data(show_spinner=False)
def year_findings(year: int | None, threshold: int) -> dict | None:
    """Summarize chronic-drug + PSTATS exports for the Home tab.

    ``year`` is ``None`` for the merged all-years frame.
    """
    if year is None:
        df = load_merged_frame()
        bridge = load_bridge_frame()
    else:
        df = load_main_frame(year)
        bridge = load_year_bridge(year)
    if df is None or "meps_adherence_ratio" not in df.columns:
        return None

    ratio = df["meps_adherence_ratio"]
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


def render_year_findings(year: int | None, findings: dict) -> None:
    thr = findings["threshold"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Patients", fmt_int(findings["patients"]))
    m2.metric("Person–drug pairs", fmt_int(findings["rows"]))
    m3.metric("Mean adherence", fmt_pct(findings["mean"]))
    m4.metric(f"Pairs ≥ {thr}%", fmt_pct(findings["pct_ge_threshold"]))

    cohort_scope = (
        "each survey year (2020–2023)"
        if year is None
        else "the year"
    )
    st.markdown(
        f"""
- **Cohort**: chronic medications linked to chronic conditions, with eligible days based on
  survey participation for {cohort_scope}.
- **Scale**: {fmt_int(findings['patients'])} patients · {fmt_int(findings['drugs'])} unique drugs ·
  {fmt_int(findings['conditions'])} conditions.
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
        index=0,  # default: All years
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
                _cached_parquet.clear()
                _cached_excel.clear()
                load_all_years_filter_options.clear()
                st.success(f"{year_label} data refreshed.")
                st.rerun()
            except Exception as exc:
                st.error(f"Refresh failed: {exc}")

    if st.button("Rebuild analysis frame", use_container_width=True):
        cached_build.clear()
        _cached_parquet.clear()
        _cached_excel.clear()
        load_all_years_filter_options.clear()
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


tab_home, tab_analysis, tab_method, tab_viz, tab_gates = st.tabs(
    ["Home", "Analysis", "Methodology", "Visualization", "Gates"]
)


# ---- Home -----------------------------------------------------------------

with tab_home:
    st.title("MEPS Medical Adherence")

    st.subheader("Project Goals")
    st.markdown(
        """
- Identify chronic conditions and medication groups associated with lower adherence.
- Examine the economic, clinical, and demographic factors linked to adherence patterns.
- Support the development of future predictive models for medication non-adherence.
- Produce transparent, reproducible, and comparable results across multiple years.
        """
    )

    st.subheader("What Is Medication Adherence?")
    st.markdown(
        """
- Medication adherence refers to how consistently a person takes medication as prescribed.
- This includes taking the correct medication quantity over a prolonged period of time.
- MEPS data is survey-based research where each round happens every 3–4 months to estimate
  refill activity and days of medication supply. These measures indicate whether medication
  was likely available to the patient, rather than confirming that each dose was taken.
- Adherence is especially important for chronic conditions that require continuous, long-term treatment.
- Poor medication adherence is associated with reduced disease control, preventable
  hospitalizations, and higher healthcare costs.
        """
    )

    st.subheader("About the Project")
    st.markdown(
        """
- This project uses nationally representative data from the Agency for Healthcare Research
  and Quality’s Medical Expenditure Panel Survey (MEPS) for 2020–2023.
- Since the survey was only conducted a couple months per year — only used chronic conditions
  because they are more likely to have more complications/side effects which may lead to a
  higher chance of non-adherence.
- Prescription records were linked to chronic conditions, while medications used for acute
  illnesses or temporary flare-ups were excluded.
- The project evaluates factors that may influence medication adherence, including:
  - Economic status
  - Insurance coverage
  - Medication costs
  - Age
  - Sex
  - Chronic-condition burden
  - Other patient characteristics
        """
    )

    # Findings for the selected year (or all years)
    st.divider()
    st.subheader(f"Findings · {year_label}")
    st.caption(
        f"Chronic medications with participation-based eligible days. "
        f"Threshold: **{threshold}%**. "
        "Demographic filters apply on Analysis / Visualization."
    )
    findings = year_findings(year, threshold)
    if findings is None:
        st.warning(
            f"{year_label}: data not found — use Refresh year data in the sidebar."
        )
    else:
        render_year_findings(year, findings)


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

# Worked-example counts from the documented 2023 build (decisions_log.md).
# Intermediate stage counts are year-specific; only 2023 is fully logged today.
METHOD_EXAMPLE_YEAR = 2023
METHOD_COUNTS_2023 = {
    "rx_fills_in": "192,275",
    "rx_persons_in": "11,858",
    "rx_fills_clean": "114,018",
    "rx_persons_clean": "9,255",
    "chronic_cond_rows": "30,372",
    "chronic_cond_persons": "10,137",
    "linked_rows": "84,334",
    "linked_persons": "6,820",
    "person_drug_all": "21,718",
    "person_drug_chronic": "16,461",
    "persons_final": "5,968",
}


def _method_file_stems(y: int) -> dict[str, str]:
    files = YEAR_FILES[y]
    return {
        "rx": Path(files["rx"]).stem,
        "clnk": Path(files["clnk"]).stem,
        "cond": Path(files["cond"]).stem,
        "person": Path(files["person"]).stem,
    }


def render_methodology(
    *,
    example_year: int,
    year_label: str,
    is_all_years: bool,
    threshold: int,
    final_rows: int | None = None,
    final_persons: int | None = None,
) -> None:
    """Render the Methods narrative for All years or a single-year selection."""
    stems = _method_file_stems(example_year)
    c = METHOD_COUNTS_2023
    # When viewing a non-2023 year, keep the same steps but do not imply
    # 2023 intermediate counts apply. Final sample can come from the loaded frame.
    use_2023_counts = example_year == METHOD_EXAMPLE_YEAR
    if use_2023_counts:
        n_final = c["person_drug_chronic"]
        n_persons = c["persons_final"]
        n_pairs_pre = c["person_drug_all"]
    else:
        n_final = f"{final_rows:,}" if final_rows is not None else "the retained"
        n_persons = f"{final_persons:,}" if final_persons is not None else "the retained"
        n_pairs_pre = "the aggregated"

    st.header(f"Methodology · {year_label}")
    st.subheader("Methods")

    if is_all_years:
        st.markdown(
            "The same cleaning and adherence pipeline is applied independently to each "
            f"MEPS year ({YEARS[0]}–{YEARS[-1]}). The steps below use **{example_year}** "
            "as the worked example (file numbers and sample sizes differ by year)."
        )
    elif not use_2023_counts:
        st.markdown(
            f"The steps below describe the {example_year} pipeline. Intermediate "
            f"row counts illustrated in the project documentation are from the "
            f"{METHOD_EXAMPLE_YEAR} build; final sample size reflects the loaded "
            f"{example_year} export when available."
        )

    st.markdown("**Step 1: Clean the prescription data.**")
    if use_2023_counts:
        st.markdown(
            f"The analysis began with {c['rx_fills_in']} prescription fills among "
            f"{c['rx_persons_in']} persons in the {example_year} MEPS Prescribed Medicines "
            f"file ({stems['rx']}). Records were retained when RXDAYSUP was between 1 and 989. "
            f"Values of 999, which represent as-needed medication use, and negative MEPS "
            f"sentinel values were excluded. Records without a valid medication start year "
            f"in RXBEGYRX were also removed. After cleaning, {c['rx_fills_clean']} fills "
            f"among {c['rx_persons_clean']} persons remained."
        )
    else:
        st.markdown(
            f"The analysis began with prescription fills in the {example_year} MEPS "
            f"Prescribed Medicines file ({stems['rx']}). Records were retained when "
            f"RXDAYSUP was between 1 and 989. Values of 999, which represent as-needed "
            f"medication use, and negative MEPS sentinel values were excluded. Records "
            f"without a valid medication start year in RXBEGYRX were also removed."
        )

    st.markdown("**Step 2: Identify chronic conditions.**")
    if use_2023_counts:
        st.markdown(
            f"The Medical Conditions file ({stems['cond']}) was merged with a "
            f"study-defined chronic-condition allowlist. Conditions with `is_chronic` "
            f"greater than zero were retained, resulting in {c['chronic_cond_rows']} "
            f"chronic-condition records among {c['chronic_cond_persons']} persons."
        )
    else:
        st.markdown(
            f"The Medical Conditions file ({stems['cond']}) was merged with a "
            f"study-defined chronic-condition allowlist. Conditions with `is_chronic` "
            f"greater than zero were retained."
        )

    st.markdown("**Step 3: Link prescriptions to chronic conditions.**")
    if use_2023_counts:
        st.markdown(
            f"Prescription fills were linked to chronic conditions through the CLNK file "
            f"({stems['clnk']}). Only prescribed medicine events, identified by EVENTYPE "
            f"equal to 8, were included. Records were matched using DUPERSID and the event "
            f"identifier, with LINKIDX from {stems['rx']} matched to EVNTIDX in CLNK. "
            f"Unlinked prescription fills were removed, producing {c['linked_rows']} "
            f"fill–condition records among {c['linked_persons']} persons."
        )
    else:
        st.markdown(
            f"Prescription fills were linked to chronic conditions through the CLNK file "
            f"({stems['clnk']}). Only prescribed medicine events, identified by EVENTYPE "
            f"equal to 8, were included. Records were matched using DUPERSID and the event "
            f"identifier, with LINKIDX from {stems['rx']} matched to EVNTIDX in CLNK. "
            f"Unlinked prescription fills were removed."
        )

    st.markdown("**Step 4: Remove duplicate prescription fills.**")
    st.markdown(
        "Because a single fill could be linked to multiple chronic conditions, the merged "
        "data could contain duplicate physical fills. Records were deduplicated using "
        "DUPERSID, DRUGIDX, and RXRECIDX before RXDAYSUP was summed. A separate "
        "patient–drug–condition table was retained to preserve information about all "
        "linked conditions without duplicating days of supply."
    )

    st.markdown("**Step 5: Create person–drug records.**")
    if use_2023_counts:
        st.markdown(
            f"Prescription fills were aggregated to one row per person and drug. "
            f"RXDAYSUP was summed, the earliest medication start year and valid start "
            f"month were retained, and linked chronic conditions were summarized. This "
            f"produced {n_pairs_pre} person–drug pairs."
        )
    else:
        st.markdown(
            "Prescription fills were aggregated to one row per person and drug. "
            "RXDAYSUP was summed, the earliest medication start year and valid start "
            "month were retained, and linked chronic conditions were summarized."
        )

    st.markdown("**Step 6: Restrict the sample to maintenance medications.**")
    if use_2023_counts:
        st.markdown(
            f"Medications were classified using a manually labeled list of 1,158 unique "
            f"RXNAME values: 518 chronic, 373 flare-up, and 267 non-chronic. Flare-up and "
            f"non-chronic treatments, such as antibiotics, short steroid courses, and "
            f"selected topical medications, were excluded. The resulting sample contained "
            f"{n_final} person–drug pairs among {n_persons} persons."
        )
    else:
        st.markdown(
            "Medications were classified using a manually labeled list of 1,158 unique "
            "RXNAME values: 518 chronic, 373 flare-up, and 267 non-chronic. Flare-up and "
            "non-chronic treatments, such as antibiotics, short steroid courses, and "
            "selected topical medications, were excluded. "
            + (
                f"The resulting {example_year} sample contained {n_final} person–drug "
                f"pairs among {n_persons} persons."
                if final_rows is not None
                else f"The resulting {example_year} sample retains chronic medications only."
            )
        )

    st.markdown("**Step 7: Add demographic and participation data.**")
    st.markdown(
        f"Demographic and survey-participation variables from the Full-Year Consolidated "
        f"file ({stems['person']}) were merged using DUPERSID. Missing age values were "
        f"labeled as “unknown.”"
    )

    st.markdown("**Step 8: Calculate person-level eligible days.**")
    st.markdown(
        "Eligible observation days were calculated using person-status and "
        "reference-period variables for rounds 3/1, 4/2, and 5/3. Respondents with "
        "full-year participation were assigned 365 eligible days. For other respondents, "
        "the observation window was based on their survey entry, participation, and exit "
        "dates. Death, relocation, or other participation-ending statuses closed the "
        "observation window. Unknown statuses were flagged rather than treated as active "
        "participation."
    )
    st.markdown(
        "Persons who did not participate in Round 5/3 were retained but flagged because "
        "their shorter observation period could produce higher adherence estimates."
    )

    st.markdown(f"**Step 9: Adjust for medications started during {example_year}.**")
    st.markdown(
        f"When a medication began during {example_year} and had a valid start month, the "
        f"drug-specific observation period was calculated from the first day of that month "
        f"through December 31. Medications that began before {example_year} or had an "
        f"unknown start month retained the person-level observation period. The final "
        f"denominator was the smaller of the person-level and drug-specific observation "
        f"windows."
    )

    st.markdown("**Step 10: Calculate medication adherence.**")
    st.markdown(
        "Adherence was calculated using a Proportion of Days Covered–style measure:"
    )
    st.latex(
        r"\text{Adherence} = "
        r"100 \times "
        r"\dfrac{\min(\text{summed RXDAYSUP},\, 365,\, \text{eligible days})}"
        r"{\text{eligible days}}"
    )
    st.markdown(
        f"The numerator was capped at the eligible observation period so adherence could "
        f"not exceed 100%. Adherence was recorded as missing when eligible days equaled "
        f"zero. A {threshold}% threshold was used only for exploratory visualizations and "
        f"was not considered a clinical standard."
    )

    st.markdown("**Step 11: Define the final analytic sample.**")
    if use_2023_counts:
        st.markdown(
            f"The final dataset contained {n_final} person–drug observations among "
            f"{n_persons} unique persons. The measure reflects estimated medication "
            f"availability rather than confirmed medication use. Important limitations "
            f"include the absence of exact prescription fill dates, missing days-of-supply "
            f"values, broad three-digit ICD-10 codes, study-defined chronic-condition and "
            f"medication classifications, and possible upward bias among respondents with "
            f"incomplete final-round participation."
        )
    else:
        sample_bit = (
            f"The final {example_year} dataset contained {n_final} person–drug "
            f"observations among {n_persons} unique persons. "
            if final_rows is not None
            else f"The final {example_year} dataset retains one row per person–drug pair "
            f"after chronic-condition and chronic-drug filters. "
        )
        st.markdown(
            sample_bit
            + "The measure reflects estimated medication availability rather than "
            "confirmed medication use. Important limitations include the absence of exact "
            "prescription fill dates, missing days-of-supply values, broad three-digit "
            "ICD-10 codes, study-defined chronic-condition and medication classifications, "
            "and possible upward bias among respondents with incomplete final-round "
            "participation."
        )


with tab_method:
    method_year = year if year is not None else METHOD_EXAMPLE_YEAR
    method_final_rows = None
    method_final_persons = None
    if frame_preview is not None and not frame_preview.empty:
        method_final_rows = len(frame_preview)
        if "DUPERSID" in frame_preview.columns:
            method_final_persons = int(frame_preview["DUPERSID"].nunique())
    render_methodology(
        example_year=method_year,
        year_label=year_label,
        is_all_years=(year_selection == ALL_YEARS_LABEL),
        threshold=int(threshold),
        final_rows=method_final_rows,
        final_persons=method_final_persons,
    )


# ---- Visualization --------------------------------------------------------

with tab_viz:
    st.header(f"Visualization · {year_label}")

    frame = frame_preview
    bridge = resolve_active_bridge(year_selection)
    if frame is None:
        st.warning("Data not found for this selection. Use Refresh year data in the sidebar.")
    else:
        viz_dist, viz_compare = st.tabs(["Distributions", "Compare conditions"])

        with viz_dist:
            n_before = len(frame)
            dist_frame = apply_filters(
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
                f"rows {n_before:,} → {len(dist_frame):,} · threshold {threshold}%"
            )

            if dist_frame.empty:
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
                    values = dist_frame["meps_adherence_ratio"].dropna()
                    ylabel = "Number of person–drug pairs"
                    xlabel = "Adherence ratio (%)"
                    title = "Distribution of person–drug adherence"
                    rank_df = None
                    label_col = None
                elif level == "Condition":
                    by = condition_level_adherence(dist_frame, bridge)
                    values = by["mean adherence"]
                    ylabel = "Number of conditions"
                    xlabel = "Average adherence by condition (%)"
                    title = "Distribution of condition-level average adherence"
                    rank_df = by
                    label_col = "condition name"
                elif level == "Drug × condition":
                    by = drug_level_adherence(dist_frame, bridge)
                    values = by["mean adherence"]
                    ylabel = "Number of drug × condition pairs"
                    xlabel = "Average adherence by drug × condition (%)"
                    title = "Distribution of drug × condition average adherence"
                    rank_df = by.assign(
                        label=by["drug name"].astype(str)
                        + " · "
                        + by["condition name"].astype(str)
                    )
                    label_col = "label"
                else:
                    by = drug_category_adherence(dist_frame)
                    values = by["mean adherence"]
                    ylabel = "Number of drug categories"
                    xlabel = "Average adherence by drug category (%)"
                    title = "Distribution of drug-category average adherence (TC1, else TC1S1)"
                    rank_df = by
                    label_col = "drug category"
                    n_tc1 = (
                        int(by["drug category"].astype(str).str.startswith("TC1-").sum())
                        if len(by)
                        else 0
                    )
                    n_tc1s1 = (
                        int(by["drug category"].astype(str).str.startswith("TC1S1-").sum())
                        if len(by)
                        else 0
                    )
                    st.caption(
                        f"Category rule: use **TC1** when present; otherwise **TC1S1**. "
                        f"This selection has {n_tc1:,} TC1 groups and "
                        f"{n_tc1s1:,} TC1S1 fallback groups."
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
                        st.info(
                            f"No groups have ≥ {min_n} patients under the current filters."
                        )
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

        with viz_compare:
            # Demographic filters only — ignore sidebar Condition so any set can be compared.
            compare_base = apply_filters(
                frame,
                year,
                genders=genders,
                age_range=age_range,
                conditions=[],
                incomes=incomes,
                insurance=insurance,
            )
            st.caption(
                "Pick any conditions to plot side-by-side. "
                "Gender / age / income / insurance sidebar filters still apply; "
                f"the sidebar Condition filter does not. Threshold {threshold}%."
            )
            if compare_base.empty:
                st.warning("No rows match the current demographic filters.")
            else:
                cond_by = condition_level_adherence(compare_base, bridge)
                if cond_by.empty or "condition name" not in cond_by.columns:
                    st.warning("No condition-level adherence available for this selection.")
                else:
                    available = (
                        cond_by.sort_values("n patients", ascending=False)["condition name"]
                        .astype(str)
                        .tolist()
                    )
                    picked = st.multiselect(
                        "Conditions to compare",
                        options=available,
                        default=[],
                        key="viz_compare_conditions",
                        help="Select as many conditions as you want (e.g. asthma, depression, ADHD).",
                    )
                    if not picked:
                        st.info("Select one or more conditions above to see them on one graph.")
                    else:
                        compare_df = (
                            cond_by[cond_by["condition name"].astype(str).isin(picked)]
                            .sort_values("mean adherence", ascending=False)
                            .reset_index(drop=True)
                        )
                        compare_fig = condition_compare_figure(
                            compare_df,
                            threshold=threshold,
                        )
                        st.plotly_chart(compare_fig, use_container_width=True)

                        display = compare_df[
                            [c for c in ("condition name", "mean adherence", "n patients") if c in compare_df.columns]
                        ].copy()
                        if "mean adherence" in display.columns:
                            display["mean adherence"] = display["mean adherence"].map(
                                lambda v: f"{v:.1f}%"
                            )
                        st.dataframe(display, use_container_width=True, hide_index=True)
                        st.caption(
                            f"{len(compare_df):,} conditions · "
                            f"overall mean of selected means "
                            f"{compare_df['mean adherence'].mean():.1f}%"
                        )


# ---- Gates ----------------------------------------------------------------

with tab_gates:
    # Always reload so edits to gates.py show up without a full server restart.
    import importlib

    importlib.reload(_gates_mod)
    load_chronic_icd_lookup = _gates_mod.load_chronic_icd_lookup
    run_all_gates = _gates_mod.run_all_gates

    st.header(f"Data quality gates · {year_label}")
    st.write(
        "These checks make sure the cleaned MEPS table is trustworthy before "
        "you trust the charts or train a model. Each gate is written in plain "
        "language — green means the check passed, red means something needs attention."
    )

    gate_frame = frame_preview
    if gate_frame is None:
        st.warning(
            "No analysis data is loaded for this selection. "
            "Use Refresh year data or Rebuild analysis frame in the sidebar."
        )
    else:
        try:
            meps_dir = resolve_meps_dir()
        except Exception:
            meps_dir = None
        chronic_lookup = load_chronic_icd_lookup(meps_dir)

        with st.spinner("Running quality gates…"):
            results = run_all_gates(gate_frame, chronic_lookup=chronic_lookup)

        n_pass = sum(1 for r in results if r.passed)
        n_fail = len(results) - n_pass
        m1, m2, m3 = st.columns(3)
        m1.metric("Checks run", f"{len(results)}")
        m2.metric("Passing", f"{n_pass}")
        m3.metric("Failing", f"{n_fail}")

        if chronic_lookup is None:
            st.info(
                "Could not find is_chronic.xlsx — the chronic-condition gate "
                "will rely only on the is_chronic column already in the table."
            )

        st.caption(
            f"Checked {len(gate_frame):,} person–drug rows for {year_label} "
            "(sidebar filters are not applied here — gates use the full loaded table)."
        )
        st.markdown(
            """
**What each gate checks**
1. **Days covered ≤ eligible days** — `total_valid_days` ≤ `total_days_supply` (the adherence formula)
2. **One-hot is 0/1** — category flags (ICD, insurance, income, …) are proper one-hots
3. **No negative supply / disease codes** — `RXDAYSUP` must be **> 0**; `ICD10CDX` must be real (not negative); technical details show both ranges
4. **Conditions are chronic** — every unique ICD has `is_chronic == 1` in `is_chronic.xlsx`
5. **Adherence in 0–100%** — `meps_adherence_ratio`
6. **No negative ages** — `AGE` / `AGEyyX` (`unknown` is OK)
            """
        )

        for result in results:
            icon = "✅" if result.passed else "❌"
            status = "PASS" if result.passed else "FAIL"
            with st.container(border=True):
                st.markdown(f"### {icon} {result.title}")
                st.markdown(f"**{status}** — {result.summary}")
                with st.expander("Technical details"):
                    st.code(result.detail or "(no technical details)")
                    st.caption(f"Violations counted: {result.n_violations:,}")
