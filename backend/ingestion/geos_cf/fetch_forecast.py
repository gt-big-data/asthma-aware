"""NASA GEOS-CF v2 forecast ingestion.

Resolves a GEOS-CF v2 forecast run (the newest by default), pulls the air-quality
and meteorology surface fields for an area of Georgia through NASA's OPeNDAP server,
validates the result, and writes a NetCDF file (time x lat x lon) plus a
metadata.json sidecar.

Usage:
    python -m ingestion.geos_cf.fetch_forecast --region georgia --hours 120 --output ./data/geos_cf
    python -m ingestion.geos_cf.fetch_forecast --run 2026-10-14                  # an earlier day's run
    python -m ingestion.geos_cf.fetch_forecast --point 33.75,-84.39 --time 2026-10-23T15:00 --tz America/New_York
    python -m ingestion.geos_cf.fetch_forecast --bbox 33.4,34.2,-84.9,-83.9 --start 2026-10-23 --end 2026-10-24

Run from the backend/ directory so that `ingestion` resolves as a package.
See ingestion/geos_cf/README.md for how GEOS-CF forecasts are published.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import multiprocessing as mp
import re
import sys
import time
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
import xarray as xr

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

OPENDAP_ROOT = "https://opendap.nccs.nasa.gov/dods/gmao/geos-cf/v2/fcst"
SOURCE = "NASA GEOS-CF"
SOURCE_VERSION = "v2"
NATIVE_RESOLUTION_DEG = 0.25
NATIVE_SPATIAL_RESOLUTION = "0.25 x 0.25 degree global lat/lon grid (1440 x 721)"

# GEOS-CF v2 forecasts initialize once per day at 09 UTC and run 120 hours.
INIT_HOUR_UTC = 9
MAX_FORECAST_HOURS = 120
# tavg collections are hourly means stamped at the middle of the hour (09:30, 10:30, ...).
TAVG_OFFSET = pd.Timedelta(minutes=30)

STALE_AFTER_HOURS = 30
DEFAULT_LOOKBACK_DAYS = 3
OPEN_RETRIES = 3
RETRY_BACKOFF_SECONDS = 10
# A Georgia variable normally downloads in ~10s; past this the request is killed and retried.
REQUEST_TIMEOUT_SECONDS = 180

AQC_COLLECTION = "aqc_tavg_1hr_glo_L1440x721_slv"
MET_COLLECTION = "met_tavg_1hr_glo_L1440x721_slv"
COLLECTIONS = (AQC_COLLECTION, MET_COLLECTION)

# Georgia's state extent. The subset keeps every grid cell whose 0.25 deg
# footprint intersects this box (see _grid_selection).
REGIONS: Dict[str, Dict[str, float]] = {
    "georgia": {"min_lat": 30.36, "max_lat": 35.00, "min_lon": -85.61, "max_lon": -80.84},
}
GEORGIA_BBOX = REGIONS["georgia"]


@dataclass(frozen=True)
class Area:
    """What part of the grid to download. `name` is used in output file names."""

    name: str
    bbox: Dict[str, float]  # the extent to subset; for a point, the containing grid cell's center
    point: Optional[Tuple[float, float]] = None  # the (lat, lon) the user asked for, if a point


def region_area(name: str) -> Area:
    return Area(name, REGIONS[name])


def _check_in_georgia(lat: float, lon: float) -> None:
    g = GEORGIA_BBOX
    if not (g["min_lat"] <= lat <= g["max_lat"] and g["min_lon"] <= lon <= g["max_lon"]):
        raise ValueError(
            f"({lat}, {lon}) is outside Georgia's extent "
            f"({g['min_lat']}..{g['max_lat']} N, {g['min_lon']}..{g['max_lon']} E)"
        )


def _snap(value: float) -> float:
    return round(value / NATIVE_RESOLUTION_DEG) * NATIVE_RESOLUTION_DEG


def point_area(lat: float, lon: float) -> Area:
    """The single 0.25 deg grid cell containing (lat, lon)."""
    _check_in_georgia(lat, lon)
    clat, clon = _snap(lat), _snap(lon)
    name = f"point_{abs(lat):.2f}{'N' if lat >= 0 else 'S'}_{abs(lon):.2f}{'E' if lon >= 0 else 'W'}"
    return Area(name, {"min_lat": clat, "max_lat": clat, "min_lon": clon, "max_lon": clon}, point=(lat, lon))


def bbox_area(min_lat: float, max_lat: float, min_lon: float, max_lon: float) -> Area:
    if min_lat > max_lat or min_lon > max_lon:
        raise ValueError("bbox must be MIN_LAT,MAX_LAT,MIN_LON,MAX_LON with min <= max")
    _check_in_georgia(min_lat, min_lon)
    _check_in_georgia(max_lat, max_lon)
    name = f"bbox_{min_lat:g}_{max_lat:g}_{min_lon:g}_{max_lon:g}"
    return Area(name, {"min_lat": min_lat, "max_lat": max_lat, "min_lon": min_lon, "max_lon": max_lon})


@dataclass(frozen=True)
class TimeWindow:
    """A requested span of valid times, as naive UTC timestamps.

    end=None means "through the end of the run"; end == start means the single hour
    containing start. Otherwise end is exclusive: 15:00-18:00 is the 15, 16 and 17 o'clock hours.
    """

    start: pd.Timestamp
    end: Optional[pd.Timestamp] = None

    def describe(self) -> str:
        if self.end is None:
            return f"from {self.start.isoformat()}Z"
        if self.end == self.start:
            return f"at {self.start.isoformat()}Z"
        return f"{self.start.isoformat()}Z to {self.end.isoformat()}Z"


def parse_time(value: str, tz: str = "UTC") -> pd.Timestamp:
    """Parse an ISO time ('2026-10-23T15:00', '2026-10-23', or with an offset) to naive UTC."""
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize(tz)
    return ts.tz_convert("UTC").tz_localize(None)


def lead_slice(init: pd.Timestamp, window: Optional[TimeWindow] = None, hours: int = MAX_FORECAST_HOURS) -> slice:
    """Indices into a run's 120 hourly steps. Step k is the mean over [init + k h, init + (k+1) h)."""
    if window is None:
        return slice(0, hours)
    hour = pd.Timedelta(hours=1)
    first = int(np.floor((window.start - init) / hour))
    if window.end is None:
        stop = MAX_FORECAST_HOURS
    else:
        stop = max(int(np.ceil((window.end - init) / hour)), first + 1)
    return slice(first, stop)


