# AsthmaAware backend

One FastAPI application, independent data collectors, an environmental database
layer, and a private application schema. Authenticated V1 profile and symptom-log
endpoints are implemented. Live forecast providers, model fitting, and cloud
deployment remain separate work.

## Layout

| Path | Responsibility |
| --- | --- |
| `app/main.py` | Single API entry point and app factory |
| `app/api/` | V1 profile/log routes, demo dashboard, and operational health check |
| `app/auth.py` | Supabase asymmetric access-token verification |
| `app/dependencies.py` | Restricted transactional database context for V1 |
| `app/models/` | API request/response schemas |
| `app/services/` | Dashboard assembly and explicitly mocked providers |
| `app/data/` | Demo JSON fixtures, not collected environmental data |
| `ingestion/geos_cf/` | Shared NASA forecast acquisition and validation |
| `ingestion/air_quality/` | EPA AirNow observation client and normalization |
| `ingestion/satellite/` | Optional Earth Engine research collector |
| `ingestion/socioeconomic/` | Optional Census ACS research collector |
| `ingestion/grid_definition.py` | Historical raster grid, not the future forecast grid |
| `db/` | SQLAlchemy models, repositories, sessions, and Alembic migrations |
| `scripts/` | Reference seeding and historical data/scaler utilities |
| `tests/` | Unified offline tests and opt-in PostGIS integration tests |
| `requirements/` | API, database, collectors, development, and satellite dependencies |
| `legacy/` | Retired map API, ConvLSTM experiment, rasters, and historical docs |
| `data/` | Ignored local collector outputs; future storage adapters should support GCS |

Read [the backend assessment](docs/backend-review.md) for findings, limitations,
and the implementation sequence. The old prototype is preserved for reference
and is not imported or registered by the active API.

## Local API

Use Python 3.11 or newer. From `backend/`, using your activated virtual environment:

```powershell
python -m pip install -r requirements.txt
Copy-Item .env.example .env
# Edit .env: set DASHBOARD_DEMO_ENABLED=true to try fixture responses.
python -m uvicorn app.main:app --reload
```

`GET /dashboard?lat=33.749&lon=-84.388` is the existing route. It returns 503
unless demo mode is enabled. Demo responses carry
`X-AsthmaAware-Data-Source: demo`. No user authentication or live forecast
integration is claimed. [Dashboard demo contract](docs/dashboard.md).

`GET /health` returns `{"status":"ok"}` independently of demo mode, the database,
and forecast providers. It checks process liveness, not dependency readiness.

The obsolete `/map/*` routes are no longer registered. The old
frontend still calls `/map/*`; migrating it to the new API is separate work.
V1 now supports profile GET/PATCH, dated symptom PUT/GET, today's log, and paginated
history. Configure `SUPABASE_URL`, review migration 0002, and provision the runtime
database login before using these routes. See the [application contract and setup](docs/application-api.md).
V1 never accepts the demo identity. It automatically finalizes today's entry at
the stored local-midnight boundary; exposure matching remains pending until a
future worker supplies it.

For connecting the shared development project, follow the
[Supabase connection checklist](docs/supabase-connection.md). Its preflight
command checks readiness without applying migrations or reading user records.

## Collectors

Run from `backend/`; do not change into individual collector folders.

```powershell
python -m pip install -r requirements/ingestion.txt
python -m ingestion.geos_cf.fetch_forecast --region georgia --hours 120 --output data/geos_cf
python -m ingestion.air_quality.collector --hours 24
```

AirNow requires `AIRNOW_API_KEY` in the environment or `backend/.env`. GEOS-CF
uses public upstream access. These commands perform external downloads; the
test suite uses local fixtures instead. AirNow currently archives JSON only;
it has no database writer. GEOS-CF currently archives NetCDF and metadata;
it has no GCS publication or dashboard adapter.

Satellite collection additionally needs `requirements/satellite.txt`, Earth
Engine credentials, and `EE_PROJECT_ID`. Census collection needs
`CENSUS_API_KEY` and the environmental database. Neither runs during API startup.
NWS and pollen have no collector implementation in this checkout.

## Database

Keep the migration history. [Database reference](db/README.md) describes the public
environmental schema and private `app` tables. Set `MIGRATION_DATABASE_URL` to a
migration-capable connection and `DATABASE_URL` to a restricted runtime login.
Review the migration and target database before running:

```powershell
python -m alembic upgrade head
python scripts/seed_reference_data.py
```

The seed intentionally retains the historical 56×96 raster grid for compatibility.
Its extent is south of downtown Atlanta. Do not reuse it as product coverage or
silently relabel historical cells when designing the planned 500 m grid.
No migration is automatically applied by API startup.

## Verification

```powershell
python -m pip install -r requirements/dev.txt
python -m pytest
```

PostGIS integration tests skip unless `TEST_DATABASE_URL` is set. They **drop
tables**, so they additionally require `TEST_DATABASE_ALLOW_RESET=true` and
must target a disposable database. They exercise actual Alembic upgrades and
downgrades, API transactions, JWT signatures, concurrent writes, and RLS isolation.
Dependencies have version ranges, not a deployment lockfile. Hosted Supabase
configuration and network connectivity still need environment-specific checks.
