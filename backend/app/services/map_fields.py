"""Published hourly physical fields for public maps; no inference in requests."""
from functools import lru_cache
from pathlib import Path
from threading import RLock
from uuid import UUID, uuid4

import netCDF4
import numpy as np
import pandas as pd
import xarray as xr

from app.services.geos_dashboard import ForecastUnavailable
from app.services.risk_artifacts import LATITUDES, LONGITUDES, grid_mapping, read_surface, reference_id

IO_LOCK = RLock()  # NetCDF/HDF5 reads are serialized across request threads.
POLLUTION_COLORS = ["#eef2ff", "#b4d3ee", "#6a9acb", "#8162aa", "#843951"]
SPECS = {
    "o3": ("Ozone", "ppb", 0, 100), "no2": ("Surface NO₂", "ppb", 0, 10),
    "so2": ("SO₂", "ppb", 0, 2), "pm25": ("PM2.5", "µg/m³", 0, 75),
    "temperature": ("Temperature", "°C", -10, 45),
    "pm10": ("PM10", "µg/m³", 0, 150), "co": ("Carbon monoxide", "ppm", 0, 2),
}


def write_map_fields(source: Path, layers: Path | None, output: Path):
    """Keep native fields compact; preserve learned fields on the exact model grid."""
    raw = read_surface(source)
    digest = reference_id(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(f".{output.name}.{uuid4()}.tmp")
    try:
        from contextlib import ExitStack
        with ExitStack() as stack:
            nasa = stack.enter_context(xr.open_dataset(source))
            models = stack.enter_context(xr.open_dataset(layers)) if layers else None
            if models is not None and (models.attrs.get("source_sha256") != digest
                    or not np.array_equal(models.time, raw.time)
                    or models.sizes.get("lat") != len(LATITUDES) or models.sizes.get("lon") != len(LONGITUDES)
                    or not np.allclose(models.lat, LATITUDES) or not np.allclose(models.lon, LONGITUDES)):
                raise ValueError("Map models differ from the NASA source or output grid")
            result = stack.enter_context(netCDF4.Dataset(pending, "w"))
            result.source_run = nasa.attrs["forecast_run"]
            result.issued_at = nasa.attrs["initialization_time"]
            result.source_sha256 = digest
            for dim, values in (("time", raw.time.values), ("lat", LATITUDES), ("lon", LONGITUDES),
                                ("source_lat", raw.lat.values), ("source_lon", raw.lon.values)):
                result.createDimension(dim, len(values))
                coordinate = result.createVariable(dim, "f8", (dim,))
                if dim == "time":
                    coordinate.units = "hours since 1970-01-01 00:00:00 UTC"
                    coordinate[:] = (pd.DatetimeIndex(values) - pd.Timestamp("1970-01-01")) / pd.Timedelta(hours=1)
                else:
                    coordinate[:] = values
            fields = {name: (raw[name], False) for name in ("o3", "no2", "so2", "pm25")}
            for name, model_name in (("o3", "surface_o3_ppb"), ("no2", "surface_no2_ppb"), ("temperature", "temperature_c")):
                if models is not None and model_name in models:
                    expected = "degC" if name == "temperature" else "ppb"
                    if models[model_name].attrs.get("units") != expected:
                        raise ValueError(f"Wrong model units for {name}")
                    fields[name] = (models[model_name], True)
            if "temperature" not in fields and "air_temperature" in nasa:
                if nasa.air_temperature.attrs.get("units") not in {"k", "K"}:
                    raise ValueError("NASA air temperature must be Kelvin")
                fields["temperature"] = (nasa.air_temperature.sortby("lat").sortby("lon") - 273.15, False)
            for name in ("pm10", "co"):
                if name in nasa and bool(np.isfinite(nasa[name]).any()):
                    expected = "ug m-3" if name == "pm10" else "mol mol-1"
                    if nasa[name].attrs.get("units") != expected:
                        raise ValueError(f"Wrong NASA units for {name}")
                    fields[name] = (nasa[name].sortby("lat").sortby("lon") * (1 if name == "pm10" else 1e6), False)
            for name, (data, modeled) in fields.items():
                dims = ("time", "lat", "lon") if modeled else ("time", "source_lat", "source_lon")
                sizes = (len(LATITUDES), len(LONGITUDES)) if modeled else (len(raw.lat), len(raw.lon))
                variable = result.createVariable(name, "f4", dims, zlib=True, complevel=2,
                    chunksizes=(1, *sizes), fill_value=np.float32(np.nan))
                variable.units = SPECS[name][1]
                variable.processing = "Learned downscaling · experimental" if modeled else "NASA native 0.25° · copied into 0.01° cells"
                variable.experimental = int(modeled)
                available = result.createVariable(f"{name}_available", "i1", ("time",))
                # Availability is for the rendered footprint, not the larger source extent.
                rows, columns = grid_mapping(raw.lat.values, raw.lon.values)
                for hour in range(len(raw.time)):
                    frame = data.isel(time=hour).transpose("lat", "lon").values
                    frame = np.where(np.isfinite(frame) & ((frame >= 0) | (name == "temperature")), frame, np.nan)
                    variable[hour] = frame
                    available[hour] = np.isfinite(frame if modeled else frame[np.ix_(rows, columns)]).any()
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return reference_id(output)


class MapFields:
    def __init__(self, path, digest):
        self.path, self.digest = path, digest
        with IO_LOCK, xr.open_dataset(path) as data:
            self.source_run = data.attrs["source_run"]
            self.issued_at = pd.Timestamp(data.attrs["issued_at"])
            self.times = pd.DatetimeIndex(data.time.values).tz_localize("UTC")
            if not self.times.is_unique or not np.all(np.diff(self.times.asi8) == 3600000000000):
                raise ValueError("Invalid map field timeline")
            if (len(data.lat) != 420 or len(data.lon) != 408
                    or not np.allclose(data.lat, LATITUDES) or not np.allclose(data.lon, LONGITUDES)):
                raise ValueError("Invalid map field grid")
            self.rows, self.columns = grid_mapping(data.source_lat.values, data.source_lon.values)
            self.layers, self.available = {}, {}
            for name, (label, unit, low, high) in SPECS.items():
                if name not in data:
                    continue
                if data[name].attrs.get("units") != unit:
                    raise ValueError("Invalid map field units")
                fine = data[name].dims == ("time", "lat", "lon")
                if not fine and data[name].dims != ("time", "source_lat", "source_lon"):
                    raise ValueError("Invalid map field dimensions")
                # Keep one scale across hours; the learned surface model exceeds
                # the old native-NASA 10 ppb display cap around Atlanta.
                if name == "no2" and fine:
                    high = 20
                self.layers[name] = dict(id=name, label=label, unit=unit, minimum=low, maximum=high,
                    colors=["#3155a4", "#5ab6c9", "#f1df88", "#eaa05e", "#b84c50"] if name == "temperature" else POLLUTION_COLORS,
                    description="Hourly temperature" if name == "temperature" else "Hourly concentration · not AQI",
                    processing=data[name].attrs["processing"], experimental=bool(data[name].attrs.get("experimental", 0)))
                self.available[name] = np.asarray(data[f"{name}_available"].values, dtype=bool)

    def frame(self, layer, time):
        index = self.times.get_indexer([time])[0]
        if layer not in self.layers or index < 0:
            raise ValueError("Map layer or time unavailable")
        # Opening xarray here decoded every field's metadata for every slider
        # request (~650 ms locally). Direct NetCDF slices take ~7 ms and read
        # only the requested field/hour. Keep HDF5 calls serialized.
        with IO_LOCK, netCDF4.Dataset(self.path) as data:
            values = np.asarray(np.ma.filled(data[layer][index], np.nan), dtype=np.float32)
            return values if data[layer].dimensions == ("time", "lat", "lon") else values[np.ix_(self.rows, self.columns)]


@lru_cache(maxsize=2)
def checked_fields(path, modified, size, expected):
    if reference_id(path) != expected:
        raise ValueError("Map artifact checksum mismatch")
    return MapFields(path, expected)


def resolve_map_fields(settings, run):
    if not run.map_artifact:
        return None
    if settings.forecast_gcs_bucket:
        path = cached_map_file(settings.forecast_gcs_bucket, run.map_artifact, run.map_artifact_sha256)
    else:
        name = Path(run.map_artifact)
        if name.name != str(name) or not name.name.endswith(".nc"):
            raise ValueError("Invalid map artifact name")
        path = Path(settings.dashboard_forecast_path).parent / name
    info = path.stat()
    fields = checked_fields(path, info.st_mtime_ns, info.st_size, run.map_artifact_sha256)
    if (fields.source_run != run.source_run or fields.issued_at.to_pydatetime() != run.issued_at
            or list(fields.times.to_pydatetime()) != run.times):
        raise ValueError("Map fields and publication differ")
    return fields


def cached_map_file(bucket_name, key, digest):
    parts = key.split("/")
    if len(parts) != 3 or parts[0] != "published" or parts[2] != "map-fields.nc":
        raise ForecastUnavailable("Invalid map object key")
    UUID(parts[1])
    from tempfile import gettempdir
    from app.services.gcs_forecast import get_bucket
    directory = Path(gettempdir()) / "asthmaaware-map-fields" / bucket_name
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{digest}.nc"
    if not output.exists():
        pending = directory / f".{uuid4()}.tmp"
        try:
            get_bucket(bucket_name).blob(key).download_to_filename(pending, timeout=120)
            if reference_id(pending) != digest:
                raise ValueError("Map artifact checksum mismatch")
            pending.replace(output)
            files = sorted(directory.glob("*.nc"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            for old in files[2:]:
                if old != output:
                    try:
                        old.unlink(missing_ok=True)
                    except PermissionError:
                        pass  # A concurrent Windows request may still have the file open.
        finally:
            pending.unlink(missing_ok=True)
    return output
