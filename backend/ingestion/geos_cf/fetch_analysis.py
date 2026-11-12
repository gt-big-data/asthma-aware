"""NASA GEOS-CF analysis (historical) ingestion, v2 (Aug 2025 on) and v1 (2018 to Jan 2026).

The analysis is GEOS-CF's continuous hourly reconstruction of past conditions: the model
run alongside observations, as opposed to the 5-day forecasts in fetch_forecast. Same
variables, grid and units as the forecast, written as NetCDF (time x lat x lon) plus a
metadata.json sidecar.

Data comes from GMAO's Zarr copy on S3 where it has it (seconds for a year, any area) and
from NASA's OPeNDAP server for the rest (slow: ~0.1 s per variable-hour).

Dates before v2 began (2025-08-04) come from GEOS-CF v1, an earlier model version: same
output columns and units, but no PM10 (kept as an all-missing column), and values are not
strictly comparable with v2's.

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
from dataclasses import dataclass
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


@dataclass(frozen=True)
class Product:
    """One GEOS-CF analysis record: where it lives and how its variables map to our columns."""

    version: str
    opendap_root: str
    zarr_url: str
    specs: Tuple[ff.VariableSpec, ...]  # our column name -> this record's collection/variable
    unavailable: Tuple[str, ...]  # our columns this record can't supply; written as all-NaN
    units_in_long_name: bool  # v2's GrADS server puts units in long_name; v1's doesn't
    record_start: pd.Timestamp


V2 = Product("v2", ANALYSIS_ROOT, ZARR_URL, ff.VARIABLES, (), True, pd.Timestamp("2025-08-04T09:00"))

V1_AQC = "aqc_tavg_1hr_g1440x721_v1"
V1_MET = "met_tavg_1hr_g1440x721_x1"
V1_XGC = "xgc_tavg_1hr_g1440x721_x1"


def _v1_spec(name: str, source_name: str, collection: str) -> ff.VariableSpec:
    """A v1 source for one of our columns, with the same output units as v2.

    v1's OPeNDAP has no units, so they come from our v2 spec; the v1 Zarr copy (which does
    carry units) is checked against them in read_zarr."""
    v2 = next(s for s in ff.VARIABLES if s.name == name)
    return ff.VariableSpec(name, source_name, collection, v2.units, f"{v2.description} (GEOS-CF v1)")


V1 = Product(
    "v1",
    "https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/assim",
    "https://smce-geos-cf-public.s3.amazonaws.com/geos-cf-rpl.zarr",
    (
        _v1_spec("pm25", "pm25_rh35_gcc", V1_AQC),
        _v1_spec("no2", "no2", V1_AQC),
        _v1_spec("o3", "o3", V1_AQC),
        _v1_spec("co", "co", V1_AQC),
        _v1_spec("air_temperature", "t", V1_MET),
        _v1_spec("specific_humidity", "q", V1_MET),
        _v1_spec("relative_humidity", "rh", V1_MET),
        _v1_spec("surface_pressure", "ps", V1_MET),
        _v1_spec("precipitation", "tprec", V1_MET),
        _v1_spec("pbl_height", "zpbl", V1_MET),
        _v1_spec("tropcol_no2", "tropcol_no2", V1_XGC),
    ),
    ("pm10",),
    False,
    pd.Timestamp("2018-01-01T00:00"),
)
PRODUCTS = {"v1": V1, "v2": V2}


def product_for(windows: Sequence[ff.TimeWindow]) -> Product:
    """v2 where it exists, v1 for anything earlier. A request can't straddle the two."""
    first = min(w.start for w in windows)
    last = max(w.end or w.start for w in windows)
    if first >= V2.record_start:
        return V2
    if last <= V2.record_start:
        return V1
    raise ValueError(
        f"the request spans the switch from GEOS-CF v1 to v2 on {V2.record_start:%Y-%m-%d}; "
        "split it there, since the two model versions aren't strictly comparable"
    )


def analysis_url(collection: str, product: Product = V2) -> str:
    return f"{product.opendap_root}/{collection}"


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


def _open_zarr(url: str = ZARR_URL) -> xr.Dataset:
    ds = xr.open_zarr(url, consolidated=True)
    # Both stores have repeated appends (e.g. September 2025 twice); keep each hour's last copy.
    _, last = np.unique(ds["time"].values[::-1], return_index=True)
    return ds.isel(time=np.sort(ds.sizes["time"] - 1 - last))