def first_valid_time(init: pd.Timestamp, lead: slice) -> np.datetime64:
    """Valid time of the first step in lead (steps are stamped mid-hour)."""
    return (init + TAVG_OFFSET + pd.Timedelta(hours=lead.start)).to_datetime64()


def run_covers(init: pd.Timestamp, window: TimeWindow) -> bool:
    lead = lead_slice(init, window)
    return 0 <= lead.start and lead.stop <= MAX_FORECAST_HOURS


def _run_span(init: pd.Timestamp) -> str:
    end = init + pd.Timedelta(hours=MAX_FORECAST_HOURS)
    return f"{init.isoformat()}Z to {end.isoformat()}Z"


def latest_init_at_or_before(t: pd.Timestamp) -> pd.Timestamp:
    return (t - pd.Timedelta(hours=INIT_HOUR_UTC)).normalize() + pd.Timedelta(hours=INIT_HOUR_UTC)


@dataclass(frozen=True)
class VariableSpec:
    name: str  # name in our output file
    source_name: str  # name on the OPeNDAP server
    collection: str
    units: str  # units the source must report (validated against long_name)
    description: str


VARIABLES: Tuple[VariableSpec, ...] = (
    VariableSpec("pm25", "pm25_rh35", AQC_COLLECTION, "ug m-3", "PM2.5 mass concentration at 35% RH"),
    VariableSpec("pm10", "pm10_rh35", AQC_COLLECTION, "ug m-3", "PM10 mass concentration at 35% RH"),
    VariableSpec("no2", "no2", AQC_COLLECTION, "mol mol-1", "NO2 dry-air mole fraction"),
    VariableSpec("o3", "o3", AQC_COLLECTION, "mol mol-1", "O3 dry-air mole fraction"),
    VariableSpec("so2", "so2", AQC_COLLECTION, "mol mol-1", "SO2 dry-air mole fraction"),
    VariableSpec("co", "co", AQC_COLLECTION, "mol mol-1", "CO dry-air mole fraction"),
    VariableSpec("air_temperature", "t", MET_COLLECTION, "k", "Air temperature, lowest model layer"),
    VariableSpec("specific_humidity", "q", MET_COLLECTION, "kg kg-1", "Specific humidity, lowest model layer"),
    VariableSpec("relative_humidity", "rh", MET_COLLECTION, "1", "Relative humidity (fraction 0-1), lowest model layer"),
    VariableSpec("eastward_wind", "u", MET_COLLECTION, "m s-1", "Eastward (U) wind, lowest model layer"),
    VariableSpec("northward_wind", "v", MET_COLLECTION, "m s-1", "Northward (V) wind, lowest model layer"),
    VariableSpec("surface_pressure", "ps", MET_COLLECTION, "pa", "Surface pressure"),
    VariableSpec("precipitation", "tprec", MET_COLLECTION, "kg m-2 s-1", "Total precipitation rate (x3600 = mm/h)"),
    VariableSpec("pbl_height", "zpbl", MET_COLLECTION, "m", "Planetary boundary layer height"),
)


