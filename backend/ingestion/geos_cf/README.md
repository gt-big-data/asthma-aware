# NASA GEOS-CF forecast ingestion

`fetch_forecast.py` downloads the newest NASA GEOS-CF v2 composition forecast for Georgia
and saves it as one NetCDF file (`time × lat × lon`) plus a JSON metadata sidecar.

```bash
cd backend
pip install -r ingestion/requirements.txt
python -m ingestion.geos_cf.fetch_forecast --region georgia --hours 120 --output ./data/geos_cf
```

### Choosing where, when, and which run

**Where** (pick one; default is all of Georgia):

| Flag | Gets you |
|---|---|
| `--region georgia` | The whole state (20 × 20 cells) |
| `--point LAT,LON` | The single 0.25° (~25 km) cell containing that point |
| `--bbox MIN_LAT,MAX_LAT,MIN_LON,MAX_LON` | Every cell that touches that box |

Points and boxes must be inside Georgia's bounding box.

**When** (pick one; default is all 120 hours):

| Flag | Gets you |
|---|---|
| `--hours N` | The first N hours of the run (1–120) |
| `--time T` | The one hourly value whose hour contains `T` |
| `--start T [--end T]` | Every hour from `--start` up to, but not including, `--end`. Leave out `--end` to go to the end of the run |
| `--tz ZONE` | The timezone for times written without an offset. Default `UTC`; use `America/New_York` for Georgia local time |

Times are ISO 8601: `2026-10-23T15:00`, `2026-10-23`, or with an offset such as `2026-10-23T15:00-04:00`.
For example, `--start 2026-10-23T15:00 --end 2026-10-23T18:00 --tz America/New_York` returns
3 values, the 3, 4 and 5 pm hours.

**Which run:**

| Flag | Default | Purpose |
|---|---|---|
| `--run YYYY-MM-DD` | newest | Use the 09z run from that day. The run must cover `--time`/`--start` if you give one |
| `--lookback-days` | `3` | How many earlier runs to try if the best run is missing |

Without `--run`, a time request uses the **freshest forecast that covers it**: the newest
run that started at or before `--start`/`--time`. A time in the next few days therefore
uses today's run, and a past time uses the run from that day. One run spans 120 hours, so a
window that is longer than that or past the newest run's end is rejected with a message
saying what the newest run covers.

**Other:**

| Flag | Default | Purpose |
|---|---|---|
| `--output` | `data/geos_cf` | Output directory |
| `--overwrite` | off | Download again even if this output file already exists |

```bash
# Atlanta, tomorrow 3–6 pm Eastern, from the newest run
python -m ingestion.geos_cf.fetch_forecast --point 33.75,-84.39 --start 2026-10-23T15:00 --end 2026-10-23T18:00 --tz America/New_York
# The full 5-day forecast issued on Oct 10
python -m ingestion.geos_cf.fetch_forecast --run 2026-10-10
# Metro Atlanta box, all of Oct 23 (UTC)
python -m ingestion.geos_cf.fetch_forecast --bbox 33.4,34.2,-84.9,-83.9 --start 2026-10-23 --end 2026-10-24
```

The script exits with status 1 and lists every problem it found if no complete run is
available or if validation fails. A full Georgia pull takes about 2–3 minutes and writes
about 1 MB.

## Output

`data/geos_cf/geos_cf_<area>_<YYYYMMDD>_09z[_hSSS-EEE].nc` and `…metadata.json`.
`<area>` is `georgia`, `point_33.75N_84.39W` or `bbox_…`. The `_hSSS-EEE` suffix (lead
hours, end exclusive) is added only when you ask for less than the full run. For example,
`geos_cf_point_33.75N_84.39W_20261022_09z_h034-037.nc`.

The example below describes the default full-state, full-run file. A point request gives
`lat: 1, lon: 1`. The metadata also records `requested_point`, `requested_time_window` and
`lead_hour_range`.

- Dimensions: `time: 120, lat: 20, lon: 20`. Coordinates are grid-cell centers, lat
  30.25–35.0 and lon -85.5 to -80.75. The grid keeps every 0.25° cell that overlaps
  Georgia's bounding box (30.36–35.00 N, 85.61–80.84 W).
