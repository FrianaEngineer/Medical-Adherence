# MEPS Project Context for Notebook Review

## Audience and Role

This briefing is for Mehak, the technical mentor on the project. Do not rewrite the mentee's notebooks or produce polished replacement analysis unless Mehak explicitly asks. The task is to read what the notebooks actually do, explain it plainly, and identify where Cursor-generated code diverges from the intended MEPS adherence workflow.

## Project Goal

The project studies medication non-adherence using MEPS-HC data from 2020-2023. The intended atomic unit is patient-drug-year adherence: group fills by person and drug, sum valid `RXDAYSUP`, divide by a defensible year-level denominator, and apply the mentee's 75% threshold.

Target files by year:

| Year | Prescribed Medicines | Conditions | Person Round Plan | Full-Year Consolidated | CLNK |
| --- | --- | --- | --- | --- | --- |
| 2020 | `HC-220A` | `HC-222` | `HC-223` | `HC-224` | `HC-220I`, file 1 |
| 2021 | `HC-229A` | `HC-231` | `HC-232` | `HC-233` | `HC-229I`, file 1 |
| 2022 | `HC-239A` | `HC-241` | `HC-242` | `HC-243` | `HC-239I`, file 1 |
| 2023 | `HC-248A` | `HC-249` | `HC-250` | `HC-251` | `HC-248I`, file 1 |

## What the 2023 Docs Establish

PDF audit status: all 10 PDFs in `data/MEPS/docs/` were converted to text and reviewed for file purpose, row grain, identifiers, linkage rules, reserved codes, trend/longitudinal cautions, and variables relevant to medication adherence. The large consolidated codebook has 1,374 variables, so do not assume every variable has been semantically analyzed; use it as a lookup when a new covariate is needed.

`HC-248A` is the 2023 Prescribed Medicines PUF. Each row is a fill or refill obtained in calendar year 2023. It contains 192,275 records. Key fields include `DUPERSID`, `RXRECIDX`, `LINKIDX`, `DRUGIDX`, `RXDRGNAM`, `RXNDC`, `RXDAYSUP`, `PERWT23F`, `VARSTR`, and `VARPSU`.

`HC-249` is the 2023 Medical Conditions PUF. Each row is one condition record for a person. It contains 63,656 records. Key fields include `DUPERSID`, `CONDIDX`, `CONDRN`, `AGEDIAG`, `ICD10CDX`, `CCSR1X`-`CCSR4X`, `RXCOND`, `PERWT23F`, `VARSTR`, and `VARPSU`.

`HC-248I` file 1 is CLNK, the condition-event link file. It contains six variables: `DUPERSID`, `CONDIDX`, `EVNTIDX`, `CLNKIDX`, `EVENTYPE`, and `PANEL`. For 2023 it has 281,158 records. `EVENTYPE=8` means the linked event is a prescribed medicine event.

`HC-251` is the 2023 Full-Year Consolidated PUF. Each row is a person. It contains demographics, person weights, survey design variables, priority condition indicators, and utilization totals. Relevant fields include `DUPERSID`, `AGE23X`, `SEX`, `DIABDX_M18`, `DIABAGED`, `PERWT23F`, `VARSTR`, and `VARPSU`.

`HC-250` is the Person Round Plan file. It is a person-round-policyholder-establishment-plan file for private insurance coverage, not a simple person-level file. It is not central to patient-drug adherence unless the notebook explicitly studies plan structure.

The prescribed medicines codebook also includes `RXBEGMM` and `RXBEGYRX`, the month and year the person first started taking the medicine. These may help reason about medication exposure windows better than condition `AGEDIAG`, but they are not exact fill dates and do not prove disease onset.

## PDF Files Reviewed

- `h248adoc.pdf` and `h248acb.pdf`: 2023 Prescribed Medicines documentation/codebook. Reviewed for fill-level grain, `DRUGIDX`, `RXRECIDX`, `LINKIDX`, `RXBEGMM`, `RXBEGYRX`, `RXDAYSUP`, payment variables, Multum therapeutic classes, missing codes, weights, and linking guidance.
- `h248idoc.pdf` and `h248icb.pdf`: 2023 event appendix/CLNK documentation/codebook. Reviewed fully for CLNK structure, `EVENTYPE`, `EVNTIDX`, `CONDIDX`, `CLNKIDX`, and linkage caveats.
- `h249doc.pdf` and `h249cb.pdf`: 2023 Medical Conditions documentation/codebook. Reviewed for condition row grain, `CONDIDX`, `ICD10CDX`, `CCSR1X`-`CCSR4X`, `AGEDIAG`, condition-round fields, `RXCOND`, reserved codes, and current-condition limitations.
- `h250doc.pdf` and `h250cb.pdf`: 2023 Person Round Plan documentation/codebook. Reviewed for row grain and why it is not a simple person-level adherence table; use only for plan/coverage subquestions after collapsing to an analytic unit.
- `h251doc.pdf` and `h251cb.pdf`: 2023 Full-Year Consolidated documentation/codebook. Reviewed for demographics, priority condition indicators/diagnosis-age variables, access/cost variables, person weights, variance variables, and linking/trend/longitudinal cautions.

