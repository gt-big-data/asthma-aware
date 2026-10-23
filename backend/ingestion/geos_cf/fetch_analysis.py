"""NASA GEOS-CF v2 analysis (historical) ingestion.

The analysis is GEOS-CF's continuous hourly reconstruction of past conditions: the model
run alongside observations, as opposed to the 5-day forecasts in fetch_forecast. Same
variables, grid and units as the forecast, written as NetCDF (time x lat x lon) plus a
metadata.json sidecar.

Data comes from GMAO's Zarr copy on S3 where it has it (seconds for a year, any area) and
from NASA's OPeNDAP server for the rest (slow: ~0.1 s per variable-hour).

Usage:
    python -m ingestion.geos_cf.fetch_analysis --point 33.75,-84.39 \\
        --start 2025-10-01 --end 2026-10-01 --tz America/New_York

Run from the backend/ directory so that `ingestion` resolves as a package.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import xarray as xr

from ingestion.geos_cf import fetch_forecast as ff

logger = logging.getLogger(__name__)

ANALYSIS_ROOT = "https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/v2/ana"
# GMAO's cloud copy of the same analysis ("replay"), stored as 663-hour x 50 x 50 cell tiles.
# A year at any area reads in seconds, but as of 2026-10 it lacks specific humidity, is missing
# Dec 2025-Jan 2026, and lags a day or so behind. Gaps are filled from OPeNDAP.
ZARR_URL = "https://smce-geos-cf-public.s3.amazonaws.com/geos-cf-v2-rpl.zarr"
# OPeNDAP reads one hourly file per time step per variable (~0.1 s/hour, regardless of area),
# and NCCS's proxy drops requests after roughly a minute: a month (744 h) failed repeatedly,
# while a week finishes in ~15 s.
CHUNK_HOURS = 168
DEFAULT_WORKERS = 6
# The netCDF/HDF5 library isn't thread-safe, so local cache reads/writes are serialized.
_NETCDF_LOCK = threading.Lock()


def analysis_url(collection: str) -> str:
    return f"{ANALYSIS_ROOT}/{collection}"


def time_indices(times: np.ndarray, window: ff.TimeWindow) -> slice:
    """Indices of the hourly means overlapping [start, end). Stamps are mid-hour."""
    start = window.start.to_datetime64()
    end = (window.end or window.start).to_datetime64()
    half = np.timedelta64(30, "m")
    first = int(np.searchsorted(times, start - half, side="right"))
    stop = int(np.searchsorted(times, end + half, side="left"))
    if window.end is None or window.end == window.start:
        stop = first + 1
    return slice(first, stop)


def native_grid(area: ff.Area) -> Tuple[np.ndarray, np.ndarray]:
    """The GEOS-CF 0.25 deg cell centers inside the area's selection."""
    sel = ff._grid_selection(area.bbox)
    lat = np.arange(-90, 90.001, ff.NATIVE_RESOLUTION_DEG)
    lon = np.arange(-180, 180, ff.NATIVE_RESOLUTION_DEG)
    lat = lat[(lat >= sel["lat"].start) & (lat <= sel["lat"].stop)]
    lon = lon[(lon >= sel["lon"].start) & (lon <= sel["lon"].stop)]
    return lat, lon


def _place(target: np.ndarray, times: pd.DatetimeIndex, lat: np.ndarray, lon: np.ndarray, data: xr.DataArray) -> int:
    """Copy data's non-missing hours into target (time x lat x lon). Returns hours written."""
    if not (np.allclose(data["lat"].values, lat) and np.allclose(data["lon"].values, lon)):
        raise ff.ValidationError(f"{data.name}: grid does not match the native 0.25 deg grid")
    values = data.transpose("time", "lat", "lon").values
    positions = times.get_indexer(pd.DatetimeIndex(data["time"].values))
    present = (positions >= 0) & ~np.isnan(values).all(axis=(1, 2))
    target[positions[present]] = values[present]
    return int(present.sum())


