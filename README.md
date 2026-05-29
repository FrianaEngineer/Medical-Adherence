## **Medication Adherence Prediction: Problem Statement**

### **Project Overview**

This project develops a probabilistic machine learning model to predict 90-day medication non-adherence among Medicare beneficiaries using CMS claims data. The model outputs the probability that an individual patient will fail to maintain adequate medication coverage over the next 90 days, enabling targeted clinical interventions before adherence breaks down. The methodology is designed to be reproducible and transferable to other populations, allowing researchers to apply the same analytical framework to commercial insurance data, Medicaid populations, or international healthcare systems.

### **Stakeholders and Use Cases**

**Physicians** will use the model's probability outputs to inform prescribing and follow-up decisions at the point of care. When a patient presents with a 75% probability of non-adherence, the physician can proactively discuss barriers, simplify the regimen, switch to a longer-acting formulation, or schedule earlier follow-up rather than discovering non-adherence months later through clinical deterioration or lab abnormalities. The model supports the decision of *how aggressively to intervene*, not whether to prescribe.

**Pharmacists** will use risk stratification to prioritize medication therapy management (MTM) resources. Rather than calling every patient for refill reminders, pharmacists can focus counseling, synchronization programs, and adherence packaging on the highest-risk patients identified by the model. The decision the model supports is *which patients receive limited intervention resources*.

**Researchers** will use the model in two distinct ways. First, the methodology itself is reusable—the feature engineering pipeline, the operational definition of non-adherence, and the evaluation framework can be applied to different populations and medication classes to study adherence patterns broadly. Second, researchers studying *why* adherence varies between patients on the same medication will need to extend this work with interpretable models (such as SHAP-based feature attribution) and causal inference techniques, because the predictive model described here identifies risk but does not establish causation. This distinction matters: predicting *who* will miss medications is a different methodological problem than explaining *why* they miss them.

### **Data Source**

The analysis uses the CMS Data Entrepreneurs' Synthetic Public Use File (DE-SynPUF), specifically requiring two linked files: the Beneficiary Summary File (containing demographics, chronic condition indicators, and coverage information) and the Prescription Drug Event (PDE) file (containing individual prescription fill dates, days supply, quantity dispensed, and drug identifiers). These files are linked using the DESYNPUF\_ID. The Beneficiary Summary File alone is insufficient for adherence measurement because it contains no individual prescription transactions—only aggregate annual reimbursement totals.

### **Operational Definitions**

**Non-adherence** is defined as Proportion of Days Covered (PDC) below 80% over the 90-day prediction window. PDC is calculated as the number of days the patient has medication available divided by 90 days, multiplied by 100\. The 80% threshold is the CMS-established standard for chronic medications and aligns with how Medicare Star Ratings measure adherence, ensuring our model's outputs are interpretable to healthcare stakeholders.

**Prediction window** is 90 days forward from the index date. **Lookback window** is 180 days prior to the index date, providing sufficient history to establish baseline refill patterns while limiting the impact of stale behavioral signals.

**Medication classes studied** will be limited to chronic disease medications where adherence is clinically meaningful and CMS measures it for quality reporting: oral antidiabetics, statins, and renin-angiotensin system antagonists (ACE inhibitors and ARBs). These classes have established adherence thresholds, daily dosing patterns that make PDC calculation straightforward, and clear clinical consequences for non-adherence.

### **Constraints and Limitations**

**Sample representativeness:** The DE-SynPUF is a 5% sample of Medicare beneficiaries, which is sufficient for model development but means population-level prevalence estimates from this analysis should not be generalized to the full Medicare population without weighting.

**Synthetic data:** The DE-SynPUF is synthetically generated to protect beneficiary privacy. Statistical relationships in synthetic data may not perfectly mirror real-world claims data, so the validation in this project applies to the *methodology* rather than to specific predictions. Before clinical deployment, the model must be retrained and revalidated on real claims data.

**Population scope:** Medicare beneficiaries include adults 65 and older, individuals under 65 with qualifying disabilities, and patients with End-Stage Renal Disease (ESRD). The model's performance may differ across these subpopulations, and stratified evaluation will be reported. Findings should not be extrapolated to commercially insured or uninsured populations without revalidation.

**Claims data limitations:** Pharmacy claims capture prescription fills but not actual medication ingestion. A patient who fills a prescription but does not take it will be counted as adherent by PDC. Cash-pay transactions outside Medicare are invisible to claims data. The model cannot distinguish between intentional discontinuation (e.g., patient stopped due to side effects) and unintentional non-adherence (e.g., patient forgot or could not afford). These distinctions matter clinically but require additional data sources beyond claims.

**Polypharmacy as a predictor, not a confounder:** Patients on multiple medications have higher non-adherence risk, and the number of concurrent medications will be included as a predictor variable. True confounders—variables that affect both medication count and adherence—include depression, cognitive impairment, and socioeconomic status, and these will be addressed through feature inclusion and stratified analysis where data permits.

**Outcome unknown:** This dataset contains no linked clinical outcomes (hospitalizations attributable to non-adherence, disease progression, mortality from medication discontinuation), so the model predicts the *behavior* of non-adherence rather than its clinical consequences.

### **Modeling Approach**

A probabilistic classification model will be developed that outputs P(non-adherent in next 90 days) for each patient. Probability outputs are preferred over hard binary classifications because clinical decision support benefits from nuance: a patient at 85% probability warrants more aggressive intervention than one at 55%, even though both might be classified as "non-adherent" under a binary threshold. Binary classifications can be derived from probabilities by applying a decision threshold, with the threshold chosen based on the clinical context and the relative costs of false positives versus false negatives.

