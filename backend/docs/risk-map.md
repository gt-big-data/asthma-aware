# NASA population-risk map

The General risk layer uses the dashboard's exposure artifact, general-risk function and fixed
reference distribution. It is public and does not use symptom reports or
individual personalization. Scores are relative 0–100 ranks against the
configured reference, not a probability of an asthma attack.

```mermaid
flowchart LR
  N[NASA surface O3 / NO2 / SO2 / PM2.5] --> J[Publisher + preceding analysis]
  R[Versioned historical reference] --> J
  J --> G[Private GCS: exposures + hourly scores in risk.npz]
  G --> A[Cloud Run FastAPI]
  A --> D[Dashboard: local-day maxima at a location]
  A --> M[Map: hourly risk PNG over base map]
```

## Processing and storage

The publisher prepares lagged exposures and saves hourly population scores as
`general_scores` in the existing compressed `risk.npz`. Activated fine artifacts
hold individual exposures and scores on the 420 × 408 grid. O3 uses learned
surface downscaling; surface NO2, SO2, and PM2.5 currently inherit native cell
values. These inherited inputs can still cause visible 0.25° boundary jumps.
Legacy compact source-grid artifacts remain readable.

GCS publication stays atomic: risk uploads finish before `published/current.json`
advances. No new bucket, table, public storage access or map secret is needed.
PNG responses are derived on demand, cached in memory (24 frames per process),
and not separately persisted in GCS. A cold Cloud Run instance reconstructs
them from the durable NPZ.

Actual bounds: west −85.60°, south 30.78°, east −81.52°, north 34.98°. This is
the existing model footprint, not the full Georgia boundary. Savannah and
areas outside the rectangle have no layer. Expanding coverage requires a
coordinated change to the artifact grid and any ML model footprint.

## API and availability

- `GET /v1/map/forecast`: source run, SHA-256 `run_id`, issue time, stale and
  provisional flags, bounds, fixed colors, and valid hourly frame indices.
- `GET /v1/map/frame?run_id=<sha256>&index=<frame-index>`: PNG data URI, valid
  time and availability. Missing cells are transparent.

Both are public, use normal API CORS, and never read the database. Responses
use `Cache-Control: no-store` to recheck time availability and run identity.
The app caches up to 48 images, capped at 16 MiB of estimated string storage,
by immutable run/index. A run mismatch
returns 409 and triggers a metadata refresh. An out-of-window hour returns 404;
missing or expired publications return 503. There is no synthetic fallback.

Available frames follow the dashboard: the current hourly interval and future
midpoints up to 96 hours ahead, limited by the run's actual end. Runs older
than 24 hours retain their remaining hours with a stale label. All-missing
hours remain explicit and display no layer.

## Frontend

The existing map, Heat/Cells toggle, zoom controls and timeline remain; the
dummy Georgia generator is removed. Each timeline step selects a real hourly
frame, labeled in America/New_York. Returning to the tab or waiting five minutes
revalidates metadata, preserving the selected valid time when possible.
Cached hours display immediately; uncached selections are coalesced for 50 ms
to avoid fetching every intermediate position during a fast drag. A two-request
loader deduplicates downloads and prefetches up to eight hours in the direction
of travel and three behind. Known unavailable hours are never prefetched.
Jumping outside the window cancels obsolete work and prioritizes the new target.

While a requested hour loads, the previous frame remains visible with an explicit
`Showing ... · Selected ...` timestamp. The renderer acknowledges a displayed
frame only after decoding and the visual transition. An unavailable hour clears
the layer; a new publication cannot reuse images from the prior run. Image caches
and request queues remain bounded, and downloads stop when leaving the map tab.

