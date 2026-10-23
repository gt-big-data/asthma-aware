# Backend assessment and cleanup

Reviewed October 18, 2026 against the supplied project context and local source.
This is a source review with offline verification, not evidence of upstream data
availability, deployed infrastructure, or model validity.

## Assessment

The old folder structure combines three independently developed projects. Both
`backend/app` and `backend/dashboard/app` used the import name `app`, while EPA
had its own `ingestion` namespace. Working directory determined which code ran.
The consolidated backend now has one API entry point and one ingestion package.

| Component | Quality and role | Decision |
| --- | --- | --- |
| Dashboard | Good separation of route/schema/calculation; useful DST and formula tests. Inputs, identity, Georgia grid, risk thresholds, and run identity are fixtures. Positive correction shares omit population effects. | Keep as a demo scaffold, explicit opt-in; preserve its current response shape. |
| GEOS-CF | Strongest collector: pins dated runs across collections, subsets remotely, retries with process timeouts, validates units/time/grid, records provenance. Local-file job, not a forecast serving pipeline. | Keep as primary forecast acquisition. Add validated cache reuse, first-valid-time checks, finite-value checks, and atomic sidecar replacement. |
| AirNow (EPA) | Small client/normalizer/CLI with mocked tests, bounding-box filtering, duplicate handling, raw archive, UTC fields, and redacted request metadata. | Keep for observed air quality, evaluation, and possible exposure inputs. Reject nonfinite numbers. No database sink exists yet. |
| NWS | Only empty data directories and references in docs/schema were found. | No code to grade. Decide whether its observations add needed weather validation/history beyond GEOS-CF before building it. |
| Earth Engine satellite | Provider-specific clients, QA masks, NoData handling, source dates, raster provenance, and optional DB sink are useful. Hardcoded historical geography, broad exception handling, synchronous network calls, and limited collector tests need work. | Keep as optional research ingestion. It is not an established downscaling design or a substitute for surface pollutant forecasts. |
| Census ACS | Useful raw-plus-derived history and sentinel handling. Previously imported an obsolete HTTP service and used private helpers. National fetch and incomplete area lookup still need review. | Extract client from retired API; preserve missing totals, tie housing age to dataset year, and add focused tests. Retain as optional contextual/research data, not a required core dashboard/log/map dependency. |
| ConvLSTM/map prototype | Hardcoded three-feature tensor and daily horizons; placeholder preprocessing, in-memory cache, synthetic timestamps, full-grid JSON, and stale endpoint contracts. | Archive, including artifacts, rasters, and tests. No active API imports it. |
| SQLAlchemy/Alembic | Reasonable modules, explicit constraints/indexes, PostGIS geometry, lazy engine, repositories, raw values, missing-data handling, and integration fixtures. | Retain environmental foundation and migration history, but extend deliberately; do not call it the finished app database. |

The frontend still consumes the retired map endpoints. Their removal is
intentional per the requested backend direction; frontend integration remains
future work. `/dashboard` has not been renamed to `/v1/dashboard` during cleanup.

## Database verdict

Useful environmental research infrastructure, **not ready as the application
database**. The 15-table schema contains reference regions/cells/variables/sources,
ingestion runs/issues/raster metadata, grid/station observations, Census areas and
records, scalers, model runs, and environmental forecasts.

Missing application entities include profiles linked to Supabase identity,
one editable symptom log per user/local date, reproducible exposure snapshots,
versioned personal correction vectors and input revisions, devices/preferences,
outbox events, and notification deduplication. There are no Supabase JWT checks,
user ownership policies, RLS migrations, or service-role access design.

Existing `forecasts` store values by cell/variable/horizon and optional **date**.
That is the old ConvLSTM output contract. It cannot represent the new issued-at /
hourly-valid-at / published-at / immutable-run contract as designed. Add explicit
run metadata and GCS manifest/object references; do not populate this old table
with every future tile value by default. Keep bulk numerical forecast arrays in
GCS as planned.

The old gridded observation key `(cell, variable, date)` does not distinguish
providers or pipeline revisions. Re-ingestion overwrites values and raster
provenance. This is useful as a current observation view but insufficient as an
immutable training/exposure archive. Retain snapshots or immutable references
before using these records to reproduce user model fits.

