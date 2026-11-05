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
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union

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


def monthly_windows(start: str, end: str, days: int, tz: str = "UTC") -> List[ff.TimeWindow]:
    """The first `days` days of every month in [start, end), counted in local time `tz`."""
    lo, hi = pd.Timestamp(start), pd.Timestamp(end)
    lo = lo.tz_localize(tz) if lo.tzinfo is None else lo.tz_convert(tz)
    hi = hi.tz_localize(tz) if hi.tzinfo is None else hi.tz_convert(tz)
    windows = []
    month = lo.normalize().replace(day=1)
    while month < hi:
        next_month = month + pd.DateOffset(months=1)
        w_start, w_end = max(month, lo), min(month + pd.DateOffset(days=days), next_month, hi)
        if w_end > w_start:
            windows.append(ff.TimeWindow(w_start.tz_convert("UTC").tz_localize(None), w_end.tz_convert("UTC").tz_localize(None)))
        month = next_month
    return windows


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


def _cached_cover(cache_dir: Path, source_name: str, lead: slice) -> Optional[Path]:
    """A cached chunk of this variable whose index range contains lead, if any."""
    for path in cache_dir.glob(f"{source_name}_*_*.nc"):
        try:
            a, b = (int(x) for x in path.stem[len(source_name) + 1:].split("_"))
        except ValueError:
            continue
        if a <= lead.start and lead.stop <= b:
            return path
    return None


def _fetch_chunk(
    url: str, spec: ff.VariableSpec, selection: Dict[str, slice], lead: slice,
    lead_times: pd.DatetimeIndex, cache_dir: Optional[Path],
) -> Tuple[Optional[xr.DataArray], Optional[float]]:
    """One variable x up to a week from OPeNDAP, or from any cached chunk that covers it.

    Returns (data, seconds spent downloading); seconds is None when it came from the cache.
    Cache files are named by absolute OPeNDAP time index, so any run for the same area can
    reuse them regardless of its own date window."""
    if cache_dir is not None:
        cover = _cached_cover(cache_dir, spec.source_name, lead)
        if cover is not None:
            with _NETCDF_LOCK, xr.open_dataarray(cover) as cached:
                return cached.sel(time=cached["time"].isin(lead_times.values)).load(), None
    started = time.monotonic()
    data = _fetch_range(url, spec, selection, lead, ff.OPEN_RETRIES)
    seconds = time.monotonic() - started
    if data is not None and cache_dir is not None:
        cache_path = cache_dir / f"{spec.source_name}_{lead.start}_{lead.stop}.nc"
        tmp = cache_path.with_suffix(".tmp")
        with _NETCDF_LOCK:
            data.to_netcdf(tmp)
        tmp.replace(cache_path)
    return data, seconds


def _runs(indices: np.ndarray, max_len: int) -> List[slice]:
    """Split sorted indices into contiguous slices that never cross a multiple of max_len.

    Aligning chunks to a fixed grid on the absolute time axis means overlapping requests
    (e.g. a year, then one month of it) ask for, and can reuse, the same cached chunks."""
    out: List[slice] = []
    for i in indices:
        if out and out[-1].stop == i and i % max_len != 0:
            out[-1] = slice(out[-1].start, int(i) + 1)
        else:
            out.append(slice(int(i), int(i) + 1))
    return out


