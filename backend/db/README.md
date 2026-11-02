# Database foundation

This package provides SQLAlchemy models, repositories, session management, and
Alembic migrations for the historical environmental research pipeline and a new
private application schema. The [application contract](../docs/application-api.md)
documents auth, role provisioning, logging semantics, and remaining worker work.
The earlier [assessment](../docs/backend-review.md) is a historical cleanup review;
its missing-application-table findings predate migration 0002.

## Existing tables

| Group | Tables | Current purpose |
| --- | --- | --- |
| Reference | regions, grid_cells, variables, data_sources | Historical geography, measurement definitions, providers |
| Ingestion | ingestion_runs, ingestion_issues, raster_files | Collection provenance and status |
| Observations | grid_observations, stations, station_observations | Daily gridded values and timestamped station readings |
| Census | zctas, socioeconomic_records | Annual area statistics with raw payload |
| Modeling | scaler_params, model_runs, forecasts | Versioned scaling and old model inference outputs |

Migration 0002 adds `app.profiles`, `app.symptom_logs`, immutable
`app.symptom_log_revisions`, `app.exposure_snapshots`,
`app.personalization_revisions`, and `app.outbox_events`, with restricted grants
and forced user-scoped RLS. Devices and production forecast metadata remain future work.
Bulk forecast tiles should follow the planned GCS architecture; the old
`forecasts` table does not implement hourly issued/valid/publication semantics.

## Setup

From `backend/`, using the project's virtual environment:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Set MIGRATION_DATABASE_URL to an operator connection; review the target first.
python -m alembic upgrade head
python scripts/seed_reference_data.py
```

Use a connection with the permissions needed for migrations and PostGIS setup.
Use a separate runtime login in the migration-created `asthmaaware_api` group;
never use operator credentials in the deployed API. Set its `DATABASE_URL` and
the HTTPS `SUPABASE_URL`. Validate pooling for your deployment separately.
No migration is applied automatically. Do not create a second database provider
as part of following historical examples; Supabase is the project direction.

The reference seed retains the 56x96 historical grid (latitude 33.39-33.64), which
**does not include downtown Atlanta**. Do not change it in place or use it as the
future API grid. Add a versioned grid definition and a migration strategy instead.

## Access conventions

Use `db.repositories` for queries/writes and `db.session.session_scope` for
transactional scripts. The engine is lazy, so importing the API does not connect
to Postgres. Historical `get_session` closes without committing. V1 instead uses
`app.dependencies.get_user_session`, which sets a transaction-local restricted
role/user context and commits before sending a response. The private schema is
not intended for direct access through Supabase's Data API.

Store observed values in their declared units; missing grid values are NULL
and become NaN in arrays. Ground timestamps must be timezone-aware. Satellite
column amounts, surface concentrations, and AQI are different quantities;
normalizing their names does not make them interchangeable model features.

`grid_observations` is an overwriteable daily view keyed by cell/variable/date.
It is not source-versioned immutable exposure history. Save values or immutable
object references plus feature/model versions when implementing symptom training.
Local raster paths also need durable GCS references before cloud execution.

`run_scope` rolls failed work back to a savepoint and never commits for its caller.
A failed audit row is not durable if the outer caller rolls back. Implement
separate job failure auditing before scheduling production collection.

Scaler versions cannot be refitted in place. Fit a new version and activate it;
activation currently lacks a database uniqueness guarantee under concurrent
writers. Do not use per-variable active scalers as a substitute for pinning the
whole model/feature/normalization contract.

`updated_at` uses SQLAlchemy onupdate; raw SQL and bulk conflict updates do not
receive a database-triggered timestamp update.

## Historical tools

The scripts in `../scripts/` support reference seeding, raster backfill, scaler
fitting, and building the old ConvLSTM input sequence. The raster inputs now live
in `../legacy/raw_rasters/`, and sequence output goes to
`../legacy/map_prototype/data/`. These are research utilities, not the new daily
GEOS-CF forecast publication job. Backfill requires a verified band-to-date mapping.

## Validation

```powershell
python -m pip install -r requirements/dev.txt
python -m pytest
```

The PostGIS integration tests skip unless TEST_DATABASE_URL is set. The fixtures
**drop tables** and require TEST_DATABASE_ALLOW_RESET=true. Use a disposable
Postgres/PostGIS database, never the shared project database.

Integration fixtures now exercise Alembic upgrade/downgrade round trips, including
application RLS/grants. Never point them at the shared project database. Review
migrations and hosted-role compatibility before deploying to Supabase. The
NOLOGIN group is retained on downgrade because external runtime logins may use it.
