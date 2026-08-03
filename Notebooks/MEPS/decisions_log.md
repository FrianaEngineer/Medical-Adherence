# Decisions Log

Auto-appended by `clean_meps.build()`. Each block records one invocation of the pipeline — rows and patients lost at every stage, columns dropped, and decisions taken.

## 2023 build — 2026-08-03T15:32:46.933364+00:00

**Source dir**: `/Users/friana/Medical Adherence/data/MEPS`
**Final rows**: 16,461    **Final unique patients**: 5,968

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 192,275 | 192,275 | 0 | 11,858 | 11,858 | 0 |
| `filter_RXDAYSUP_1_to_989` | 192,275 | 139,681 | 52,594 | 11,858 | 9,765 | 2,093 |
| `filter_RXBEGYRX_positive` | 139,681 | 114,018 | 25,663 | 9,765 | 9,255 | 510 |
| `merge_chronic_icd_list` | 63,656 | 63,656 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 63,656 | 30,372 | 33,284 | 13,490 | 10,137 | 3,353 |
| `link_rx_to_chronic_conditions` | 114,018 | 84,334 | 29,684 | 9,255 | 6,820 | 2,435 |
| `dedup_fills_before_summing_days` | 84,334 | 80,344 | 3,990 | 6,820 | 6,820 | 0 |
| `groupby_person_drug` | 80,344 | 21,718 | 58,626 | 6,820 | 6,820 | 0 |
| `build_patient_drug_condition_bridge` | 84,334 | 22,625 | 61,709 | 6,820 | 6,820 | 0 |
| `filter_drug_chronic_only` | 21,718 | 16,461 | 5,257 | 6,820 | 5,968 | 852 |
| `merge_person_demographics` | 16,461 | 16,461 | 0 | 5,968 | 5,968 | 0 |
| `compute_reference_days` | 18,919 | 18,919 | 0 | 18,919 | 18,919 | 0 |

### Detail per stage

- **load_rx**: file=h248a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **link_rx_to_chronic_conditions**: CLNK EVENTYPE=8, LEFT JOIN rx on (DUPERSID, LINKIDX=EVNTIDX), drop non-chronic. Multi-condition fills expand to N rows here; unwound in the fills dedup below.
- **dedup_fills_before_summing_days**: One row per (DUPERSID, DRUGIDX, RXRECIDX). Removes CLNK multi-condition duplication so RXDAYSUP is not double-counted when a fill links to more than one chronic ICD.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). RXDAYSUP is summed on the deduped fills; ICD10CDX = primary_ICD10CDX kept as first-encountered chronic ICD (aliased so downstream code that reads ICD10CDX keeps working); n_chronic_conditions + chronic_conditions list cover the full linkage set.
- **build_patient_drug_condition_bridge**: One row per (DUPERSID, DRUGIDX, ICD10CDX, CONDIDX). Bridge for condition-level rollups; carries no RXDAYSUP so cannot be summed by accident.
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h251.xlsx on DUPERSID; validated many_to_one to catch dupe person rows; AGE23X -1 → 'unknown'.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2023. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h251.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- CLNK multi-condition duplication fix: RXDAYSUP is summed on deduplicated fills (one row per RXRECIDX per person-drug), not on the rx×condition join. Previous behaviour inflated days when a fill linked to multiple chronic conditions and silently kept only the first ICD. Multi-condition context is preserved via n_chronic_conditions + chronic_conditions list on patient_drug and the patient_drug_condition bridge frame.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h251.xlsx: AGE23X, SEX, RACEV2X, INSCOV23, POVCAT23, FAMINC23, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- AGE23X sentinel -1 replaced with string 'unknown' (MEPS missing/inapplicable age).
- R5/3 nonresponders flagged (PSTATS53 == -1): 137 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.
- Drug-start denominator adjust: for pairs whose earliest fill has RXBEGYRX == this year AND RXBEGMM in 1..12, total_days_supply is clamped to (Dec 31 - first-of-first-month + 1). Sentinel months (-1/-7/-8/-15) and earlier-year starts fall back to the full person-year window. ~31.6 pct of 2023 chronic-drug pairs get a shorter denominator; the remaining ~68 pct keep 365 because the drug is old or the start month is missing.

---