def _open_zarr() -> xr.Dataset:
    ds = xr.open_zarr(ZARR_URL, consolidated=True)
    # The store has repeated appends (September 2025 appears twice); keep each hour's last copy.
    _, last = np.unique(ds["time"].values[::-1], return_index=True)
    return ds.isel(time=np.sort(ds.sizes["time"] - 1 - last))


def read_zarr(area: ff.Area, times: pd.DatetimeIndex) -> Dict[str, xr.DataArray]:
    """Every variable the cloud copy has for area/times. Hours it lacks are simply absent or NaN."""
    try:
        zds = _open_zarr()
    except Exception as exc:  # noqa: BLE001 - the cloud copy is an accelerator; OPeNDAP still works
        logger.warning("Cloud copy unavailable (%s); downloading everything from OPeNDAP", exc)
        return {}
    specs = [s for s in ff.VARIABLES if s.source_name in zds.data_vars]
    for spec in specs:
        units = zds[spec.source_name].attrs.get("units", "")
        if units.lower() != spec.units.lower():  # the Zarr copy writes "K" where OPeNDAP says "k"
            raise ff.ValidationError(f"cloud copy of {spec.source_name} has units '{units}', expected '{spec.units}'")
    half = pd.Timedelta(minutes=30)
    sub = zds[[s.source_name for s in specs]].sel(**ff._grid_selection(area.bbox)).sel(time=slice(times[0] - half, times[-1] + half))
    if "lev" in sub.dims:
        sub = sub.isel(lev=0, drop=True)
    started = time.monotonic()
    sub = sub.load()  # dask fetches the tiles for all variables in parallel
    logger.info("Read %d variables x %d hours from the cloud copy in %.0fs", len(specs), sub.sizes["time"], time.monotonic() - started)
    return {spec.name: sub[spec.source_name] for spec in specs}


def _fetch_range(
    url: str, spec: ff.VariableSpec, selection: Dict[str, slice], lead: slice, retries: int
) -> Optional[xr.DataArray]:
    """Fetch lead from OPeNDAP. If it keeps failing, bisect: some hourly files on the server
    are unreadable (instant 502 for every variable, e.g. around 2025-12-28), and retrying
    can't fix those. Readable hours are returned; unreadable ones are left out."""
    try:
        return ff.remote_call(
            ff._read_variable, url, spec.source_name, selection, lead,
            what=f"Downloading {spec.source_name} [{lead.start}:{lead.stop}]", retries=retries,
        )
    except OSError as exc:
        if lead.stop - lead.start == 1:
            logger.warning("%s hour index %d is unreadable on the server (%s); leaving it missing", spec.source_name, lead.start, exc)
            return None
    mid = (lead.start + lead.stop) // 2
    # One attempt per half: the hour that broke the whole chunk fails fast with a 502.
    halves = [_fetch_range(url, spec, selection, part, retries=1) for part in (slice(lead.start, mid), slice(mid, lead.stop))]
    halves = [h for h in halves if h is not None]
    return xr.concat(halves, dim="time") if halves else None


def _fetch_chunk(
    url: str, spec: ff.VariableSpec, selection: Dict[str, slice], lead: slice, cache_dir: Optional[Path]
) -> Optional[xr.DataArray]:
    """Download one variable x up to a week from OPeNDAP, reusing a cached copy from an earlier attempt."""
    cache_path = None if cache_dir is None else cache_dir / f"{spec.source_name}_{lead.start}_{lead.stop}.nc"
    if cache_path is not None and cache_path.exists():
        with _NETCDF_LOCK, xr.open_dataarray(cache_path) as cached:
            return cached.load()
    data = _fetch_range(url, spec, selection, lead, ff.OPEN_RETRIES)
    if data is None:
        return None
    if cache_path is not None:
        tmp = cache_path.with_suffix(".tmp")
        with _NETCDF_LOCK:
            data.to_netcdf(tmp)
        tmp.replace(cache_path)
    return data


