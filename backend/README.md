# AsthmaAware backend

One FastAPI application, independent data collectors, and a reusable environmental
database layer. This is a development foundation; authentication, live dashboard
providers, the application schema, and cloud deployment are unfinished.

## Layout

| Path | Responsibility |
| --- | --- |
| `app/main.py` | Single API entry point and app factory |
| `app/api/` | Dashboard route, operational health check, and dependencies |
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
The planned `/v1` endpoint inventory is not implemented by this cleanup.

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

Keep the existing migration history. [Database reference](db/README.md) describes
the environmental schema; it is not yet the Supabase-backed user-data design.
Set a migration-capable `DATABASE_URL`, review the target database, then run:

```powershell
python -m alembic upgrade head
python scripts/seed_reference_data.py
```

The seed intentionally retains the historical 56×96 raster grid for compatibility.
Its extent is south of downtown Atlanta. Do not reuse it as product coverage or
silently relabel historical cells when designing the planned 500 m grid.
No migration is automatically applied by API startup or this cleanup.

## Verification

```powershell
python -m pip install -r requirements/dev.txt
python -m pytest
```

PostGIS integration tests skip unless `TEST_DATABASE_URL` is set. They **drop
tables**, so they additionally require `TEST_DATABASE_ALLOW_RESET=true` and
must target a disposable database. They currently test ORM schema creation,
not the Alembic upgrade path. A real migration round trip remains necessary
before deployment. Dependencies have version ranges, not a deployment lockfile.
