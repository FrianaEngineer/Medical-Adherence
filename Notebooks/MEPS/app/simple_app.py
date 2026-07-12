"""MEPS Medical Adherence Streamlit app.

Run from this directory:

    streamlit run simple_app.py

Uses ``clean_meps`` for builds/exports and reads cached tables/graphs from
``../output/<year>/`` when they already exist.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from clean_meps import (
    YEAR_FILES,
    build,
    normalize_age_column,
    output_dirs,
    resolve_meps_dir,
    run_exports,
    write_log,
)

YEARS = sorted(YEAR_FILES)

# MEPS codebook labels
SEX_LABELS = {1: "Male", 2: "Female"}
POVCAT_LABELS = {
    1: "Poor / negative",
    2: "Near poor",
    3: "Low income",
    4: "Middle income",
    5: "High income",
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


def table_path(year: int, name: str) -> Path:
    return tables_dir(year) / name


def graph_path(year: int, name: str) -> Path:
    return graphs_dir(year) / name


def age_col(year: int) -> str:
    return f"AGE{year % 100:02d}X"


def povcat_col(year: int) -> str:
    return f"POVCAT{year % 100:02d}"


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
    year: int,
    *,
    genders: list[str],
    age_range: tuple[int, int],
    conditions: list[str],
    incomes: list[str],
) -> pd.DataFrame:
    """Filter person–drug rows by sidebar demographic controls.

    Empty gender or income selection → no rows. Empty condition selection → all conditions.
    Age slider applies to numeric ages; rows with age ``unknown`` are kept.
    """
    out = df
    a_col = age_col(year)
    p_col = povcat_col(year)

    if "SEX" in out.columns:
        wanted_sex = {code for code, label in SEX_LABELS.items() if label in genders}
        out = out[out["SEX"].isin(wanted_sex)]

    if a_col in out.columns and age_range is not None:
        lo, hi = age_range
        age_num = pd.to_numeric(out[a_col], errors="coerce")
        is_unknown = out[a_col].astype(str).str.lower().eq("unknown")
        out = out[((age_num >= lo) & (age_num <= hi)) | is_unknown]

    if conditions and "ICD10CDX_LABEL" in out.columns:
        out = out[out["ICD10CDX_LABEL"].isin(conditions)]

    if p_col in out.columns:
        wanted_inc = {code for code, label in POVCAT_LABELS.items() if label in incomes}
        out = out[out[p_col].isin(wanted_inc)]

    return out


def apply_adherence_view(df: pd.DataFrame, view: str, value_col: str = "mean adherence") -> pd.DataFrame:
    """Return all / top-10 / bottom-10 rows by mean adherence."""
    ranked = df.sort_values(value_col, ascending=False).reset_index(drop=True)
    if view.startswith("Top 10 most"):
        return ranked.head(10)
    if view.startswith("Top 10 least"):
        return ranked.tail(10).sort_values(value_col, ascending=True).reset_index(drop=True)
    return ranked


def drug_level_adherence(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.dropna(subset=["meps_adherence_ratio"])
        .groupby(["RXNAME", "ICD10CDX_LABEL"], as_index=False)
        .agg(**{"mean adherence": ("meps_adherence_ratio", "mean")})
    )
    return out.rename(columns={"RXNAME": "drug name", "ICD10CDX_LABEL": "condition name"})


def condition_level_adherence(df: pd.DataFrame) -> pd.DataFrame:
    out = (
        df.dropna(subset=["meps_adherence_ratio"])
        .groupby(["ICD10CDX_LABEL"], as_index=False)
        .agg(**{"mean adherence": ("meps_adherence_ratio", "mean")})
    )
    return out.rename(columns={"ICD10CDX_LABEL": "condition name"})


def patient_level_adherence(df: pd.DataFrame) -> pd.DataFrame:
    import hashlib

    out = (
        df.dropna(subset=["meps_adherence_ratio"])
        .groupby(["DUPERSID"], as_index=False)
        .agg(**{"mean adherence": ("meps_adherence_ratio", "mean")})
    )
    # Stable anonymous label — do not expose survey person IDs in the UI
    out["patient"] = out["DUPERSID"].map(
        lambda x: "P-" + hashlib.md5(str(x).encode("utf-8")).hexdigest()[:8]
    )
    return out[["patient", "mean adherence"]]


def adherence_histogram_figure(
    values: pd.Series,
    *,
    threshold: int,
    title: str,
    xlabel: str,
    ylabel: str,
):
    """Interactive histogram with bins colored by adherence threshold."""
    import plotly.graph_objects as go

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


def top_bottom_bar_figure(
    df: pd.DataFrame,
    *,
    label_col: str,
    value_col: str,
    threshold: int,
    title: str,
    top_n: int = 10,
):
    """Horizontal bars for top/bottom groups; color by threshold."""
    import plotly.express as px

    ranked = df.dropna(subset=[value_col]).sort_values(value_col, ascending=False)
    if ranked.empty:
        return None
    top = ranked.head(top_n).copy()
    bottom = ranked.tail(top_n).sort_values(value_col, ascending=True).copy()
    top["group"] = f"Top {top_n}"
    bottom["group"] = f"Bottom {top_n}"
    plot_df = pd.concat([top, bottom], ignore_index=True)
    plot_df["meets_threshold"] = plot_df[value_col] >= threshold
    # Truncate long labels for readability
    plot_df["_label"] = plot_df[label_col].astype(str).str.slice(0, 48)
    fig = px.bar(
        plot_df,
        x=value_col,
        y="_label",
        color="meets_threshold",
        color_discrete_map={True: "#5cb85c", False: "#d9534f"},
        facet_col="group",
        orientation="h",
        title=f"{title} (threshold {threshold}%)",
        labels={
            value_col: "Mean adherence (%)",
            "_label": "",
            "meets_threshold": f"≥ {threshold}%",
        },
        category_orders={"group": [f"Top {top_n}", f"Bottom {top_n}"]},
        hover_data={label_col: True, "_label": False},
    )
    fig.update_yaxes(matches=None, showticklabels=True, autorange="reversed")
    fig.update_layout(height=520, margin=dict(t=80, l=10))
    fig.for_each_annotation(lambda a: a.update(text=a.text.split("=")[-1]))
    return fig


@st.cache_data(show_spinner=False)
def year_findings(year: int, threshold: int) -> dict | None:
    """Summarize chronic-drug + PSTATS exports for the Home tab."""
    df = load_table(year, "new_grouped_merge_df_chronic_drugs.xlsx")
    if df is None or "meps_adherence_ratio" not in df.columns:
        return None

    ratio = df["meps_adherence_ratio"]
    by_condition = (
        df.groupby(["ICD10CDX", "ICD10CDX_LABEL"], as_index=False)
        .agg(mean_adherence=("meps_adherence_ratio", "mean"), n=("DUPERSID", "count"))
        .sort_values("mean_adherence", ascending=False)
    )
    high = by_condition[by_condition["mean_adherence"] >= threshold]
    low = by_condition.nsmallest(3, "mean_adherence")
    top = by_condition.head(3)

    persons = df.drop_duplicates("DUPERSID")
    part_counts = (
        persons["participation_type"].value_counts().to_dict()
        if "participation_type" in persons.columns
        else {}
    )

    worst = None
    top20 = load_table(year, "top_20_least_adherent_chronic_drugs.xlsx")
    if top20 is not None and len(top20):
        row = top20.iloc[0]
        worst = {
            "condition": str(row.get("ICD10CDX_LABEL", "")),
            "drug": str(row.get("RXNAME", "")),
            "ratio": float(row.get("meps_adherence_ratio", float("nan"))),
        }

    return {
        "rows": int(len(df)),
        "patients": int(df["DUPERSID"].nunique()),
        "drugs": int(df["RXNAME"].nunique()),
        "conditions": int(df["ICD10CDX"].nunique()),
        "mean": float(ratio.mean()),
        "median": float(ratio.median()),
        "pct_ge_threshold": float((ratio >= threshold).mean() * 100),
        "pct_lt_10": float((ratio < 10).mean() * 100),
        "conditions_ge_threshold": int(len(high)),
        "conditions_total": int(len(by_condition)),
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
- **Conditions**: {findings['conditions_ge_threshold']} of {findings['conditions_total']}
  condition groups average ≥ {thr}%.
        """
    )

    top_bits = "; ".join(
        f"{r['ICD10CDX_LABEL']} ({r['mean_adherence']:.0f}%)" for r in findings["top_conditions"]
    )
    low_bits = "; ".join(
        f"{r['ICD10CDX_LABEL']} ({r['mean_adherence']:.0f}%)" for r in findings["low_conditions"]
    )
    st.markdown(f"- **Highest mean condition adherence**: {top_bits}")
    st.markdown(f"- **Lowest mean condition adherence**: {low_bits}")

    if findings.get("worst"):
        w = findings["worst"]
        st.markdown(
            f"- **Lowest condition × drug combination**: {w['condition']} × "
            f"{w['drug']} ({w['ratio']:.2f}%)."
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
    year = st.selectbox("MEPS year", YEARS, index=YEARS.index(2023) if 2023 in YEARS else 0)

    st.divider()
    st.subheader("Filters")

    # Filter option lists from the final chronic + PSTATS frame
    _preview = prepare_frame(load_main_frame(year), year)

    gender_options = list(SEX_LABELS.values())
    income_options = [POVCAT_LABELS[k] for k in sorted(POVCAT_LABELS)]

    if _preview is not None and age_col(year) in _preview.columns:
        age_vals = pd.to_numeric(_preview[age_col(year)], errors="coerce").dropna()
        age_min, age_max = int(age_vals.min()), int(age_vals.max())
        if age_min >= age_max:
            age_max = age_min + 1
    else:
        age_min, age_max = 0, 85

    if _preview is not None and "ICD10CDX_LABEL" in _preview.columns:
        condition_options = sorted(_preview["ICD10CDX_LABEL"].dropna().astype(str).unique())
    else:
        condition_options = []

    def _reset_filters() -> None:
        st.session_state.filt_threshold = 60
        st.session_state.filt_genders = gender_options.copy()
        st.session_state.filt_age_range = (age_min, age_max)
        st.session_state.filt_conditions = []
        st.session_state.filt_incomes = income_options.copy()

    if "filt_threshold" not in st.session_state:
        _reset_filters()

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

    genders = st.multiselect("Gender", gender_options, key="filt_genders")
    age_range = st.slider("Age", min_value=age_min, max_value=age_max, key="filt_age_range")
    conditions = st.multiselect(
        "Condition",
        condition_options,
        key="filt_conditions",
        help="Leave empty to include all conditions.",
    )
    incomes = st.multiselect("Income", income_options, key="filt_incomes")

    st.divider()
    ready = exports_ready(year)
    st.write("Data:", "✅ ready" if ready else "⚠️ missing — run below")

    if st.button("Refresh year data", type="primary", use_container_width=True):
        with st.spinner(f"Refreshing {year} data… this can take several minutes"):
            try:
                run_exports(year)
                load_table.clear()
                list_graphs.clear()
                list_tables.clear()
                cached_build.clear()
                load_sex_lookup.clear()
                year_findings.clear()
                st.success(f"{year} data refreshed.")
                st.rerun()
            except Exception as exc:
                st.error(f"Refresh failed: {exc}")

    if st.button("Rebuild analysis frame", use_container_width=True):
        cached_build.clear()
        with st.spinner(f"Rebuilding {year} analysis frame…"):
            try:
                df_live, log_dict = cached_build(
                    year,
                    drug_chronic_only=True,
                    pstats_denominator=True,
                )
                write_log(log_dict)
                st.success(
                    f"Built {len(df_live):,} rows / "
                    f"{df_live['DUPERSID'].nunique():,} patients"
                )
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
        "using AHRQ’s Medical Expenditure Panel Survey (MEPS) for 2020–2023."
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
In this project, I use AHRQ’s nationally representative MEPS data from 2020–2023 to study
how consistently people maintain medication coverage for chronic conditions. Because MEPS is
a survey rather than a pharmacy transaction log, it does not provide exact refill dates or a
ready-made adherence measure, so I created the measure through feature engineering. I linked
prescription records to chronic conditions, excluded medications used for acute conditions or
temporary flare-ups, removed records with missing or unusable values, and combined multiple
prescription, days-supply, and survey-participation columns to estimate each person’s total
medication coverage. I also used survey response information to determine whether a person
participated for the full year or had a shorter observation period, allowing me to calculate
an appropriate number of eligible days. For each person–drug pair, I calculated a PDC-style
adherence ratio by dividing the estimated total days supplied by the eligible observation days
and capping the result at 100%. I use a 60% threshold as an exploratory indicator of possible
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
    st.header(f"Analysis · {year}")

    frame = prepare_frame(load_main_frame(year), year)

    if frame is None:
        st.warning("Data not found for this year. Use Refresh year data in the sidebar.")
    else:
        n_before = len(frame)
        frame = apply_filters(
            frame,
            year,
            genders=genders,
            age_range=age_range,
            conditions=conditions,
            incomes=incomes,
        )
        st.caption(
            f"Filtered rows: {n_before:,} → {len(frame):,} · threshold {threshold}%"
        )

        if frame.empty:
            st.warning("No rows match the current filters.")
        else:
            view = st.radio(
                "View",
                ["All", "Top 10 most adherent", "Top 10 least adherent"],
                horizontal=True,
                key="analysis_view",
            )

            drug_tab, cond_tab, patient_tab = st.tabs(
                ["Drug level", "Condition level", "Patient level"]
            )

            with drug_tab:
                drug_df = apply_adherence_view(drug_level_adherence(frame), view)
                st.dataframe(drug_df, use_container_width=True, hide_index=True)
                st.caption(f"{len(drug_df):,} drug × condition rows")

            with cond_tab:
                cond_df = apply_adherence_view(condition_level_adherence(frame), view)
                st.dataframe(cond_df, use_container_width=True, hide_index=True)
                st.caption(f"{len(cond_df):,} condition rows")

            with patient_tab:
                patient_df = apply_adherence_view(patient_level_adherence(frame), view)
                st.dataframe(patient_df, use_container_width=True, hide_index=True)
                st.caption(f"{len(patient_df):,} patient rows")


# ---- Methodology ----------------------------------------------------------

with tab_method:
    st.header(f"Methodology · {year}")
    st.write(
        "This section summarizes how the adherence measure is built from MEPS "
        "prescription, condition, and survey-participation information."
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
9. Compute a PDC-style adherence ratio:
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


# ---- Visualization --------------------------------------------------------

with tab_viz:
    st.header(f"Visualization · {year}")

    frame = prepare_frame(load_main_frame(year), year)
    if frame is None:
        st.warning("Data not found for this year. Use Refresh year data in the sidebar.")
    else:
        n_before = len(frame)
        frame = apply_filters(
            frame,
            year,
            genders=genders,
            age_range=age_range,
            conditions=conditions,
            incomes=incomes,
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
                    "Drug subclass",
                    "Drug × condition",
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
            elif level == "Drug subclass":
                by = (
                    frame.dropna(subset=["meps_adherence_ratio"])
                    .groupby("TC1S1", as_index=False)
                    .agg(**{"mean adherence": ("meps_adherence_ratio", "mean")})
                    .rename(columns={"TC1S1": "drug subclass"})
                )
                values = by["mean adherence"]
                ylabel = "Number of drug subclasses"
                xlabel = "Average adherence by drug subclass (%)"
                title = "Distribution of drug-subclass average adherence"
                rank_df = by
                label_col = "drug subclass"
            else:
                by = drug_level_adherence(frame)
                values = by["mean adherence"]
                ylabel = "Number of drug × condition pairs"
                xlabel = "Average adherence by drug × condition (%)"
                title = "Distribution of drug × condition average adherence"
                rank_df = by.assign(
                    label=by["drug name"].astype(str) + " · " + by["condition name"].astype(str)
                )
                label_col = "label"

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
                rank_fig = top_bottom_bar_figure(
                    rank_df,
                    label_col=label_col,
                    value_col="mean adherence",
                    threshold=threshold,
                    title=title.replace("Distribution of ", ""),
                )
                if rank_fig is not None:
                    st.plotly_chart(rank_fig, use_container_width=True)
