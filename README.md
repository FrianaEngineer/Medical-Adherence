# Medication Adherence — MEPS Capstone

A small-scale medication-adherence analysis built on the **AHRQ Medical Expenditure Panel Survey (MEPS) 2020–2023**. The atomic unit is **Proportion of Days Covered (PDC) per patient per drug class per year**, computed with an eligibility-window denominator (not naive 365 days), filtered to chronic conditions and maintenance drugs.

The project answers a small number of focused questions and ends with a Streamlit demo on GitHub.

---

## Sub-questions

1. **Which medication groups show lower refill continuity?** Two views: by drug class (Multum `TC1S1`) and by condition (3-digit ICD-10).
2. Adherence drivers:
   - **(a)** Does **cost** affect adherence? (`RXSF23X` per-fill OOP, `RXSLF23` per-year OOP, plus `DLAYPM42` — the survey item that literally asks if the respondent delayed a prescription because of cost.)
   - **(b)** How does **chronic burden** relate to adherence? (Count of `*DX` flags on h251 plus h249 ICD-10 codes flagged chronic.)
   - **(c)** How do **side effects** relate to adherence? *MEPS does not directly capture side effects; we use perceived-health change and discontinuation patterns as imperfect proxies. This is a known data limit.*
3. **Can we predict future non-adherence?** Year T−1 features → label `PDC_in_T < 0.75`. Baseline to beat: prior-year PDC alone. Final model is logistic regression + gradient boosting, evaluated with precision-at-k for a capacity-limited outreach setting.

---

## Why MEPS, not CMS

The project started on CMS DE-SynPUF (synthetic Medicare claims), discovered the synthetic generator does not preserve within-person drug continuity, and moved to MEPS in late May. CMS production data is not openly redistributable; MEPS is free, real, and survey-based. **MEPS does not have calendar fill dates** — only survey rounds (~4 months each) — which is the main methodological limit, named explicitly.

---

## Data

Five MEPS files for 2023 (with year-suffix swaps for 2020/2021/2022):

| File | Purpose | One row = | 2023 rows |
|---|---|---|---|
| `h248a` | Prescribed Medicines | one fill | 192,275 |
| `h248if1` | CLNK — bridges fills to conditions | one event-condition link | 281,158 |
| `h249` | Medical Conditions | one current condition for a person | 63,656 |
| `h250` | Person Round Plan (private insurance) — **last public release year** | one person × round × plan | 35,064 |
| `h251` | Full-year consolidated person file | one person × 2023 | 18,919 |

Plus `is_chronic.xlsx`, a hand-curated 0/1 flag per 3-digit ICD-10 derived from MEPS HC-249 Appendix 1 Table 1.

**Important MEPS gotchas the pipeline handles:**
- `RXDAYSUP == -8` (Don't Know) for 27% of fills — dropped, loss documented.
- `RXDAYSUP == 999` ("as needed") — excluded.
- `DIABEQUIP == 1` (diabetic supplies, ~4,000 rows) — excluded as non-therapy.
- Join chain: `h248a.LINKIDX → h248if1.EVNTIDX` filtered to `EVENTYPE == 8`, then `h248if1.CONDIDX → h249.CONDIDX`. Direct `h248a × h249` on `DUPERSID` alone = Cartesian explosion.

---

## How to run

Notebooks read MEPS workbooks directly with the `python-calamine` engine (10× faster than openpyxl on these files).

```bash
pip install pandas numpy python-calamine matplotlib seaborn scikit-learn
jupyter notebook
```

Then open in order:

1. **`Notebooks/INDEX.md`** — one-page orientation; which notebook is which.
2. **`Notebooks/PDC_Walkthrough.ipynb`** — the teaching reference; the full PDC calculation on 2023 with conditions, h250 insurance features, and the eligibility-window concept. Sections 15–18 are markdown stubs for sub-questions 2a/b/c and the model.
3. **`Notebooks/Friana_PDC_2023.ipynb`** *(in progress)* — the from-scratch student replication.
4. **`Notebooks/allYearMerge_v2.ipynb`** *(planned)* — same pipeline across 2020–2023.

---

## Repo structure

```
.
├── README.md                 # this file
├── CLAUDE.md                 # guidance for AI coding assistants
├── data/
│   └── MEPS/
│       ├── excels/           # the .xlsx files used by the notebooks
│       ├── docs/             # AHRQ codebook + documentation PDFs (2023)
│       ├── zips/             # original AHRQ archives
│       └── MEPS_SCHEMA_NOTES.md  # mentor-side schema reference
├── Notebooks/
│   ├── INDEX.md              # which notebook to use, when
│   ├── PDC_Walkthrough.ipynb # teaching reference (PDC + conditions + h250)
│   ├── allYearMerge.ipynb    # 2020–2023 stacked frame, v1 (known gaps)
│   ├── EDAmeps.ipynb         # MEPS structural EDA
│   └── 2020EDA…2023EDA.ipynb # per-year EDA
└── Study_Plan/
    ├── Friana_Study_Plan_v3.docx   # student workbook
    ├── Mehak_Playbook_v3.docx      # mentor playbook (data + tactical reference)
    └── v1/                          # historical record
```

`data/CMS_SynPUF/` exists from the earlier CMS phase. Out of scope; do not extend.

---

## Status (as of June 14, 2026)

- **Weekend 1–2 of the summer block: complete.** Cleaning-as-decisions, groupby, the diabetes single-cohort merge done.
- **Weekend 3 (June 20–21): current.** Real MEPS cleaning, the negatives protocol, the eligibility-window concept introduced via `PDC_Walkthrough.ipynb`.
- **Weekends 4–7 (late June through mid-July):** all four years; cost; chronic burden; features and leakage.
- **Weekend 8 (late July):** real model + honest evaluation.
- **Early August:** notebook code lifted into `src/med_adherence/` Python modules.
- **Weekends 9–10 (Aug 1–9):** Streamlit front-end + GitHub.
- **Weekend 11 (Aug 15–16):** final defense rehearsal.

---

## Limits to state in any defense

- **No fill dates.** MEPS resolution is round-level (~4 months). Annual MPR / PDC is computable; day-level PDC is not.
- **Recall bias.** Households report fills from memory. Pharmacy-Component verification (`IMPFLAG == 2`) catches most but not all.
- **3-digit ICD-10 only** for confidentiality. Subtypes (e.g., `E11.9` vs `E11.21`) are collapsed to `E11`.
- **27% of `RXDAYSUP` is missing** as Don't Know. The 73% retained is the basis of every reported number.
- **No side-effects field.** Sub-question 2c hits a data wall; proxies are stated, not hidden.
- **No causal claims.** This is descriptive + predictive on a sample, not a clinical efficacy study.

---

## Who built this

Friana (Cornell freshman) building the work under mentorship from Dr. Mehak Rafiq, May–August 2026. Project sponsored by Pheroze. The original V9 production-system scaffold from Pheroze is reference only — this repo is Friana's defensible vertical slice, written to be owned and defended in interviews.
