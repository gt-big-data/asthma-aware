# General risk and daily-report personalization

The backend integrates personalization-v1 from upstream main 9d74d44. It fixes
rolling-window recovery after missing inputs and uses a shared percentile base
for general and personal display scores.

## Inputs and reference

Risk uses hourly surface O3, NO2 and SO2 in ppb and PM2.5 in µg/m³. NASA gases
are multiplied by 1e9 from mol/mol. Tropospheric NO2 column and the temperature
and dewpoint checkpoints are separate experimental layers. The risk model
requires the four surface pollutants. Their native 0.25° values are inherited
by target cells on the checkpoints' north-up 0.01° grid, covering
30.78–34.98° N and 85.60–81.52° W.

Each target hour uses that hour and 78 preceding hours. The exposure model
computes trailing ozone max-8h, NO2/SO2 max-1h and PM2.5 mean-24h, then averages
the daily metrics at lags 0, 24 and 48 hours. Files are joined by actual UTC
hourly midpoints. Missing hours remain missing and recover once outside the
window. Forecast values supersede history at overlapping forecast times.

A fixed reference contains each feature's mean/std, fifth-percentile clean
baseline, and the combined log-relative-risk distribution. Its SHA-256 is the
normalization version. It is fitted from historical analysis and remains fixed
across forecast runs. Percentile 80 means above roughly 80% of the reference
exposures. It is not an individual probability of an asthma attack.

Set RISK_REFERENCE_PATH in backend/.env to reuse a local reference without
passing --reference on every publication.

The local development reference uses actual November 1–16, 2026 GEOS-CF
analysis and is explicitly provisional. A longer and seasonally representative
reference should be validated before user-facing release. Changing the
reference rebuilds personal state using the same saved raw exposure features.

## Local preparation and publication

Run from backend with the repository's API, database, and ingestion requirements
installed. Preserve the fixed reference file once fitted.

```powershell
python -m scripts.fetch_risk_analysis --start 2026-11-01T00:00Z --end 2026-11-16T09:30Z
python -m scripts.prepare_risk reference --input data/geos_cf/geos_cf_analysis_georgia_20261101-20261116_risk.nc --output data/risk/reference-development.npz --provisional
python -m scripts.publish_dashboard_forecast --reference data/risk/reference-development.npz
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

The publisher fetches a complete surface forecast and up to six days of preceding
analysis plus the 78-hour warmup. This provides both forecast risk and historical
exposure for recent daily reports. Use --input to select an existing current
forecast and --history with one or more analysis NetCDF files to reuse downloads.
Incomplete historical coverage leaves affected risk hours unavailable.
The command stores immutable risk/history artifacts before switching current.json.
The optional --model-dir still produces the experimental checkpoint layers.
When O3 is selected, it activates learned surface ozone on the 0.01° risk grid;
the historical inputs and fixed reference must use that same checkpoint.
See [fine-grid activation](o3-downscaling.md#fine-grid-risk-activation).
Migration 0005 keeps separate immutable exposures for each pipeline version;
learning uses only the pipeline associated with the current forecast.

The compact NPZ stores shared source exposure values and explicit row/column
mapping for the 0.01° grid, reference values, source hashes, versions and times.
Replicated target cells share the same numbers without storing redundant copies.

## API and individual learning

GET /v1/dashboard/risk serves the general forecast. Authenticated
GET /v1/dashboard/personal-risk returns the same response with user adjustments,
matched/pending report counts, model revision and a still-learning flag.
The dashboard selects the personal route when signed in. Its gauge and four-day
tiles use the highest valid hourly score in each local day.

A report saved for today stays provisional until the stored local midnight.
On the next personal dashboard request, or a refresh job, the backend:

1. Locks the user's profile, serializing with report edits.
2. Reads only current finalized log revisions under owner RLS.
3. Matches complete historical-analysis exposures to each local day and location.
   DST days use their actual 23, 24 or 25 hourly values.
4. Stores an immutable exposure snapshot with the source artifact hash.
5. Converts severity 1–5 to 0–4 and fits the personal model from all matched
   active revisions. An edit replaces the old observation, and retries never
   count an observation twice.
6. Stores a new immutable model revision only when training inputs or model/
   reference versions change. Matched or superseded outbox events are completed.
   Reports lacking complete historical data remain pending.

The model retains a personal baseline, four feature sensitivities, and Bayesian
precision/evidence arrays. The shared population formula explains its base
severity. Daily reports update the residual severity.

Display score = clip(general percentile + 25 * confidence * personal severity
adjustment, 0, 100). The adjustment is relative to the default baseline and
weights. Confidence measures posterior variance reduction at the queried
exposure and is multiplied by min(matched_reports / 25, 1). A new user therefore
gets exactly the general score. The blend, priors and 25-report ramp are
development choices requiring evaluation.

GET performs a transactionally consistent lazy refresh. Automatic training at
midnight needs a scheduled refresh execution. A CLI can refresh known user IDs:

```powershell
python -m scripts.refresh_personalization --user-id USER_UUID
```

The CLI uses the runtime database login and sets a separate owner context per
user. It accepts repeated --user-id arguments. It writes no personal state to
GCS. Users' state and history stay in Supabase.

## Cloud setup additions

Migration 0003 grants only INSERT on the two derived-data tables and UPDATE on
the outbox dispatch timestamp fields to the existing owner-scoped runtime role.
Audit revisions, exposure snapshots and stored model revisions remain immutable.

Upload the fixed reference once, using --bucket on prepare_risk reference.
The command prints RISK_REFERENCE_OBJECT. Set that object key and
FORECAST_GCS_BUCKET on the forecast job. Each successful publication uploads
risk.npz plus an analysis-only history artifact before advancing the pointer.
The API downloads and caches immutable artifacts, and synchronizes historical
objects for matching. The fixed reference is embedded in each artifact so all
requests use the normalization that produced that run.

No trained downscaling checkpoint is required by this four-surface-pollutant
risk path. The standard backend image includes the required NumPy/NetCDF
dependencies. Keep both authenticated and public requests subject to the same
freshness, geographic coverage and missing-data rules.
