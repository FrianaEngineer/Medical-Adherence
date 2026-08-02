# How to read this project (student-friendly narrative)

This file is the guided tour of the MEPS medical-adherence pipeline. Read this section first, then open `2023_clean.ipynb` and follow along. Every design choice below has a "why" — that is what matters. The pipeline itself is straightforward pandas; the hard part is knowing which choice would have been wrong.

## The question

Given a person's demographics, insurance, cost exposure, and the chronic medications they filled during a year, can we predict whether they were adherent to those medications? "Adherent" here means the prescription-refill ratio (days supplied ÷ days eligible) reached ≥60% for the year, PDC-style (capped at 100%). We use 2023 as the primary year and MEPS as the data source.

## The data

Four files per year from AHRQ's Medical Expenditure Panel Survey:

| File     | What it contains                                                             |
|----------|------------------------------------------------------------------------------|
| `h248a`  | Prescription fills — one row per pickup, with days supply, cost, drug name.  |
| `h249`   | Conditions — one row per (person, condition), with ICD-10 3-digit code.      |
| `h248if1`| CLNK — event ↔ condition linkage table (the "bridge" between fills and dx).  |
| `h251`   | Person-level demographics, insurance, poverty, marital, region, education.   |

Plus two hand-curated lookups: `is_chronic.xlsx` (chronic ICD allowlist) and `unique_rxname_chronic_labeled_revised.csv` (chronic/flare/acute label per drug name).

## The pipeline, stage by stage

