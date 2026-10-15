# NASA GEOS-CF forecast ingestion

`fetch_forecast.py` downloads the newest NASA GEOS-CF v2 composition forecast for Georgia
and saves it as one NetCDF file (`time × lat × lon`) plus a JSON metadata sidecar.

```bash
cd backend
pip install -r ingestion/requirements.txt
python -m ingestion.geos_cf.fetch_forecast --region georgia --hours 120 --output ./data/geos_cf
```

| Flag | Default | Purpose |
|---|---|---|
| `--region` | `georgia` | Named bounding box from `REGIONS` |
| `--hours` | `120` | Forecast hours to keep (1–120) |
| `--output` | `data/geos_cf` | Output directory |
| `--run YYYY-MM-DD` | latest | Fetch that date's 09z run instead of the newest one |
| `--lookback-days` | `3` | How many earlier daily runs to try if the newest one is incomplete |
| `--overwrite` | off | Download again even if this run's file already exists |

The script exits with status 1 and lists every problem it found if no complete run is
available or if validation fails. A full Georgia pull takes about 2–3 minutes and writes
about 1 MB.

## Output

`data/geos_cf/geos_cf_georgia_<YYYYMMDD>_09z.nc` and `…metadata.json`

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
- `<collection>/<collection>.YYYYMMDD_09z`: one dataset per run. Runs go back to
  2025-08 and the archive has gaps (for example, 2025-08-06 through 08-08 are missing).

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
