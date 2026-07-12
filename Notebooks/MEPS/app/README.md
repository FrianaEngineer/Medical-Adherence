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

## Refresh a year

```bash
cd Notebooks/MEPS/app
python 2023_clean.py
```

Outputs still write to `Notebooks/MEPS/output/<year>/`.