A separate forecasting model will predict the date of the next prescription refill, framed as a regression or time-to-event problem. This is methodologically distinct from the classification task and will be evaluated using different metrics.

Candidate models include logistic regression (as an interpretable baseline), gradient boosted trees (XGBoost or LightGBM, for performance), and calibrated versions of both. Model selection will be based on validation set performance with calibration as a primary consideration, since probability outputs are only useful if they are well-calibrated to actual non-adherence rates.

### **Baseline for Comparison**

The model must outperform a simple rule-based baseline: "predict non-adherence if the patient had any refill gap exceeding 7 days in the 90 days prior to the index date." A model that does not substantially improve on this heuristic does not justify the complexity of machine learning deployment. Expected improvement targets will be set during model development based on baseline performance.

### **Success Metrics**

**For the probabilistic classifier**, performance will be evaluated using metrics appropriate for imbalanced classification rather than accuracy. Accuracy is misleading in this context because if 80% of patients are adherent, a model predicting "everyone adheres" achieves 80% accuracy while being clinically useless.

Primary metrics include the Area Under the Receiver Operating Characteristic curve (AUC-ROC) for overall discriminative ability, precision and recall on the non-adherent class (treating non-adherence as the positive class), F1 score balancing precision and recall, and calibration metrics including the Brier score and reliability diagrams to ensure predicted probabilities reflect actual non-adherence rates.

**Error type definitions and clinical implications:** With non-adherence defined as the positive class, a **false positive** occurs when the model predicts non-adherence but the patient actually adheres (PDC ≥ 80%). The clinical consequence is unnecessary intervention—wasted pharmacist time, possibly intrusive outreach to a patient who needs none. A **false negative** occurs when the model predicts adherence but the patient actually misses medications (PDC \< 80%). The clinical consequence is a missed opportunity to intervene with a patient who is silently failing their treatment, potentially leading to disease progression, hospitalization, or worse clinical outcomes.

False negatives are the more costly error in this application. Missing a non-adherent patient means the underlying condition (diabetes, hypertension, hyperlipidemia) goes inadequately treated, with consequences that compound over time. An unnecessary intervention call from a pharmacist is a minor cost. Consequently, the classification threshold will be chosen to favor recall on the non-adherent class, accepting more false positives to minimize false negatives. The target is recall ≥ 0.75 on the non-adherent class while maintaining precision ≥ 0.50, with threshold-tuning curves reported across the full precision-recall trade-off.

**For the refill forecasting model**, success metrics are different because this is a regression problem rather than classification. Primary metrics include Mean Absolute Error (MAE) in days between predicted and actual refill dates, Root Mean Squared Error (RMSE) which penalizes larger errors more heavily, and the percentage of predictions within a clinically acceptable tolerance window. The tolerance window will be set at ±3 days for medications with 30-day fills (10% of the supply duration), with stretch goals of ±1 day for highest-adherence patients. Predictions further than 14 days off will be considered model failures requiring investigation.

**Stratified evaluation** will be reported across age groups (under 65, 65-74, 75-84, 85+), sex, race/ethnicity categories present in the data, chronic condition burden, ESRD status, and medication class. A model that performs well overall but poorly for specific subpopulations (for example, missing non-adherence in patients with cognitive decline) creates equity concerns that aggregate metrics would hide.

### **Handling Edge Cases**

Patients with insufficient lookback history (fewer than 60 days of enrollment before the index date) will be excluded from the training set and flagged as ineligible for prediction in deployment, since the model cannot make reliable predictions without sufficient behavioral history. Patients who die during the prediction window will be censored rather than counted as non-adherent, since death and non-adherence are distinct outcomes. Patients who switch medications within a therapeutic class will be tracked as continuous therapy if the new medication serves the same clinical purpose. Patients who discontinue medication on physician advice (where this can be inferred from claims patterns) represent intentional rather than problematic non-adherence and will be analyzed separately when possible.

### **Deliverables**

The project will produce a trained probabilistic classification model with documented performance characteristics, a refill forecasting model with corresponding evaluation, a reproducible analysis pipeline that researchers can apply to other populations, a methodology document describing operational definitions and feature engineering decisions, and a limitations document clearly stating what the model can and cannot do for clinical and research users.

Project Identity

Doctors, \- medical, pharmacies, – you can use it to check up on the patients, deciding on what to prescribe patients

Researchers \- gather data from doctors to see why the same medication has higher non adherence from one person to another, come to conclusions based on different side effects

Researchers should be able to use my methodology to study a different population set. 

Constraint \- Only 5% sample \- cannot represent the full population, age-limit 65 and older, people taking multiple medications \- might miss medication (influences predictor and outcome)  
Synthetic data \- not generalizable to the whole population

claims data shows fills but not actual ingestion (patients can fill prescriptions and not take them) — u cant really know if they didn't , and if they hadn’t taken the medication they would not have come for a refill , we need further investigate if the data has prescription dosage, the medication bottle pills , (1 pill a day and for 60 days and the bottle has 30 pills \- they need a refill after 30 days , not 40 , not 50 and definitely 60\) 

 

* Binary \- is this person likely to miss a medication in the next 90 days ( yes or no)   
* Probabilistic \- what are the chances that this person will miss his/her medication in the next 90 days (ratio/ percentage – something like 80% or 20% )   
* Forecast – based on previous behavior when will this person refill his medications 

What does success look like – your model success 

* Binary – accuracy on yes or no (90% accurate or 99% accurate)   
* Probabilistic – on the percentage , false \+ve/  false \-ve (threshold based)   
  * False \+ve \- adherences predicted but does not adhere   
  * False \-ve – adheres but predicted not to   
* Forecasting – person A refilled in next 3 days 

Next 90 days based on past 90 days, prior year, then all 3 years 