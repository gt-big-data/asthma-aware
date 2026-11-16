# Live NASA forecast integration (development)

> Historical prototype documentation. The scope has changed: do not use the
> scoring/model commands below as the cloud/backend implementation. Current
> commands and real raw/ML-output contracts are in
> [BACKEND_API_DOCUMENTATION.md](../../BACKEND_API_DOCUMENTATION.md),
> [NASA_PIPELINE.md](../../NASA_PIPELINE.md) and
> [GOOGLE_CLOUD_DEPLOYMENT.md](../../GOOGLE_CLOUD_DEPLOYMENT.md).

This is a local development implementation. Live NASA download, shared Supabase
publication and real-user device flows are not verified in this restricted
environment. See the root [verification report](../../VERIFICATION_REPORT.md).
No batch is started by opening the app, importing the API, or calling an endpoint.

## Architecture

```text
Manual bounded publisher
  -> NASA GEOS-CF v2 AQC (.latest discovery, then dated/pinned subset)
  -> PM2.5 / NO2 / O3 NetCDF + metadata
  -> validate, convert units and averaging-window timestamps
  -> compact native-cell JSON
  -> local atomic file OR TLS Postgres forecast.current (one complete snapshot)
  -> per-process validated snapshot cache
  -> authenticated GET /v1/forecast
  -> shared frontend ForecastProvider
  -> Dashboard and Map; selected-hour approximately 1 km display cells
```

The existing `public.forecasts`/model-run tables are the historical ML output
contract. They are not populated with this temporary development score. The new
`forecast.current` singleton stores the bounded native-cell artifact as JSONB.
User profiles and logs stay in the private `app` schema. The synthetic cloud
publisher/`/internal/forecast` stays a separate, explicitly opt-in diagnostic.

## Data and placeholder contract

- NASA URL root: `https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/v2/fcst`.
  Only `aqc_tavg_1hr_glo_L1440x721_slv` is needed for this slice. The existing full
  ingestion command continues supporting its other variables/collections.
- PM2.5 is `pm25_rh35`, in `ug m-3` (NASA concentration at 35% relative humidity).
  NO2 and O3 use dry-air mole fractions: multiply `mol mol-1` by 1e9 to obtain ppb.
  Column-density products are not interchangeable with surface concentrations.
- NASA hourly means are midpoint stamped. A `12:30Z` source represents
  `[12:00Z,13:00Z)` and is exposed as `12:00Z`. Initialization/run ID is retained;
  requests do not shift forecasts to today's date. All contract timestamps carry
  UTC offsets. The current hour must exist; the final hour's end is exclusive.
- A native cell spans 0.25 degrees. It is **not an exact 25 by 25 km square**.
  Divide its footprint evenly into `ceil(height_km)` rows and `ceil(width_km)`
  columns. Near Atlanta this is 28 by 24 = 672 cells, each at most approximately
  1 km along either axis. Pixel values copy their containing native cell exactly.
  No learned spatial detail or interpolation is claimed. The frontend materializes
  only the selected hour; storing every identical pixel/hour would waste memory.
- The score is `round(100 * mean(clip(PM2.5/50), clip(NO2_ppb/100), clip(O3_ppb/100)))`,
  with each clip in `[0,1]`. Denominators are arbitrary engineering scales, not
  AQI breakpoints or validated health thresholds. Missing, negative, nonfinite,
  boolean, or nonnumeric required inputs reject the artifact. Missing values do
  not become zeros. No personalization from symptom logs is applied.
- Contract: `nasa-placeholder-v1`; exact source `NASA GEOS-CF v2`; one daily 09Z run;
  native 0.25-degree centers; identical contiguous hourly axes across cells;
  at most 120 hours, 1,000 native cells and 16 MiB JSON. Reject runs older than 48h,
  future initialization, unsupported coordinates, invalid units and corrupt data.
- Boundary snapping matches the existing ingestion point helper's tie-to-even
  convention. A missing snapped native cell is unsupported, not nearest-neighbor
  extrapolation into neighboring coverage. Georgia support is a rectangle.

## Local use

From `backend/`, with the API/database/ingestion dependencies installed:

```powershell
# Download only three pollutants, one native point, up to 48 future hours.
# Hard deadline is 180s by default (configurable 10..240s).
python -m scripts.publish_nasa_forecast --lat 33.75 --lon -84.39 --hours 48

# Or process existing real ingestion output and its .metadata.json sidecar.
python -m scripts.publish_nasa_forecast --input data/geos_cf/YOUR_FORECAST.nc
```

Set these in ignored `backend/.env`, then start the API from `backend/`:

```dotenv
SUPABASE_URL=https://YOUR_PROJECT.supabase.co
LIVE_FORECAST_STORE=file
LIVE_FORECAST_PATH=data/live-forecast.json
```