def _runs(indices: np.ndarray, max_len: int) -> List[slice]:
    """Split sorted indices into contiguous slices of at most max_len."""
    out: List[slice] = []
    for i in indices:
        if out and out[-1].stop == i and out[-1].stop - out[-1].start < max_len:
            out[-1] = slice(out[-1].start, i + 1)
        else:
            out.append(slice(int(i), int(i) + 1))
    return out


def build_analysis(
    area: ff.Area,
    window: ff.TimeWindow,
    workers: int = DEFAULT_WORKERS,
    cache_dir: Optional[Path] = None,
    use_cloud: bool = True,
) -> Tuple[xr.Dataset, List[str], Dict[str, int]]:
    """Assemble the analysis for area/window: the cloud copy first, then OPeNDAP for whatever
    it lacks. OPeNDAP chunks are cached in cache_dir so a rerun after a failure only fetches
    what's missing; the cache is the caller's to clean up. Also returns, per variable, how
    many hours came from OPeNDAP."""
    lat, lon = native_grid(area)
    long_names: Dict[str, str] = {}
    urls: List[str] = []
    times: Optional[pd.DatetimeIndex] = None
    offset = 0  # index of the window's first hour on the OPeNDAP time axis
    for collection in ff.COLLECTIONS:
        url = analysis_url(collection)
        urls.append(url)
        names, axis, _ = ff.remote_call(ff._read_inventory, url, what=f"Opening {url}")
        idx = time_indices(axis, window)
        if idx.start >= len(axis) or idx.stop > len(axis) or idx.stop <= idx.start:
            available = f"{pd.Timestamp(axis[0]).isoformat()}Z to {pd.Timestamp(axis[-1]).isoformat()}Z"
            raise ff.ForecastUnavailableError(f"{window.describe()} is outside the analysis record ({available})")
        window_times = pd.DatetimeIndex(axis[idx])
        if times is not None and not window_times.equals(times):
            raise ff.ValidationError("aqc and met analysis collections disagree on the time axis")
        times, offset = window_times, idx.start
        for spec in ff.VARIABLES:
            if spec.collection == collection:
                if spec.source_name not in names:
                    raise ff.ValidationError(f"{url} is missing required variable {spec.source_name}")
                long_names[spec.name] = names[spec.source_name]

    grids = {spec.name: np.full((len(times), lat.size, lon.size), np.nan, dtype="float32") for spec in ff.VARIABLES}
    if use_cloud:
        for name, data in read_zarr(area, times).items():
            _place(grids[name], times, lat, lon, data)

    jobs = []  # (spec, url, OPeNDAP index slice)
    for spec in ff.VARIABLES:
        missing = np.where(np.isnan(grids[spec.name]).all(axis=(1, 2)))[0]
        for run in _runs(missing + offset, CHUNK_HOURS):
            jobs.append((spec, analysis_url(spec.collection), run))
    from_opendap = {spec.name: 0 for spec in ff.VARIABLES}
    if jobs:
        gap_hours = sum(r.stop - r.start for _, _, r in jobs)
        logger.info("Filling %d variable-hours the cloud copy lacks from OPeNDAP: %d chunks, %d at a time", gap_hours, len(jobs), workers)
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
        selection = ff._grid_selection(area.bbox)
        started = time.monotonic()
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(_fetch_chunk, url, spec, selection, lead, cache_dir): spec for spec, url, lead in jobs}
            for done, future in enumerate(as_completed(futures), 1):
                spec = futures[future]
                data = future.result()
                if data is None:
                    logger.warning("[%3d/%d] %-18s chunk entirely unreadable on the server", done, len(jobs), spec.name)
                    continue
                from_opendap[spec.name] += _place(grids[spec.name], times, lat, lon, data)
                elapsed = time.monotonic() - started
                logger.info(
                    "[%3d/%d] %-18s %s .. %s  (%.0fs elapsed, ~%.0fs left)", done, len(jobs), spec.name,
                    str(data["time"].values[0])[:13], str(data["time"].values[-1])[:13], elapsed, elapsed / done * (len(jobs) - done),
                )
    else:
        logger.info("The cloud copy covered everything; nothing to fetch from OPeNDAP")

    ds = xr.Dataset(coords={"time": times.values, "lat": lat, "lon": lon})
    for spec in ff.VARIABLES:
        ds[spec.name] = (("time", "lat", "lon"), grids[spec.name], {
            "units": ff.parse_units(long_names[spec.name]) or "",
            "long_name": spec.description,
            "source_name": spec.source_name,
            "source_collection": spec.collection,
            "source_long_name": long_names[spec.name],
        })
    ds["time"].attrs.update({"long_name": "valid time (center of hourly averaging window)", "standard_name": "time"})
    ds["lat"].attrs.update({"long_name": "latitude (grid cell center)", "units": "degrees_north", "standard_name": "latitude"})
    ds["lon"].attrs.update({"long_name": "longitude (grid cell center)", "units": "degrees_east", "standard_name": "longitude"})
    return ds, ([ZARR_URL] if use_cloud else []) + urls, from_opendap


