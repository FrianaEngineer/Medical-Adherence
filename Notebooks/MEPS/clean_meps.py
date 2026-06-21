import pandas as pd
import numpy as np



# MEPS sentinel values. These mean 'missing,' not a real measurement.
SENTINELS = [-1, -7, -8, -9, -15]

# Columns we sanitize on h248a.
H248A_NUMERIC = ["RXDAYSUP", "RXQUANTY", "RXSF23X", "RXMR23X" ]

def clean_h248a(df):
    df = df.copy()
    # 1) Drop diabetic supplies (test strips, lancets). They have no drug name.
    df = df[df["DIABEQUIP"] != 1].copy()

    # 2) Mask sentinels in numeric columns to NaN.
    for c in H248A_NUMERIC:
        if c in df.columns:
            df.loc[df[c].isin(SENTINELS), c] = np.nan

    # 3) RXDAYSUP = 999 is the 'as needed' flag. Only 8 rows. Mask to NaN
    #    rather than treating as a real days-supply.
    df.loc[df["RXDAYSUP"] == 999, "RXDAYSUP"] = np.nan

    # 4) Drop rows where RXDRGNAM starts with '-' (cannot be computed).
    df = df[~df["RXDRGNAM"].astype(str).str.startswith("-")].copy()

    # 5) Sanity assertions.
    assert (df["RXDAYSUP"].dropna() > 0).all(), "negative or zero RXDAYSUP survived cleaning"
    assert (df["DIABEQUIP"] != 1).all(), "DIABEQUIP rows survived cleaning"
    assert not df["RXDRGNAM"].astype(str).str.startswith("-").any()
    return df

# h251 sanitization is the same idea: mask SENTINELS to NaN on every numeric
# column you intend to use. Do NOT impute. Carry the NaN through and decide
# at use-site (e.g., drop for the MPR numerator, keep with an indicator flag
# for feature engineering).