- `lead_time`: a coordinate along `time` holding the time since initialization (0.5 h … 119.5 h).
- Each variable carries `units`, `source_name`, `source_collection` and `source_long_name`.
- Global attributes and the JSON sidecar both record: `source`, `forecast_run`,
  `initialization_time`, `downloaded_at`, `valid_times`, `native_spatial_resolution`,
  `region_bbox`, `grid_extent`, `variables`, `variable_source_names`, `units` and the
  exact `source_urls`.

Units are left exactly as GEOS-CF reports them, and validation fails if they change upstream:

| Output name | Source variable | Collection | Units | Notes |
|---|---|---|---|---|
| `pm25` | `pm25_rh35` | aqc | ug m-3 | At 35% RH (includes particle water), matching how monitors report |
| `pm10` | `pm10_rh35` | aqc | ug m-3 | At 35% RH |
| `no2`, `o3`, `so2`, `co` | same | aqc | mol mol-1 | Dry-air mole fraction. Multiply by 1e9 for ppb |
| `air_temperature` | `t` | met | K | Lowest model layer (~60 m), **not** 2 m temperature |
| `specific_humidity` | `q` | met | kg kg-1 | Lowest model layer |
| `relative_humidity` | `rh` | met | 1 | Fraction from 0 to 1, not percent |
| `eastward_wind` / `northward_wind` | `u` / `v` | met | m s-1 | Lowest model layer, not 10 m |
| `surface_pressure` | `ps` | met | Pa | |
| `precipitation` | `tprec` | met | kg m-2 s-1 | Rate. Multiply by 3600 for mm/h |
| `pbl_height` | `zpbl` | met | m | Boundary-layer height, a strong driver of surface pollution |

## How GEOS-CF v2 forecasts are published

All of the following was checked against the live server on 2026-10-15.

**Datasets.** The v2 forecasts are served by NASA NCCS's GrADS Data Server (OPeNDAP/DAP2)
at `https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/v2/fcst/`. This script uses two
of its collections:

- `aqc_tavg_1hr_glo_L1440x721_slv`: the surface air-quality product, with exactly six
  variables: `co, no2, o3, pm10_rh35, pm25_rh35, so2`.
- `met_tavg_1hr_glo_L1440x721_slv`: surface/lowest-layer meteorology (`t, q, rh, u, v,
  ps, slp, tprec, ts, zpbl, cldtt, troppb, zl, phis`).

The naming scheme is `tavg` = hourly means, `glo_L1440x721` = global 0.25° grid,
`slv` = a single level (the lowest model layer, `lev = 72`). The same directory also
holds `chm`, `xgc`, `ems`, `met_inst…_p23` (pressure levels) and other collections that
we don't need. The older `/gmao/geos-cf/fcast/…_v1` tree is v1 and should not be used.

**Latest vs. dated runs.** Each collection is published in two forms:

- `<collection>.latest`: a moving alias for the newest run.
- `<collection>/<collection>.YYYYMMDD_09z`: one dataset per run. Air-quality runs go back
  to 2025-08, with gaps (for example, 2025-08-06 through 08-08 are missing). **Weather runs are
  kept for only ~17 days**, so `--run` only works for about the last 2½ weeks.

`.latest` does not report which run it is. The script works it out from the time axis:
init = first timestamp − 30 min. It then **pins every collection to that run's dated
URL**, so the AQ and met data always come from the same run, even if `.latest` moves
forward partway through a download.

**Initialization and length.** There is one run per day, initialized at **09 UTC**, with
**120 hourly steps** (5 days). Each `tavg` value is the mean over one hour and is stamped
at the middle of that hour. The 2026-10-15 run covers `09:30Z 15 Oct → 08:30Z 20 Oct`.

**Coordinates.** Longitude runs from -180 to 179.75 and latitude from -90 to 90. Both
are cell centers, in ascending order, with a 0.25° step, so `.sel(lat=slice(lo, hi))`
works directly. The script fails if it ever sees 0–360 longitudes. Time is encoded as
`days since 1-1-1`, which xarray decodes correctly (the script suppresses the ambiguity
warning).

**Units.** The server does **not** set a `units` attribute. Units appear only as the
trailing `(…)` in each variable's `long_name`. The script parses them from there, writes
them out as proper `units` attributes, and checks them against the expected values in
`VARIABLES`. The fill value is `1e15`, which xarray turns into NaN.