class ForecastUnavailableError(RuntimeError):
    """No complete GEOS-CF forecast run could be resolved."""


class ValidationError(RuntimeError):
    """The retrieved forecast is incomplete or malformed."""


def collection_url(collection: str, run_id: Optional[str] = None) -> str:
    if run_id is None:
        return f"{OPENDAP_ROOT}/{collection}.latest"
    return f"{OPENDAP_ROOT}/{collection}/{collection}.{run_id}"


def run_id_for(init_time: pd.Timestamp) -> str:
    return init_time.strftime("%Y%m%d_%Hz")


def parse_units(long_name: str) -> Optional[str]:
    """GEOS-CF's GrADS server has no `units` attribute; units are the trailing "(...)" of long_name."""
    match = re.search(r"\(([^()]*)\)\s*$", long_name or "")
    return match.group(1).strip() if match else None


def infer_init_time(times: Sequence[np.datetime64]) -> pd.Timestamp:
    """The first tavg step is the mean over [init, init+1h], stamped at init+30min."""
    return pd.Timestamp(times[0]) - TAVG_OFFSET


def open_remote(url: str) -> xr.Dataset:
    with warnings.catch_warnings():
        # GrADS reports "days since 1-1-1 00:00:0.0"; xarray decodes it correctly but warns.
        warnings.simplefilter("ignore", category=xr.SerializationWarning)
        return xr.open_dataset(url)


# The _read_* functions run inside the worker process (see remote_call), so they take and
# return only picklable values, never an open remote dataset.


def _read_inventory(url: str) -> Tuple[Dict[str, str], np.ndarray, float]:
    """Variable long_names, valid times, and max longitude of a remote collection."""
    with open_remote(url) as remote:
        long_names = {name: remote[name].attrs.get("long_name", "").strip() for name in remote.data_vars}
        # When the server's .das request fails (e.g. a 502 from NCCS's proxy), netCDF-C still
        # opens the dataset, just without attributes. Raise so remote_call retries.
        if not all(long_names.values()):
            raise OSError(f"{url} returned no variable attributes (server metadata request failed)")
        return long_names, remote["time"].values, float(remote["lon"].max())


def _read_variable(url: str, source_name: str, selection: Dict[str, slice], lead: slice) -> xr.DataArray:
    with open_remote(url) as remote:
        # OPeNDAP only transfers the constrained hyperslab, not the global field.
        data = remote[source_name].sel(**selection).isel(time=lead)
        if "lev" in data.dims:
            data = data.isel(lev=0, drop=True)
        data = data.load()
    # A failed .dods transfer can also come back as all fill values instead of an error.
    if data.size and bool(data.isnull().all()):
        raise OSError(f"{source_name} from {url} came back entirely missing (server data request failed)")
    return data


