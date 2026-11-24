# Personalization v1

Turns hourly pollutant grids into (1) a general asthma risk map and (2) a personal risk map per user that learns from daily symptom logs. Implements the v1 plan in *Personalization Model Plan*.

**Currently runs on synthetic pollution data.** Swap in the real upscaled grid when it's ready (see below).

## Quick start

```bash
pip install -r requirements.txt
jupyter notebook demo_v1.ipynb      # full walkthrough with plots
pytest -q tests                     # 10 tests
```

```python
from asthmaaware_v1 import exposure, ReferenceStats, general_risk, PersonalModel

E   = exposure(X)                    # X: [T, 4, 25, 27] hourly O3, NO2, SO2 (ppb), PM2.5 (µg/m³)
ref = ReferenceStats.fit(E)          # once, on the Jan–Jun 2023 cube; ref.save("ref.npz")
g   = general_risk(E, ref)           # g.log_rr, g.rr, g.score (0–100), g.contributions
Z   = ref.standardize(E)

m = PersonalModel(k=3.0)             # new user = general map
m.update(z_bar, log_rr_bar, severity)   # after each daily log (instant, no retraining)
pred = m.predict(Z, g.log_rr)        # pred["score"] 0–100, pred["severity"], pred["sd"]
m.top_factors(Z, E, ref, t, (row, col))
m.still_learning(recent_z)           # drives the "still learning your triggers" banner
state = m.to_dict()                  # ~700 bytes of JSON to store per user
```

## Files

| File | What |
|---|---|
| `asthmaaware_v1/model.py` | The model: exposure step, reference stats, general risk, personal model |
| `asthmaaware_v1/synthetic.py` | Fake pollution grid + simulated users (for dev/demo only) |
| `demo_v1.ipynb` | End-to-end demo with figures |
| `tests/test_v1.py` | Exposure math, incremental = batch ridge, cold start, parameter recovery, etc. |

## How it works

1. **Exposure.** At each hour, over the trailing 24 h: O3 max 8-h mean, NO2/SO2 max 1-h, PM2.5 24-h mean (the statistics the coefficients were measured on). Then a 3-day average. Needs 78 h of history.
2. **General risk.** `log(RR) = Σ β_p (E_p − clean_p)`, β from Strickland et al. 2011 (Atlanta pediatric asthma ER visits); clean = 5th percentile. Score = percentile vs Jan–Jun 2023.
3. **Personal.** `severity = b + k·log(RR) + w·z`, z standardized per pollutant. Bayesian ridge: store a 5×5 precision `P` and 5-vector `h`; each log is a rank-1 update; `θ = P⁻¹h` (identical to refitting on all logs). Score = 25 × severity, clipped to 0–100.

## Demo results (synthetic)

Three simulated users, ~177 daily logs each, integer 0–4 severities:

| User | Planted | Learned |
|---|---|---|
| Typical | w = 0 | all |w| ≤ 0.06 except uncertain SO₂ |
| Ozone-sensitive | O3 +0.80 | O3 +0.79 ± 0.05 |
| Traffic-sensitive | NO2 +0.70, PM2.5 +0.30 | NO2 +0.64 ± 0.07, PM2.5 +0.29 ± 0.06 |

The "still learning" banner turns off after ~25 logs.

## Plugging in real data

Replace `synthetic_grid()` with the pipeline output:
- **Shape** `[T, 4, H, W]`, channel order O3, NO2, SO2, PM2.5.
- **Units must be ground-level** ppb / µg/m³. Satellite column densities or AOD make the coefficients meaningless.
- At least 78 h of history before the first hour you need.

## Open decisions / known limitations

- `k` (severity points per log-RR) and priors (`prior_baseline`, `prior_sd_w`, `sigma2`) are placeholders.
- Coefficients come from warm-season studies; our data window is Jan–Jun.
- Personal weights are learned at the user's location but applied map-wide, so an uncertain weight can create spurious hotspots in places the user never goes (e.g. near an SO₂ source). v2 should shrink uncertain weights where exposure is outside the user's experience.
- Severity is treated as continuous; rounding/clipping at 0 and 4 slightly biases estimates (v2: ordinal model).
