# Repository Guidelines

## Project Structure & Module Organization

This repository contains a notebook-first analysis for medication adherence prediction using CMS DE-SynPUF and MEPS data. The main project description and modeling assumptions live in `README.md`. Exploratory and feature-development work lives in `Notebooks/`, including year-specific EDA notebooks such as `2020EDA.ipynb`, `2021EDA.ipynb`, and merge work in `allYearMerge.ipynb`. Source datasets are stored under `data/CMS_SynPUF/` and `data/MEPS/`, with raw archives separated from extracted CSV or Excel files. Planning documents are in `Study_Plan/`.

Before reviewing MEPS notebooks, read `MEPS_CONTEXT.md`. It records the 2023 codebook/linkage findings, Mehak's review goals, and the expected RX-condition linkage logic.

## Build, Test, and Development Commands

There is no package manifest or Makefile yet. Use a local Python environment and run notebooks directly:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install pandas numpy matplotlib seaborn scipy python-calamine openpyxl jupyter
jupyter lab
```

Open notebooks from `Notebooks/` and restart the kernel before running all cells. If adding scripts later, prefer placing reusable Python modules outside notebooks and documenting any new command in this file.

## Coding Style & Naming Conventions

Use Python 3, four-space indentation, descriptive variable names, and clear section headings in notebooks. Keep file names descriptive and consistent with existing patterns, such as `2023EDA.ipynb` for year-specific exploration and `Sample1_Graphs.ipynb` for sample-specific visualizations. Avoid hard-coded absolute paths; build paths with `pathlib.Path` relative to the repository root. Keep notebook outputs only when they clarify analysis results.

## Testing Guidelines

No automated tests are configured. For notebook changes, validate by running the modified notebook from a clean kernel and confirming key tables, plots, and exported files are regenerated. If reusable data-cleaning or modeling code is extracted into Python modules, add focused tests under a future `tests/` directory using `pytest`, with names like `test_pdc_calculation.py`.

## Commit & Pull Request Guidelines

Recent commits use short, imperative summaries such as `updated gitignore` and `Update EDA notebooks`. Continue using concise messages that name the changed analysis or data workflow. Pull requests should include a brief purpose, the notebooks or data files changed, validation performed, and screenshots or saved plots when visual output changes. Link any relevant study-plan task or issue.

## Security & Configuration Tips

Do not commit private credentials, API keys, or external patient-level data. Treat `data/` as large-source-data storage; document any newly added raw files and keep derived outputs reproducible from notebooks.