def _child_main(conn, fn: Callable, args: tuple) -> None:
    try:
        result = (True, fn(*args))
    except BaseException as exc:  # noqa: BLE001 - shipped back to the parent to re-raise
        result = (False, exc)
    try:
        conn.send(result)
    except Exception as exc:  # noqa: BLE001 - e.g. an exception type that can't be pickled
        conn.send((False, RuntimeError(repr(result[1] if not result[0] else exc))))
    finally:
        conn.close()


def run_with_timeout(fn: Callable, *args, timeout: float):
    """Run fn(*args) in a fresh child process, SIGKILLing it if it takes longer than timeout.

    netCDF4's OPeNDAP client blocks inside libcurl with no default timeout and can't be
    interrupted from Python, so one stalled transfer can hang the whole run (we've seen 30+
    minutes). multiprocessing.Pool isn't used because Pool.terminate() itself can deadlock
    while a worker is stuck.
    """
    ctx = mp.get_context("spawn")
    receiver, sender = ctx.Pipe(duplex=False)
    proc = ctx.Process(target=_child_main, args=(sender, fn, args), daemon=True)
    proc.start()
    sender.close()
    try:
        if not receiver.poll(timeout):
            raise TimeoutError(f"no response within {timeout:.0f}s")
        try:
            ok, value = receiver.recv()
        except EOFError:
            proc.join(5)
            raise OSError(f"worker exited without a result (exit code {proc.exitcode})") from None
    finally:
        if proc.is_alive():
            proc.kill()
        proc.join()
        receiver.close()
    if not ok:
        raise value
    return value


def remote_call(fn: Callable, *args, what: str, retries: int = OPEN_RETRIES):
    for attempt in range(1, retries + 1):
        try:
            return run_with_timeout(fn, *args, timeout=REQUEST_TIMEOUT_SECONDS)
        except OSError as exc:  # DAP/network failures surface as OSError; TimeoutError is one too
            if attempt == retries:
                raise
            logger.warning("%s failed (attempt %d/%d): %s; retrying", what, attempt, retries, exc)
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise AssertionError("unreachable")


def _run_available(run_id: str) -> bool:
    for collection in COLLECTIONS:
        url = collection_url(collection, run_id)
        try:
            remote_call(_read_inventory, url, what=f"Checking {url}", retries=2)
        except OSError:
            logger.info("Run %s not available for %s", run_id, collection)
            return False
    return True


def _newest_run() -> pd.Timestamp:
    try:
        url = collection_url(AQC_COLLECTION)
        _, times, _ = remote_call(_read_inventory, url, what=f"Opening {url}")
        newest = infer_init_time(times)
        logger.info("%s.latest points at run %s", AQC_COLLECTION, run_id_for(newest))
    except OSError as exc:
        now = pd.Timestamp.now(tz="UTC").tz_localize(None)
        newest = latest_init_at_or_before(now)
        logger.warning("Could not open .latest (%s); probing dated runs from %s", exc, run_id_for(newest))
    return newest


def resolve_forecast_run(
    run_date: Optional[dt.date] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    window: Optional[TimeWindow] = None,
) -> pd.Timestamp:
    """Return the initialization time of the run to download.

    With run_date, that day's 09z run (which must cover `window`, if given). Otherwise the
    newest run published for every collection we need that also covers `window`.

    `.latest` is a moving alias, so we only use it to discover the newest run and then pin
    each collection to that run's dated URL. That keeps AQ and met from the same run even if
    `.latest` advances mid-download, and lets us walk back to earlier runs when today's run
    is late, missing, or only partially published.
    """
    if run_date is not None:
        init = pd.Timestamp(dt.datetime(run_date.year, run_date.month, run_date.day, INIT_HOUR_UTC))
        if window is not None and not run_covers(init, window):
            raise ForecastUnavailableError(
                f"Run {run_id_for(init)} covers {_run_span(init)}, which does not include {window.describe()}"
            )
        if not _run_available(run_id_for(init)):
            raise ForecastUnavailableError(f"GEOS-CF run {run_id_for(init)} is not available on {OPENDAP_ROOT}")
        return init

    newest = _newest_run()
    first = newest
    if window is not None:
        # The freshest forecast for a time is the newest run that started at or before it.
        first = min(newest, latest_init_at_or_before(window.start))
        if not run_covers(first, window):
            raise ForecastUnavailableError(
                f"No single run covers {window.describe()}: the newest run, {run_id_for(newest)}, "
                f"covers {_run_span(newest)}, and each run is {MAX_FORECAST_HOURS} hours long"
            )

    for days_back in range(lookback_days + 1):
        candidate = first - pd.Timedelta(days=days_back)
        if window is not None and not run_covers(candidate, window):
            break
        if _run_available(run_id_for(candidate)):
            if days_back:
                logger.warning("Falling back %d day(s) to run %s", days_back, run_id_for(candidate))
            return candidate

    raise ForecastUnavailableError(
        f"No GEOS-CF run available for all of {list(COLLECTIONS)} within {lookback_days} day(s) of {run_id_for(first)}"
        + (f" that covers {window.describe()}" if window is not None else "")
    )


