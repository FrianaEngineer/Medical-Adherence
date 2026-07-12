what do we do with the 27% of rows where we don't know RXDAYSUP. Drop them? Impute to median? Flag and keep? We will choose 'flag and keep' for most uses, and 'drop' only for the MPR numerator calculation.

what is the MPR numerator calculation

We should drop the rows where we dont know the day supply because that would make it difficult to determine the amount of medication they are supposed to take daily. 
---

# Decisions Log

Auto-appended by `clean_meps.build()`. Each block records one invocation of the pipeline — rows and patients lost at every stage, columns dropped, and decisions taken.

## 2020 build — 2026-07-05T14:39:52.889445+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 22,990    **Final unique patients**: 8,305

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 279,755 | 279,755 | 0 | 15,743 | 15,743 | 0 |
| `filter_RXDAYSUP_1_to_989` | 279,755 | 199,663 | 80,092 | 15,743 | 12,908 | 2,835 |
| `filter_RXBEGYRX_positive` | 199,663 | 169,621 | 30,042 | 12,908 | 12,401 | 507 |
| `merge_chronic_icd_list` | 80,802 | 80,802 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 80,802 | 50,846 | 29,956 | 18,569 | 14,819 | 3,750 |
| `filter_CLNK_EVENTYPE_8` | 342,300 | 124,656 | 217,644 | 18,569 | 15,436 | 3,133 |
| `merge_rx_to_chronic_condition` | 184,942 | 145,368 | 39,574 | 12,401 | 10,125 | 2,276 |
| `groupby_person_drug` | 145,368 | 34,965 | 110,403 | 10,125 | 10,125 | 0 |
| `filter_drug_chronic_only` | 34,965 | 22,990 | 11,975 | 10,125 | 8,305 | 1,820 |
| `merge_person_demographics` | 22,990 | 22,990 | 0 | 8,305 | 8,305 | 0 |
| `compute_reference_days` | 27,805 | 27,805 | 0 | 27,805 | 27,805 | 0 |

### Detail per stage

- **load_rx**: file=h220a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person H224.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2020. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from H224.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from H224.xlsx: AGE20X, SEX-adjacent (RACEV2X), INSCOV20, POVCAT20, FAMINC20, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 215 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2021 build — 2026-07-05T14:41:31.254011+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 26,719    **Final unique patients**: 9,002

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 303,394 | 303,394 | 0 | 16,534 | 16,534 | 0 |
| `filter_RXDAYSUP_1_to_989` | 303,394 | 217,158 | 86,236 | 16,534 | 13,476 | 3,058 |
| `filter_RXBEGYRX_positive` | 217,158 | 181,789 | 35,369 | 13,476 | 12,937 | 539 |
| `merge_chronic_icd_list` | 94,641 | 94,641 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 94,641 | 58,513 | 36,128 | 19,896 | 15,937 | 3,959 |
| `filter_CLNK_EVENTYPE_8` | 400,671 | 144,127 | 256,544 | 19,896 | 16,243 | 3,653 |
| `merge_rx_to_chronic_condition` | 195,500 | 154,354 | 41,146 | 12,937 | 10,780 | 2,157 |
| `groupby_person_drug` | 154,354 | 39,627 | 114,727 | 10,780 | 10,780 | 0 |
| `filter_drug_chronic_only` | 39,627 | 26,719 | 12,908 | 10,780 | 9,002 | 1,778 |
| `merge_person_demographics` | 26,719 | 26,719 | 0 | 9,002 | 9,002 | 0 |
| `compute_reference_days` | 28,336 | 28,336 | 0 | 28,336 | 28,336 | 0 |

### Detail per stage

