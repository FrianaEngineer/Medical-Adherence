# Notebook Review for Mehak

## Executive Read

The notebooks partially follow MEPS correctly. The year-specific notebooks now understand the major table relationships: prescription fills merge to the full-year consolidated file on `DUPERSID`, and prescription events link to conditions through CLNK using `LINKIDX == EVNTIDX`, then `CONDIDX`.

The analysis goes sideways after linkage, mainly in `allYearMerge.ipynb`. It starts treating linked condition-prescription rows as if they are already clean patient-drug-year adherence rows. They are not. A prescription can link to multiple conditions, conditions can link to multiple events, and the same person-drug can appear across multiple years. The notebook also uses diagnosis age as if it can determine a precise medication eligibility denominator, but MEPS does not provide exact diagnosis dates.

Your instinct is right: if someone is diagnosed with heart disease mid-year, a 365-day denominator is not fair for that heart-disease medication. But MEPS only gives age at diagnosis for some priority conditions, not diagnosis month/day. `AGEDIAG == current age` means "diagnosed at this age," not "diagnosed on January 1." For clean Q1 teaching, use patient-drug-year refill continuity among observed users first, and treat incident-condition denominator adjustments as a limitation or later sensitivity step.

## Research Question Fit

### Q1: Which medication groups show lower refill continuity?

This is the strongest question for the current files. Use prescribed medicines files (`HC-220A/229A/239A/248A`) grouped by `meps_year`, `DUPERSID`, and a drug/group field such as `DRUGIDX`, `RXDRGNAM`, `TC1S1`, or `TC1S1_1`. Clean `RXDAYSUP` first.

Do not start Q1 from the condition-linked `complete_df`; it is already expanded by condition-event relationships. Q1 is about medication groups, so the prescription file itself is the right base.

### Q2a: Does cost affect adherence?

The files are usable. The Rx files contain fill-level out-of-pocket payment variables like `RXXP23X`, and consolidated files contain person-level cost/access proxies such as `PMEDUP*` where available. This should be joined after building patient-drug-year refill metrics.

### Q2b: How do conditions/chronic burden relate to adherence?

The files are usable, but chronic burden should be aggregated at the person-year level before joining to patient-drug-year outcomes. `is_chronic.xlsx` can support a condition-level chronic flag, but it should not be used to make every linked condition row become an adherence row.

### Q2c: How do side effects relate to adherence?

MEPS is weak for this. The notebooks use proxies like `CHPMED42`, mood, pain, or sleep variables. These can support exploratory associations, not drug-specific side-effect claims.

### Q3: Can we predict future non-adherence for a patient-drug pair?

Possible only after Q1 is made clean. The outcome needs to be patient-drug-year or next-year patient-drug behavior. Be careful: MEPS has overlapping two-year panels, so not every `DUPERSID` has a full 2020-2023 longitudinal path.

## Notebook-by-Notebook Review

## `2020EDA.ipynb`, `2021EDA.ipynb`, `2022EDA.ipynb`, `2023EDA.ipynb`

These are year-specific exploration notebooks. Each loads that year's prescribed medicines file, loads the matching full-year consolidated file, and merges Rx rows to person rows on `DUPERSID` with a many-to-one validation. The stored outputs show no unmatched prescription rows in the consolidated merge.

They then inspect demographics and therapeutic class variables. Later cells load the CLNK file, merge prescriptions to CLNK using `LINKIDX == EVNTIDX`, and merge to the conditions file using `CONDIDX`. That is the right MEPS linkage direction.

Issues:

- They do not consistently filter CLNK to `EVENTYPE == 8`. The `LINKIDX == EVNTIDX` merge should mostly constrain this to prescription events, but an explicit filter would make the logic teachable.
- These notebooks are exploratory. They do not compute a defensible adherence denominator.
- They show `AGEDIAG`, but do not resolve how it should or should not affect eligibility days.

## `EDAmeps.ipynb`

This is a broader orientation notebook. It inspects MEPS file types, builds a one-year prescription aggregation example, and correctly says prescription files are for outcomes while consolidated files provide demographics/cost/access covariates.

It has a useful one-year aggregation:

