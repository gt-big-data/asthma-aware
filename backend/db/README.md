# AsthmaAware Database

Shared Postgres + PostGIS store for everything the backend fetches from
upstream APIs: satellite rasters, ground observations, and Census data —
plus the model's normalisation constants and its forecasts.

Before this existed, each pipeline wrote files to whoever's laptop ran
it. `data/satellite/` is gitignored, so satellite data one person
collected was invisible to everyone else, and the Census API was called
live on every cold start. The database is the shared surface all of that
now lands on.

---

## Contents

- [Quick start](#quick-start)
- [What goes where](#what-goes-where)
- [Running the pipelines](#running-the-pipelines)
- [Using the data from Python](#using-the-data-from-python)
- [Adding a new pipeline](#adding-a-new-pipeline)
- [Schema reference](#schema-reference)
- [Design decisions worth knowing](#design-decisions-worth-knowing)
- [Migrations](#migrations)
- [Testing](#testing)
- [Troubleshooting](#troubleshooting)

---

## Quick start

### 1. Get a database

One person on the team creates it; everyone else reuses the connection
string. Either provider works — both have a free tier with PostGIS.

**Supabase** — <https://supabase.com/dashboard> → New project. Once it is
up: Project Settings → Database → Connection string → **URI**. Use the
**Session pooler** string (port 5432), not the transaction pooler —
`alembic` and the collectors both use prepared statements and features
the transaction pooler does not support.

**Neon** — <https://console.neon.tech> → New project → copy the connection
string from the dashboard.

> **Do not paste the connection string into Slack, a commit, or this
> file.** It grants full read/write to everything. Share it through
> whatever the team uses for secrets, and if it does leak, rotate it in
> the provider dashboard.

### 2. Configure your checkout

```bash
cd backend
cp .env.example .env
```

Edit `.env` and set `DATABASE_URL`. `.env` is gitignored.

```
DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres
```

Any of these forms work — the scheme is normalised automatically:
`postgres://…`, `postgresql://…`, `postgresql+psycopg://…`.

### 3. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pip install -r ingestion/requirements.txt   # only if running collectors
```

### 4. Create the schema and seed reference data

```bash
alembic upgrade head
python scripts/seed_reference_data.py
```

The first command creates 15 tables and enables the PostGIS extension.
The second populates the Atlanta region, its 5,376 grid cells, the 18
variables, and the 4 upstream data sources. **Only one person needs to run
these** — everyone shares the database. Both are safe to re-run.

### 5. Load the existing history

```bash
python scripts/backfill_raw_rasters.py --start-date YYYY-MM-DD
```

This loads the 181 days of NO2/SO2 and 12 NDVI composites already in
`raw_rasters/`. **You need the real start date from the ML team** — the
band-to-date mapping is not stored in the files. Use `--dry-run` first.

### 6. Freeze the scalers

```bash
python scripts/fit_scalers.py --region atlanta --version v1 --activate
```

Read [Design decisions](#design-decisions-worth-knowing) for why this
step matters more than it looks.

### 7. Check it worked

```bash
python -c "
from db.session import session_scope
from db.repositories import grid, reference
with session_scope() as s:
    r = reference.get_region(s, 'atlanta')
    v = reference.get_variables(s, ['so2','ndvi','no2'])
    print('region:', r.slug, f'{r.grid_rows}x{r.grid_cols}')
    print('complete frames:', len(grid.complete_dates(s, r, v, min_coverage=0.8)))
"
```

---

## What goes where

| Data | Shape | Table | Written by |
|---|---|---|---|
| NO2, SO2, CO, NDVI, AOD | gridded, daily | `grid_observations` | `ingestion.satellite.collector` |
| GeoTIFF provenance | one row per file | `raster_files` | `ingestion.satellite.collector` |
| PM2.5, ozone, AQI | point, hourly | `station_observations` | *air_quality — not built yet* |
| Temperature, wind, humidity | point, hourly | `station_observations` | *nws — not built yet* |
| Pollen counts | point, daily | `station_observations` | *pollen — not built yet* |
| Census ACS indicators | by ZCTA, yearly | `socioeconomic_records` | `ingestion.socioeconomic.collector` |
| Normalisation constants | per variable | `scaler_params` | `scripts/fit_scalers.py` |
| Model predictions | gridded, per run | `forecasts` | your inference code |

Raster **pixel values** live in `grid_observations`, resampled onto the
56×96 model grid. The **GeoTIFFs** stay on disk at native resolution
(~1113m Sentinel-5P, 250m MODIS) with their path and provenance in
`raster_files`, so the downsample is never lossy in a way you cannot undo.

---

## Running the pipelines

### Satellite (Earth Engine)

Requires a one-time `earthengine authenticate` and `EE_PROJECT_ID` in
`.env`.

```bash
python -m ingestion.satellite.collector --date 2026-09-10
python -m ingestion.satellite.collector --start-date 2026-09-01 --end-date 2026-09-10
python -m ingestion.satellite.collector --date 2026-09-10 --no-db   # files only
```

Writes GeoTIFFs and `metadata.json` exactly as before, **and** the pixel
values, file provenance and any missing-variable reasons to the database.
With no `DATABASE_URL` set it warns and falls back to files only.

### Socioeconomic (Census ACS)

```bash
python -m ingestion.socioeconomic.collector
```

Annual job, not scheduled.

**`CENSUS_API_KEY` is required**, not optional. The Census API rejects
unauthenticated requests for a query this large — it answers `302` to
`/missing_key.html` rather than an error status, which surfaces as an
opaque redirect error. Get a free key instantly at
<https://api.census.gov/data/key_signup.html> and put it in `.env`.

### Backfill and model input

```bash
python scripts/backfill_raw_rasters.py --start-date 2025-01-01 --dry-run
python scripts/fit_scalers.py --region atlanta --version v1 --activate
python scripts/build_latest_sequence_from_db.py --require-consecutive
```

`build_latest_sequence_from_db.py` writes the same
`app/data/latest_observed_sequence.npy` with the same `(4, 3, 56, 96)`
shape as the original `build_latest_sequence.py`, so nothing downstream
changes. The original still works and is untouched.

---

## Using the data from Python

Always go through `db.repositories`. Do not write queries inline —
keeping the SQL in one place is what lets the schema change without
touching every caller.

**Load the model input tensor:**

```python
from app.constants import FEATURE_ORDER
from db.repositories import grid, reference, scalers
from db.session import session_scope

with session_scope() as session:
    region = reference.get_region(session, "atlanta")
    variables = reference.get_variables(session, FEATURE_ORDER)  # so2, ndvi, no2

    dates = grid.latest_complete_dates(
        session, region, variables, 4,
        require_consecutive=True, min_coverage=0.8,
    )
    sequence = grid.load_sequence(session, region, variables, dates)  # (4,3,56,96)
    sequence = scalers.transform_sequence(
        sequence, scalers.get_active_many(session, region, variables)
    )
```

**Write a frame:**

```python
grid.upsert_frame(session, region, variable, date, array_56x96)
```

Idempotent — re-running a collector overwrites rather than duplicating.

**One cell's time series:**

```python
dates = grid.available_dates(session, region, variable, start=d1, end=d2)
```

**Save a forecast:**

```python
from db.repositories import forecasts

run = forecasts.create_model_run(
    session, region=region, model_name="base_convlstm",
    feature_order=FEATURE_ORDER, scaler_version="v1",
)
forecasts.save_forecast_frame(
    session, model_run=run, region=region, variables=variables,
    horizon="24h", frame=predicted, frame_scaled=raw_model_output,
)
```

### Conventions that will bite you if you ignore them

- **Missing data is `NULL` / `np.nan`.** Never `0`, never `-9999`. SQL
  aggregates skip NULL, so a masked pixel cannot contaminate a mean or a
  scaler fit.
- **`grid_cells.lat/lon` is the cell's upper-left corner**, matching
  `app/utils/geo.py`. The true centre is `center_lat`/`center_lon` and
  the PostGIS `centroid`. Use the corner for API responses, the centroid
  for distance and point-in-polygon work.
- **Row 0 is the northern edge.** Latitude decreases as the row index
  grows.
- **Channel order is `["so2", "ndvi", "no2"]`** — `FEATURE_ORDER` in
  `app/constants.py`. Pass the constant; never hardcode the list.
- **`observed_at` must be timezone-aware UTC.** Naive datetimes are
  rejected, because ground feeds report local time and silently
  misaligning them against daily satellite frames is very hard to notice.

---

## Adding a new pipeline

This is the section to read if you have been handed a data source and
told to get it into the database.

### First, work out which kind of source you have

| Your data looks like | It is | Write it to | Using |
|---|---|---|---|
| Readings from named monitoring sites, each at one lat/lon | **point** | `station_observations` | `stations.upsert_observations` |
| A raster / image / array covering the whole area | **gridded** | `grid_observations` | `grid.upsert_frame` |
| One value per ZIP code, county or tract | **areal** | `socioeconomic_records` | `socioeconomic.upsert_record` |

Point is by far the most common — AirNow, PurpleAir, NWS stations, pollen
counters are all point sources. If your API returns a list of sites with
coordinates, it is point data.

Both worked examples below are complete and runnable. Copy the one that
matches, replace the fetch, keep the structure.

### The shape every collector follows

```
open a session
  look up the region, the data source, and the variables
  open a run_scope          <- gives you status, timing, failure capture
    fetch from the API
    convert units, convert timestamps to UTC
    upsert
    record anything you could not get
```

### Example A — a point source

The `air_quality`, `nws` and `pollen` collectors are empty placeholder
files on `origin/backend_data_ingestion`. The tables and the write API
they need already exist. Here is a complete working collector — adapt the
fetch, keep the structure.

```python
"""ingestion/air_quality/collector.py"""
from __future__ import annotations

import datetime as dt
import httpx

from db.repositories import ingestion as ingestion_repo
from db.repositories import reference, stations
from db.session import session_scope


def main() -> None:
    with session_scope() as session:
        region = reference.get_region(session, "atlanta")

        # Register the upstream source once; get-or-create.
        source = reference.upsert_data_source(
            session, "airnow",
            name="AirNow", provider="US EPA",
            url="https://docs.airnowapi.org/",
        )
        # Slugs are seeded already — reuse them rather than inventing
        # new ones, or the data will not join.
        pm25 = reference.get_variable(session, "pm25")

        with ingestion_repo.run_scope(
            session, "air_quality", region=region, source=source,
        ) as run:
            payload = httpx.get("https://…", timeout=30).json()

            readings = []
            for site in payload:
                station = stations.upsert_station(
                    session,
                    source=source,
                    external_id=site["SiteID"],       # stable upstream id
                    name=site["SiteName"],
                    lat=site["Latitude"],
                    lon=site["Longitude"],
                    region=region,
                )
                readings.append({
                    "station_id": station.id,
                    "variable_id": pm25.id,
                    # MUST be timezone-aware UTC.
                    "observed_at": dt.datetime.fromisoformat(
                        site["UTC"]
                    ).replace(tzinfo=dt.timezone.utc),
                    "value": site["Value"],
                    "unit": "ug/m^3",
                })

            if not readings:
                ingestion_repo.record_issue(session, run, "upstream returned no sites")

            run.rows_written = stations.upsert_observations(
                session, readings, run_id=run.id
            )


if __name__ == "__main__":
    main()
```

### Example B — a gridded source

Anything that arrives as a raster covering the whole area: another
satellite product, gridded weather reanalysis, a modelled surface.

The work is getting the data onto the canonical 56×96 grid. If your
source is a georeferenced file, `resample_to_grid` does it. If you
already have a correctly-shaped array, pass it straight to
`upsert_frame`.

```python
"""ingestion/myproduct/collector.py"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

from db.repositories import grid, ingestion as ingestion_repo, reference
from db.session import session_scope
from ingestion import grid_resample


def main(date: dt.date, downloaded: Path) -> None:
    with session_scope() as session:
        region = reference.get_region(session, "atlanta")
        source = reference.upsert_data_source(
            session, "myproduct", name="My Gridded Product",
        )
        # Add the variable to VARIABLES in scripts/seed_reference_data.py
        # first, then look it up here.
        variable = reference.get_variable(session, "my_variable")

        with ingestion_repo.run_scope(
            session, "myproduct", region=region, source=source,
            start=date, end=date,
        ) as run:
            # Warps onto the model grid. Masked and uncovered pixels come
            # back as NaN, which upsert_frame stores as NULL.
            values = grid_resample.resample_to_grid(
                downloaded,
                bounds={
                    "min_lat": region.min_lat, "max_lat": region.max_lat,
                    "min_lon": region.min_lon, "max_lon": region.max_lon,
                },
                rows=region.grid_rows,
                cols=region.grid_cols,
                method="nearest",   # "average" only when coarsening a dense field
            )

            # Optional but recommended: keep the original file on record.
            raster = ingestion_repo.record_raster_file(
                session, region=region, variable=variable,
                observation_date=date, path=str(downloaded),
                run=run, source=source, unit="my_unit", crs="EPSG:4326",
            )

            run.rows_written = grid.upsert_frame(
                session, region, variable, date, values,
                raster_file_id=raster.id, run_id=run.id,
            )
```

Two things specific to gridded sources:

- **Check the resampling warning.** `resample_to_grid` logs a warning if
  your source barely overlaps the target grid. That almost always means a
  wrong or mis-declared CRS, and the warp will otherwise succeed and hand
  you a correctly-shaped, silently wrong array. Read the module docstring
  in `ingestion/grid_resample.py` before trusting the output.
- **Already have an array?** Skip the resample and call `upsert_frame`
  directly with any `(56, 96)` NumPy array. Use `np.nan` for missing
  values, or pass `nodata_value=` if your data uses a sentinel.

### Checklist for either kind

1. **Convert to the canonical unit** before writing. `variables.canonical_unit`
   is the contract; NWS reports °F, so convert to °C.
2. **Reuse the seeded variable slugs.** Add new ones to `VARIABLES` in
   `scripts/seed_reference_data.py` rather than calling `upsert_variable`
   ad hoc, so there is one list to read. Inventing a second slug for
   something that already exists means the two never join.
3. **Wrap everything in `run_scope`.** You get run status, timing, params
   and failure capture for free.
4. **Record what you could not fetch** with `record_issue`. A silent gap
   is the thing most likely to break the sequence builder later.
5. **Make it re-runnable.** Every upsert helper handles conflicts, so
   overlapping windows are fine — most hourly feeds publish a rolling one.
6. **Missing means NULL.** Never write `0` or `-9999` for absent data.

### Checking your work

```bash
python -m ingestion.myproduct.collector --date 2026-09-10
```

Then confirm it landed:

```sql
-- Did the run complete, and how much did it write?
SELECT pipeline, status, started_at, finished_at, rows_written, error
FROM ingestion_runs ORDER BY started_at DESC LIMIT 5;

-- Anything it could not fetch?
SELECT v.slug, i.observation_date, i.kind, i.reason
FROM ingestion_issues i
LEFT JOIN variables v ON v.id = i.variable_id
WHERE i.run_id = (SELECT max(id) FROM ingestion_runs);
```

A run showing `partial` is normal for satellite data — it means some
variables had no usable observation that day. `failed` means an exception
escaped, and the message is in `error`.

---

## Schema reference

**Reference** — small, seeded once, everything points at these.

| Table | Purpose |
|---|---|
| `regions` | Study area bbox and grid dimensions |
| `grid_cells` | The 56×96 grid, materialised, with PostGIS geometry |
| `variables` | What we measure, and its canonical unit |
| `data_sources` | Upstream providers, with licence and QA notes |

**Ingestion bookkeeping** — replaces `metadata.json`.

| Table | Purpose |
|---|---|
| `ingestion_runs` | One row per collector invocation: status, timing, params |
| `ingestion_issues` | What could not be fetched, and why |
| `raster_files` | Archived GeoTIFF provenance: unit, scale, CRS, QA, checksum |

**Measurements.**

| Table | Purpose |
|---|---|
| `grid_observations` | Gridded values. One row per (cell, variable, date) |
| `stations` | Fixed monitoring sites |
| `station_observations` | Point values. One row per (station, variable, timestamp) |
| `zctas` | ZIP Code Tabulation Areas |
| `socioeconomic_records` | ACS indicators per ZCTA-year, plus the raw payload |

**Modelling.**

| Table | Purpose |
|---|---|
| `scaler_params` | Frozen, versioned normalisation constants |
| `model_runs` | One inference pass: model, weights checksum, input window |
| `forecasts` | Predicted values, both scaled and in canonical units |

Volume at Atlanta's scale: ~27k rows/day across 5 gridded variables, so
roughly 10M rows/year in `grid_observations`. That is unremarkable for
Postgres with the indexes in place — no partitioning needed.

---

## Design decisions worth knowing

### Values are stored raw; scaling happens at read time

This fixes a real bug. `scripts/build_latest_sequence.py:24-27` calls
`MinMaxScaler().fit_transform(...)` on the whole stack **every run**, so
the normalisation is re-derived from whatever is on disk that day. Each
new observation that sets a new min or max shifts every historical value
— meaning the model is trained under one scaling and served under
another. Nothing errors; the forecasts just quietly get worse.

Storing raw values and pinning the constants in `scaler_params` makes the
scaling stable and auditable. Retraining writes a new `version` and
activates it; old versions stay so past forecasts remain interpretable.

### Missing data is NULL, not a sentinel

`ingestion/satellite/client.py` bakes `-9999.0` into masked pixels on
export (for good reasons — see its `NODATA_VALUE` comment). If that
reached the database as a number it would poison every aggregate. The
collector converts it to NULL on the way in.

Related: `build_latest_sequence.py` runs `np.nan_to_num` over the
rasters, turning masked pixels into real-looking `0.0` readings which
then skew any scaler fitted on them. The database path keeps them NULL.

### Don't reproject `raw_rasters/*.tif`

Those files carry MODIS **spherical** sinusoidal data tagged with the
**WGS84 ellipsoid**. Any tool that honours the declared CRS places them
~17km north of where they belong:

```
as declared (WGS84 ellipsoid):  lat 33.5397 .. 33.7921   ← ~17km off
as MODIS sphere (correct):      lat 33.3882 .. 33.6400   ← matches ATLANTA_BOUNDS
```

The arrays are already exactly the 56×96 Atlanta grid, so
`backfill_raw_rasters.py` reads them straight through with no warping.
`grid_resample.resample_to_grid` logs a warning if you point it at a
source that barely overlaps the target grid — the warp otherwise succeeds
and returns a correctly-shaped, silently wrong array.

GeoTIFFs downloaded by the satellite collector *are* genuine EPSG:4326
and do get warped. Both paths land on the same grid.

### Forecasts are persisted, not cached in a global

`app/services/map_service.py` keeps forecasts in a module-level
`_forecast_cache`. That dies on restart and is per-process — under more
than one uvicorn worker, two users can get forecasts built from different
input data with no way to tell. Persisting them also makes the model
scoreable: you cannot measure 72h forecast error if the 72h forecast was
never written down.

### NDVI carries two dates

MOD13Q1 is a 16-day composite, so a frame requested for the 20th may be
imagery from the 5th. `observation_date` is what you asked for;
`source_date` is what you got. The model indexes on the former;
provenance needs the latter.

---

## Migrations

Alembic, configured in `alembic.ini` with the revision scripts in
`db/migrations/`. The connection string comes from your `.env` — it is
deliberately **not** in `alembic.ini`, so no credential is ever committed.

```bash
alembic upgrade head            # apply everything
alembic current                 # what is applied now
alembic history                 # all revisions
alembic downgrade -1            # step back one
```

After changing anything in `db/models/`:

```bash
alembic revision --autogenerate -m "add pollen station metadata"
```

**Always read the generated file before applying it.** Autogenerate is
good but not infallible — it does not detect column renames (it emits a
drop plus an add, which loses data), and it cannot see server-side
defaults it did not create.

If you add a new model module, import it in `db/models/__init__.py` or
Alembic will not see it and will propose dropping nothing.

---

## Testing

```bash
pytest tests/test_grid_geometry.py tests/test_db_scalers.py tests/test_grid_resample.py
```

Those need no database and always run. They cover the scaler arithmetic,
the grid/coordinate convention, and the mis-tagged-CRS regression.

The integration tests need a real Postgres with PostGIS:

```bash
export TEST_DATABASE_URL='postgresql+psycopg://user:pass@host:5432/asthma_test'
pytest tests/test_db_integration.py -v
```

> ### Point this at a throwaway database
>
> The session fixture runs `drop_all` then `create_all`, so **every table
> and every row in `TEST_DATABASE_URL` is destroyed.** Never point it at
> the shared database once real data is in it. A second free
> Supabase/Neon project is the easy answer.
>
> It also leaves `alembic_version` stale — that table is not part of
> `Base.metadata`, so `drop_all` does not touch it, and alembic will
> report `head` while no tables exist. If you do wipe a database this
> way, bring it back with:
>
> ```bash
> alembic stamp base && alembic upgrade head
> python scripts/seed_reference_data.py
> ```

Within a run the tests are safe: each one executes inside a transaction
that is rolled back, so they cannot interfere with each other.

---

## Troubleshooting

**`DATABASE_URL is not set`**
`backend/.env` is missing or has no `DATABASE_URL`. Copy `.env.example`.

**`No region with slug 'atlanta'`** (or variable / data source)
Reference data is not seeded. Run `python scripts/seed_reference_data.py`.

**`No active scaler for variable 'so2'`**
Run `python scripts/fit_scalers.py --region atlanta --version v1 --activate`.
Scalers need data, so backfill or collect first.

**`type "geometry" does not exist`**
PostGIS is not enabled. `alembic upgrade head` does this, but the role
needs permission. On Supabase/Neon it is available by default; on a
self-hosted Postgres install the `postgis` package and connect as a
superuser once:
```sql
CREATE EXTENSION IF NOT EXISTS postgis;
```

**`Need 4 complete frames … but only N exist`**
Not enough usable history. Lower `--min-coverage` (Sentinel-5P is often
partly cloud-masked, so 1.0 can legitimately match nothing), collect more
dates, or backfill.

**`The 4 most recent complete frames are not consecutive days`**
There is a gap. Check `ingestion_issues` for the reason:
```sql
SELECT r.pipeline, i.observation_date, v.slug, i.kind, i.reason
FROM ingestion_issues i
JOIN ingestion_runs r ON r.id = i.run_id
LEFT JOIN variables v ON v.id = i.variable_id
ORDER BY i.observation_date DESC LIMIT 20;
```
Then backfill the gap, or drop `--require-consecutive` if you accept it.

**Connection drops on long ingests**
Hosted Postgres closes idle connections. The engine already recycles at
280s and pre-pings; tune with `DB_POOL_RECYCLE` in `.env` if needed.

**Slow or hanging `alembic upgrade` on Supabase**
You are probably on the transaction pooler (port 6543). Switch to the
session pooler connection string (port 5432).

### Useful queries

```sql
-- Recent pipeline activity
SELECT pipeline, status, started_at, finished_at, rows_written
FROM ingestion_runs ORDER BY started_at DESC LIMIT 10;

-- Coverage per variable per day
SELECT v.slug, o.observation_date,
       COUNT(o.value) AS valid,
       ROUND(100.0 * COUNT(o.value) / COUNT(*), 1) AS pct
FROM grid_observations o
JOIN variables v ON v.id = o.variable_id
GROUP BY v.slug, o.observation_date
ORDER BY o.observation_date DESC, v.slug
LIMIT 20;

-- Which scalers are live
SELECT v.slug, s.version, s.method, s.fit_min, s.fit_max, s.sample_count
FROM scaler_params s
JOIN variables v ON v.id = s.variable_id
WHERE s.is_active;
```