def validate_analysis(ds: xr.Dataset, expected_hours: int) -> Dict[str, int]:
    """Like the forecast validation, but isolated missing hours are reported, not fatal."""
    errors: List[str] = []
    if ds.sizes.get("time") != expected_hours:
        errors.append(f"expected {expected_hours} hours, got {ds.sizes.get('time')}")
    steps = np.diff(ds["time"].values)
    if np.any(steps != np.timedelta64(1, "h")):
        errors.append("valid times are not strictly increasing in 1-hour steps")
    for dim in ("lat", "lon"):
        if ds.sizes.get(dim, 0) == 0:
            errors.append(f"dimension '{dim}' is empty")
    missing_hours: Dict[str, int] = {}
    for spec in ff.VARIABLES:
        var = ds[spec.name]
        if var.attrs["units"] != spec.units:
            errors.append(f"'{spec.name}' units: expected '{spec.units}', got '{var.attrs['units']}'")
        if bool(var.isnull().all()):
            errors.append(f"'{spec.name}' is entirely NaN")
        missing_hours[spec.name] = int(var.isnull().all(dim=["lat", "lon"]).sum())
    if errors:
        raise ff.ValidationError("GEOS-CF analysis failed validation:\n  - " + "\n  - ".join(errors))
    return missing_hours


def write_analysis(ds: xr.Dataset, metadata: Dict[str, object], output_dir: Path) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    first, last = metadata["valid_time_start"], metadata["valid_time_end"]
    stem = f"geos_cf_analysis_{metadata['region']}_{first[:10].replace('-', '')}-{last[:10].replace('-', '')}"
    nc_path, json_path = output_dir / f"{stem}.nc", output_dir / f"{stem}.metadata.json"
    ds.attrs = {k: v if isinstance(v, (str, int, float, list)) else json.dumps(v) for k, v in metadata.items() if v is not None}
    encoding = {name: {"zlib": True, "complevel": 4, "dtype": "float32"} for name in ds.data_vars}
    encoding["time"] = {"units": "hours since 2000-01-01 00:00:00", "dtype": "float64"}
    tmp_path = nc_path.with_suffix(".nc.tmp")
    ds.to_netcdf(tmp_path, encoding=encoding, format="NETCDF4")
    tmp_path.replace(nc_path)
    json_path.write_text(json.dumps(metadata, indent=2) + "\n")
    return nc_path, json_path