def _grid_selection(bbox: Dict[str, float]) -> Dict[str, slice]:
    """Keep grid points whose cell (center +/- half the resolution) intersects the bbox."""
    half = NATIVE_RESOLUTION_DEG / 2
    return {
        "lat": slice(bbox["min_lat"] - half, bbox["max_lat"] + half),
        "lon": slice(bbox["min_lon"] - half, bbox["max_lon"] + half),
    }


def fetch_collection(
    collection: str, run_id: str, specs: Sequence[VariableSpec], bbox: Dict[str, float], lead: slice
) -> xr.Dataset:
    url = collection_url(collection, run_id)
    long_names, _, lon_max = remote_call(_read_inventory, url, what=f"Opening {url}")

    missing = [s.source_name for s in specs if s.source_name not in long_names]
    if missing:
        raise ValidationError(f"{url} is missing required variables: {missing}")
    if lon_max > 180:
        raise ValidationError(f"{url} uses 0-360 longitudes; expected -180..180")

    out = xr.Dataset()
    for spec in specs:
        # One request per variable, so a failure retries just that variable.
        data = remote_call(
            _read_variable, url, spec.source_name, _grid_selection(bbox), lead,
            what=f"Downloading {spec.source_name} from {url}",
        )
        source_long_name = long_names[spec.source_name]
        data.attrs = {
            "units": parse_units(source_long_name) or "",
            "long_name": spec.description,
            "source_name": spec.source_name,
            "source_collection": collection,
            "source_long_name": source_long_name,
        }
        data.encoding = {}
        out[spec.name] = data
        logger.info("  %-18s <- %s/%s (%s)", spec.name, collection, spec.source_name, data.attrs["units"])

    return out


def build_forecast(init: pd.Timestamp, area: Area, lead: slice) -> xr.Dataset:
    run_id = run_id_for(init)
    parts = []
    for collection in COLLECTIONS:
        specs = [s for s in VARIABLES if s.collection == collection]
        logger.info("Fetching %d variables from %s (run %s)", len(specs), collection, run_id)
        parts.append(fetch_collection(collection, run_id, specs, area.bbox, lead))

    for part in parts[1:]:
        if not np.array_equal(part["time"].values, parts[0]["time"].values):
            raise ValidationError(f"Collections in run {run_id} have different valid times")
    # join="exact" refuses to silently realign mismatched lat/lon grids.
    ds = xr.merge(parts, join="exact", combine_attrs="override")
    if not ds.sizes.get("time") or ds["time"].values[0] != first_valid_time(init, lead):
        raise ValidationError(f"Run {run_id} does not begin at its expected valid time")

    ds = ds.assign_coords(lead_time=("time", ds["time"].values - init.to_datetime64()))
    ds["time"].attrs.update({"long_name": "forecast valid time (center of hourly averaging window)", "standard_name": "time"})
    ds["lead_time"].attrs.update({"long_name": "time since forecast initialization", "standard_name": "forecast_period"})
    ds["lat"].attrs.update({"long_name": "latitude (grid cell center)", "units": "degrees_north", "standard_name": "latitude"})
    ds["lon"].attrs.update({"long_name": "longitude (grid cell center)", "units": "degrees_east", "standard_name": "longitude"})
    return ds.transpose("time", "lat", "lon")


