# Development checkpoints

The experimental inference adapter expects `temperature_best.pt`,
`dewpoint_best.pt`, and `no2_best.pt` under `models/checkpoints/`.
Obtain the files from the ML team; their byte sizes and SHA-256 hashes are pinned
in `checkpoints/manifest.json`. Checkpoint binaries and generated preparation
catalogs are kept out of Git. The local files are not removed by this setup.

These checkpoints supply temperature, dew point, and tropospheric NO2 column
layers. They are experimental and do not replace the surface pollutant inputs
used for population risk. O3 and PM2.5 downscaling checkpoints remain pending.

From `backend/`, install the inference dependencies with
`python -m pip install -e "../ml-work[data]"` in addition to the backend
requirements. The adapter is `scripts.downscale_forecast.write_layers`; the
forecast publisher can invoke it using `--model-dir ../models/checkpoints`.