def run(
    area: ff.Area, window: ff.TimeWindow, output_dir: Path, workers: int = DEFAULT_WORKERS, use_cloud: bool = True
) -> Path:
    logger.info("GEOS-CF analysis for %s, %s", area.name, window.describe())
    downloaded_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    cache_dir = output_dir / ".cache" / f"analysis_{area.name}_{window.start:%Y%m%dT%H}_{window.end:%Y%m%dT%H}"
    ds, urls, from_opendap = build_analysis(area, window, workers, cache_dir, use_cloud)
    missing = validate_analysis(ds, ds.sizes["time"])
    valid_times = [pd.Timestamp(t).isoformat() + "Z" for t in ds["time"].values]
    metadata = {
        "source": ff.SOURCE,
        "source_version": ff.SOURCE_VERSION,
        "product": "analysis",
        "source_urls": urls,
        "downloaded_at": downloaded_at.isoformat().replace("+00:00", "Z"),
        "requested_time_window": window.describe(),
        "hours": len(valid_times),
        "valid_time_start": valid_times[0],
        "valid_time_end": valid_times[-1],
        "time_convention": "hourly means stamped at the center of the hour",
        "native_spatial_resolution": ff.NATIVE_SPATIAL_RESOLUTION,
        "region": area.name,
        "region_bbox": area.bbox,
        "requested_point": None if area.point is None else {"lat": area.point[0], "lon": area.point[1]},
        "variables": list(ds.data_vars),
        "variable_source_names": {n: ds[n].attrs["source_name"] for n in ds.data_vars},
        "units": {n: ds[n].attrs["units"] for n in ds.data_vars},
        "missing_hours": missing,
        "hours_from_opendap": from_opendap,
        "hours_from_cloud_copy": {n: len(valid_times) - from_opendap[n] - missing[n] for n in ds.data_vars},
    }
    nc_path, json_path = write_analysis(ds, metadata, output_dir)
    shutil.rmtree(cache_dir, ignore_errors=True)

    logger.info("GEOS-CF analysis ingested and validated")
    logger.info("  first valid time     %s", valid_times[0])
    logger.info("  last valid time      %s", valid_times[-1])
    logger.info("  hours                %d", len(valid_times))
    logger.info("  grid                 %d lat x %d lon cells", ds.sizes["lat"], ds.sizes["lon"])
    gaps = {k: v for k, v in missing.items() if v}
    logger.info("  missing hours        %s", gaps or "none")
    logger.info("  from OPeNDAP         %s", {k: v for k, v in from_opendap.items() if v} or "nothing")
    logger.info("  output               %s (%s)", nc_path, ff._format_bytes(nc_path.stat().st_size))
    logger.info("  metadata             %s", json_path)
    return nc_path


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Download NASA GEOS-CF v2 analysis (historical hourly) data for Georgia.")
    where = parser.add_mutually_exclusive_group()
    where.add_argument("--region", choices=sorted(ff.REGIONS))
    where.add_argument("--point", type=lambda t: ff._floats(t, 2), metavar="LAT,LON")
    where.add_argument("--bbox", type=lambda t: ff._floats(t, 4), metavar="MIN_LAT,MAX_LAT,MIN_LON,MAX_LON")
    parser.add_argument("--start", required=True, help="first time (ISO 8601)")
    parser.add_argument("--end", required=True, help="end time, not included (ISO 8601)")
    parser.add_argument("--tz", default="UTC", help="timezone for times without an offset (default UTC)")
    parser.add_argument("--output", type=Path, default=Path("data/geos_cf"))
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS, help="parallel requests to NASA")
    parser.add_argument("--no-cloud", action="store_true", help="skip the S3 cloud copy and use only OPeNDAP")
    args = parser.parse_args(argv)

    try:
        if args.point:
            area = ff.point_area(*args.point)
        elif args.bbox:
            area = ff.bbox_area(*args.bbox)
        else:
            area = ff.region_area(args.region or "georgia")
        window = ff.TimeWindow(ff.parse_time(args.start, args.tz), ff.parse_time(args.end, args.tz))
    except ValueError as exc:
        parser.error(str(exc))
    if window.end <= window.start:
        parser.error("--end must be after --start")

    try:
        run(area, window, args.output, args.workers, use_cloud=not args.no_cloud)
    except (ff.ForecastUnavailableError, ff.ValidationError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