MapLibre keeps one georeferenced canvas source instead of removing/rebuilding
the source and layer for each hour. Images decode offscreen, then blend over
140 ms while preserving transparency. Animation is disabled for reduced-motion
preferences, and the canvas source pauses after the transition to avoid constant
rendering ([CanvasSource implementation](https://github.com/maplibre/maplibre-gl-js/blob/v5.6.2/src/source/canvas_source.ts)).
This is a visual transition between hourly frames; it does not calculate
additional forecasts or change risk scores. Latitude is warped to Mercator image
coordinates with fourfold vertical oversampling to retain source rows. Cells
uses nearest-pixel sampling; Heat linearly interpolates the same image colors.
Heat is visual smoothing, not a separate risk calculation or density estimate.
Both modes use a fixed 0–100 palette without per-hour/viewport normalization.
The UI notes provisional calibration, older runs and limited coverage.

An independent **Cell lines** switch adds faint geographic lines at the
forecast's 0.01° cell edges. It defaults off, works in both modes, and remains
above the risk layer when changing hours. Lines fade out at low zoom to avoid
clutter. The overlay has 830 lines across the current footprint and does not
change scores, interpolate concentrations, or add information to copied fields.

The same HTML runs in a sandboxed iframe on web and a WebView on mobile.
The app retrieves images through FastAPI and passes them through the bridge;
the renderer needs no GCS URL or credentials. Base tiles and map assets still
require OpenFreeMap and unpkg access.

## Local and cloud operation

Existing local risk NPZs work immediately. For a new publication, run from backend:

```powershell
python -m scripts.publish_dashboard_forecast --model-dir ../models/checkpoints --reference data/risk/reference-fine-o3-v1.npz
python -m uvicorn app.main:app --reload --port 8000
```

Follow [fine-grid setup](o3-downscaling.md) for historical preprocessing and the
matching reference, and [the deployment guide](deploy-forecast-gcs.md) for Cloud Run/GCS. The job
needs `RISK_REFERENCE_OBJECT` plus `FORECAST_GCS_BUCKET`; the API needs only
`FORECAST_GCS_BUCKET` for map storage. Keep references immutable and versioned.
Use attached service accounts through Application Default Credentials. Set
`EXPO_PUBLIC_API_URL` to the HTTPS API, configure the frontend origin in CORS,
and rebuild/restart the frontend. No map migration is required. Schedule the
publisher regularly and alert on job failures/stale forecasts. Live GCP rollout
remains an infrastructure step; local tests mock GCS rather than deploy resources.

Verification covers hourly general scores versus dashboard daily maxima,
missing-cell transparency, fixed colors, spatial alignment, run transitions,
expiry and cold-cache cloud reads. A personalized dashboard can differ because
it blends individual symptom-based adjustments; the map stays population risk.


## Physical forecast layers

The layer picker defaults to **General risk**, which is never personalized.
Published physical layers are ozone (ppb), surface NO2 (ppb), SO2 (ppb), PM2.5
(µg/m³), and temperature (°C). Pollutant concentrations are hourly NASA/model
outputs, **not AQI**, and are separate from the rolling exposures used for risk.
The NO2 column checkpoint is not a surface NO2 model and is not used here.

When publishing a risk artifact, the publisher creates `map-fields.nc` from the exact raw NASA source and optional
matching model output. It verifies model source SHA, timestamps, units, and the
0.01° footprint. Learned O3 and temperature remain at fine resolution; native
surface NO2/SO2/PM2.5 stay compact on their source grid and are copied to fine
cells only while rendering. No inference runs inside an API request.

The publication JSON pins the map artifact by filename and SHA-256. Local
publication moves this file before advancing the JSON; GCS uploads it privately
to `published/<publication-id>/map-fields.nc` before advancing `published/current.json`.
Cloud Run verifies the checksum and retains at most two cached physical-field
files. Each request reads one hourly field. The current artifact is about 90 MB;
include local caches and existing risk artifacts in Cloud Run memory sizing.
No extra bucket, credentials, or database migration is needed. Deploy the updated
publisher and API together and publish a new run to enable the physical layers.
Legacy runs with no map artifact continue to offer General risk only.

Both endpoints accept `layer=risk|o3|no2|so2|pm25|temperature|pm10|co`.
Metadata includes available layers, units, processing method, and fixed scale.
A physical-layer run ID includes risk artifact identity, map checksum, and layer,
so the existing image caches cannot mix layers. Switching layers preserves the
selected valid time and clears the previous variable before changing its legend.
Missing layers return 404; missing cells are transparent; unavailable hours are
explicit. There is no invented data or conversion from column NO2 to surface NO2.

Fixed display scales (not health thresholds) are 0–100 ppb O3, 0–10 ppb NO2, 0–2 ppb SO2,
0–75 µg/m³ PM2.5, and −10–45°C temperature. Values beyond these bounds use the
end colors; legends label saturation with ≤/≥. Scales stay fixed across hours.
Optional PM10 (0–150 µg/m³) and CO (0–2 ppm) appear only if present with valid
units in a future raw source. Current NASA downloads do not request these fields.
Adding them to risk-model inputs requires a separate ML contract/model change.


Physical layers replace decoded hourly images atomically rather than blending
concentrations from different hours. The selected timestamp is distinguished from
the last displayed hour while loading. General risk retains its existing smooth
transitions and loader behavior. Physical-frame requests finish active work and
replace queued obsolete hours, avoiding cancellation storms during scrubbing.
The API reads direct serialized NetCDF slices instead of reopening/decoding the
entire xarray dataset per frame. Physical run IDs include a renderer version to
invalidate old images after palette changes; the general-risk ID is unchanged.


## Personalized asthma-risk layer

The picker also offers **Personalized asthma risk**. Sign in through Symptom Log
first. This is a separate authenticated layer; General risk remains public and
unchanged. No matched completed reports means the personal model returns the
general baseline, with an explicit note. Finalized symptom reports matched to
historical exposures gradually adjust risk using the same `personal_scores`
function, reference, and uncertainty weighting as the dashboard. Each map cell
represents the user's predicted risk if exposed at that cell, not other residents'
risk or an asthma-attack probability.

`GET /v1/map/personal/forecast` verifies the Supabase bearer token and uses the
existing owner-scoped database session/RLS. It rebuilds the user's model from
finalized matched daily reports (including GCS history sync when configured),
then returns a run ID tied to the account, forecast, model state, and revision.
`GET /v1/map/personal/frame?run_id=...&index=...` reads that user's latest committed
revision, verifies model/reference compatibility and the run ID, and scores only
one hourly exposure slice on the 0.01° grid. Changed revisions return 409 so the
client reloads metadata. Frame requests do not download historical analysis or
retrain the model. Pending historical matches are reported by metadata.

Both responses use `Cache-Control: private, no-store` and `Vary: Authorization`.
Personal PNGs bypass the shared server image cache and are never uploaded to
GCS. The frontend uses a separate in-memory personal cache and remounts map state
and the renderer on account changes/sign-out. Public caches contain only public
layers. Physical layers and personal maps use complete-frame replacement; the
General risk renderer retains its existing smooth transitions.

Cloud Run uses the existing private GCS publication, history, Supabase JWT
verification, and runtime database connection. No new bucket, IAM role, database
migration, or ML checkpoint is required for this layer.
