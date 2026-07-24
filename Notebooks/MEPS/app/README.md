# MEPS Medical Adherence — Streamlit app

All app Python scripts live here.

| File | Role |
|------|------|
| `simple_app.py` | Streamlit UI |
| `clean_meps.py` | Shared build / export backend |
| `2020_clean.py` … `2023_clean.py` | Year export runners |
| `requirements.txt` | Dependencies |

## Run the app

```bash
cd Notebooks/MEPS/app
pip install -r requirements.txt
streamlit run simple_app.py
```

## Prebuild all-years cache (recommended)

Merging years inside the Streamlit UI is slow. Build the parquet + filter cache once in a terminal:

```bash
cd Notebooks/MEPS/app
python clean_meps.py cache-all-years
```

This writes:

- `../output/all_years/tables/new_grouped_merge_df_chronic_drugs.parquet`
- `../output/all_years/tables/filter_options.json`

Requires each year’s chronic-drug export under `../output/<year>/tables/` (xlsx or parquet).

## Refresh a year

```bash
cd Notebooks/MEPS/app
python 2023_clean.py
# or:
python clean_meps.py export --year 2023
```

Then refresh the all-years cache:

```bash
python clean_meps.py cache-all-years
```

Outputs write to `Notebooks/MEPS/output/<year>/` and `Notebooks/MEPS/output/all_years/`.