The historical raster grid spans latitude 33.39–33.64, so it excludes the
dashboard's downtown Atlanta example (33.749). The dashboard independently uses
a coarse Georgia fixture grid. Neither is a validated implementation of the
planned Atlanta 500 m grid. Version the new grid and validate its extent; changing
the old seed in place would misidentify stored observations.

## Reliability changes made

- `run_scope` no longer commits the caller's pending data on an exception.
  Writes inside the run use a savepoint; a failed run rolls them back.
  **Tradeoff:** an outer rollback also loses the failure audit row. A future job
  runner must persist failure state in its own transaction or structured logs.
  No durable auditing guarantee is made by this helper.
- Satellite per-variable persistence uses a savepoint so a SQL failure does not
  leave the session unusable for subsequent variables or issue recording.
- Fitting an already existing scaler version now fails; use a new version.
  Activation rejects unknown versions before deactivating the current one.
  Constant scalers preserve missing cells instead of replacing them with zero.
- Alembic configuration escapes percent signs in encoded database passwords.
- Tests are consolidated and use importlib collection. PostGIS fixtures require
  explicit reset opt-in because they drop tables.
- Mock dashboard responses require explicit demo mode; CORS origins are
  configurable. No verified-user access is simulated as implemented auth.
- Census component totals now remain missing when any component is absent;
  housing age uses the dataset vintage instead of changing with the current year.
  Satellite initialization loads the documented local environment file, and its
  region argument cannot attribute the fixed historical box to another region.

## Verification completed

The unified offline suite finished with **107 passed and 28 skipped**. The skipped
tests require a disposable PostGIS database; none was configured. Three warnings
come from dependency deprecations (Starlette TestClient/httpx and rasterio transforms).
Python compilation and offline Alembic SQL generation passed, including an
encoded-password regression check. All five relocated model/data binaries were
checked against Git and preserved byte for byte. No migration was applied, cloud
resource created, or live provider request made. These checks do not establish
database migration execution, deployment readiness, or scientific validity.

After adding the operational `/health` endpoint, the suite finished with
**108 passed and 28 skipped**. Health is independent of demo mode, database
configuration, and forecast providers; it reports process liveness only.

## Remaining issues before deployment

1. Define symptom target/exposure semantics and the versioned prediction contract
   before implementing the user tables and personalization worker. Preserve
   one active observation per local day and prevent older refits overwriting newer ones.
2. Add Supabase token verification, authorization, RLS/access-role design, and
   ownership tests before storing or serving real user data.
3. Add migrations for the application entities and shared forecast-run metadata.
   Run Alembic upgrade/downgrade and repository tests against disposable PostGIS.
   Existing integration tests use `metadata.create_all`; they do not validate migrations.
4. Add source-aware immutable history and model/normalization/transform compatibility.
   Current scaler activation has no database uniqueness guarantee under concurrent
   writers; reference/station get-or-create paths also need concurrency work.
   `updated_at` uses ORM `onupdate`, not a database trigger; raw SQL and bulk upserts
   do not automatically maintain it.
5. Introduce forecast and personalization provider interfaces; implement a GCS
   adapter and numerical tile reader. Dashboard must read relevant tile chunks,
   enforce coverage/version compatibility, and return unavailable states.
   Decide rolling 24-hour versus local-day curve semantics and full attribution.
6. Harden collection jobs: shared region/units/provenance contracts, structured
   logging, durable failed-run audit, source-specific freshness checks, AirNow
   DB sink if needed, safe retries, and bounded connection pools. GEOS-CF's local
   two-file publication and fixed temporary names are not a concurrent cloud
   publication protocol; add immutable runs and a validated current-run pointer.
7. Add Cloud Run/API and job packaging, GCS/CDN publication, service identities,
   Secret Manager wiring, and Scheduler/Tasks/outbox dispatch only after contracts
   are concrete. There are currently no deployment definitions to validate.
8. Lock dependencies after CI on the chosen Python runtime. Test actual upstream
   access separately; this review does not revalidate provider documentation,
   product versions, scientific units/conversions, or operational availability.

Environmental downscaling remains deferred. Keeping the existing resampling
utility does not endorse it as an accurate 500 m prediction model.
