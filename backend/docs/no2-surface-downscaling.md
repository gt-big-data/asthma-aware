# Surface NO2 checkpoint integration

`models/checkpoints/no2_best_surface.pt` predicts `surface_no2_ppb` on the shared
420 × 408, north-up 0.01° grid. Its pinned identity is in the checkpoint manifest.
The weights are distributed separately and remain ignored by Git.

The `no2` model selection prefers this file to the older `no2_best.pt` column
checkpoint. Column output is never used as surface NO2. The adapter uses the
checkpoint's own predictor order, normalization, residual baseline and weights.
Predictors are NASA surface NO2 (mol/mol), temperature, specific humidity,
surface pressure and boundary-layer height; the baseline is NO2 × 1e9 (ppb).

This checkpoint was trained on forecasts. It requires five time features:
four cyclic features for the hourly interval's start, plus the **actual forecast
lead in hours divided by 72**. NASA timestamps remain interval midpoints.
Splitting a run into batches must not reset initialization or lead times.
The inference code also retains support for the older four-feature checkpoints.

## Validation limits

The supplied epoch-13 metrics report 6.44 ppb validation RMSE over 836 station
observations in the 0–24h bucket. No 24–48h or 48–72h observations are reported;
the checkpoint's `forecast_skill_validated` flag is false. No baseline-relative
skill or independent monitor evaluation was supplied. Running later hours is
experimental; beyond 72h even the lead feature exceeds the training pipeline's
defined range. Successful inference is not evidence of forecast skill.

This is a spatial downscaler, not an autoregressive forecast model. Its output
cannot replace NASA's next-day meteorology and chemistry inputs. Resetting the
lead for each 24-hour batch would misrepresent those forecasts to the model.

On five hours of the cached November 18 09Z NASA run, about 4.6–8.0% of predicted
cells were negative and were masked as missing. Missing cells propagate through
exposure windows; they are not silently clamped to zero or filled from native NO2.
The ML team should assess these negatives as well as longer lead times.

## Local inference

Run from `backend/`, using the project's environment with `ml-work[data]` installed:

```powershell
python -m scripts.downscale_forecast --input PATH_TO_ENRICHED_NASA_FORECAST.nc --model-dir ../models/checkpoints --models no2 --hours 2 --output data/no2-smoke.nc
```

The same publisher accepts `--models o3 no2`. The physical NO2 map consumes the
learned ppb field. Fine risk uses it for both general and personalized scores,
provided a matching historical reference and history artifact are available.
The learned NO2 map uses a fixed 0–20 ppb color scale across hours, avoiding the
old native-field 10 ppb cap hiding variation around Atlanta. This is a display
scale, not an AQI or a change to concentration values or risk calculations.

## Historical calibration and activation

Historical analysis has no forecast initialization/lead. The adapter therefore
rejects applying this forecast-trained model to analysis by default. Ask ML for
a validated historical application policy or an analysis-compatible checkpoint,
and evaluation through the application's 120-hour NASA horizon.

For an explicitly experimental study only, `--analysis-lead-hours 0.5` can supply
a fixed half-hour lead while using actual historical analysis predictors. This
is a surrogate input assumption, not an actual forecast or a validated method.
Use the same choice in **both** calibration and publication:

```powershell
python -m scripts.prepare_fine_risk --input PATH_TO_ENRICHED_ANALYSIS.nc --model-dir ../models/checkpoints --models o3 no2 --analysis-lead-hours 0.5 --reference-output data/risk/reference-o3-no2-experimental.npz --provisional
python -m scripts.publish_dashboard_forecast --model-dir ../models/checkpoints --models o3 no2 --analysis-lead-hours 0.5 --reference data/risk/reference-o3-no2-experimental.npz
```

Do not reuse an ozone-only reference. Cache and preprocessing identities include
both checkpoint hashes and the historical lead assumption. Reference matching,
personal exposure snapshots and GCS history selection enforce this identity.
An explicit `--models o3` preserves the previous ozone-only pipeline.

## Google Cloud

Upload `no2_best_surface.pt` with the other versioned checkpoint files. The GCS
downloader maps logical source `no2` to this filename and verifies hash/size.
Run inference and calibration in the ML-enabled Cloud Run Job, never during
dashboard/map requests. Publish the new matching reference, risk/history files,
and map fields together before switching the current pointer. No database
migration or frontend layout changes are required.

The integration does not automatically change the running app's publication,
cloud configuration, or reference. Validate the policy and publish deliberately.

## Local app activation

Start the backend from the checkout containing this integration. On Windows,
`./backend/start-local.ps1` from the repository root locates the shared project
virtual environment and supplies this checkout's ML import path. The existing
Expo frontend can continue using `http://127.0.0.1:8000`.
If its saved environment points to Cloud Run, override it for the local web
process without editing that saved configuration. From `Front-end/`:

```powershell
$env:EXPO_PUBLIC_API_URL = 'http://127.0.0.1:8000'
npx expo start --web --port 8081 --clear --max-workers 2
```

Restart Metro after changing this public build-time setting; reloading the
browser alone does not change an already bundled API URL.

Use a local ignored `backend/.env` with `FORECAST_GCS_BUCKET` empty,
`RISK_REFERENCE_PATH=data/risk/reference-o3-no2-experimental.npz`, and
`RISK_ARCHIVE_DIR=data/risk/history`. Leave other checkouts' environment files
unchanged. Run the calibration/publication commands above with the explicit
historical assumption. Multiple analysis files can be supplied to `--history`
to reuse cached history and download only its missing tail. `--trends-input`
can reuse combined raw analysis for environmental trends without another download.

After publication, reload the frontend. The NO2 map metadata should report
`Learned downscaling · experimental`; the risk artifact must use the
`surface-o3-no2-unet-001deg-v1:` pipeline. Both risk maps and the dashboard read
that same versioned exposure artifact. An older backend checkout cannot read
this new pipeline, even if pointed at the new files.
