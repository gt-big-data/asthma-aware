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

The risk downloader discovers the newest air-quality initialization independently
of the unrelated meteorology/column collections. It downloads four pollutants in
24-hour batches. The latest alias is accepted only when its inventory and every
batch exactly match the pinned initialization's 120 expected hourly midpoints;
changing runs, units or grids are rejected. This avoids unreliable large DAP
responses and dated metadata that can lag the live alias. Source URLs and the
batch validation method are recorded in the downloaded metadata sidecar.

`GET /v1/dashboard/risk?lat=33.75&lon=-84.39` serves public population scores without
accessing Supabase. It returns the issue time, a stale flag for runs older than 24
hours, and four Eastern calendar dates (today and the next three). Each day's
score is the maximum of its usable hourly scores, and each day includes the
number of contributing hours. The current hour is eligible until its interval
ends, and future timestamps are capped at 96 hours ahead. Missing hours are
omitted from aggregation, days without usable hours have null scores, and an
expired run returns 503. Locations outside the downloaded grid return 404.

Both dashboard routes also return `hourly`: consecutive available forecast
slots containing `valid_at`, `score`, and `general_score`. A missing score stays
null in its original slot rather than being removed or replaced with zero.
The authenticated route applies the same personal model to each hourly score;
the public route uses population scores. Daily values remain maxima of these
hourly predictions. NASA valid times are hourly midpoints; the frontend labels
the corresponding hour's start in the response's IANA `timezone`.

The dashboard emphasizes an interactive hourly outlook: the next 24 slots or
one of the four calendar days, a default line graph with a Line/Bar toggle on a
fixed 0–100 scale, selected-hour details, and
horizontal scrolling on smaller screens. The gauge shows the current hourly
score, with today's peak separately labeled. The daily overview is retained.
Requests refresh on tab focus and every five minutes. These are additive
response fields: no database migration, new GCS object, or cloud job is needed.

Each risk-enabled hourly slot also returns `drivers` for O3, NO2, SO2 and PM25:
the model exposure and reference exposure (with units), `score_without`, and
signed `impact` in score points. For each pollutant independently, the API
sets that exposure to the fixed reference level and reruns the entire scoring
pipeline, including personal confidence and clipping. `impact` is the original
unrounded score minus that comparison, rounded to one decimal. `score_without`
is the rounded comparison score; it means reference exposure, not zero pollution.
The results are sorted by descending impact. They are marginal model comparisons,
not additive shares or causal estimates. Missing hours and legacy PM-only runs
return an empty driver list.

The selected hour's frontend explanation lists pollutant influences, model
exposure values, and reference values. Values are lagged multi-day metrics, not
instantaneous hourly concentrations. Signed-in users with matched reports also
see the general score plus the applied personal adjustment equal to their score.
The disclosure explains non-additivity, score limits, exposure aggregation and
provisional calibration. Driver calculations only use already-loaded artifacts;
they work through the same local/GCS path with no storage or migration changes.

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