**Server-side subsetting.** It works. `xr.open_dataset(url)` reads only the metadata.
`.sel()` / `.isel()` are lazy, and `.load()` sends a DAP constraint
(`var[t0:t1][lev][lat0:lat1][lon0:lon1]`), so only the Georgia hyperslab is transferred
(about 190 KB per variable for 120 hours), not the 500 MB global field. The script loads
one variable per request so that a failed request only retries that variable.

**Late or missing runs.** When the day's run hasn't been published yet, `.latest` keeps
pointing at the previous run. A missing dated run returns a DAP error ("is not an
available dataset"), which netCDF4 raises as `OSError`. The script handles both cases:

1. It treats a run as usable only if it exists for **every** collection we need.
   Otherwise it steps back one day at a time, up to `--lookback-days` runs.
2. If `.latest` itself can't be opened, it guesses the newest 09z run from the clock and
   searches backwards from there.
3. It logs a warning when the run it uses is more than 30 hours old, which means a newer
   run is late.
4. It retries network errors 3 times with backoff before giving up.

## Validation

Before anything is written, `validate_forecast` checks all of the following and reports
every failure together:

- The `time`, `lat` and `lon` dimensions exist and are non-empty, i.e. the region subset
  is not empty.
- The number of forecast hours equals `--hours`.
- Valid times are strictly increasing in regular 1-hour steps.
- Every required variable is present with dimensions `(time, lat, lon)`.
- Each variable has units that match the expected ones.
- No variable is entirely NaN, and no single forecast hour is entirely NaN.

The NetCDF file is written to a temp file and then renamed, so a reader never sees a
half-written file.

## Historical data: `fetch_analysis.py`

The forecast archive can't supply a past year. Weather forecasts are kept for only ~17 days
(air-quality forecasts go back to Aug 2025), and forecasts are predictions anyway.
For history, use the **analysis**: GEOS-CF's continuous hourly reconstruction of past
conditions, made by running the model alongside observations. Weather fields are tightly tied
to observations. Pollutants are mostly model chemistry, with limited satellite constraints, so
treat them as estimates and use EPA AirNow/AQS for measured values.

```bash
python -m ingestion.geos_cf.fetch_analysis --point 33.75,-84.39 \
    --start 2025-10-01 --end 2026-10-01 --tz America/New_York
```

It takes the same `--point` / `--bbox` / `--region` and `--tz` options as the forecast script,
and `--start` and `--end` are required. Output: `geos_cf_analysis_<area>_<start>-<end>.nc`, with
the same variables and units as the forecast plus `missing_hours`, `hours_from_opendap` and
`hours_from_cloud_copy` in the metadata.

**Where the data comes from, and why:**

1. **GMAO's Zarr copy on S3** (`s3://smce-geos-cf-public/geos-cf-v2-rpl.zarr`) is stored in
   663-hour × 50 × 50-cell tiles, so a year at any area reads in about a minute. Where it has
   data, it is identical to OPeNDAP. As of Oct 2026 it has **no specific humidity**, is
   **missing Dec 2025 to Jan 2026**, stores **September 2025 twice** (the duplicates are
   removed), and lags a day or so behind.
2. **OPeNDAP** (`/dods/gmao/geos-cf/v2/ana/…`) fills whatever the Zarr copy lacks. It costs
   about 0.1 s per variable per hour **regardless of area**: a week took 15 s for a point and
   17 s for the whole continental US. Asking for several variables in one request doesn't
   help. NCCS's proxy drops requests that run past about a minute, so the script requests
   1 week at a time with 6 in parallel. Chunks are cached under `data/geos_cf/.cache/`, so
   rerunning after a failure resumes where it stopped. Some hourly files around late Dec
   2025 sometimes return an instant 502. Failing chunks are split in half repeatedly, and
   only truly unreadable hours are left missing.

Use `--no-cloud` to skip S3. Expect about 10 minutes for a year at one point. Most of that is
specific humidity and the Dec–Jan gap coming from OPeNDAP; area adds little.

The forecast archive also lives on that S3 bucket as `geos-cf-v2-fcst-latest.zarr`, but only
for the latest run.
