# Prior-year lag analysis — short note

## (1) Who dropped out from 28,691 → 9,919 rows, and why?

The full panel is one row per `(DUPERSID, YEAR)` for 2020–2023. The lagged frame keeps only rows that can be joined to a **previous calendar year already in the panel** (`prior["YEAR"] += 1`, `how="inner"`). That drops:

- **All 7,718 rows from 2020** — there is no 2019 row in this extract to supply priors.
- **Every single-year patient** (~9,826 people / rows) — they never have a prior year in-panel.
- **Additional multi-year rows with gaps** (e.g. present in 2020 and 2022 but not 2021), which cannot form a consecutive `Y → Y+1` pair.

What remains are **9,919 consecutive-year person–years** (8,748 people): target years 2021 (4,753), 2022 (3,174), and 2023 (1,992).

## (2) Could Step M put the same person in train and test? How to check?

**Yes, previously.** Step M used `train_test_split` on patient–**year** rows, so the same `DUPERSID` could land in train for one year and test for another. Check with:

```python
len(set(train["DUPERSID"]) & set(test["DUPERSID"]))
```

Step M now uses a **year split** (train 2021–2022, test 2023) and prints that overlap. On the **full** 28,691-row panel that overlap is large (~1,992 people). On the **lagged** 9,919-row frame used in Steps P–Q, the same year split yields **0 overlapping DUPERSID** (train 6,756 + test 1,992 = 8,748), so there is no person leakage for the prior-feature models.

## (3) Which added prior feature helped most?

Baseline 2023 ROC-AUC with **`prior_is_adherent` alone** = **0.6044**. With all four priors (`prior_is_adherent`, `prior_ratio`, `prior_n_drugs`, `prior_n_cond`) = **0.6452** (Δ **+0.0408**).

Among the three *added* features, **`prior_ratio`** contributed the most: adding it alone to `prior_is_adherent` raised AUC to **0.6408** (Δ **+0.036**), versus +0.026 for `prior_n_cond` and +0.020 for `prior_n_drugs`. Leave-one-out from the full set agrees: dropping `prior_ratio` hurts most (AUC falls to 0.625). So the continuous prior adherence ratio carries most of the gain beyond the binary prior label.