def read_zarr(area: ff.Area, times: pd.DatetimeIndex, product: Product = V2) -> Dict[str, xr.DataArray]:
    """Every variable the cloud copy has for area/times. Hours it lacks are simply absent or NaN."""
    try:
        zds = _open_zarr(product.zarr_url)
    except Exception as exc:  # noqa: BLE001 - the cloud copy is an accelerator; OPeNDAP still works
        logger.warning("Cloud copy unavailable (%s); downloading everything from OPeNDAP", exc)
        return {}
    specs = [s for s in product.specs if s.source_name in zds.data_vars]
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
    product: Product = V2,
) -> Tuple[xr.Dataset, List[str], Dict[str, int]]:
    """Assemble the analysis for area and one or more time windows: the cloud copy first, then
    OPeNDAP for whatever it lacks. OPeNDAP chunks are cached in cache_dir (per area and
    product) and reused by later runs. Also returns, per variable, how many hours came from
    OPeNDAP. Columns the product can't supply are all-NaN."""
    lat, lon = native_grid(area)
    long_names: Dict[str, str] = {}
    urls: List[str] = []
    windows = [window] if isinstance(window, ff.TimeWindow) else list(window)
    times: Optional[pd.DatetimeIndex] = None
    axis_times: Optional[pd.DatetimeIndex] = None  # the full OPeNDAP time axis
    positions = np.array([], dtype=int)  # OPeNDAP time index of each requested hour
    collections = list(dict.fromkeys(spec.collection for spec in product.specs))
    for collection in collections:
        url = analysis_url(collection, product)
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
        for spec in product.specs:
            if spec.collection == collection:
                if spec.source_name not in names:
                    raise ff.ValidationError(f"{url} is missing required variable {spec.source_name}")
                long_names[spec.name] = names[spec.source_name]

    grids = {spec.name: np.full((len(times), lat.size, lon.size), np.nan, dtype="float32") for spec in ff.VARIABLES}
    if use_cloud:
        for name, data in read_zarr(area, times, product).items():
            _place(grids[name], times, lat, lon, data)

    jobs = []  # (spec, url, OPeNDAP index slice)
    for spec in product.specs:
        missing = np.where(np.isnan(grids[spec.name]).all(axis=(1, 2)))[0]
        for run in _runs(positions[missing], CHUNK_HOURS):
            jobs.append((spec, analysis_url(spec.collection, product), run))
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
    sources = {spec.name: spec for spec in product.specs}
    for column in ff.VARIABLES:
        spec = sources.get(column.name)
        if spec is None:
            attrs = {
                "units": column.units,
                "long_name": column.description,
                "source_name": "",
                "comment": f"not available in GEOS-CF {product.version}; all values are missing",
            }
        else:
            units = ff.parse_units(long_names[spec.name]) if product.units_in_long_name else spec.units
            attrs = {
                "units": units or "",
                "long_name": spec.description,
                "source_name": spec.source_name,
                "source_collection": spec.collection,
                "source_long_name": long_names[spec.name],
            }
        ds[column.name] = (("time", "lat", "lon"), grids[column.name], attrs)
    ds["time"].attrs.update({"long_name": "valid time (center of hourly averaging window)", "standard_name": "time"})
    ds["lat"].attrs.update({"long_name": "latitude (grid cell center)", "units": "degrees_north", "standard_name": "latitude"})
    ds["lon"].attrs.update({"long_name": "longitude (grid cell center)", "units": "degrees_east", "standard_name": "longitude"})
    return ds, ([product.zarr_url] if use_cloud else []) + urls, from_opendap


def validate_analysis(ds: xr.Dataset, expected_hours: int, unavailable: Sequence[str] = ()) -> Dict[str, int]:
    """Like the forecast validation, but isolated missing hours are reported, not fatal, and
    columns the product can't supply (`unavailable`) are allowed to be entirely NaN."""
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
        if bool(var.isnull().all()) and spec.name not in unavailable:
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
    if metadata.get("source_version") != "v2":  # v2 files keep their original names
        stem = stem.replace("geos_cf_analysis_", f"geos_cf_{metadata['source_version']}_analysis_", 1)
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
    product: Optional[Product] = None,
) -> Path:
    """sampling labels a non-contiguous request (e.g. "first5days") in the file name and metadata.
    product defaults to v2, or v1 for dates before v2 began."""
    windows = [window] if isinstance(window, ff.TimeWindow) else list(window)
    product = product or product_for(windows)
    described = [w.describe() for w in windows]
    logger.info(
        "GEOS-CF %s analysis for %s, %s", product.version, area.name,
        described[0] if len(windows) == 1 else f"{len(windows)} windows ({sampling})",
    )
    if product.unavailable:
        logger.warning("GEOS-CF %s has no %s; those columns will be empty", product.version, ", ".join(product.unavailable))
    downloaded_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    # Kept after the run: later requests for the same area reuse any chunks they overlap.
    # Separate per product, since cache names are indices into that product's time axis.
    tag = "" if product.version == "v2" else f"{product.version}_"
    cache_dir = output_dir / ".cache" / f"analysis_{tag}{area.name}"
    ds, urls, from_opendap = build_analysis(area, windows, workers, cache_dir, use_cloud, product)
    missing = validate_analysis(ds, ds.sizes["time"], product.unavailable)
    valid_times = [pd.Timestamp(t).isoformat() + "Z" for t in ds["time"].values]
    metadata = {
        "source": ff.SOURCE,
        "source_version": product.version,
        "variables_not_available": list(product.unavailable),
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
    parser = argparse.ArgumentParser(description="Download NASA GEOS-CF analysis (historical hourly) data for Georgia.")
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
    parser.add_argument(
        "--model-version", choices=sorted(PRODUCTS),
        help="GEOS-CF version to read; default v2, or v1 for dates before 2025-08-04",
    )
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
        listed = [windows] if isinstance(windows, ff.TimeWindow) else windows
        product = PRODUCTS[args.model_version] if args.model_version else product_for(listed)
    except ValueError as exc:
        parser.error(str(exc))

    try:
        run(area, windows, args.output, args.workers, use_cloud=not args.no_cloud, sampling=sampling, product=product)
    except (ff.ForecastUnavailableError, ff.ValidationError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
