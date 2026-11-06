# Asthma Attack Early-Warning System: Why a Smartwatch and Cough Monitoring

## Overview

Our system predicts asthma attack risk with two models. An **environmental model** estimates exposure risk from air quality, pollen, and weather. A **personalized model** estimates how the individual user's body is responding. The environmental model tells us what a person is being exposed to, but not how they are reacting to it. Two people breathing the same air can have very different outcomes. To close that gap, the personalized model needs continuous, objective data from the user's own body, collected without extra effort on their part. A smartwatch and passive nighttime cough monitoring provide exactly that.

## Why a smartwatch

Most asthma attacks are not sudden. A large study of 425 severe exacerbations (Tattersfield et al., 1999) showed that symptoms and lung function typically worsen gradually over several days before an attack peaks. That buildup window is what makes early warning possible, but only if something is measuring the user every day.

Traditional monitoring (peak flow diaries, symptom logs) depends on the patient remembering to do it, and adherence drops quickly, especially when people feel well. A smartwatch collects data passively, 24/7, and is something many users already wear.

The watch captures the physiological signals most linked to worsening asthma:

- **Resting heart rate.** A 2024 systematic review of digital markers of asthma exacerbations identified heart rate as a promising predictor, noting that it is easy to measure with consumer smartwatches.
- **Heart rate variability (HRV).** A multi-cohort study using everyday wearables (chest strap and smartwatch) found HRV features differed significantly between healthy controls and patients with asthma and COPD.
- **Sleep disruption, nighttime respiratory rate, activity levels, SpO2, and skin temperature.** Together these reflect nighttime symptoms, reduced exercise tolerance, and possible infection, which are all recognized signs of poor asthma control.

The most important advantage is **personalization**. Because the watch collects data every day, the model can learn each user's personal baseline and flag deviations from it (for example, resting heart rate drifting upward over three nights), rather than relying on population averages. This is what turns a general risk score into a personal early warning.

## Why nocturnal cough

Cough is one of the few signals that comes directly from the respiratory system and can be captured passively with an ordinary smartphone microphone. Nighttime cough is especially valuable because nocturnal symptoms are a core indicator of uncontrolled asthma, and nighttime recordings avoid much of the noise and ambiguity of daytime coughing.

The evidence is encouraging:

- In a Swiss study of 79 adults with asthma, smartphone-recorded nocturnal cough and sleep quality were significantly associated with asthma control, both between patients and within the same patient over time (Tinschert et al., 2020).
- The same research group showed that asthmatic nighttime coughs can be automatically detected in real-world smartphone audio recordings (Barata et al., 2020).
- A 2024 systematic review of acoustic biomarkers in asthma reported that nocturnal cough monitoring predicted exacerbations with roughly 70–75% accuracy.
- The digital markers review above also named cough as a potential marker of asthma exacerbations alongside heart rate and rescue inhaler use.

Recent tools make this practical for a student project. Google's open HeAR model was trained on over 300 million health-related audio clips (including coughs, breathing, and throat clearing), so a small labeled dataset is enough to train an accurate cough classifier on top of it.

## How the pieces fit together

The smartwatch and cough monitor complement each other. The watch reveals the body's systemic stress response (heart rate, HRV, sleep), while cough reflects airway irritation directly. They also cross-check each other: the watch's accelerometer can confirm that a cough detected by the phone actually came from the user, not a partner or the TV.

Combined with the environmental model, the system covers three layers:

| Layer | Source | Tells us |
|---|---|---|
| Exposure | Environmental model | What the user is breathing, now and in the forecast |
| Body response | Smartwatch | Whether the user's physiology is drifting from baseline |
| Airway symptoms | Nocturnal cough | Whether the airways are becoming irritated |

Published models using home monitoring data typically achieve their best results predicting attacks 1 to 5 days ahead, which matches the gradual buildup these sensors are designed to detect.

---

## Key research papers

### Wearables and physiological markers