def validate_forecast(ds: xr.Dataset, expected_hours: int, specs: Sequence[VariableSpec] = VARIABLES) -> None:
    """Raise ValidationError listing every problem found."""
    errors: List[str] = []

    for dim in ("time", "lat", "lon"):
        if dim not in ds.dims:
            errors.append(f"missing dimension '{dim}'")
        elif ds.sizes[dim] == 0:
            errors.append(f"dimension '{dim}' is empty (region subset returned no {dim} cells)")

    if "time" in ds.dims and ds.sizes["time"] > 0:
        times = ds["time"].values
        if ds.sizes["time"] != expected_hours:
            errors.append(f"expected {expected_hours} forecast hours, got {ds.sizes['time']}")
        steps = np.diff(times)
        if np.any(steps <= np.timedelta64(0, "s")):
            errors.append("valid times are not strictly increasing")
        elif np.any(steps != np.timedelta64(1, "h")):
            errors.append("valid times are not on a regular 1-hour step")

    for spec in specs:
        if spec.name not in ds.data_vars:
            errors.append(f"required variable '{spec.name}' ({spec.source_name}) is missing")
            continue
        var = ds[spec.name]
        if set(var.dims) != {"time", "lat", "lon"}:
            errors.append(f"'{spec.name}' has dims {var.dims}, expected (time, lat, lon)")
        units = var.attrs.get("units")
        if not units:
            errors.append(f"'{spec.name}' has no units")
        elif units != spec.units:
            errors.append(f"'{spec.name}' units changed upstream: expected '{spec.units}', got '{units}'")
        if var.size and bool(np.isinf(var).any()):
            errors.append(f"'{spec.name}' contains infinite values")
        if var.size and bool(var.isnull().all()):
            errors.append(f"'{spec.name}' is entirely NaN")
        elif var.size and "time" in var.dims:
            nan_hours = int(var.isnull().all(dim=[d for d in var.dims if d != "time"]).sum())
            if nan_hours:
                errors.append(f"'{spec.name}' has {nan_hours} forecast hour(s) that are entirely NaN")

    if errors:
        raise ValidationError("GEOS-CF forecast failed validation:\n  - " + "\n  - ".join(errors))


def build_metadata(
    ds: xr.Dataset,
    init: pd.Timestamp,
    area: Area,
    lead: slice,
    downloaded_at: dt.datetime,
    window: Optional[TimeWindow] = None,
) -> Dict[str, object]:
    run_id = run_id_for(init)
    valid_times = [pd.Timestamp(t).isoformat() + "Z" for t in ds["time"].values]
    return {
        "source": SOURCE,
        "source_version": SOURCE_VERSION,
        "source_urls": [collection_url(c, run_id) for c in COLLECTIONS],
        "forecast_run": run_id,
        "initialization_time": init.isoformat() + "Z",
        "downloaded_at": downloaded_at.isoformat().replace("+00:00", "Z"),
        "forecast_hours": len(valid_times),
        "lead_hour_range": [lead.start, lead.stop],
        "requested_time_window": None if window is None else window.describe(),
        "valid_time_start": valid_times[0],
        "valid_time_end": valid_times[-1],
        "valid_times": valid_times,
        "time_convention": "hourly means stamped at the center of the hour (init+0.5h, init+1.5h, ...)",
        "native_spatial_resolution": NATIVE_SPATIAL_RESOLUTION,
        "region": area.name,
        "region_bbox": area.bbox,
        "requested_point": None if area.point is None else {"lat": area.point[0], "lon": area.point[1]},
        "grid_extent": {
            "min_lat": float(ds["lat"].min()),
            "max_lat": float(ds["lat"].max()),
            "min_lon": float(ds["lon"].min()),
            "max_lon": float(ds["lon"].max()),
            "n_lat": int(ds.sizes["lat"]),
            "n_lon": int(ds.sizes["lon"]),
        },
        "variables": list(ds.data_vars),
        "variable_source_names": {name: ds[name].attrs["source_name"] for name in ds.data_vars},
        "variable_source_collections": {name: ds[name].attrs["source_collection"] for name in ds.data_vars},
        "units": {name: ds[name].attrs["units"] for name in ds.data_vars},
    }