## Correct Linkage Logic

Person-level attributes from `HC-251` merge to RX or conditions by `DUPERSID`.

Conditions do not attach to prescriptions by directly merging RX and conditions on `DUPERSID`. That creates every drug for a person crossed with every condition for that person.

Correct RX-condition linkage for 2023 is:

1. Filter CLNK to prescribed medicine events: `EVENTYPE == 8`.
2. Merge RX `HC-248A` to CLNK using `HC-248A.LINKIDX == HC-248I.EVNTIDX`.
3. Merge the result to conditions `HC-249` using `CONDIDX`.
4. Use `DUPERSID` as a consistency/person key, not as the event-condition key.

The 2023 CLNK documentation states that `EVNTIDX` is not included on `HC-248A`; `LINKIDX` is the RX-side variable used to link to CLNK `EVNTIDX`.

## Missing and Reserved Codes

Numeric MEPS fields use reserved negative codes that must not be summed or averaged as real values:

| Code | Meaning |
| --- | --- |
| `-1` | Inapplicable |
| `-7` | Refused |
| `-8` | Don't know / not ascertained |
| `-15` | Cannot be computed |

For `RXDAYSUP`, valid day supply values are `1-990`; `999` means taken as needed. In 2023, `RXDAYSUP` has many `-8` values and a small number of `-7` values. Any adherence denominator/numerator must decide how to handle `999` and exclude negative reserved codes from sums.

## Age of Diagnosis

`AGEDIAG` does exist in `HC-249`, but it is mostly inapplicable in the conditions file. In 2023, `AGEDIAG` has 50,385 `-1` inapplicable records out of 63,656 condition records; only 12,613 records have a value from `0-85`. The documentation says age of diagnosis is collected for priority conditions, except joint pain, and cancer conditions are set to `-1` for confidentiality.

For diabetes specifically, the consolidated file has `DIABDX_M18` and `DIABAGED`. In 2023, `DIABAGED` has valid values for 2,029 persons and `-1` inapplicable for 16,765 persons. If a notebook's diagnosis-age column became null, check whether it converted negative reserved codes to missing, merged on the wrong key, or used a condition record that was never eligible for `AGEDIAG`.

## Year Handling

When stacking 2020-2023 files, preserve a `year` column before concatenation. Otherwise patient-drug aggregation silently combines years and makes a 365-day denominator invalid.

Your assumption that core concepts repeat across years is reasonable, but names with year suffixes must be harmonized. Examples from 2023 include `PERWT23F`, `AGE23X`, and `DIABDX_M18`; older files will use their year-specific equivalents.

## Denominator Guidance for Review

For the intended MPR-style notebook, flag whether the code computes a denominator. Defensible choices for this session are per-year 365 days or a per-year eligible-days denominator. A four-year denominator such as 1460 is not appropriate unless the numerator and eligibility window are explicitly four-year person-drug intervals and IDs are validated for longitudinal use.

## Notebook Review Checklist

For each notebook, report what each cell accomplishes in purpose terms: what file it loads, what it filters, what it joins, on which key, and what the resulting frame represents.

Check these failure modes first:

- Direct RX-to-condition merge on `DUPERSID`, causing patient-level Cartesian expansion.
- Missing `year` before stacking annual files.
- Summing `RXDAYSUP` without removing `-7`, `-8`, `-15`, or handling `999`.
- Treating all drugs as maintenance drugs outside a clearly restricted disease/drug case.
- Using `AGEDIAG` or diagnosis-age columns without preserving reserved-code meaning.
- Computing adherence over a combined multi-year denominator when the intended unit is patient-drug-year.
- Reporting `RXDAYSUP` distributions with suspicious zeros or huge totals without tracing whether they came from missing-code handling, duplicate joins, or multi-year aggregation.
