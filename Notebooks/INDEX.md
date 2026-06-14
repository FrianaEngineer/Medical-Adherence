# Notebooks — INDEX

A 30-second orientation. Open this file when you're not sure which notebook to use.

Last updated: 2026-06-14.

---

## In active use

### `PDC_Walkthrough.ipynb`
**Authored by Mehak. This is the teaching reference, not a workbook you write in.**
The Friday/weekend session uses this. It walks through PDC (Proportion of Days Covered) end-to-end on the 2023 MEPS data, including the h248a → CLNK → h249 → is_chronic → h250 merge chain, the eligibility-window denominator concept, and two views of Sub-question 1 (by drug class, by condition). Sections 15–18 are markdown-only stubs for Sub-questions 2a/2b/2c and the modeling part — those get coded in future sessions.

Open this when:
- You want to look up a method, a column name, or how the merge graph fits together.
- You're stuck on something Mehak walked through and want to re-read the explanation.
- You're heading into Section 15/16/17/18 work and want to read the plan before coding.

Do NOT open this when:
- You're writing your own code in `Friana_PDC_2023.ipynb`. Close this tab. The point is for the code to come from your memory, not from copying.

### `Friana_PDC_2023.ipynb` *(to be created by Friana)*
**You write this. From scratch. Cursor closed unless Mehak prompts.**
Same content as `PDC_Walkthrough.ipynb` but written in your own hand. The goal is ownership — you walk into August able to explain every cell because you wrote every cell. When you finish a section, defend it to Mehak before moving on.

### `allYearMerge.ipynb`
The v1 stacked frame you built (2020–2023, with `meps_year` column). Joins h248a × h248if1 × h249 × is_chronic across all four years. Computes raw `sum(RXDAYSUP)` per (DUPERSID, DRUGIDX) for diabetes. **Has known gaps:** no `EVENTYPE == 8` filter, no `DIABEQUIP` exclusion, the `age == AGEDIAG` filter restricts the cohort accidentally, and there's no denominator. These are corrected in `allYearMerge_v2.ipynb`.

### `allYearMerge_v2.ipynb` *(to be created)*
The all-years version of the PDC calculation in `Friana_PDC_2023.ipynb`. Same logic, applied across 2020/2021/2022/2023 with year-suffix swaps. Outputs `pdc_panel_2020_2023.csv` — the panel frame that feeds the modeling work.

### `EDAmeps.ipynb`
Structural reference EDA. Confirms `RXRECIDX` as the row key for the Rx file, documents the unsafe vs safe joins (the Cartesian-explosion trap), and shows the recommended person-drug grain. Read this once if you forget how the files link; otherwise it's reference only.

### `2020EDA.ipynb`, `2021EDA.ipynb`, `2022EDA.ipynb`, `2023EDA.ipynb`
Per-year EDA notebooks. Each builds the year's `merged_df` (Rx fills left-joined to the full-year person file on `DUPERSID`). These are predecessors to `allYearMerge.ipynb` and have the same gaps as the v1 stacker. Use them as historical reference, not as templates.

### `insights.ipynb`
CMS DE-SynPUF refill-frequency exploratory analysis. Out of scope for the current MEPS-only direction. Don't extend.

### `Sample1_Graphs.ipynb`
CMS DE-SynPUF visualizations. Out of scope. Don't extend.

### `EDA.ipynb`
Legacy CMS SynPUF exploration. Out of scope. Don't extend.

---

## Data files this folder reads from

| File | Where it lives | One row = |
|---|---|---|
| `h248a.xlsx` | `../data/MEPS/excels/` | one prescription fill (~192k for 2023) |
| `h248if1.xlsx` | `../data/MEPS/excels/` | one event-condition link (~281k for 2023) |
| `h249.xlsx` | `../data/MEPS/excels/` | one current condition per person (~64k) |
| `h250.xlsx` | `../data/MEPS/excels/` | one person × round × establishment × plan (~35k) — **2023 is the last public year** |
| `h251.xlsx` | `../data/MEPS/excels/` | one person × year (~19k) — 1,374 columns |
| `is_chronic.xlsx` | `../data/MEPS/excels/` | one ICD-10 code with hand-curated chronic flag (~251 codes) |

Year mapping for older files:

| Year | Rx | CLNK | Conditions | Plans | Consolidated |
|---|---|---|---|---|---|
| 2020 | h220a | h220if1 | h222 | h223 | h224 |
| 2021 | h229a | h229if1 | h231 | h232 | h233 |
| 2022 | h239a | h239if1 | h241 | h242 | h243 |
| 2023 | h248a | h248if1 | h249 | h250 | h251 |

---

## Output files (CSVs) the notebooks write

| File | Written by | Read by |
|---|---|---|
| `pdc_2023_by_class.csv` | `PDC_Walkthrough.ipynb` Section 14 / `Friana_PDC_2023.ipynb` | Sub-question 2a notebook, future modeling |
| `pdc_2023_by_class_and_condition.csv` | same | by-condition stratification |
| `pdc_2023_with_insurance.csv` | same (Section 13 + 14) | Sub-question 2a (cost) work |
| `pdc_panel_2020_2023.csv` | `allYearMerge_v2.ipynb` | modeling table builder |

---

## When you move to Python scripts (early August)

The notebook structure ports one-to-one into `../src/med_adherence/` modules. See the closing section of `PDC_Walkthrough.ipynb` for the section-to-function mapping. After scripts ship, the notebooks become demos that import from the package rather than re-implementing everything inline.

---

## Notebook hygiene rules

1. **One canonical chronic-flag file.** `is_chronic.xlsx` and `Table1_is_chronic.xlsx` are near-duplicates. Pick one before August. If you ship both you'll get the "why are there two of these?" interview question.
2. **Don't `.sample()` the stacked frame.** `allYearMerge.ipynb` builds `merged_df` in deliberate `[2023, 2022, 2021, 2020]` order. A `.sample()` randomizes years and breaks downstream year-stratified analysis.
3. **Don't open `PDC_Walkthrough.ipynb` while writing `Friana_PDC_2023.ipynb`.** Closed tab = real ownership. Open tab = copying, even if accidentally.
4. **Cursor stays closed unless Mehak prompts it.** Only when we've genuinely tried and a method or error refuses to clarify.
