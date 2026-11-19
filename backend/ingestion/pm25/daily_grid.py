"""Daily PM2.5 GeoTIFF from a GEOS-CF analysis NetCDF (the output of ``fetch_analysis``).

One band per local day, stored (days, height, width) north-up on the native 0.25 deg grid,
with a JSON sidecar. If any daily values are missing, gap-filling methods are compared on
hidden known values and the best one fills the real gaps; the comparison lands in the sidecar.

Usage (from backend/):
    python -m ingestion.geos_cf.fetch_analysis --region georgia --start 2025-10-01 --end 2026-10-01 --tz America/New_York --output data/geos_cf
    python -m ingestion.pm25.daily_grid data/geos_cf/geos_cf_analysis_georgia_20251001-20261001.nc --output data/pm25
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Dict, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import rasterio
import xarray as xr
from rasterio.transform import from_origin

from ingestion.geos_cf import fetch_forecast as ff

from . import gapfill

logger = logging.getLogger(__name__)

VARIABLE = "pm25"
DEFAULT_TZ = "America/New_York"
MIN_HOURS = 18  # a day needs this many of its 24 hours or it counts as missing


def _netcdf_engine() -> Optional[str]:
    """netCDF4 when it imports; otherwise h5netcdf (xarray only checks that netCDF4 is installed)."""
    try:
        import netCDF4  # noqa: F401
    except ImportError:
        return "h5netcdf"
    return None


def load_hourly(path: Path, variable: str = VARIABLE) -> xr.DataArray:
    with xr.open_dataset(path, engine=_netcdf_engine()) as ds:
        if variable not in ds:
            raise ValueError(f"{path} has no '{variable}' variable (has {list(ds.data_vars)})")
        return ds[variable].load()


def crop(hourly: xr.DataArray, bbox: Dict[str, float]) -> xr.DataArray:
    """Keep every native cell whose footprint touches bbox."""
    sel = ff._grid_selection(bbox)
    lat_up = bool(hourly.lat[0] < hourly.lat[-1])
    lat_sel = sel["lat"] if lat_up else slice(sel["lat"].stop, sel["lat"].start)
    return hourly.sel(lat=lat_sel, lon=sel["lon"])


def daily_means(hourly: xr.DataArray, tz: str = DEFAULT_TZ, min_hours: int = MIN_HOURS) -> xr.DataArray:
    """Average each local day's hours; days with fewer than min_hours present are NaN.

    Time stamps are the UTC mid-hour means GEOS-CF uses, so 04:30Z on Oct 2 is Oct 1 local
    in Georgia. The returned 'day' coordinate holds naive local dates.
    """
    stamps = pd.DatetimeIndex(hourly["time"].values)
    local = stamps.tz_localize("UTC").tz_convert(tz).normalize().tz_localize(None)
    hourly = hourly.assign_coords(day=("time", local.values))
    counts = hourly.notnull().groupby("day").sum("time")
    means = hourly.groupby("day").mean("time")
    return means.where(counts >= min_hours)


def to_cube(daily: xr.DataArray, days: pd.DatetimeIndex) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(days, height, width) float32, north-up, reindexed onto `days` (missing days -> NaN)."""
    daily = daily.reindex(day=days)
    cube = daily.transpose("day", "lat", "lon").values.astype("float32")
    lat, lon = daily["lat"].values, daily["lon"].values
    if lat[0] < lat[-1]:
        cube, lat = cube[:, ::-1, :], lat[::-1]
    return cube, lat, lon


def write_geotiff(cube: np.ndarray, lat: np.ndarray, lon: np.ndarray, days: pd.DatetimeIndex, path: Path, tags: Dict[str, str]) -> None:
    """Band d = day d, band description = its date. Cells are centred on lat/lon."""
    res_lat = float(abs(lat[1] - lat[0])) if len(lat) > 1 else ff.NATIVE_RESOLUTION_DEG
    res_lon = float(abs(lon[1] - lon[0])) if len(lon) > 1 else ff.NATIVE_RESOLUTION_DEG
    transform = from_origin(float(lon[0]) - res_lon / 2, float(lat[0]) + res_lat / 2, res_lon, res_lat)
    tmp = path.with_suffix(".tmp.tif")
    with rasterio.open(
        tmp, "w", driver="GTiff", count=cube.shape[0], height=cube.shape[1], width=cube.shape[2],
        dtype="float32", crs="EPSG:4326", transform=transform, compress="deflate", nodata=np.nan,
    ) as dst:
        dst.write(cube)
        for d, day in enumerate(days):
            dst.set_band_description(d + 1, day.strftime("%Y-%m-%d"))
        dst.update_tags(dims="(days, height, width)", **tags)
    tmp.replace(path)