- **load_rx**: file=h229a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h233.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2021. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h233.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h233.xlsx: AGE21X, SEX-adjacent (RACEV2X), INSCOV21, POVCAT21, FAMINC21, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 219 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2022 build — 2026-07-05T14:43:16.034094+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 20,966    **Final unique patients**: 7,222

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 232,605 | 232,605 | 0 | 13,602 | 13,602 | 0 |
| `filter_RXDAYSUP_1_to_989` | 232,605 | 168,162 | 64,443 | 13,602 | 11,128 | 2,474 |
| `filter_RXBEGYRX_positive` | 168,162 | 137,700 | 30,462 | 11,128 | 10,588 | 540 |
| `merge_chronic_icd_list` | 83,173 | 83,173 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 83,173 | 50,526 | 32,647 | 16,375 | 13,278 | 3,097 |
| `filter_CLNK_EVENTYPE_8` | 322,174 | 116,727 | 205,447 | 15,783 | 13,383 | 2,400 |
| `merge_rx_to_chronic_condition` | 147,789 | 116,682 | 31,107 | 10,588 | 8,713 | 1,875 |
| `groupby_person_drug` | 116,682 | 30,919 | 85,763 | 8,713 | 8,713 | 0 |
| `filter_drug_chronic_only` | 30,919 | 20,966 | 9,953 | 8,713 | 7,222 | 1,491 |
| `merge_person_demographics` | 20,966 | 20,966 | 0 | 7,222 | 7,222 | 0 |
| `compute_reference_days` | 22,431 | 22,431 | 0 | 22,431 | 22,431 | 0 |

### Detail per stage

- **load_rx**: file=h239a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h243.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2022. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h243.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h243.xlsx: AGE22X, SEX-adjacent (RACEV2X), INSCOV22, POVCAT22, FAMINC22, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 175 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2023 build — 2026-07-05T14:44:34.364978+00:00

**Source dir**: `/home/mehak/Documents/Personal/Friana/Medical-Adherence/data/MEPS/excels`
**Final rows**: 17,913    **Final unique patients**: 6,268

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 192,275 | 192,275 | 0 | 11,858 | 11,858 | 0 |
| `filter_RXDAYSUP_1_to_989` | 192,275 | 139,681 | 52,594 | 11,858 | 9,765 | 2,093 |
| `filter_RXBEGYRX_positive` | 139,681 | 114,018 | 25,663 | 9,765 | 9,255 | 510 |
| `merge_chronic_icd_list` | 63,656 | 63,656 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 63,656 | 40,620 | 23,036 | 13,490 | 11,178 | 2,312 |
| `filter_CLNK_EVENTYPE_8` | 281,158 | 97,941 | 183,217 | 13,490 | 11,654 | 1,836 |
| `merge_rx_to_chronic_condition` | 122,117 | 96,328 | 25,789 | 9,255 | 7,503 | 1,752 |
| `groupby_person_drug` | 96,328 | 25,662 | 70,666 | 7,503 | 7,503 | 0 |
| `filter_drug_chronic_only` | 25,662 | 17,913 | 7,749 | 7,503 | 6,268 | 1,235 |
| `merge_person_demographics` | 17,913 | 17,913 | 0 | 6,268 | 6,268 | 0 |
| `compute_reference_days` | 18,919 | 18,919 | 0 | 18,919 | 18,919 | 0 |

### Detail per stage

- **load_rx**: file=h248a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h251.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
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
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h251.xlsx: AGE23X, SEX-adjacent (RACEV2X), INSCOV23, POVCAT23, FAMINC23, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 137 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2020 build — 2026-07-05T15:50:33.263482+00:00

**Source dir**: `/Users/friana/Medical Adherence/data/MEPS`
**Final rows**: 21,178    **Final unique patients**: 7,954

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 279,755 | 279,755 | 0 | 15,743 | 15,743 | 0 |
| `filter_RXDAYSUP_1_to_989` | 279,755 | 199,663 | 80,092 | 15,743 | 12,908 | 2,835 |
| `filter_RXBEGYRX_positive` | 199,663 | 169,621 | 30,042 | 12,908 | 12,401 | 507 |
| `merge_chronic_icd_list` | 80,802 | 80,802 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 80,802 | 38,279 | 42,523 | 18,569 | 13,282 | 5,287 |
| `filter_CLNK_EVENTYPE_8` | 342,300 | 124,656 | 217,644 | 18,569 | 15,436 | 3,133 |
| `merge_rx_to_chronic_condition` | 184,942 | 126,152 | 58,790 | 12,401 | 9,205 | 3,196 |
| `groupby_person_drug` | 126,152 | 29,709 | 96,443 | 9,205 | 9,205 | 0 |
| `filter_drug_chronic_only` | 29,709 | 21,178 | 8,531 | 9,205 | 7,954 | 1,251 |
| `merge_person_demographics` | 21,178 | 21,178 | 0 | 7,954 | 7,954 | 0 |
| `compute_reference_days` | 27,805 | 27,805 | 0 | 27,805 | 27,805 | 0 |

