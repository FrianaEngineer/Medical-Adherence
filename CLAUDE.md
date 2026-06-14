# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Who you're working with, and what this repo is

The primary user of this repo via Claude Code is **Mehak**, who is the **mentor** on a mentee-led research project, not the implementer. The mentee is a Cornell freshman building a medication non-adherence analysis on MEPS-HC (2020–2023). The mentee writes notebooks by prompting Cursor at the level of "make a scatter of x and y" — the notebook code is Cursor's output, not the mentee's. **Mehak's job (and therefore yours, when she asks) is to read the notebooks and tell her what's actually happening in them, so she can teach against the gaps.** Default behavior:

- Do **not** rewrite a mentee's notebook unless Mehak explicitly says to. Rewriting bypasses the lesson she's trying to teach.
- Do **not** address output to the mentee. Briefings are for Mehak.
- Do **not** speculate. If a notebook cell is ambiguous, say so and point at the cell.
- Mehak is technical and does not need pandas or MEPS hand-holding. Lead with findings, not background.

## Project scope

**MEPS-HC 2020–2023, all chronic conditions.** The atomic unit is **MPR-style adherence per patient per drug**: `sum(valid RXDAYSUP) / eligible_days` per `(DUPERSID, drug)`, with the mentee's threshold of 75% ("covers 9 months of the year"). Denominator must be per-year (365), not 4-year combined (1460). Diabetes (ICD-10 E11) is the first concrete case the mentee starts with; the project generalizes to all chronic conditions thereafter.

**Out-of-pocket cost is a first-class predictor**, not a side note. It lives at three grains: per-fill (`h248a.RXSF23X`), per-year (`h251.RXSF23` and the `*SLF23` family across services), and at the plan level (`h250.OOPPREMX`, `OOPX12X`, `ANNDEDCTP`, `PMEDINS`). All three matter.

**CMS SynPUF is out of scope.** The original README was written around CMS SynPUF, but CMS data isn't openly redistributable for this project. Ignore the SynPUF framing in `README.md` — the data files in `data/CMS_SynPUF/` are not the active dataset and the SynPUF-specific success metrics (90-day PDC, 180-day lookback, refill-gap baseline) don't directly transfer to MEPS because MEPS has no fill dates (only survey rounds).

Code is currently **exploratory only** — Jupyter notebooks doing EDA and merging. No training pipeline or package yet.

## Repository Layout

- `Notebooks/` — all analysis lives here as `.ipynb` files. There is no `src/` package and no test suite.
  - `EDAmeps.ipynb` — MEPS exploration. Establishes `RXRECIDX` as the Prescribed Medicines primary key, documents safe vs unsafe joins, defines the **person-drug grain** as the recommended unit.
  - `2020EDA.ipynb` … `2023EDA.ipynb` — per-year MEPS EDA, each building a `merged_df` (Rx fills left-joined to the full-year person file on `DUPERSID`).
  - `allYearMerge.ipynb` — stacks 2023 → 2022 → 2021 → 2020 from the per-year notebooks and adds a `meps_year` column. Row order is preserved deliberately — **do not `.sample()` the stacked frame**; it randomizes years.
  - `insights.ipynb`, `Sample1_Graphs.ipynb` — visualizations and diagnostic aggregates (e.g., non-adherence rate by chronic burden × year).
  - `EDA.ipynb` — legacy CMS SynPUF exploration. Out of scope for the active project; do not extend.
- `data/MEPS/` — the active dataset.
  - `excels/` — one `.xlsx` per file-type per year (e.g., `h248a.xlsx` is the 2023 Prescribed Medicines file).
  - `docs/` — the official AHRQ codebook and documentation PDFs for the 2023 files (h248a, h248i, h249, h250, h251). The variable names and conventions apply to 2020–2022 with year-suffix swaps.
  - `zips/` — original AHRQ archives.
  - **Never mix years in one row.** Matched file sets are joined within a single calendar year on `DUPERSID`.
- `data/CMS_SynPUF/` — **out of scope.** Legacy from when the project was framed against CMS SynPUF. Don't pull these unless Mehak explicitly redirects.
- `Study_Plan/` — `.docx` planning documents (Friana_Study_Plan, Mehak_Playbook, Cursor_Guide, SessionPrompts_and_ChangeLog). Design notes, not code.

## MEPS schema deep-dive — read this first

**Before doing anything substantive with the MEPS notebooks, read `data/MEPS/MEPS_SCHEMA_NOTES.md`.** That doc is the synthesized briefing from full reads of the official 2023 codebooks (`data/MEPS/docs/h248acb.pdf`, `h248icb.pdf`, `h249cb.pdf`, `h250cb.pdf`, `h251cb.pdf`) and the matching documentation PDFs. It covers:

- The five files: h248a (RX, 192k rows), h248i (CLNK bridge, 281k rows), h249 (conditions, 64k rows), h250 (private-insurance plans, 35k rows, **final public release**), h251 (person-year consolidated, 19k rows).
- The correct 3-table RX → conditions join (`h248a.LINKIDX = h248i.EVNTIDX`, filter `EVENTYPE == 8`, then `h248i.CONDIDX = h249.CONDIDX`). Why `h248a × h249` on `DUPERSID` alone is a Cartesian explosion.
- The `RXDAYSUP == 999` ("as needed") trap and the full list of MEPS missing-value codes (`-1`, `-2`, `-7`, `-8`, `-10`, `-14`, `-15`).
- The complete OOP / source-of-payment inventory across all three grains (per-fill, per-year, plan-level).
- Where each chronic condition lives — direct flags on h251, ICD-10 prefixes on h249 (with row counts), CCSR groupings, and the priority-condition list. CKD, hypothyroidism, heart failure, A-fib are **not** on h251 — they live only on h249.
- Why `AGEDIAG` came back null for the mentee (most likely the wrong file; `h251.<COND>AGED` is the better source for any chronic cohort built around a person-file flag).
- Denominator choices, `DUPERSID` stability and year-specific weight caveats, and Multum `TC1`/`TC1S1` codes for maintenance-drug filtering.
- A bottom-line checklist for reading a mentee notebook.

**Use it as the source of truth for MEPS variable semantics.** If something in a mentee notebook conflicts with that doc, that's the finding.

## Working in Notebooks

- Excel reading uses the **`calamine`** engine (via `python-calamine`); notebooks auto-pip-install it if missing. Don't switch to `openpyxl` — calamine is materially faster on these workbooks.
- Notebooks resolve `data/MEPS` by walking up from `Path.cwd()` and checking several candidates, so they run whether the kernel CWD is the repo root or `Notebooks/`. Preserve this pattern.
- One per-year MEPS notebook is the canonical builder of that year's `merged_df`; `allYearMerge.ipynb` re-applies the same column sets across years. If you change a column list or join rule in a per-year notebook, mirror it in `allYearMerge.ipynb`.
- Stack: `pandas`, `numpy`, `scipy`, `matplotlib`, `seaborn`. No sklearn or DL pipeline committed yet.