def output_stem(area_name: str, run_id: str, lead: slice) -> str:
    stem = f"geos_cf_{area_name}_{run_id}"
    if (lead.start, lead.stop) != (0, MAX_FORECAST_HOURS):
        stem += f"_h{lead.start:03d}-{lead.stop:03d}"
    return stem


def write_outputs(ds: xr.Dataset, metadata: Dict[str, object], output_dir: Path) -> Tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = output_stem(metadata["region"], metadata["forecast_run"], slice(*metadata["lead_hour_range"]))
    nc_path = output_dir / f"{stem}.nc"
    json_path = output_dir / f"{stem}.metadata.json"

    # NetCDF attributes must be flat; nested values are stored as JSON strings.
    ds.attrs = {
        k: v if isinstance(v, (str, int, float, list)) else json.dumps(v) for k, v in metadata.items() if v is not None
    }
    encoding = {name: {"zlib": True, "complevel": 4, "dtype": "float32"} for name in ds.data_vars}
    encoding["time"] = {"units": f"hours since {metadata['initialization_time'][:-1]}", "dtype": "float64"}
    encoding["lead_time"] = {"units": "hours", "dtype": "float32"}

    tmp_path = nc_path.with_suffix(".nc.tmp")
    ds.to_netcdf(tmp_path, encoding=encoding, format="NETCDF4")
    tmp_path.replace(nc_path)  # atomic, so readers never see a half-written file
    metadata_tmp = json_path.with_suffix(".json.tmp")
    metadata_tmp.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    metadata_tmp.replace(json_path)
    return nc_path, json_path


def _format_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def run(
    area: Area,
    output_dir: Path,
    run_date: Optional[dt.date] = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    overwrite: bool = False,
    hours: int = MAX_FORECAST_HOURS,
    window: Optional[TimeWindow] = None,
) -> Path:
    init = resolve_forecast_run(run_date, lookback_days, window)
    run_id = run_id_for(init)
    lead = lead_slice(init, window, hours)

    age_hours = (pd.Timestamp.now(tz="UTC").tz_localize(None) - init) / pd.Timedelta(hours=1)
    if run_date is None and window is None and age_hours > STALE_AFTER_HOURS:
        logger.warning("Run %s is %.0f hours old; a newer daily run may be late or missing", run_id, age_hours)

    nc_path = output_dir / f"{output_stem(area.name, run_id, lead)}.nc"
    if nc_path.exists() and not overwrite:
        try:
            sidecar = json.loads(nc_path.with_suffix(".metadata.json").read_text(encoding="utf-8"))
            if (sidecar.get("forecast_run") != run_id
                    or sidecar.get("region_bbox") != area.bbox
                    or sidecar.get("lead_hour_range") != [lead.start, lead.stop]):
                raise ValidationError("cached metadata does not match the requested forecast")
            with xr.open_dataset(nc_path) as cached:
                validate_forecast(cached, lead.stop - lead.start)
                if cached["time"].values[0] != first_valid_time(init, lead):
                    raise ValidationError("cached valid times do not match the requested run")
        except (OSError, ValueError, ValidationError) as exc:
            logger.warning("Rebuilding unusable cached forecast %s: %s", nc_path, exc)
        else:
            logger.info("%s is complete and matches the request; reusing", nc_path)
            return nc_path

    logger.info("Area %s, run %s, forecast hours %d-%d", area.name, run_id, lead.start, lead.stop)
    downloaded_at = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    ds = build_forecast(init, area, lead)
    validate_forecast(ds, lead.stop - lead.start)
    metadata = build_metadata(ds, init, area, lead, downloaded_at, window)
    nc_path, json_path = write_outputs(ds, metadata, output_dir)

    logger.info("GEOS-CF forecast ingested and validated")
    logger.info("  forecast run         %s (initialized %s)", run_id, metadata["initialization_time"])
    if window is not None:
        logger.info("  requested            %s", window.describe())
    if area.point is not None:
        logger.info("  point                %s -> grid cell %.2f, %.2f", area.point, area.bbox["min_lat"], area.bbox["min_lon"])
    logger.info("  first valid time     %s", metadata["valid_time_start"])
    logger.info("  last valid time      %s", metadata["valid_time_end"])
    logger.info("  forecast hours       %d (lead hours %d-%d)", metadata["forecast_hours"], lead.start, lead.stop)
    logger.info("  grid                 %d lat x %d lon cells @ %.2f deg", ds.sizes["lat"], ds.sizes["lon"], NATIVE_RESOLUTION_DEG)
    logger.info("  variables            %s", ", ".join(metadata["variables"]))
    logger.info("  downloaded           %s (uncompressed)", _format_bytes(ds.nbytes))
    logger.info("  output               %s (%s)", nc_path, _format_bytes(nc_path.stat().st_size))
    logger.info("  metadata             %s", json_path)
    return nc_path