### Detail per stage

- **load_rx**: file=h220a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person H224.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2020. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from H224.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from H224.xlsx: AGE20X, SEX-adjacent (RACEV2X), INSCOV20, POVCAT20, FAMINC20, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 215 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2021 build — 2026-07-05T15:55:05.450275+00:00

**Source dir**: `/Users/friana/Medical Adherence/data/MEPS`
**Final rows**: 24,664    **Final unique patients**: 8,631

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 303,394 | 303,394 | 0 | 16,534 | 16,534 | 0 |
| `filter_RXDAYSUP_1_to_989` | 303,394 | 217,158 | 86,236 | 16,534 | 13,476 | 3,058 |
| `filter_RXBEGYRX_positive` | 217,158 | 181,789 | 35,369 | 13,476 | 12,937 | 539 |
| `merge_chronic_icd_list` | 94,641 | 94,641 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 94,641 | 43,748 | 50,893 | 19,896 | 14,375 | 5,521 |
| `filter_CLNK_EVENTYPE_8` | 400,671 | 144,127 | 256,544 | 19,896 | 16,243 | 3,653 |
| `merge_rx_to_chronic_condition` | 195,500 | 133,933 | 61,567 | 12,937 | 9,860 | 3,077 |
| `groupby_person_drug` | 133,933 | 33,585 | 100,348 | 9,860 | 9,860 | 0 |
| `filter_drug_chronic_only` | 33,585 | 24,664 | 8,921 | 9,860 | 8,631 | 1,229 |
| `merge_person_demographics` | 24,664 | 24,664 | 0 | 8,631 | 8,631 | 0 |
| `compute_reference_days` | 28,336 | 28,336 | 0 | 28,336 | 28,336 | 0 |

### Detail per stage

- **load_rx**: file=h229a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h233.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2021. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h233.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h233.xlsx: AGE21X, SEX-adjacent (RACEV2X), INSCOV21, POVCAT21, FAMINC21, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 219 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

## 2022 build — 2026-07-05T15:59:37.700848+00:00

**Source dir**: `/Users/friana/Medical Adherence/data/MEPS`
**Final rows**: 19,325    **Final unique patients**: 6,932

### Pipeline stages