def build(
    hourly: xr.DataArray,
    start: str,
    end: str,
    tz: str = DEFAULT_TZ,
    min_hours: int = MIN_HOURS,
    seeds: int = 5,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, pd.DatetimeIndex, Dict[str, object]]:
    """Daily cube for local days start..end inclusive, gaps filled, plus a results dict."""
    days = pd.date_range(start, end, freq="D")
    cube, lat, lon = to_cube(daily_means(hourly, tz, min_hours), days)
    missing = np.isnan(cube)
    results: Dict[str, object] = {
        "shape": list(cube.shape),
        "days": [str(d.date()) for d in days],
        "lat": lat.tolist(), "lon": lon.tolist(),
        "missing_cells": int(missing.sum()),
        "missing_fraction": float(missing.mean()),
        "missing_days": [str(days[d].date()) for d in np.where(missing.all(axis=(1, 2)))[0]],
        "stats": {
            "min": float(np.nanmin(cube)), "mean": float(np.nanmean(cube)),
            "p99": float(np.nanpercentile(cube, 99)), "max": float(np.nanmax(cube)),
        },
    }
    if cube.shape[1] * cube.shape[2] >= 4 and seeds > 0:
        results["gapfill"] = gapfill.score_methods(cube, seeds=seeds)
    if missing.any():
        method = results["gapfill"]["best"] if "gapfill" in results else "time_linear"
        cube = gapfill.fill(cube, method)
        results["filled_with"] = method
    cube = np.clip(cube, 0, None).astype("float32")
    return cube, lat, lon, days, results


def run(nc_path: Path, output_dir: Path, bbox: Optional[Dict[str, float]], start: str, end: str, tz: str, min_hours: int, seeds: int) -> Tuple[Path, Path]:
    hourly = load_hourly(nc_path)
    if bbox:
        hourly = crop(hourly, bbox)
    cube, lat, lon, days, results = build(hourly, start, end, tz, min_hours, seeds)
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"pm25_daily_{start.replace('-', '')}-{end.replace('-', '')}"
    tif, sidecar = output_dir / f"{stem}.tif", output_dir / f"{stem}.json"
    write_geotiff(cube, lat, lon, days, tif, {
        "variable": VARIABLE, "units": "ug m-3", "source": f"{ff.SOURCE} {ff.SOURCE_VERSION} analysis",
        "aggregation": f"daily mean of hourly values, {tz} days, >= {min_hours} hours",
        "source_file": nc_path.name,
    })
    results.update(file=tif.name, source_file=nc_path.name, tz=tz, min_hours=min_hours)
    sidecar.write_text(json.dumps(results, indent=2) + "\n")
    logger.info("Daily PM2.5 grid: %s x %s x %s, %d missing cells (%.2f%%)%s", *cube.shape, results["missing_cells"],
                100 * results["missing_fraction"], f", filled with {results['filled_with']}" if "filled_with" in results else "")
    if "gapfill" in results:
        logger.info("Gap-fill MAE (ug/m3): %s", {k: round(v, 3) for k, v in results["gapfill"]["overall"].items()})
    logger.info("  output  %s", tif)
    logger.info("  sidecar %s", sidecar)
    return tif, sidecar


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Daily PM2.5 GeoTIFF (days x height x width) from a GEOS-CF analysis NetCDF.")
    parser.add_argument("netcdf", type=Path, help="output of ingestion.geos_cf.fetch_analysis")
    parser.add_argument("--start", help="first local day (default: first full day in the file)")
    parser.add_argument("--end", help="last local day, inclusive (default: last full day in the file)")
    parser.add_argument("--bbox", type=lambda t: ff._floats(t, 4), metavar="MIN_LAT,MAX_LAT,MIN_LON,MAX_LON", help="crop to the cells touching this box")
    parser.add_argument("--tz", default=DEFAULT_TZ)
    parser.add_argument("--min-hours", type=int, default=MIN_HOURS)
    parser.add_argument("--seeds", type=int, default=5, help="repeats of the gap-fill comparison; 0 skips it")
    parser.add_argument("--output", type=Path, default=Path("data/pm25"))
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    hourly = load_hourly(args.netcdf)
    stamps = pd.DatetimeIndex(hourly["time"].values).tz_localize("UTC").tz_convert(args.tz)
    start = args.start or str((stamps[0] + pd.Timedelta(days=1)).date())
    end = args.end or str((stamps[-1] - pd.Timedelta(days=1)).date())
    bbox = None
    if args.bbox:
        min_lat, max_lat, min_lon, max_lon = args.bbox
        bbox = {"min_lat": min_lat, "max_lat": max_lat, "min_lon": min_lon, "max_lon": max_lon}
    try:
        run(args.netcdf, args.output, bbox, start, end, args.tz, args.min_hours, args.seeds)
    except (ValueError, OSError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