File mode is for a single local writer. Replacement is atomic; unchanged JSON is
not rewritten and older runs are rejected. It is not a multi-writer distributed
store. The existing local file is reused without NASA requests if acquired within
an hour and still covering the requested point/horizon. Cache reuse is explicit;
a failed new acquisition never silently manufactures demo values. Corrupt or
insufficient cache entries trigger a bounded fresh acquisition.

The parent process limits the whole batch, including database writes. Individual
NASA requests are limited to 30s, one attempt, with a Windows process-tree kill or
Unix process-group kill on deadline. No automatic scheduler is configured. Old raw
NetCDF files are retained locally; periodic manual cleanup may be needed.

## Supabase setup (not executed here)

Use only a disposable development database first. Review the SQL before applying
`0003_live_forecast`. Migration 0002 is its predecessor. The preflight accepts both
0002 and 0003 when checking an existing application schema.

- `forecast.current`: one boolean-keyed row with `initialized_at`, `payload`,
  `published_at`. Transactional upsert promotes only a strictly newer run.
  Identical retries are no-ops; a different snapshot with the same initialization
  raises a conflict rather than silently replacing spatial coverage. Choose the
  full intended point/region/horizon for the first publication of each run.
- `asthmaaware_api` has SELECT only on the forecast table.
- `asthmaaware_forecast_writer` is NOLOGIN/NOSUPERUSER/NOBYPASSRLS, with only
  SELECT/INSERT/UPDATE on the forecast table; it cannot read private symptom logs.
- Forced RLS protects the new table. Grants/policies on existing symptom tables are
  unchanged. Public/Supabase browser roles receive no access to the new schema.
- Provision a dedicated restricted writer login through a secure operator session;
  use `FORECAST_PUBLISH_DATABASE_URL` only in the manual batch, never the browser.
  The API reads through its existing restricted `DATABASE_URL`. Both forecast
  adapter connections require `sslmode=require`, `verify-ca` or `verify-full`.
  Prefer verification modes and a trusted CA in a production design.
- API connections are short-lived, with 10s connection and statement timeouts.
  One validated snapshot is cached for up to 30s per process, with freshness
  checked on every request. Local atomic replacements invalidate immediately.
- Do not expose `app` or `forecast` through Supabase's browser Data API schemas.
  Keep migration/operator credentials out of application processes.

After a separately reviewed development migration and role provisioning:

```powershell
python -m scripts.publish_nasa_forecast --hours 48 --database
```

Configure `LIVE_FORECAST_STORE=database` for the API. This task did not apply a
migration, provision a login, query Supabase, or alter production policies/data.

## API and frontend

`GET /v1/forecast?lat=33.75&lon=-84.39` requires a verified Supabase user token.
It returns source/run/initialization, `placeholder: true`, score method/notice,
native center, requested point/pixel ID, subpixel grid geometry, units, expiry and
hourly concentrations/scores. It never accepts `user_id` from query parameters.

| Status | Meaning |
| --- | --- |
| 200 | Current complete published forecast at this location |
| 401 | Missing/invalid user authentication |
| 404 | Outside supported/native published coverage |
| 422 | Invalid coordinate parameters |
| 503 | Unconfigured, stale, future-only, corrupt, missing or inaccessible data |

Responses use `Cache-Control: private, no-store` and accurate source headers.
`/health` remains independent of NASA/database availability.

The pre-existing development Cloud Run service has an independent private Google
IAM gate. A Supabase user JWT alone does not satisfy that gate. Use the local API
for this slice; a future deployment needs a separately reviewed ingress/auth design.
No Cloud Run visibility or IAM changes were made for browser access.

Frontend setup uses the existing `.env.example`: Supabase URL/publishable key and
API URL. Only a public publishable key belongs in Expo. Dashboard and Map share
one request/cache per selected point/account. Coordinate changes and unmounts
abort requests; logout clears forecast state. Token lookup and fetch share the
15s request timeout. Expired hours disappear without API polling. Users can
refresh manually. Unsupported/failed forecasts show errors, not sample data.
The location picker permits typed coordinates when external map tiles fail.
Map frames are sandboxed and contain no credentials or symptom records.

Web export is a client-rendered SPA (`web.output=single`). Authenticated content
does not need per-route static HTML. Hosting must rewrite application routes to
`index.html`. The installed Expo server-renderer and pinned router have an API
mismatch; static rendering remains unsupported until those dependencies are
aligned with network access. `npm run build:web` exports locally and does not
publish anything. Native builds/real device interaction have not been verified.

## Deferred work

Full-state multi-cell acquisition in the bounded publisher, regional map serving,
exposure matching/outbox consumption, personal risk weights, calibrated risk/ML,
account deletion cleanup, live JWT/DB smoke tests, a disposable PostgreSQL/PostGIS
environment, device/browser interaction and production deployment are separate
follow-up work. Existing ingestion can generate a multi-cell NetCDF for later
conversion; the current map deliberately displays the selected native cell only.