| Stage | Rows in | Rows out | Rows dropped | Patients in | Patients out | Patients dropped |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rx` | 232,605 | 232,605 | 0 | 13,602 | 13,602 | 0 |
| `filter_RXDAYSUP_1_to_989` | 232,605 | 168,162 | 64,443 | 13,602 | 11,128 | 2,474 |
| `filter_RXBEGYRX_positive` | 168,162 | 137,700 | 30,462 | 11,128 | 10,588 | 540 |
| `merge_chronic_icd_list` | 83,173 | 83,173 | 0 | — | — | — |
| `filter_conditions_to_chronic` | 83,173 | 37,234 | 45,939 | 16,375 | 12,058 | 4,317 |
| `filter_CLNK_EVENTYPE_8` | 322,174 | 116,727 | 205,447 | 15,783 | 13,383 | 2,400 |
| `merge_rx_to_chronic_condition` | 147,789 | 100,998 | 46,791 | 10,588 | 7,952 | 2,636 |
| `groupby_person_drug` | 100,998 | 26,069 | 74,929 | 7,952 | 7,952 | 0 |
| `filter_drug_chronic_only` | 26,069 | 19,325 | 6,744 | 7,952 | 6,932 | 1,020 |
| `merge_person_demographics` | 19,325 | 19,325 | 0 | 6,932 | 6,932 | 0 |
| `compute_reference_days` | 22,431 | 22,431 | 0 | 22,431 | 22,431 | 0 |

### Detail per stage

- **load_rx**: file=h239a.xlsx
- **filter_RXDAYSUP_1_to_989**: Excludes RXDAYSUP == 999 (MEPS 'as needed' flag) and negative sentinels (-1, -7, -8, -9, -15).
- **filter_RXBEGYRX_positive**: Excludes rows where the start-year variable is a MEPS sentinel (-1, -7, -8).
- **merge_chronic_icd_list**: Added is_chronic + ICD10CDX_LABEL to h249 rows.
- **filter_conditions_to_chronic**: Keeps only conditions whose ICD-10 3-digit code is in the chronic allowlist.
- **filter_CLNK_EVENTYPE_8**: Keeps only prescription-medicine event links (EVENTYPE 1=office, 2=OP, 3=ER, 4=IP, 7=HH, 8=PMED). Only 8 is relevant for Rx adherence.
- **merge_rx_to_chronic_condition**: LEFT JOIN on (DUPERSID, LINKIDX=EVNTIDX); drops Rx rows whose CLNK link isn't to a chronic condition. This is where non-chronic-condition fills leave the pipeline.
- **groupby_person_drug**: One row per (DUPERSID, DRUGIDX). If a fill was CLNK-linked to multiple chronic ICDs, only the first-encountered ICD is kept (documented attribution choice).
- **filter_drug_chronic_only**: Uses unique_rxname_chronic_labeled_revised.csv. Drops drugs labeled 'flare_up' or 'non_chronic'. Keeps 'chronic'.
- **merge_person_demographics**: LEFT JOIN person h243.xlsx on DUPERSID; validated many_to_one to catch dupe person rows.
- **compute_reference_days**: PSTATS + BEGRF/ENDRF → per-person eligible days in 2022. Clamped to [Jan 1, Dec 31].

### Columns dropped

- **person_year_round_detail**: `PSTATS31, PSTATS42, PSTATS53, BEGRFM31, BEGRFY31, ENDRFM31, ENDRFY31, BEGRFM42, BEGRFY42, ENDRFM42, ENDRFY42, BEGRFM53, BEGRFY53, ENDRFM53, ENDRFY53`

### Decisions taken

- Denominator is PSTATS-derived reference days from h243.xlsx (BEGRF/ENDRF per round). Full-year respondents get 365; deceased and moved-out get windowed.
- PSTATS taxonomy covers 25 documented codes per h251doc Tables 7-8; unknown codes marked 'UNCLASSIFIED' rather than silently included.
- RXDAYSUP == 999 rows dropped: 999 is a flag for 'as-needed' meds, not a day count. Treating it as adherence data would create fake 100% MPR.
- Fills with unknown RXBEGYRX (~28% of raw rows) are excluded because the denominator branch can't route them. Loses coverage for meds with missing history; documented as a limitation.
- Chronic-condition filter uses is_chronic.xlsx (Friana's DRAFT ICD-10 3-digit allowlist). The Data Reference Guide flags this file as needing verification — treat downstream ICD-level counts with care until it's audited.
- Rx→condition join uses (DUPERSID, LINKIDX=EVNTIDX) — never DUPERSID alone (Cartesian explosion) and never LINKIDX alone (round-scoped). Documented in MEPS_SCHEMA_NOTES.
- Multi-condition fills attribute to the first ICD encountered in the join. Alternative: allocate days across linked conditions. Deferred until multi-attribution is a modeling requirement.
- Drug-side chronic filter uses Friana's hand-labeled RXNAME list (1,158 unique drug names, 518 chronic, 373 flare_up, 267 non_chronic). Prevents acute meds (antibiotics, steroid bursts, topical FLUOROURACIL) from contaminating chronic-condition MPR.
- Person-level columns pulled from h243.xlsx: AGE22X, SEX-adjacent (RACEV2X), INSCOV22, POVCAT22, FAMINC22, plus PSTATS/BEGRF/ENDRF for reference-days computation. Guide-spec features (AFRDPM42, DLAYPM42, RACETHX, chronic flags) NOT included here — add at RF-modeling step.
- R5/3 nonresponders flagged (PSTATS53 == -1): 175 persons. Their denominator ends at R4/2 interview date but their numerator can include RXDAYSUP that extends past it — MPR biased HIGH for this group. Kept in the frame; filter downstream if the analysis needs to.
- meps_adherence_ratio caps numerator at min(sum RXDAYSUP, 365, eligible_days). This is PDC-style (100% ceiling), not MPR-style (uncapped). Documented; can be relaxed later if uncapped MPR is wanted.

---

