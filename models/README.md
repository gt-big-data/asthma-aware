# Development checkpoints

The experimental inference adapter supports `temperature_best.pt`,
`dewpoint_best.pt`, `no2_best.pt`, and `o3_best.pt` under `models/checkpoints/`.
Obtain the files from the ML team; their byte sizes and SHA-256 hashes are pinned
in `checkpoints/manifest.json`. Checkpoint binaries and generated preparation
catalogs are kept out of Git. The local files are not removed by this setup.

These checkpoints supply temperature, dew point, tropospheric NO2 column, and
surface O3 in ppb on a 420 × 408 grid at 0.01°. PM2.5 downscaling is pending.
The O3 checkpoint uses NASA surface ozone, air temperature, specific humidity,
surface pressure, and boundary-layer height. It normalizes those raw predictors
with the embedded training scaler and learns a residual over NASA O3 × 1e9.
Its station labels refer to the start of an hour, so inference subtracts 30
minutes from NASA's hourly center timestamp for the cyclic time features.
Output timestamps still identify the center of the same hourly interval.

The models were trained on analysis, and forecast skill is unvalidated.
When O3 is selected with a matching calibrated reference, the workflow uses it
for individual 0.01° risk cells and applies the same preprocessing to historical
analysis. PM2.5, surface NO2 and SO2 retain their native values in each fine cell.
The other checkpoints supply supporting layers. The NO2 column model cannot
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
