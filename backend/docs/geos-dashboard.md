# GEOS-CF dashboard risk

From the backend directory, install the API and ingestion requirements, then publish
the latest complete Georgia run. Run one publisher at a time.

```powershell
python -m pip install -r requirements.txt -r requirements/ingestion.txt
python -m scripts.publish_dashboard_forecast
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Use `--input PATH` to publish an existing GEOS-CF forecast NetCDF. The job validates
the run and atomically replaces `data/dashboard/current.json`. Set
`DASHBOARD_FORECAST_PATH` in the API environment if using a different path.
A failed download or validation leaves the previous artifact available.

`GET /v1/dashboard/risk?lat=33.75&lon=-84.39` serves public population scores without
accessing Supabase. It returns the issue time, a stale flag for runs older than 24
hours, and four Eastern calendar dates (today and the next three). Each day's
score is the maximum of its usable hourly scores, and each day includes the
number of contributing hours. The current hour is eligible until its interval
ends, and future timestamps are capped at 96 hours ahead. Missing hours are
omitted from aggregation, days without usable hours have null scores, and an
expired run returns 503. Locations outside the downloaded grid return 404.

With a fixed reference configured, the publisher produces four-pollutant risk
artifacts. The dashboard serves general scores publicly and symptom-adjusted
scores through its authenticated personal route. See [personalization.md](personalization.md).
Without a reference, legacy publications use the explicit development placeholder
`min(100, PM2.5 * 2.5)` with PM2.5 in µg/m³; these do not provide map risk layers.

NASA's native grid is 0.25 degrees. Risk-enabled publications use the shared
0.01° model footprint; each target cell inherits a source value until compatible
downscalers are available. This introduces no additional spatial detail. The
legacy PM2.5-only fallback retains its earlier 1,000 m EPSG:26916 grid for
compatibility. The [map](risk-map.md) uses only risk-enabled publications.

For Google Cloud, see the [deployment walkthrough](deploy-forecast-gcs.md).

## Experimental ML output

Install the checkout's ML package in the publisher environment. From `backend/`:

```powershell
python -m pip install -r requirements.txt -r requirements/ingestion.txt
python -m pip install -e ../ml-work
python -m scripts.publish_dashboard_forecast --model-dir ../models/checkpoints
```

Add `--input PATH` to reuse a current NetCDF run. The publisher loads the
three checkpoints with PyTorch's weights-only loader and uses the same input
preparation as training. It uses the checkpoint's feature order and scaler,
the GEOS-CF valid time in UTC, and the aligned coarse baseline. The resulting
compressed NetCDF contains hourly temperature and dew point in °C and
tropospheric NO₂ column in mol/m². The grid is the checkpoints' native
420 × 408 at 0.01° in EPSG:4326. Each variable records its checkpoint SHA-256,
training data signature, epoch, and input order.

Hourly fields are preserved for later scoring and map queries. A physically
invalid negative NO₂ column remains missing. Missing model inputs or non-finite
inference output fail the optional publication before advancing the dashboard
forecast. The layers are marked experimental because the checkpoints were
trained on analysis and forecast skill has yet to be validated. The dashboard
uses surface pollutant inputs for risk rather than the experimental column layers.

Locally the job prints the generated layer file location alongside the dashboard
artifact. In GCS it uploads `published/<publication-id>/downscaled.nc` before
promoting the current pointer, which records whether the layers exist. The
standard Cloud Run image supports the existing publisher. Enabling ML there
requires a job image with PyTorch and `ml-work` installed, and the versioned
checkpoints accessible at the path passed to `--model-dir`.

The [personalization integration](personalization.md) supplies four-pollutant risk when a reference is configured.