1. **Digital markers of asthma exacerbations: a systematic review.** *ERJ Open Research*, 2024; 10(6): 00014-2024. Reviewed 23 studies. Heart rate, rescue inhaler (SABA) use, and potentially cough are the most promising digital markers. *Core justification for the smartwatch.*
2. **Rahman MJ et al. Automated assessment of pulmonary patients using heart rate variability from everyday wearables.** *Smart Health*, 2019/2020. HRV from a chest band and smartwatch in 131 subjects, including 69 with asthma. *Supports HRV as a feature.*
3. **Kruizinga MD et al. Clinical validation of digital biomarkers for paediatric patients with asthma and cystic fibrosis.** *European Respiratory Journal*, 2022; 59(6): 2100208. Smartwatch-derived activity and heart rate as candidate clinical endpoints in pediatric asthma.
4. **Tattersfield AE et al. Exacerbations of asthma: a descriptive study of 425 severe exacerbations (FACET).** *Am J Respir Crit Care Med*, 1999; 160(2): 594–599. Shows attacks build over several days. *Justifies why early prediction is possible.*

### Nocturnal cough

5. **Tinschert P et al. Nocturnal cough and sleep quality to assess asthma control and predict attacks.** *Journal of Asthma and Allergy*, 2020; 13: 669–678. doi:10.2147/JAA.S278155. *Main evidence for cough in this project.*
6. **Rassouli F et al. Characteristics of asthma-related nocturnal cough: a potential new digital biomarker.** *Journal of Asthma and Allergy*, 2020; 13: 649–657. doi:10.2147/JAA.S278119.
7. **Barata F et al. Automatic recognition, segmentation, and sex assignment of nocturnal asthmatic coughs and cough epochs in smartphone audio recordings.** *J Med Internet Res*, 2020; 22(7): e18082. doi:10.2196/18082. *Closest technical precedent for our cough pipeline.*
8. **Acoustic biomarkers in asthma: a systematic review.** *Journal of Asthma*, 2024. doi:10.1080/02770903.2024.2344156.
9. **Baur S et al. HeAR: Health Acoustic Representations.** arXiv:2403.02522, 2024. Foundation model for cough and breath sounds.

### Prediction models and horizons

10. **Tsang KCH et al. Home monitoring with connected mobile devices for asthma attack prediction with machine learning (AAMOS-00).** *Scientific Data*, 2023; 10: 370. doi:10.1038/s41597-023-02241-9. Public dataset combining smartwatch, smart inhaler, peak flow, and environmental data. *Most directly comparable to our system.*
11. **de Hond AAH et al. Machine learning did not beat logistic regression in time series prediction for severe asthma exacerbations.** *Scientific Reports*, 2022. doi:10.1038/s41598-022-24909-9. Highlights the challenge of rare events and false alarms.
12. **Finkelstein J, Jeong IC. Machine learning approaches to personalize early prediction of asthma exacerbations.** *Annals of the New York Academy of Sciences*, 2017; 1387(1): 153–165. Predicts next-day exacerbations from a 7-day monitoring window.
13. **Tsang KCH et al. Application of machine learning algorithms for asthma management with mHealth: a clinical review.** *Journal of Asthma and Allergy*, 2022; 15: 855–873.
14. **Machine learning approaches for asthma exacerbation predictions: a systematic review.** *Artificial Intelligence Review*, 2026. doi:10.1007/s10462-026-11536-3. Discusses the trade-off between short and long prediction windows.

### Datasets and tools

- **AAMOS-00 dataset** (Edinburgh DataShare, CC BY 4.0): https://datashare.ed.ac.uk/handle/10283/4761
- **Asthma Mobile Health Study** (Chan YY et al., *Nature Biotechnology*, 2017; 35: 354–362): large self-reported symptom dataset
- **COUGHVID** (Orlandic L et al., *Scientific Data*, 2021) and **Coswara** (Bhattacharya D et al., *Scientific Data*, 2023): cough audio for training the detector
- **ESC-50** (Piczak, 2015) and **AudioSet** (Gemmeke et al., 2017): non-cough "hard negative" sounds such as snoring and sneezing
- **HeAR model**: https://huggingface.co/google/hear
- **Samsung Health Sensor SDK**: raw PPG, accelerometer, SpO2, and skin temperature from Galaxy Watch