1. **Load `h248a`, drop unusable fills.** Keep `RXDAYSUP` 1–989 (999 is the MEPS "as needed" flag, not a day count — treating it as data would create fake 100% adherence). Drop rows with `RXBEGYRX ≤ 0` (unusable start-year sentinels: -1 refused, -7 don't know, -8 inapplicable).
2. **Label chronic conditions.** Join `h249` to the 3-digit ICD-10 chronic allowlist, keep only chronic rows. Analysis is scoped to chronic-disease adherence, so acute one-off diagnoses are not the target.
3. **Link fills to conditions through CLNK.** `h248a.LINKIDX = h248if1.EVNTIDX`, filtered to `EVENTYPE == 8` (prescription-medicine events only). Never join on DUPERSID alone (Cartesian explosion) and never on LINKIDX alone (round-scoped, collides across people).
4. **CLNK many-to-many dedup — the invariant that used to break.** A single fill linked to N chronic conditions appears N times after step 3. Summing `RXDAYSUP` at that point double-counts days and silently drops all but the first ICD. Fix: deduplicate on `(DUPERSID, DRUGIDX, RXRECIDX)` **before** summing, then build a separate bridge frame at `(DUPERSID, DRUGIDX, ICD10CDX, CONDIDX)` grain for condition-level rollups. The bridge carries no days-supply column so it cannot be accidentally summed. `test_pipeline.py::test_multi_condition_fill_counts_days_once` locks this behavior.
5. **Groupby to person-drug grain.** Sum `RXDAYSUP` per `(DUPERSID, DRUGIDX)` on the deduped fills. Attach the multi-condition context: `n_chronic_conditions`, `chronic_conditions` (comma-joined list), and a primary ICD alias for backwards-compat.
6. **Drug-side chronic filter.** Drop fills whose drug name is labeled `flare_up` or `non_chronic` — prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic adherence.
7. **PSTATS-based reference days (the denominator).** Load `h251`, walk each person's round-1/2/3 PSTATS status codes and BEGRF/ENDRF month-year windows, produce per-person eligible days in 2023. Full-year 11/11/11 respondents get 365; deceased get a shortened window ending at their round's ENDRF; dropouts get the window up to their exit. Documented in `clean_meps._compute_reference_coverage` and tested in `test_pipeline.py`.
8. **Drug-start-date adjustment (the second denominator constraint).** A drug first prescribed in July 2023 does not deserve a 365-day denominator; it deserves 184 (July 1 → Dec 31). Formula: `total_days_supply = min(pstats_days, drug_start_days)` where `drug_start_days = 365` for old drugs and MEPS-sentinel start months, else `Dec 31 − first-of-first-month + 1`. This dropped mean adherence from 61.2% to 61.2% and moved median from 49.3% to 65.2% — old drugs kept 365, mid-year starters got shorter, more honest windows.
9. **Compute the adherence ratio.** `total_valid_days = min(RXDAYSUP, 365, total_days_supply)`; `ratio = 100 × total_valid_days ÷ total_days_supply` (NaN if denom is 0). PDC-style: capped at 100%. Not MPR-style (uncapped). The cap is a choice, not a mistake — documented so it can be relaxed if needed.
10. **Model preparation.** One-hot encode drug names + ICDs (multi-hot from `chronic_conditions`), collapse to one row per patient. Build three parallel feature sets: `model_df` (with RXNAME), `model_df_no_rx` (drops RXNAME dummies — XGBoost can't cleanly handle that cardinality), `model_df_no_rx_tc` (adds patient-level TC1S1 drug-class dummies).

## The models — three-step arc

At the bottom of the notebook, three XGBoost models are trained on the same target, same CV strategy, same hyperparameter grid. Only the feature set changes. That is the point — it isolates what each layer of information contributes.

| Model | Features                                                                                 | What it answers                                                        |
|-------|------------------------------------------------------------------------------------------|------------------------------------------------------------------------|
| **A — baseline**       | Demographics, insurance, cost, ICD dummies, no drug identity                 | How well can patient context alone predict adherence?                  |
| **B — drug class**     | A + TC1S1 patient-level drug-class dummies                                   | Does knowing the drug class add signal over patient context?           |
| **C — paper enriched** | B + MARRYXX, REGIONXX, EDUCYR, RACETHX, medication_dose/freq/freq_bin        | Do the socioeconomic + dosing variables the literature flagged help?   |

Which features are added in **C** and why they're there:

| Variable                 | MEPS column     | Cited by                                                       |
|--------------------------|-----------------|----------------------------------------------------------------|
| Marital status           | `MARRYXX`       | Scoping review (JMIRx Med 2021, PMC10414315)                   |
| Geographic region        | `REGIONXX`      | Haas et al. (JMIR Med Inform 2019, PMC6470459) + scoping       |
| Education years          | `EDUCYR`        | Scoping review                                                 |
| Race/ethnicity           | `RACETHX`       | Scoping review                                                 |
| Medication dose          | `RXSTRENG`      | Haas et al. + scoping                                          |
| Medication frequency     | `RXQUANTY/RXDAYSUP` | Toy et al. (COPD, ScienceDirect S0954611110003926)         |
| Med-freq bins (1x/2x/3x/4x+) | derived     | Toy et al. — categorical binning of dosing frequency           |

MARRYXX and REGIONXX are backfilled newest-round-first (`23X → 53 → 42 → 31`) so a patient in only one round still gets a value. Negative MEPS sentinels (-1, -7, -8) are treated as missing.

The grid is trimmed to 16 combinations (2×2×2×2 on `max_depth`, `n_estimators`, `learning_rate`, `subsample`) × 5-fold CV = 80 fits per model. XGBoost runs with `tree_method="hist"` + `n_jobs=-1` for CPU parallelism on Apple Silicon (there is no MPS backend for XGBoost — verified against the official docs).

Each model reports: features count, CV best AUC, test AUC / F1 / precision / recall, best hyperparameters. Then Gini (gain) importance and SHAP (TreeExplainer, 500-row sample) run for all three so you can compare *what* the model actually learned.

## What we deliberately do NOT do

- **No same-year label leakage.** `is_adherent`, `meps_adherence_ratio`, `total_valid_days`, `total_days_supply`, `drug_start_days`, `n_drugs`, `n_conditions` are all dropped from the feature matrix.
- **No GPU pretense.** sklearn, XGBoost, and SHAP have no Apple Silicon GPU backend. Adding fake `device=` code that silently falls back to CPU would teach the wrong lesson. CPU + `hist` + `n_jobs=-1` is the fast path on M4.
- **No cross-year longitudinal model.** Each year is independent. That is a limitation; the scoping review's "initial medication adherence" predictor would need panel linkage, which we do not do.
- **No claim of clinical validity.** These predictions describe adherence in a survey sample, not a clinical population. Same-year classification, not future prediction.

## Reproducing

- One year: `python Notebooks/MEPS/app/2023_clean.py` (calls `clean_meps.run_exports(2023)`).
- All four years cached: run 2020/2021/2022/2023 in turn, then the all-years merge is auto-produced.
- Tests: `cd Notebooks/MEPS/app && pytest tests/ -v` — 47 tests, gates + pipeline extracts.
- App: `cd Notebooks/MEPS/app && streamlit run simple_app.py`.

---

## Historical / freeform notes

what do we do with the 27% of rows where we don't know RXDAYSUP. Drop them? Impute to median? Flag and keep? We will choose 'flag and keep' for most uses, and 'drop' only for the MPR numerator calculation.

what is the MPR numerator calculation

We should drop the rows where we dont know the day supply because that would make it difficult to determine the amount of medication they are supposed to take daily.

---

## Decisions Log (auto-appended)

Each block below is written by `clean_meps.build()` on every run — rows and patients lost at every stage, columns dropped, and decisions taken. The heading line still starts with `## YYYY build` so the appender keeps working.

## 2020 build — 2026-07-05T14:39:52.889445+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 22,990    **Final unique patients**: 8,305

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 279,755 | 279,755 | 0 | 15,743 | 15,743 | 0 |
| `filter_RXDAYSUP_1_to_989` | 279,755 | 199,663 | 80,092 | 15,743 | 12,908 | 2,835 |
| `filter_RXBEGYRX_positive` | 199,663 | 169,621 | 30,042 | 12,908 | 12,401 | 507 |
| `merge_chronic_icd_list` | 80,802 | 80,802 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 80,802 | 50,846 | 29,956 | 18,569 | 14,819 | 3,750 |
| `filter_CLNK_EVENTYPE_8` | 342,300 | 124,656 | 217,644 | 18,569 | 15,436 | 3,133 |
| `merge_rx_to_chronic_condition` | 184,942 | 145,368 | 39,574 | 12,401 | 10,125 | 2,276 |
| `groupby_person_drug` | 145,368 | 34,965 | 110,403 | 10,125 | 10,125 | 0 |
| `filter_drug_chronic_only` | 34,965 | 22,990 | 11,975 | 10,125 | 8,305 | 1,820 |
| `merge_person_demographics` | 22,990 | 22,990 | 0 | 8,305 | 8,305 | 0 |
| `compute_reference_days` | 27,805 | 27,805 | 0 | 27,805 | 27,805 | 0 |

### Detail per stage

- **load_rx**: file=h220a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person H224.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2020. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from H224.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from H224.xlsx: AGE20X, SEX-adjacent (RACEV2X), INSCOV20, POVCAT20, FAMINC20, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 215 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2021 build — 2026-07-05T14:41:31.254011+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 26,719    **Final unique patients**: 9,002

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 303,394 | 303,394 | 0 | 16,534 | 16,534 | 0 |
| `filter_RXDAYSUP_1_to_989` | 303,394 | 217,158 | 86,236 | 16,534 | 13,476 | 3,058 |
| `filter_RXBEGYRX_positive` | 217,158 | 181,789 | 35,369 | 13,476 | 12,937 | 539 |
| `merge_chronic_icd_list` | 94,641 | 94,641 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 94,641 | 58,513 | 36,128 | 19,896 | 15,937 | 3,959 |
| `filter_CLNK_EVENTYPE_8` | 400,671 | 144,127 | 256,544 | 19,896 | 16,243 | 3,653 |
| `merge_rx_to_chronic_condition` | 195,500 | 154,354 | 41,146 | 12,937 | 10,780 | 2,157 |
| `groupby_person_drug` | 154,354 | 39,627 | 114,727 | 10,780 | 10,780 | 0 |
| `filter_drug_chronic_only` | 39,627 | 26,719 | 12,908 | 10,780 | 9,002 | 1,778 |
| `merge_person_demographics` | 26,719 | 26,719 | 0 | 9,002 | 9,002 | 0 |
| `compute_reference_days` | 28,336 | 28,336 | 0 | 28,336 | 28,336 | 0 |

### Detail per stage

- **load_rx**: file=h229a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h233.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2021. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h233.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h233.xlsx: AGE21X, SEX-adjacent (RACEV2X), INSCOV21, POVCAT21, FAMINC21, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 219 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2022 build — 2026-07-05T14:43:16.034094+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 20,966    **Final unique patients**: 7,222

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 232,605 | 232,605 | 0 | 13,602 | 13,602 | 0 |
| `filter_RXDAYSUP_1_to_989` | 232,605 | 168,162 | 64,443 | 13,602 | 11,128 | 2,474 |
| `filter_RXBEGYRX_positive` | 168,162 | 137,700 | 30,462 | 11,128 | 10,588 | 540 |
| `merge_chronic_icd_list` | 83,173 | 83,173 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 83,173 | 50,526 | 32,647 | 16,375 | 13,278 | 3,097 |
| `filter_CLNK_EVENTYPE_8` | 322,174 | 116,727 | 205,447 | 15,783 | 13,383 | 2,400 |
| `merge_rx_to_chronic_condition` | 147,789 | 116,682 | 31,107 | 10,588 | 8,713 | 1,875 |
| `groupby_person_drug` | 116,682 | 30,919 | 85,763 | 8,713 | 8,713 | 0 |
| `filter_drug_chronic_only` | 30,919 | 20,966 | 9,953 | 8,713 | 7,222 | 1,491 |
| `merge_person_demographics` | 20,966 | 20,966 | 0 | 7,222 | 7,222 | 0 |
| `compute_reference_days` | 22,431 | 22,431 | 0 | 22,431 | 22,431 | 0 |

### Detail per stage

- **load_rx**: file=h239a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h243.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2022. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h243.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h243.xlsx: AGE22X, SEX-adjacent (RACEV2X), INSCOV22, POVCAT22, FAMINC22, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 175 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2023 build — 2026-07-05T14:44:34.364978+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 17,913    **Final unique patients**: 6,268

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 192,275 | 192,275 | 0 | 11,858 | 11,858 | 0 |
| `filter_RXDAYSUP_1_to_989` | 192,275 | 139,681 | 52,594 | 11,858 | 9,765 | 2,093 |
| `filter_RXBEGYRX_positive` | 139,681 | 114,018 | 25,663 | 9,765 | 9,255 | 510 |
| `merge_chronic_icd_list` | 63,656 | 63,656 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 63,656 | 40,620 | 23,036 | 13,490 | 11,178 | 2,312 |
| `filter_CLNK_EVENTYPE_8` | 281,158 | 97,941 | 183,217 | 13,490 | 11,654 | 1,836 |
| `merge_rx_to_chronic_condition` | 122,117 | 96,328 | 25,789 | 9,255 | 7,503 | 1,752 |
| `groupby_person_drug` | 96,328 | 25,662 | 70,666 | 7,503 | 7,503 | 0 |
| `filter_drug_chronic_only` | 25,662 | 17,913 | 7,749 | 7,503 | 6,268 | 1,235 |
| `merge_person_demographics` | 17,913 | 17,913 | 0 | 6,268 | 6,268 | 0 |
| `compute_reference_days` | 18,919 | 18,919 | 0 | 18,919 | 18,919 | 0 |

### Detail per stage

- **load_rx**: file=h248a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h251.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2023. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h251.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h251.xlsx: AGE23X, SEX-adjacent (RACEV2X), INSCOV23, POVCAT23, FAMINC23, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 137 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2020 build — 2026-07-05T15:50:33.263482+00:00

**Source dir**: `/Users/friana/Medical Adherence/data/MEPS`
**Final rows**: 21,178    **Final unique patients**: 7,954

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 279,755 | 279,755 | 0 | 15,743 | 15,743 | 0 |
| `filter_RXDAYSUP_1_to_989` | 279,755 | 199,663 | 80,092 | 15,743 | 12,908 | 2,835 |
| `filter_RXBEGYRX_positive` | 199,663 | 169,621 | 30,042 | 12,908 | 12,401 | 507 |
| `merge_chronic_icd_list` | 80,802 | 80,802 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 80,802 | 38,279 | 42,523 | 18,569 | 13,282 | 5,287 |
| `filter_CLNK_EVENTYPE_8` | 342,300 | 124,656 | 217,644 | 18,569 | 15,436 | 3,133 |
| `merge_rx_to_chronic_condition` | 184,942 | 126,152 | 58,790 | 12,401 | 9,205 | 3,196 |
| `groupby_person_drug` | 126,152 | 29,709 | 96,443 | 9,205 | 9,205 | 0 |
| `filter_drug_chronic_only` | 29,709 | 21,178 | 8,531 | 9,205 | 7,954 | 1,251 |
| `merge_person_demographics` | 21,178 | 21,178 | 0 | 7,954 | 7,954 | 0 |
| `compute_reference_days` | 27,805 | 27,805 | 0 | 27,805 | 27,805 | 0 |

### Detail per stage

- **load_rx**: file=h220a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person H224.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2020. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from H224.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from H224.xlsx: AGE20X, SEX-adjacent (RACEV2X), INSCOV20, POVCAT20, FAMINC20, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 215 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2021 build — 2026-07-05T15:55:05.450275+00:00

**Source dir**: `/Users/friana/Medical Adherence/data/MEPS`
**Final rows**: 24,664    **Final unique patients**: 8,631

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 303,394 | 303,394 | 0 | 16,534 | 16,534 | 0 |
| `filter_RXDAYSUP_1_to_989` | 303,394 | 217,158 | 86,236 | 16,534 | 13,476 | 3,058 |
| `filter_RXBEGYRX_positive` | 217,158 | 181,789 | 35,369 | 13,476 | 12,937 | 539 |
| `merge_chronic_icd_list` | 94,641 | 94,641 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 94,641 | 43,748 | 50,893 | 19,896 | 14,375 | 5,521 |
| `filter_CLNK_EVENTYPE_8` | 400,671 | 144,127 | 256,544 | 19,896 | 16,243 | 3,653 |
| `merge_rx_to_chronic_condition` | 195,500 | 133,933 | 61,567 | 12,937 | 9,860 | 3,077 |
| `groupby_person_drug` | 133,933 | 33,585 | 100,348 | 9,860 | 9,860 | 0 |
| `filter_drug_chronic_only` | 33,585 | 24,664 | 8,921 | 9,860 | 8,631 | 1,229 |
| `merge_person_demographics` | 24,664 | 24,664 | 0 | 8,631 | 8,631 | 0 |
| `compute_reference_days` | 28,336 | 28,336 | 0 | 28,336 | 28,336 | 0 |

### Detail per stage

- **load_rx**: file=h229a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h233.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2021. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h233.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h233.xlsx: AGE21X, SEX-adjacent (RACEV2X), INSCOV21, POVCAT21, FAMINC21, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 219 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2022 build — 2026-07-05T15:59:37.700848+00:00

**Source dir**: `/Users/friana/Medical Adherence/data/MEPS`
**Final rows**: 19,325    **Final unique patients**: 6,932

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 232,605 | 232,605 | 0 | 13,602 | 13,602 | 0 |
| `filter_RXDAYSUP_1_to_989` | 232,605 | 168,162 | 64,443 | 13,602 | 11,128 | 2,474 |
| `filter_RXBEGYRX_positive` | 168,162 | 137,700 | 30,462 | 11,128 | 10,588 | 540 |
| `merge_chronic_icd_list` | 83,173 | 83,173 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 83,173 | 37,234 | 45,939 | 16,375 | 12,058 | 4,317 |
| `filter_CLNK_EVENTYPE_8` | 322,174 | 116,727 | 205,447 | 15,783 | 13,383 | 2,400 |
| `merge_rx_to_chronic_condition` | 147,789 | 100,998 | 46,791 | 10,588 | 7,952 | 2,636 |
| `groupby_person_drug` | 100,998 | 26,069 | 74,929 | 7,952 | 7,952 | 0 |
| `filter_drug_chronic_only` | 26,069 | 19,325 | 6,744 | 7,952 | 6,932 | 1,020 |
| `merge_person_demographics` | 19,325 | 19,325 | 0 | 6,932 | 6,932 | 0 |
| `compute_reference_days` | 22,431 | 22,431 | 0 | 22,431 | 22,431 | 0 |

### Detail per stage

- **load_rx**: file=h239a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h243.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2022. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h243.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h243.xlsx: AGE22X, SEX-adjacent (RACEV2X), INSCOV22, POVCAT22, FAMINC22, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 175 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

