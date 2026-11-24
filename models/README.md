# Development checkpoints

The experimental inference adapter supports `temperature_best.pt`,
`dewpoint_best.pt`, `no2_best_surface.pt`, and `o3_best.pt` under `models/checkpoints/`.
The legacy `no2_best.pt` column model is supported only when the surface file is absent.
Obtain the files from the ML team; their byte sizes and SHA-256 hashes are pinned
in `checkpoints/manifest.json`. Checkpoint binaries and generated preparation
catalogs are kept out of Git. The local files are not removed by this setup.

These checkpoints supply temperature, dew point, surface NO2 in ppb, and
surface O3 in ppb on a 420 × 408 grid at 0.01°. PM2.5 downscaling is pending.
The O3 checkpoint uses NASA surface ozone, air temperature, specific humidity,
surface pressure, and boundary-layer height. It normalizes those raw predictors
with the embedded training scaler and learns a residual over NASA O3 × 1e9.
Its station labels refer to the start of an hour, so inference subtracts 30
minutes from NASA's hourly center timestamp for the cyclic time features.
Output timestamps still identify the center of the same hourly interval.

The O3/weather models were trained on analysis. The new surface NO2 model was
trained on forecasts and requires actual lead time as a fifth time feature.
Its supplied evaluation covers only the first 24 hours; forecast skill remains unvalidated.
When O3 is selected with a matching calibrated reference, the workflow uses it
for individual 0.01° risk cells and applies the same preprocessing to historical
analysis. PM2.5 and SO2 retain their native values in each fine cell. Surface NO2
uses learned output when selected, requiring a new matching reference and an
explicit experimental historical lead assumption until ML validates that use.
The other checkpoints supply supporting layers. The legacy NO2 column model cannot
replace surface NO2 for risk; temperature and dew point are not inputs to the
current four-pollutant score.

From `backend/`, install the inference dependencies with
`python -m pip install -e "../ml-work[data]"` in addition to the backend
requirements. The adapter is `scripts.downscale_forecast.write_layers`; the
forecast publisher can invoke it using `--model-dir ../models/checkpoints`.
Add `--models o3` to use only ozone; otherwise available supported checkpoints
are selected. Missing weather predictors are fetched from the same NASA run,
with all 120 timestamps and spatial coordinates checked before merging.

See [the O3 integration guide](../backend/docs/o3-downscaling.md) for smoke tests,
publication commands, and Cloud Run requirements.
See [surface NO2 integration](../backend/docs/no2-surface-downscaling.md) for its
forecast lead contract, negative-cell findings, historical gating and activation steps.
