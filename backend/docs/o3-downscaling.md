# O3 checkpoint integration

The publisher can generate experimental **surface ozone in ppb**, using the
ML team's `o3_best.pt` and its embedded configuration. Output is hourly on the
checkpoint's 420 × 408, 0.01° EPSG:4326 grid. Checkpoint identities are recorded
in `models/checkpoints/manifest.json`; binaries remain outside Git.

## Run locally

From `backend/`, in the project's Python environment:

```powershell
python -m pip install -r requirements.txt -r requirements/ingestion.txt
python -m pip install -e "../ml-work[data]"

# Reuse a downloaded surface run and add only the missing model predictors.
python -m scripts.fetch_model_forecast --input data/geos_cf/geos_cf_georgia_risk_20261118_09z.nc --model-dir ../models/checkpoints --models o3

# Two-hour inference test, without changing the dashboard publication.
python -m scripts.downscale_forecast --input data/geos_cf/geos_cf_georgia_models_20261118_09z.nc --model-dir ../models/checkpoints --models o3 --hours 2 --output data/dashboard/o3-smoke.nc

# Download the newest usable surface run, enrich it, run all 120 hours,
# then publish. Existing RISK_REFERENCE_PATH / RISK_REFERENCE_OBJECT settings
# continue to control risk generation.
python -m scripts.publish_dashboard_forecast --model-dir ../models/checkpoints --models o3
```

Substitute the actual run date in reuse commands. `--input PATH` on the publisher
reuses an existing run; it must still contain current or future forecast hours.
Omit `--models` to run all supported checkpoints present in the model directory.
No PM2.5 checkpoint is required for O3 inference.

## Inference contract

- Inputs, in order: surface `o3` (mol/mol), `air_temperature` (K),
  `specific_humidity` (kg/kg), `surface_pressure` (Pa), `pbl_height` (m).
- Use the training preprocessing shared with `ml-work`: coarse-grid regridding,
  checkpoint input means/standard deviations, ppb baseline, target scaler,
  and residual U-Net. No label files or observation tables are needed at runtime.
- For this checkpoint, time features use the hourly interval's **start**;
  the NetCDF time coordinate retains NASA's center timestamp.
- Model selection, target, units, scaler provenance, grid coverage, and finite
  inputs/outputs are checked. Negative O3 is missing, rather than clamped to zero;
  an hour with no usable output cells fails publication.
- All weather inputs must match the surface run's 120 timestamps and exact grid.
  A moving NASA alias is accepted only after checking every downloaded batch.
  Missing or mismatched runs fail without advancing the current publication.
- The output contains `surface_o3_ppb` and checkpoint hash, training signature,
  predictor order, epoch, timing policy, and experimental status.

## Google Cloud

Run this in the forecast **Cloud Run Job**, rather than the request-serving API.
Its image must include PyTorch and the local `ml-work[data]` package in addition
to ingestion dependencies; the standard backend Dockerfile does not include ML.
Provision versioned checkpoints into the directory passed to `--model-dir`,
checking file hashes against the manifest. Do not put checkpoint binaries in Git.
Use writable ephemeral storage for temporary downloads and generated files;
`--model-threads` defaults to two CPU threads.

With `FORECAST_GCS_BUCKET` set, the existing publisher uploads the enriched raw
NASA input and `published/<publication-id>/downscaled.nc`, followed by the
current pointer only after all required artifacts succeed. Existing GCS IAM
and application-default credentials apply. No new API service is required.

## Fine-grid risk activation

With O3 selected and a matching fixed reference, the publisher now calculates
individual 0.01° exposures and scores. It prepares the same inputs for historical
analysis, then builds forecast and historical risk artifacts in spatial strips.
PM2.5, surface NO2, and SO2 use the value of the native cell containing each fine
cell center. O3 uses the learned surface-ppb output. Temperature, dew point, and
tropospheric NO2 column are published as supporting layers when their checkpoints
are selected; the four-pollutant risk formula does not consume those quantities.
Column NO2 must never be substituted directly for surface NO2.

Initial setup, from `backend/` (substitute current dates):

```powershell
python -m scripts.fetch_model_analysis --start 2026-11-09T03:00Z --end 2026-11-18T09:00Z
python -m scripts.prepare_fine_risk --input data/geos_cf/geos_cf_analysis_georgia_20261109-20261118_risk_models.nc --model-dir ../models/checkpoints --reference-output data/risk/reference-fine-o3-v1.npz --provisional
python -m alembic upgrade head
python -m scripts.publish_dashboard_forecast --model-dir ../models/checkpoints --reference data/risk/reference-fine-o3-v1.npz --history data/geos_cf/geos_cf_analysis_georgia_20261109-20261118_risk_models.nc
```

Keep this reference fixed between scheduled runs. Set `DOWNSCALE_MODEL_DIR` and
`RISK_REFERENCE_PATH` locally to make model-driven publishing the default.
Checkpoint changes require a new calibration and historical preprocessing;
the checkpoint hash is part of the risk pipeline version. Native artifacts
remain readable, but cannot be combined with a fine-grid reference.
Missing inputs and negative model concentrations remain missing and propagate
through the required exposure windows. Calibration/training use analysis only.

Migration 0005 preserves immutable exposure snapshots for each log revision and
pipeline version. Learning replays reports using matching historical exposures
and the current reference, retaining prior snapshots for audit.

The checkpoint was trained on 2023 analysis and declares
`forecast_skill_validated=false`. Fine cells provide model-estimated spatial
variation, not verified 1 km forecast accuracy. Replicated pollutants retain
their native spatial information. Compare held-out forecasts against stations
and the native NASA baseline before claiming improved accuracy.

## Cloud job image and resource use

Build from the repository root:

```powershell
docker build -f backend/Dockerfile.ml -t YOUR_IMAGE .
```

Upload the manifest-pinned checkpoint files to a versioned prefix in the private
forecast bucket. Configure `FORECAST_GCS_BUCKET`, `MODEL_GCS_PREFIX` (for example
`models/checkpoints/release-1`), and `RISK_REFERENCE_OBJECT`. The ML image defaults
to `--model-dir data/models`; checkpoints are downloaded and checked against the
packaged manifest before inference. Upload the calibrated reference under an
immutable `reference/<SHA256>.npz` object and use that key.

Start with 4 vCPU / 8 GiB for the publisher job and `--model-threads 4`; allow
an hour for the first run and measure actual usage. Fine-grid API serving also
needs more memory than native serving: start at 8 GiB and profile concurrency.
Cloud Run's writable filesystem counts against instance memory. Spatial-strip
processing and memory maps bound working arrays, but still require capacity for
scratch files ([Cloud Run runtime contract](https://docs.cloud.google.com/run/docs/container-contract#file_system)).
Configure the [job task timeout](https://docs.cloud.google.com/run/docs/configuring/task-timeout)
explicitly; its default ten minutes can be too short for initial preprocessing.
API raster caches retain no artifact objects; decoded array and
download caches are bounded. Fine-history sync selects the two newest matching
pipeline archives; each publication covers six prior report days plus warmup.
Older completed reports use their persisted versioned exposure snapshots.

Immutable inference caches avoid repeating unchanged checkpoint/source runs
within one job instance. They are ephemeral on Cloud Run. No inference happens
in dashboard or map request handlers; both read the same published fine artifact.
