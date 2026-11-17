# PM2.5: daily grid and 1 km downscaling

Turns the GEOS-CF analysis into the two PM2.5 products the ML team asked for:

1. **Daily 25 km grid** — one `.tif` shaped `(days, height, width)` over Georgia, with missing
   values filled by the best of several interpolation methods (chosen by hiding known values
   and scoring MAE, per the team's recipe).
2. **Daily ~1 km grid over metro Atlanta** — the 25 km grid sharpened with local clues (roads,
   land cover, elevation, weather) learned from regulatory monitors.

Everything downloads from public, keyless sources. Run from `backend/`.

```powershell
python -m pip install -r requirements/pm25.txt

# 1. A year of hourly GEOS-CF analysis for Georgia (the existing collector; slow for Dec-Jan,
#    which NASA's cloud copy lacks and OPeNDAP must supply).
python -m ingestion.geos_cf.fetch_analysis --region georgia --start 2025-10-01 --end 2026-10-01 --tz America/New_York --output data/geos_cf

# 2. Hourly -> daily (days, height, width) GeoTIFF + gap-fill comparison. --bbox crops to the team's box.
python -m ingestion.pm25.daily_grid data/geos_cf/geos_cf_analysis_georgia_20251001-20261001.nc --start 2025-10-01 --end 2026-09-30 --bbox 30.78,34.98,-85.60,-81.52 --output data/pm25

# 3. Monitor readings (AirNow's public daily archive, ~20 Georgia sites).
python -m ingestion.pm25.monitors --start 2025-10-01 --end 2026-09-30 --output data/pm25/monitors

# 4. 1 km downscaling over ingestion/regions/atlanta-metro.json.
python -m ingestion.pm25.downscale --daily data/pm25/pm25_daily_20251001-20260930.tif --monitors data/pm25/monitors --output data/pm25
```

## Outputs

| File | Contents |
| --- | --- |
| `pm25_daily_<start>-<end>.tif` | Daily mean PM2.5 (µg/m³), native 0.25° grid, north-up, band = day, band description = date |
| `pm25_daily_<start>-<end>.json` | Shape, dates, missing-value counts, gap-fill MAE per method and pattern, which method filled |
| `monitors/pm25_monitors.csv` | One row per monitor and day: `site`, `name`, `date`, `pm25` |
| `pm25_1km_<region>_<start>-<end>.tif` | Daily PM2.5 on the region's 0.01° grid (80 × 100 for atlanta-metro) |
| `pm25_1km_<region>_<start>-<end>.json` | Test scores (hidden-monitor and hidden-month MAE vs. GEOS-CF alone), feature importance |
| `cache/` | Downloaded clues (OSM, land cover, elevation, weather) so reruns skip the downloads |

Both GeoTIFFs are plain EPSG:4326 rasters, so `ingestion.grid_resample.resample_to_grid` can
put them on the model grid like any other layer.

## How the daily grid is made

* Hours are grouped into **local** (America/New_York) days; a day with fewer than 18 of its 24
  hours is treated as missing rather than averaged from a few hours.
* If anything is missing, five fill methods (`gapfill.METHODS`: linear and nearest in time,
  linear and inverse-distance in space, and their average) are scored by hiding known values in
  four realistic patterns (random cells, runs of consecutive days, 4×4 patches, whole days) over
  several seeds. The lowest-MAE method fills the real gaps and the scores go in the sidecar.
* With the Dec–Jan hours recovered from OPeNDAP, the 2025-10 → 2026-09 year has no gaps; the
  comparison still runs so the ranking is on record (spatial-linear wins, ~1 µg/m³ MAE).

## How the 1 km grid is made

For every monitor and day the model sees the 25 km GEOS-CF value plus the clues below, and
learns `log(monitor ÷ GEOS-CF)`. GEOS-CF keeps the regional day-to-day signal; the model adds
the local correction, which is then applied to every cell and day.

| Clue | Source | Resolution |
| --- | --- | --- |
| GEOS-CF PM2.5 | the daily grid above, bilinear to the point | 25 km |
| Weather: temperature, humidity, rain, wind, sunshine, mixing height | ERA5 via Open-Meteo archive | ~25 km, on a 0.1° lattice |
| Land cover fractions (tree, built-up, grass, …) | ESA WorldCover 2021, read straight from the public cloud tiles | 10 m |
| Elevation | Copernicus DEM, public cloud tiles | 30 m |
| Distance to motorway/trunk, to primary road, to industrial area; major-road length within 1 km | OpenStreetMap (Overpass) | vector |
| Day of year, weekday | calendar | — |

Model: scikit-learn `HistGradientBoostingRegressor`. Tests in the sidecar hide one monitor at a
time (spatial skill) and one month at a time (temporal skill); "GEOS-CF alone" is the baseline.
On the 2025-10 → 2026-09 year with 20 monitors the hidden-monitor MAE was 2.4 µg/m³ against
4.1 for GEOS-CF alone.

Limitations: only ~5–8 monitors sit inside metro Atlanta, so the fine pattern is learned from
how clues relate to PM2.5 statewide; no satellite AOD (needs an Earthdata login) or PurpleAir
(needs an API key) yet; land cover and roads are static snapshots.