def build_analysis(
    area: ff.Area,
    window: Union[ff.TimeWindow, Sequence[ff.TimeWindow]],
    workers: int = DEFAULT_WORKERS,
    cache_dir: Optional[Path] = None,
    use_cloud: bool = True,
) -> Tuple[xr.Dataset, List[str], Dict[str, int]]:
    """Assemble the analysis for area and one or more time windows: the cloud copy first, then OPeNDAP for whatever
    it lacks. OPeNDAP chunks are cached in cache_dir (per area) and reused by later runs.
    Also returns, per variable, how many hours came from OPeNDAP."""
    lat, lon = native_grid(area)
    long_names: Dict[str, str] = {}
    urls: List[str] = []
    windows = [window] if isinstance(window, ff.TimeWindow) else list(window)
    times: Optional[pd.DatetimeIndex] = None
    axis_times: Optional[pd.DatetimeIndex] = None  # the full OPeNDAP time axis
    positions = np.array([], dtype=int)  # OPeNDAP time index of each requested hour
    for collection in ff.COLLECTIONS:
        url = analysis_url(collection)
        urls.append(url)
        names, axis, _ = ff.remote_call(ff._read_inventory, url, what=f"Opening {url}")
        parts = []
        for w in windows:
            idx = time_indices(axis, w)
            if idx.start >= len(axis) or idx.stop > len(axis) or idx.stop <= idx.start:
                available = f"{pd.Timestamp(axis[0]).isoformat()}Z to {pd.Timestamp(axis[-1]).isoformat()}Z"
                raise ff.ForecastUnavailableError(f"{w.describe()} is outside the analysis record ({available})")
            parts.append(np.arange(idx.start, idx.stop))
        collection_positions = np.unique(np.concatenate(parts))
        window_times = pd.DatetimeIndex(axis[collection_positions])
        if times is not None and not window_times.equals(times):
            raise ff.ValidationError("the analysis collections disagree on the time axis")
        times, positions, axis_times = window_times, collection_positions, pd.DatetimeIndex(axis)
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
        for run in _runs(positions[missing], CHUNK_HOURS):
            jobs.append((spec, analysis_url(spec.collection), run))
    from_opendap = {spec.name: 0 for spec in ff.VARIABLES}
    if jobs:
        gap_hours = sum(r.stop - r.start for _, _, r in jobs)
        logger.info("Filling %d variable-hours the cloud copy lacks from OPeNDAP: %d chunks, %d at a time", gap_hours, len(jobs), workers)
        if cache_dir is not None:
            cache_dir.mkdir(parents=True, exist_ok=True)
        selection = ff._grid_selection(area.bbox)
        started = time.monotonic()
        durations: List[float] = []
        cached = 0
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_fetch_chunk, url, spec, selection, lead, axis_times[lead], cache_dir): (spec, lead)
                for spec, url, lead in jobs
            }
            for done, future in enumerate(as_completed(futures), 1):
                spec, lead = futures[future]
                data, seconds = future.result()
                if seconds is None:
                    cached += 1
                    took = "cached"
                else:
                    durations.append(seconds)
                    took = f"{seconds:.0f}s"
                elapsed = time.monotonic() - started
                downloads_left = len(jobs) - done
                # Time left assumes the remaining chunks download at the pace seen so far.
                eta = (elapsed / max(len(durations), 1)) * downloads_left if durations else 0
                if data is None:
                    logger.warning("[%3d/%d] %-18s chunk entirely unreadable on the server (%s)", done, len(jobs), spec.name, took)
                    continue
                from_opendap[spec.name] += _place(grids[spec.name], times, lat, lon, data)
                logger.info(
                    "[%3d/%d] %-18s %s .. %s  %7s  (%.0fs elapsed, ~%.0fs left)", done, len(jobs), spec.name,
                    str(axis_times[lead.start])[:13], str(axis_times[lead.stop - 1])[:13], took, elapsed, eta,
                )
        if durations:
            logger.info(
                "OPeNDAP: %d chunks downloaded (median %.0fs, slowest %.0fs), %d reused from cache, %.0fs total",
                len(durations), float(np.median(durations)), max(durations), cached, time.monotonic() - started,
            )
        else:
            logger.info("OPeNDAP: all %d chunks reused from cache", cached)
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
    # Steps are 1 h within a window; sampled requests (e.g. --first-days) jump between windows.
    if np.any(steps <= np.timedelta64(0, "s")) or np.any(steps % np.timedelta64(1, "h") != np.timedelta64(0, "s")):
        errors.append("valid times are not strictly increasing on the hourly grid")
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
    if metadata.get("sampling"):
        stem += f"_{metadata['sampling']}"
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
    area: ff.Area,
    window: Union[ff.TimeWindow, Sequence[ff.TimeWindow]],
    output_dir: Path,
    workers: int = DEFAULT_WORKERS,
    use_cloud: bool = True,
    sampling: Optional[str] = None,
) -> Path:
    """sampling labels a non-contiguous request (e.g. "first5days") in the file name and metadata."""
    windows = [window] if isinstance(window, ff.TimeWindow) else list(window)
    described = [w.describe() for w in windows]
    logger.info("GEOS-CF analysis for %s, %s", area.name, described[0] if len(windows) == 1 else f"{len(windows)} windows ({sampling})")
    downloaded_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    # Kept after the run: later requests for the same area reuse any chunks they overlap.
    cache_dir = output_dir / ".cache" / f"analysis_{area.name}"
    ds, urls, from_opendap = build_analysis(area, windows, workers, cache_dir, use_cloud)
    missing = validate_analysis(ds, ds.sizes["time"])
    valid_times = [pd.Timestamp(t).isoformat() + "Z" for t in ds["time"].values]
    metadata = {
        "source": ff.SOURCE,
        "source_version": ff.SOURCE_VERSION,
        "product": "analysis",
        "source_urls": urls,
        "downloaded_at": downloaded_at.isoformat().replace("+00:00", "Z"),
        "requested_time_window": described[0] if len(windows) == 1 else described,
        "sampling": sampling,
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
    parser.add_argument(
        "--first-days", type=int, metavar="N",
        help="only the first N days of each month between --start and --end (days counted in --tz)",
    )
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
        if window.end <= window.start:
            parser.error("--end must be after --start")
        windows: Union[ff.TimeWindow, List[ff.TimeWindow]] = window
        sampling = None
        if args.first_days is not None:
            if not 1 <= args.first_days <= 31:
                parser.error("--first-days must be between 1 and 31")
            windows = monthly_windows(args.start, args.end, args.first_days, args.tz)
            sampling = f"first{args.first_days}days"
    except ValueError as exc:
        parser.error(str(exc))

    try:
        run(area, windows, args.output, args.workers, use_cloud=not args.no_cloud, sampling=sampling)
    except (ff.ForecastUnavailableError, ff.ValidationError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