- groups by `DUPERSID`, `DRUGIDX`, `RXNAME`
- counts unique `RXRECIDX`
- sums positive `RXDAYSUP`
- attaches person-level variables from consolidated on `DUPERSID`

This is closer to the right Q1 starting point than the later all-condition pivot. It still does not compute the final adherence ratio, and `s.where(s > 0)` would include `999` as supply even though MEPS defines it as "taken as needed."

## `allYearMerge.ipynb`

This is the main notebook to teach from because it contains the central mistakes.

What it does correctly:

- Builds all-year `merged_df` by merging each year's Rx file to the matching consolidated file on `DUPERSID`.
- Adds `meps_year` before stacking.
- Reports 1,008,029 fill-level rows and 57,737 person-years.
- Builds `result_df` by linking Rx to CLNK and conditions through `LINKIDX == EVNTIDX` then `CONDIDX`.

Where it goes wrong:

- Cell 16 computes person-year `total_days_supply` by converting `RXDAYSUP` to numeric and filling missing with zero, but it does not remove negative MEPS missing codes. The stored output includes a person-year with `total_days_supply = -150` and `adherence_mpr = -0.410959`, which is impossible.
- Cell 16 computes a person-year MPR across all prescriptions, not a patient-drug or medication-group MPR. That answers "how much total medication supply did this person have," not Q1.
- Cell 30 pivots using `["DUPERSID", "DRUGIDX", "ICD10CDX"]` but not `meps_year`. This collapses years together and then keeps only the first `meps_year`. That breaks patient-drug-year adherence.
- Cell 30 sums `RXDAYSUP` after condition linkage. If a drug event links to multiple conditions, supply can be duplicated across condition rows.
- Cell 33 keeps rows where current age equals `AGEDIAG`. That is not a valid way to identify the condition that caused a prescription. It mostly finds newly diagnosed/same-age cases and throws away longstanding chronic disease, which is exactly the population needed for adherence.
- Stored execution counts are out of order. Later outputs cannot be trusted as a clean top-to-bottom notebook run.
- Cell 47 has a direct copy-paste bug: `PARKINSON_days_by_user_drug` is computed from `SCHIZOPHRENIA_df`, so the Parkinson summary repeats schizophrenia results.

The reported diabetes distribution is not the right final Q1 result. The notebook reports mean total days around 343 and max 7,233 for diabetes person-drug rows. That large maximum is not just "stockpiling"; it is also consistent with cross-year aggregation and possible duplicated linked condition rows. The earlier max around 1,600 would be plausible for four years; 7,233 is not plausible as a clean patient-drug-year supply.

## `EDA.ipynb` and `insights.ipynb`

These are CMS DE-SynPUF notebooks, not MEPS notebooks. They are useful for earlier thinking about claims/PDE structure, but they should not guide the MEPS Q1 implementation directly. DE-SynPUF has different identifiers, different prescription event structure, and different longitudinal assumptions.

## What Is Missing Conceptually

The missing piece is the eligible exposure window.

For a clean teaching version of Q1, define the first pass as:

`patient-drug-year refill continuity among people with at least one observed fill of that drug/group in that year`.

Then the denominator can be 365 for a simple MPR-style proxy, with a clearly stated limitation: this assumes the patient was eligible/expected to have the medication all year. It is acceptable for an introductory Q1 if framed correctly.

For incident conditions, your heart-disease example is correct. If diagnosis happens mid-year, 365 over-penalizes the patient. But MEPS does not give exact diagnosis dates, so the notebooks cannot cleanly compute "days since diagnosis" from `AGEDIAG`. At best, they can identify likely prevalent conditions where `AGEDIAG < current age`, ambiguous same-age diagnoses where `AGEDIAG == age`, and missing/inapplicable diagnosis ages.

## Teaching Direction

Teach the mentee this distinction:

- `DUPERSID + DRUGIDX + meps_year` is the adherence unit.
- `CONDIDX` explains why an event may have happened.
- `is_chronic` describes the condition, not the prescription.
- `AGEDIAG` is not a denominator.
- `RXDAYSUP` must be cleaned before summing.
- Q1 should be answered before modeling, side effects, or prediction.

The next notebook review should start from `allYearMerge.ipynb` cells 16, 19, 30, 33, and 36-51 because those cells contain the core teachable errors.
