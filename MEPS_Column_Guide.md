# MEPS Columns Needed by Question

This guide lists only the columns needed for the MEPS adherence notebook and later model. The main rule is: build the patient-drug adherence table first, then add cost, condition, and modeling features.

Verified usable for 2020, 2021, 2022, and 2023 — see the cross-year verification footnote at the end for the one column-existence exception.

## Base Outcome Table for Q1

**Question:** Which medication groups show lower refill continuity?

**Unit:** one patient-drug pair in 2023: `DUPERSID + DRUGIDX`

**File:** `h248a.xlsx` prescribed medicines

| Column | Use | Why needed |
|---|---|---|
| `DUPERSID` | Required | Patient ID. Needed for patient-drug grouping and later person merge. |
| `DRUGIDX` | Required | Patient-drug key. This is the main drug unit for adherence. |
| `RXRECIDX` | Required | Fill/acquisition row ID. Used to count fills and check duplicates. |
| `RXDAYSUP` | Required | Days supplied. This is the adherence numerator after cleaning. |
| `RXBEGMM` | Required | Month person first started the medicine. Used for the denominator sensitivity. **About 64% of 2023 rows are -15 (Cannot Be Computed); most patient-drug pairs will fall back to the `coverage_365` denominator. Expect this.** |
| `RXBEGYRX` | Required | Year person first started the medicine. Used to decide 365 days vs partial-year denominator. About 16% of 2023 rows are -8 (Don't Know). |
| `RXDRGNAM` | Required | Standard drug name. Used for drug-level drill-down. |
| `RXNAME` | Helpful | Readable medicine name. Useful for checking results. |
| `TC1` | Required | Broad therapeutic class. Backup grouping field. |
| `TC1S1` | Required | Therapeutic subclass. Main grouping if sub-subclass is missing. |
| `TC1S1_1` | Required | Therapeutic sub-subclass. Preferred medication-group field when valid. |
| `TC1S1_2` | Optional | Extra class field. Use only as a backup/sanity check. |

**Constructed Q1 columns**

| New column | Built from | Why needed |
|---|---|---|
| `valid_days_supplied` | `RXDAYSUP` | Keeps only real day values `1-990`. |
| `is_prn_fill` | `RXDAYSUP == 999` | Flags as-needed medicine rows. Do not treat as 999 days. |
| `missing_days_count` | special/missing `RXDAYSUP` codes | Tracks rows that cannot contribute to adherence numerator. |
| `total_valid_days` | sum of `valid_days_supplied` | Main adherence numerator per patient-drug. |
| `valid_fill_count` | count of valid fill rows | Refill-continuity feature. |
| `eligible_days_from_start` | `RXBEGMM`, `RXBEGYRX` | Denominator sensitivity for medicines started during the year. Fall back to `coverage_365` when start month/year are missing. |
| `coverage_365` | `total_valid_days / 365` | Simple annual coverage measure. |
| `coverage_from_start` | `total_valid_days / eligible_days_from_start` | Better measure for same-year medication starts. |
| `coverage_from_start_capped` | capped coverage ratio | Prevents stockpiling from exceeding 1 in summaries. |
| `below_75` | capped coverage `< 0.75` | Main low-continuity outcome. |
| `med_group` | `TC1S1_1`, else `TC1S1`, else `TC1` | Main Q1 grouping variable. |

**Do not use for Q1 base:** `ICD10CDX`, `AGEDIAG`, `is_chronic.xlsx`, or `complete_df_pivot.xlsx`.

## Q2a Cost and Adherence

**Question:** Does cost affect adherence?

Start from the Q1 patient-drug table, then add these.

**Note on `h250.xlsx` (Person Round Plan):** excluded from v1. Most of its plan-design columns (`PLANMETL` 98% inapplicable, `ANNDEDCTP` 72% inapplicable, `OOPPREMX` mostly inapplicable) are missing for the majority of rows, and the file is discontinued after 2023, so anything we teach Friana on it does not generalize. Cost signals come from `h251.xlsx` (`RXSLF{yy}`, `INSCOV{yy}`, `PMEDUP{round}`) instead.

### From `h248a.xlsx`

| Column | Use | Why needed |
|---|---|---|
| `RXSF23X` | Required for fill-level cost | Amount paid by self/family for the prescription event. Year-suffix changes per year (`RXSF22X` etc.). |
| `RXXP23X` | Helpful | Total prescription event payment. Use for total cost context. Year-suffix changes per year. |

**Constructed cost columns**

| New column | Built from | Why needed |
|---|---|---|
| `total_oop_cost` | sum of `RXSF23X` by `DUPERSID + DRUGIDX` | Patient-drug out-of-pocket burden. |
| `mean_oop_per_fill` | mean `RXSF23X` | Easier comparison across drugs with different fill counts. |
| `total_rx_cost` | sum of `RXXP23X` | Total event cost, not just patient cost. |

### From `h251.xlsx`

| Column | Use | Why needed |
|---|---|---|
| `DUPERSID` | Required | Merge key from person file to patient-drug table. |
| `AFRDPM42` | Required | Direct survey item: could not afford prescribed medicine care. |
| `DLAYPM42` | Required | Direct survey item: delayed prescribed medicine care because of cost. |
| `PMEDUP31` | Helpful | Usual third-party payer for medicines, round 3/1. |
| `PMEDUP42` | Helpful | Usual third-party payer for medicines, round 4/2. |
| `PMEDUP53` | Helpful | Usual third-party payer for medicines, round 5/3. |
| `INSCOV23` | Required | Insurance coverage category. Year-suffix changes per year. |
| `POVCAT23` | Required | Poverty category. Year-suffix changes per year. |

## Q2b Conditions and Chronic Burden

**Question:** How do conditions/chronic burden relate to adherence?

Use the Q1 patient-drug table first. Then link prescriptions to conditions through CLNK.

### From `h248a.xlsx`

| Column | Use | Why needed |
|---|---|---|
| `DUPERSID` | Required | Keeps patient-drug rows tied to the person. |
| `DRUGIDX` | Required | Keeps condition context attached to the patient-drug row. |
| `LINKIDX` | Required | Correct event link from prescription file to CLNK. |

### From `h248if1.xlsx`

| Column | Use | Why needed |
|---|---|---|
| `EVNTIDX` | Required | Join key from `h248a.LINKIDX`. |
| `CONDIDX` | Required | Join key into condition file `h249`. |
| `EVENTYPE` | Required | Filter to prescription events with `EVENTYPE == 8`. |
| `CLNKIDX` | Helpful | Checks duplicate condition-event links. |

### From `h249.xlsx`

| Column | Use | Why needed |
|---|---|---|
| `CONDIDX` | Required | Correct merge key from CLNK. |
| `ICD10CDX` | Required | Main condition code. Example: diabetes `E11`. |
| `CCSR1X` | Helpful | Broader condition group. Useful when ICD codes are too granular. |
| `RXCOND` | Helpful | Sanity check that a medicine purchase is associated with the condition. **Not present in the 2020 conditions file (`h222.xlsx`); guard with `if year >= 2021`.** |
| `AGEDIAG` | Diagnostic only | Do not use for adherence denominator. It is condition diagnosis age and often missing. |

### From `h251.xlsx`

Use these as broad condition-burden flags without CLNK.

| Column | Use | Why needed |
|---|---|---|
| `DIABDX_M18` | Required | Diabetes diagnosis flag. |
| `HIBPDX` | Required | High blood pressure diagnosis flag. |
| `CHDDX` | Required | Coronary heart disease flag. |
| `ANGIDX` | Required | Angina flag. |
| `MIDX` | Required | Myocardial infarction flag. |
| `OHRTDX` | Required | Other heart disease flag. |
| `STRKDX` | Required | Stroke flag. |
| `ARTHDX` | Helpful | Arthritis flag. |
| `ASTHDX` | Helpful | Asthma flag. |

**Constructed Q2b columns**

| New column | Built from | Why needed |
|---|---|---|
| `linked_condition_count` | CLNK + `CONDIDX` | Number of conditions linked to the patient-drug. |
| `linked_icd10_codes` | CLNK + `ICD10CDX` | Describes conditions linked to the drug event. |
| `priority_condition_count` | selected `h251` condition flags | Broad chronic/condition burden feature. |

**Use cautiously:** `is_chronic.xlsx`. It is a draft lookup, not truth. It needs review before being used as a model feature.

## Q2c Side Effects / Symptom Proxies

**Question:** How do side effects relate to adherence?

MEPS does not give clean drug-specific side effects in these files. These variables are only proxies for symptoms or mental/physical burden.

**File:** `h251.xlsx`

| Column | Use | Why needed |
|---|---|---|
| `K6SUM42` | Optional proxy | Psychological distress score. Could affect adherence but is not a side effect. |
| `PHQ242` | Optional proxy | Depression screen score. Could affect adherence but is not drug-specific. |
| `ADPAIN42` | Optional proxy | Pain item. Could relate to medication burden or adherence. |

**Teaching note:** For Q2c, say clearly that this project cannot directly measure medication side effects with the current MEPS files.

## Q3 Model Features

**Question:** Can we predict future non-adherence for a patient-drug pair?

With 2023 only, the model can predict low 2023 continuity, not future non-adherence. A true future model needs multiple years where earlier-year features predict later-year adherence.

### Model target

| Column | Source | Why needed |
|---|---|---|
| `below_75` | constructed from Q1 | Binary target: low refill continuity. |
| `coverage_from_start_capped` | constructed from Q1 | Continuous target or outcome for regression. |

### Model feature columns

| Feature group | Columns | Why needed |
|---|---|---|
| Medication | `med_group`, `RXDRGNAM`, `valid_fill_count`, `total_valid_days`, `is_prn_fill`, `missing_days_count` | Captures medication type and fill pattern. |
| Cost | `total_oop_cost`, `mean_oop_per_fill`, `AFRDPM42`, `DLAYPM42`, `INSCOV23`, `POVCAT23` | Tests whether cost/access predicts low continuity. |
| Demographics | `AGE23X`, `SEX`, `RACETHX`, `REGION23` | Basic patient controls. |
| Condition burden | `priority_condition_count`, `linked_condition_count`, selected condition flags | Captures illness burden and drug-condition context. |
| Symptom proxies | `K6SUM42`, `PHQ242`, `ADPAIN42` | Optional proxies, not drug-specific side effects. |

### From `h251.xlsx` for model demographics

| Column | Use |
|---|---|
| `AGE23X` | Age feature. Year-suffix changes per year. |
| `SEX` | Sex feature. |
| `RACETHX` | Race/ethnicity feature. |
| `REGION23` | Region feature. Year-suffix changes per year. |
| `EDUCYR` | Years of education (0–17). Sentinels: −1 inapplicable, −7 refused, −8 DK. |
| `MARRY31X` / `MARRY42X` / `MARRY53X` / `MARRY23X` | Marital status by round (use year-end `MARRY23X` with backfill). |
| `REGION31` / `REGION42` / `REGION53` / `REGION23` | Census region by round (use year-end `REGION23` with backfill). |

### Constructed person features (2023_clean `model_df`)

| New column | Built from | Notes |
|---|---|---|
| `MARRYXX` | `MARRY23X` ← `53` ← `42` ← `31` | First non-negative round walking newest → oldest. |
| `REGIONXX` | `REGION23` ← `53` ← `42` ← `31` | Same backfill rule. |
| `medication_dose` | `RXSTRENG` | Numeric strength; −15 / text combos → missing. |
| `medication_dose_unit` | `RXSTRUNT` | e.g. MG, MCG; −15 → missing. |
| `medication_freq` | `RXQUANTY / RXDAYSUP` | Pills (or form units) per day; only if qty > 0 and days in 1…989; **>10 → missing**. |
| `medication_qty_unit` | `RXFORM` | What quantity counts (TABS, CAPS, SOLN, …); −15 → missing. |

**`MARRYXX` codes:** −8 DK, −7 refused, −1 inapplicable; 1 married, 2 widowed, 3 divorced, 4 separated, 5 never married, 6 under age 16, 7–10 married/widowed/divorced/separated in round.

**`REGIONXX` codes:** −1 inapplicable; 1 Northeast, 2 Midwest, 3 South, 4 West.

**`EDUCYR` codes:** −8 DK, −7 refused, −1 inapplicable; 0 no school/K only; 1–8 elementary; 9–11 HS incomplete; 12 grade 12; 13–16 college years; 17 5+ years college.

### From `h248a.xlsx` for dose / quantity

| Column | Use |
|---|---|
| `RXSTRENG` | Medication strength amount. |
| `RXSTRUNT` | Strength unit (MG, MCG, …). |
| `RXQUANTY` | Quantity dispensed (count of form units). Official name is `RXQUANTY`, not `RXQUANTITY`. |
| `RXFORM` | Dosage form / quantity counting unit (TABS, CAPS, …). |
| `RXDAYSUP` | Days supplied (denominator for pills/day). |

## Survey Design Columns

Use only when producing weighted estimates or survey-aware standard errors. Do not let these distract from the first unweighted teaching notebook.

| File | Columns | Why needed |
|---|---|---|
| `h248a.xlsx` or `h251.xlsx` | `PERWT23F`, `VARSTR`, `VARPSU` | MEPS person weight, variance stratum, and variance PSU. Year-suffix changes per year (`PERWT22F` etc.). |

## Exclude From the Modeling Base for Now

| Item | Why exclude |
|---|---|
| `complete_df_pivot.xlsx` | Already mixes years, conditions, and drugs; not a clean patient-drug table. |
| Direct Rx-to-condition merge on `DUPERSID` | Creates every drug crossed with every condition for a person. |
| `AGEDIAG` as denominator | Diagnosis age is not medication start date. |
| `is_chronic.xlsx` without review | Draft chronic labels have questionable examples. |
| `h250.xlsx` plan-design columns | Mostly inapplicable rows in 2023; discontinued after 2023, so anything learned on it doesn't generalize. |

---

## Cross-year verification (2020 – 2023)

Verified by loading each year's consolidated, conditions, and Rx files and checking column existence directly. Bottom line: **this guide is usable for all four years with one specific caveat.**

### Columns confirmed present in every year (2020, 2021, 2022, 2023)

| File | Columns |
|---|---|
| `h251.xlsx` (consolidated) | `AFRDPM42`, `DLAYPM42`, `DLAYCA42`, `DLAYDN42`, `PMEDUP31`, `PMEDUP42`, `PMEDUP53`, `ADPAIN42`, `DIABDX_M18`, `HIBPDX`, `CHDDX`, `ANGIDX`, `MIDX`, `OHRTDX`, `STRKDX`, `ARTHDX`, `ASTHDX`, `RACETHX`, `SEX`, `AGE{yy}X`, `INSCOV{yy}`, `POVCAT{yy}`, `REGION{yy}`, `RXSLF{yy}`, `RXTOT{yy}`, `RXEXP{yy}`, `K6SUM42`, `PHQ242` |
| `h249.xlsx` (conditions) | `DUPERSID`, `CONDIDX`, `ICD10CDX`, `CCSR1X`, `AGEDIAG` |
| `h248a.xlsx` (Rx) | `DUPERSID`, `DRUGIDX`, `RXRECIDX`, `LINKIDX`, `RXDAYSUP`, `RXBEGMM`, `RXBEGYRX`, `RXDRGNAM`, `RXNAME`, `TC1`, `TC1S1`, `TC1S1_1`, `TC1S1_2`, `DIABEQUIP`, `PURCHRD`, `RXSF{yy}X`, `RXXP{yy}X`, `PERWT{yy}F` |

### The one exception

| Column | File | Status |
|---|---|---|
| `RXCOND` | conditions file | **Absent in 2020 (`h222.xlsx`).** Present from 2021 onward (`h231.xlsx`, `h241.xlsx`, `h249.xlsx`). |

Guard the lookup with `if year >= 2021:` when looping across years. Drop it from the 2020 column list. The analysis still works without it — `RXCOND` is the "Helpful, not Required" sanity check that a fill is linked to a condition; the primary CLNK-based linkage via `EVENTYPE == 8` is unaffected.

### Year-suffix swap rule

When you redo any of this work on a non-2023 year, change the year suffix only on the columns marked with `{yy}` above. Everything else stays the same name.

| Original (2023) | 2022 | 2021 | 2020 |
|---|---|---|---|
| `AGE23X` | `AGE22X` | `AGE21X` | `AGE20X` |
| `INSCOV23` | `INSCOV22` | `INSCOV21` | `INSCOV20` |
| `POVCAT23` | `POVCAT22` | `POVCAT21` | `POVCAT20` |
| `REGION23` | `REGION22` | `REGION21` | `REGION20` |
| `RXSLF23` | `RXSLF22` | `RXSLF21` | `RXSLF20` |
| `RXTOT23` | `RXTOT22` | `RXTOT21` | `RXTOT20` |
| `RXEXP23` | `RXEXP22` | `RXEXP21` | `RXEXP20` |
| `RXSF23X` | `RXSF22X` | `RXSF21X` | `RXSF20X` |
| `RXXP23X` | `RXXP22X` | `RXXP21X` | `RXXP20X` |
| `PERWT23F` | `PERWT22F` | `PERWT21F` | `PERWT20F` |

The round-suffixed columns (`AFRDPM42`, `PMEDUP31/42/53`, `DLAYPM42`, `ADPAIN42`, `K6SUM42`, `PHQ242`) keep the same name every year. The `42` is round 4 of Panel 2 / round 2 of Panel 1 — round-based, not year-based.