def _floats(text: str, count: int) -> List[float]:
    try:
        values = [float(v) for v in text.split(",")]
    except ValueError:
        values = []
    if len(values) != count:
        raise argparse.ArgumentTypeError(f"expected {count} comma-separated numbers, got '{text}'")
    return values


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download a NASA GEOS-CF v2 forecast for Georgia, or a point or box within it.",
        epilog="Times are ISO 8601 (2026-10-23T15:00, 2026-10-23, or with an offset like 2026-10-23T15:00-04:00).",
    )
    where = parser.add_argument_group("location (default: all of Georgia)").add_mutually_exclusive_group()
    where.add_argument("--region", choices=sorted(REGIONS), help="a named region")
    where.add_argument("--point", type=lambda t: _floats(t, 2), metavar="LAT,LON", help="the grid cell containing this point")
    where.add_argument(
        "--bbox", type=lambda t: _floats(t, 4), metavar="MIN_LAT,MAX_LAT,MIN_LON,MAX_LON", help="a box within Georgia"
    )

    when = parser.add_argument_group("time (default: all 120 hours of the run)")
    span = when.add_mutually_exclusive_group()
    span.add_argument("--hours", type=int, help=f"the first N hours of the run (1-{MAX_FORECAST_HOURS})")
    span.add_argument("--time", help="just the hour containing this time")
    span.add_argument("--start", help="hours from this time ...")
    when.add_argument("--end", help="... up to (not including) this time; default: end of the run")
    when.add_argument("--tz", default="UTC", help="timezone for times without an offset, e.g. America/New_York (default UTC)")
    when.add_argument(
        "--run", type=dt.date.fromisoformat, metavar="YYYY-MM-DD",
        help="use the 09z run from this day; default: the newest run (that covers --time/--start)",
    )

    parser.add_argument("--output", type=Path, default=Path("data/geos_cf"), help="output directory")
    parser.add_argument("--lookback-days", type=int, default=DEFAULT_LOOKBACK_DAYS, help="how many earlier daily runs to try if the newest is incomplete")
    parser.add_argument("--overwrite", action="store_true", help="refetch even if this output already exists")
    args = parser.parse_args(argv)

    try:
        if args.point:
            area = point_area(*args.point)
        elif args.bbox:
            area = bbox_area(*args.bbox)
        else:
            area = region_area(args.region or "georgia")

        window = None
        if args.time:
            t = parse_time(args.time, args.tz)
            window = TimeWindow(t, t)
        elif args.start:
            window = TimeWindow(parse_time(args.start, args.tz), parse_time(args.end, args.tz) if args.end else None)
            if window.end is not None and window.end <= window.start:
                parser.error("--end must be after --start")
        elif args.end:
            parser.error("--end requires --start")
    except ValueError as exc:  # bad coordinates, unknown timezone, unparseable time
        parser.error(str(exc))

    hours = MAX_FORECAST_HOURS if args.hours is None else args.hours
    if not 1 <= hours <= MAX_FORECAST_HOURS:
        parser.error(f"--hours must be between 1 and {MAX_FORECAST_HOURS}")
    if args.lookback_days < 0:
        parser.error("--lookback-days must be nonnegative")

    try:
        run(area, args.output, args.run, args.lookback_days, args.overwrite, hours, window)
    except (ForecastUnavailableError, ValidationError) as exc:
        logger.error("%s", exc)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
