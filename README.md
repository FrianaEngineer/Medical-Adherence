# Medication Adherence — MEPS Capstone

**[Explore the live Streamlit application](https://medication-adherence-explorer.streamlit.app/)**

This end-to-end healthcare data science project analyzes medication refill continuity using the **AHRQ Medical Expenditure Panel Survey (MEPS), 2020–2023**. It links prescription, condition, and patient records; constructs a **PDC-style adherence measure for each patient, medication, and year**; investigates factors associated with adherence; and evaluates models for identifying non-adherence.

The adherence measure divides reported days supplied by the patient’s eligible observation period rather than automatically using 365 days. The deployed dashboard defaults to **60%** for exploratory classification and allows the threshold to be changed. Results are interpreted as refill continuity rather than clinical confirmation of adherence.

The repo includes a reproducible cleaning pipeline, unit-tested data-quality gates, multi-year modeling notebooks, and a Streamlit demo.

---

## Research Questions

1. **Which medication groups show lower refill continuity?** Two views: by drug class (Multum `TC1S1`) and by condition (3-digit ICD-10).
2. Adherence drivers:
   - **(a)** Does **cost** affect adherence? (per-fill / per-year OOP, plus delay-for-cost survey items such as `DLAYPM42`.)
   - **(b)** How does **chronic burden** relate to adherence? (Chronic ICD allowlist + condition counts linked through CLNK.)
   - **(c)** Can side effects be evaluated? MEPS does not directly measure medication side effects, so the project documents this as an unanswered question rather than making unsupported conclusions from weak proxies.
3. **How well can we classify adherence, and does prior-year behavior improve prediction?** The project separates same-year classification experiments from lagged models using consecutive patient-year observations (`prior_is_adherent`, `prior_n_drugs`, and `prior_n_cond`; the continuous `prior_ratio` is excluded from the deployed model). Models are trained on 2021–2022 observations and evaluated on 2023.

Models explored in the notebooks include logistic regression, Random Forest, and XGBoost. The deployed **Models** tab uses XGBoost and compares feature groups using a 2023 time-based holdout.

---

## Why MEPS, not CMS

The project started on CMS DE-SynPUF (synthetic Medicare claims), discovered the synthetic generator does not preserve within-person drug continuity, and moved to MEPS. CMS production data is not openly redistributable; MEPS is free, real, and survey-based. **MEPS does not have calendar fill dates** — only survey rounds (~4 months each) — which is the main methodological limit.

---

## Data

Four MEPS files per year (plus year-suffix swaps for 2020/2021/2022):

| File | Purpose | One row = |
|---|---|---|
| `h24Xa` / `h248a` | Prescribed Medicines | one fill |
| `h24Xif1` / `h248if1` | CLNK — bridges fills to conditions | one event–condition link |
| `h24X` / `h249` | Medical Conditions | one current condition for a person |
| `h25X` / `h251` | Full-year consolidated person file | one person × year |

Plus hand-curated lookups:

- `is_chronic.xlsx` — 0/1 chronic flag per 3-digit ICD-10
- `unique_rxname_chronic_labeled_revised.csv` — chronic / flare_up / non_chronic labels per drug name

**Pipeline gotchas handled in `app/clean_meps.py`:**

- `RXDAYSUP == -8` (Don't Know) and other negatives — dropped
- `RXDAYSUP == 999` ("as needed") — excluded
- CLNK multi-condition expansion — fills deduped before summing days; separate bridge frame for condition rollups
- Join chain: `LINKIDX → EVNTIDX` with `EVENTYPE == 8`, then `CONDIDX` — never `DUPERSID` alone
- Denominator: PSTATS / BEGRF / ENDRF eligible days, further clamped by drug-start month when known
- Ratio is **PDC-style** (numerator capped at 100% of denominator), not uncapped MPR

Raw workbooks live under `data/MEPS/`. Built parquet/xlsx exports live under `Notebooks/MEPS/output/{year|all_years}/`.

---

## How to run

### Streamlit app (main demo)

Live app: [medication-adherence-explorer.streamlit.app](https://medication-adherence-explorer.streamlit.app/)

To run locally:

```bash
cd app
pip install -r requirements.txt
streamlit run simple_app.py
```

Tabs: **Home**, **Analysis**, **Methodology**, **Visualization**, **Models**, **Gates**.

Sidebar: year (single year or all years), adherence threshold, demographic filters. Prefer prebuilt parquet under `Notebooks/MEPS/output/` so the UI does not rebuild on every interaction.

### Rebuild year exports / all-years cache

```bash
cd app
python 2023_clean.py          # or 2020_clean.py … 2022_clean.py
# or:
python clean_meps.py export --year 2023
python clean_meps.py cache-all-years
```

### Notebooks

```bash
pip install -r app/requirements.txt
# or: uv sync   # uses pyproject.toml / uv.lock at repo root
jupyter lab
```

Primary notebooks:

1. **`Notebooks/MEPS/2023_clean.ipynb`** — teaching / reference year pipeline + same-year XGBoost feature arcs
2. **`Notebooks/MEPS/2020_clean.ipynb` … `2022_clean.ipynb`** — same recipe per year
3. **`Notebooks/allYearMergeClean.ipynb`** — stack 2020–2023 person–year `model_df`, continuity checks, RF/XGB, prior-year lag analysis
4. **`Notebooks/PDC_Walkthrough.ipynb`** — earlier teaching walkthrough of the eligibility-window idea

Narrative of design choices: `app/decisions_log.md`. Auto-appended build logs: `Notebooks/MEPS/decisions_log.md`.

### Tests

```bash
cd app
pytest tests/ -v
```

~50 tests covering pipeline stages (including multi-condition day-count invariant) and data-quality gates.

---

## Repo structure

```
.
├── README.md
├── MEPS_Column_Guide.md      # column / codebook orientation
├── pyproject.toml / uv.lock  # notebook / analysis deps
├── app/                      # Streamlit + shared pipeline (source of truth)
│   ├── simple_app.py         # Streamlit UI
│   ├── clean_meps.py         # build / export / cache backend
│   ├── gates.py              # data-quality checks (also shown in UI)
│   ├── 2020_clean.py … 2023_clean.py
│   ├── decisions_log.md      # student-facing design narrative
│   ├── requirements.txt
│   └── tests/
├── Notebooks/
│   ├── allYearMergeClean.ipynb
│   ├── PDC_Walkthrough.ipynb
│   ├── CMS/                  # earlier SynPUF exploration
│   └── MEPS/
│       ├── 2020_clean.ipynb … 2023_clean.ipynb
│       ├── decisions_log.md  # auto-appended build logs
│       └── output/           # per-year + all_years parquet/xlsx/graphs
└── data/
    ├── MEPS/                 # raw AHRQ extracts + lookups
    ├── CMS/                  # earlier CMS exploration
    └── synthea/              # exploratory; not the main path
```

These directories (`data/CMS/`, early CMS notebooks) are retained to document the project’s earlier CMS exploration but are not part of the final analytical pipeline.

---

## Status (as of August 12, 2026)

| Area | Status |
|---|---|
| Per-year cleaning (2020–2023) | Done — notebooks + `clean_meps.build` / year runners |
| Eligibility-window PDC + drug-start clamp | Done — tested |
| CLNK multi-condition day-count fix | Done — locked by `test_pipeline.py` |
| Data-quality gates + Streamlit Gates tab | Done |
| All-years person–year panel + cache | Done — `Notebooks/MEPS/output/all_years/` |
| Same-year modeling (notebooks) | Done — logistic / RF / XGBoost |
| Deployed Models tab (XGBoost + feature-group holdout) | Done — `app/simple_app.py` |
| Prior-year lag features + year-holdout eval | Done — see `output/all_years/prior_year_lag_note.md` |
| Streamlit demo (analysis / viz / predict) | Done — [live app](https://medication-adherence-explorer.streamlit.app/) |
| Final defense rehearsal | Next |

---

## Limits to state in any defense

- **No fill dates.** MEPS resolution is round-level (~4 months). Annual PDC-style ratios are computable; day-level PDC is not.
- **Recall bias.** Households report fills from memory. Pharmacy-Component verification catches most but not all.
- **3-digit ICD-10 only** for confidentiality.
- **Large share of `RXDAYSUP` / start-year fields are missing or sentinel** — dropped with loss documented in build logs.
- **No side-effects field.** Research question 2c remains unanswered; the project does not invent conclusions from weak proxies.
- **Same-year labels are not future prediction.** Prior-year models use consecutive in-panel years only (~9.9k person–years after lag join); 2020 has no prior in this extract.
- **No causal claims.** Descriptive + predictive on a survey sample, not a clinical efficacy study.

---

## Author and mentorship

Developed by **Friana Engineer, Cornell University**, under the mentorship of **Dr. Mehak Rafiq**, May–August 2026.
