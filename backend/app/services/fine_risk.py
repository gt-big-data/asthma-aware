"""Fine-grid surface inputs and bounded-memory, versioned risk preparation."""
import json
import hashlib
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import netCDF4
import numpy as np
import pandas as pd
import xarray as xr

from app.services.personalization_model import FEATURE_VERSION, MODEL_VERSION, ReferenceStats, exposure, general_risk
from app.services.risk_artifacts import (
    CHANNELS, LATITUDES, LONGITUDES, FINE_PREFIX, grid_mapping, read_surface,
    reference_id, load_reference, supported_preprocessing, fine_preprocessing, FINE_NO2_PREFIX,
)


def write_surface(source: Path, layers: Path, output: Path):
    """Use learned surface ppb when selected; never use NO2 column for risk."""
    raw = read_surface(source)
    with xr.open_dataset(source) as opened:
        product = opened.attrs.get("product", "forecast")
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(f".{output.name}.{uuid4()}.tmp")
    try:
        with xr.open_dataset(layers) as models, netCDF4.Dataset(pending, "w") as result:
            if models.attrs.get("source_sha256") != reference_id(source):
                raise ValueError("Downscaled layers were generated from a different NASA input")
            if "surface_o3_ppb" not in models or models.surface_o3_ppb.attrs.get("units") != "ppb":
                raise ValueError("Risk requires a surface O3 model in ppb")
            if (not np.array_equal(raw.time, models.time)
                    or models.sizes.get("lat") != len(LATITUDES) or models.sizes.get("lon") != len(LONGITUDES)
                    or not np.allclose(models.lat, LATITUDES) or not np.allclose(models.lon, LONGITUDES)):
                raise ValueError("Downscaled layers do not match the risk timeline and 0.01-degree grid")
            digest = models.surface_o3_ppb.attrs["checkpoint_sha256"]
            pipeline = FINE_PREFIX + digest
            if not supported_preprocessing(pipeline):
                raise ValueError("Invalid O3 checkpoint identity")
            learned_no2 = "surface_no2_ppb" in models
            if learned_no2:
                no2 = models.surface_no2_ppb
                if no2.attrs.get("units") != "ppb" or no2.dims != ("time", "lat", "lon"):
                    raise ValueError("Risk requires surface NO2 on the model grid in ppb")
                no2_digest = no2.attrs.get("checkpoint_sha256", "")
                if not supported_preprocessing(FINE_PREFIX + no2_digest):
                    raise ValueError("Invalid NO2 checkpoint identity")
                identity = {"o3": digest, "no2": no2_digest,
                            "analysis_lead_hours": str(no2.attrs.get("analysis_lead_hours", "not-required"))}
                pipeline = FINE_NO2_PREFIX + hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
            result.preprocessing = pipeline
            result.product = product
            result.experimental = 1
            result.forecast_skill_validated = 0
            result.source_sha256 = reference_id(source)
            result.layer_sha256 = reference_id(layers)
            result.risk_inputs = json.dumps({"o3": "surface_o3_ppb", "no2": "surface_no2_ppb" if learned_no2 else "native-surface-nearest",
                "so2": "native-surface-nearest", "pm25": "native-surface-nearest"})
            result.createDimension("time", len(raw.time))
            result.createDimension("lat", len(LATITUDES))
            result.createDimension("lon", len(LONGITUDES))
            time = result.createVariable("time", "f8", ("time",))
            time.units = "hours since 1970-01-01 00:00:00 UTC"
            time[:] = (pd.DatetimeIndex(raw.time.values) - pd.Timestamp("1970-01-01")) / pd.Timedelta(hours=1)
            result.createVariable("lat", "f8", ("lat",))[:] = LATITUDES
            result.createVariable("lon", "f8", ("lon",))[:] = LONGITUDES
            rows, columns = grid_mapping(raw.lat.values, raw.lon.values)
            for channel in CHANNELS:
                var = result.createVariable(channel, "f4", ("time", "lat", "lon"),
                    zlib=True, complevel=2, chunksizes=(1, min(8, len(LATITUDES)), len(LONGITUDES)), fill_value=np.float32(np.nan))
                var.units = "ug m-3" if channel == "pm25" else "ppb"
                for hour in range(len(raw.time)):
                    model_name = {"o3": "surface_o3_ppb", "no2": "surface_no2_ppb"}.get(channel)
                    values = (models[model_name].isel(time=hour).values if model_name is not None and model_name in models
                              else raw[channel].values[hour][np.ix_(rows, columns)])
                    var[hour] = np.where(np.isfinite(values) & (values >= 0), values, np.nan)
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return output


class FineSources:
    """Later files win overlaps; missing hours stay missing. Load only spatial strips."""
    def __init__(self, paths, *, analysis=False):
        self.stack = ExitStack()
        try:
            self.datasets = [self.stack.enter_context(xr.open_dataset(path)) for path in paths]
            if not self.datasets:
                raise ValueError("At least one fine surface input is required")
            self.pipeline = self.datasets[0].attrs["preprocessing"]
            if not fine_preprocessing(self.pipeline):
                raise ValueError("Expected model-driven fine surface inputs")
            lookup = {}
            for i, data in enumerate(self.datasets):
                if analysis and data.attrs.get("product") != "analysis":
                    raise ValueError("Calibration and training require historical analysis")
                if (data.attrs["preprocessing"] != self.pipeline
                        or not np.array_equal(data.lat, LATITUDES) or not np.array_equal(data.lon, LONGITUDES)):
                    raise ValueError("Fine input preprocessing or grid mismatch")
                times = pd.DatetimeIndex(data.time.values)
                if (not len(times) or not times.is_unique or not times.is_monotonic_increasing
                        or np.any(times.minute != 30) or np.any(times.second != 0)):
                    raise ValueError("Expected unique ascending hourly midpoint timestamps")
                for channel in CHANNELS:
                    expected = "ug m-3" if channel == "pm25" else "ppb"
                    if data[channel].attrs.get("units") != expected or data[channel].dims != ("time", "lat", "lon"):
                        raise ValueError(f"Invalid fine surface channel: {channel}")
                lookup.update({stamp: (i, j) for j, stamp in enumerate(times)})
            self.times = pd.date_range(min(lookup), max(lookup), freq="h")
            self.locations = [lookup.get(stamp) for stamp in self.times]
        except BaseException:
            self.stack.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.stack.close()

    def tiles(self, height=8):
        for start in range(0, len(LATITUDES), height):
            rows = slice(start, min(start + height, len(LATITUDES)))
            cube = np.full((len(self.times), 4, rows.stop - rows.start, len(LONGITUDES)), np.nan, dtype="float32")
            for i, dataset in enumerate(self.datasets):
                pairs = [(j, location[1]) for j, location in enumerate(self.locations) if location is not None and location[0] == i]
                if pairs:
                    targets, indices = map(list, zip(*pairs))
                    for channel, name in enumerate(CHANNELS):
                        values = dataset[name].isel(time=indices, lat=rows).values
                        cube[targets, channel] = np.where(np.isfinite(values) & (values >= 0), values, np.nan)
            yield rows, exposure(cube).astype("float32")


def fit_fine_reference(paths, output, *, provisional):
    """Uniform reservoir sample of complete fine-cell exposure windows, bounded RAM."""
    rng = np.random.default_rng(42)
    sample, keys = np.empty((0, 4)), np.empty(0)
    count = 0
    with FineSources(paths, analysis=True) as sources:
        for _, E in sources.tiles():
            values = np.moveaxis(E, 1, -1).reshape(-1, 4)
            values = values[np.isfinite(values).all(axis=1)]
            count += len(values)
            priorities = rng.random(len(values))
            if len(values) > 200000:
                chosen = np.argpartition(priorities, 200000)[:200000]
                values, priorities = values[chosen], priorities[chosen]
            sample, keys = np.concatenate([sample, values]), np.concatenate([keys, priorities])
            if len(keys) > 200000:
                chosen = np.argpartition(keys, 200000)[:200000]
                sample, keys = sample[chosen], keys[chosen]
        ref = ReferenceStats.fit(sample[:, :, None, None])
        metadata = dict(preprocessing=sources.pipeline, provisional=provisional,
            first_time=str(sources.times[0]), last_time=str(sources.times[-1]),
            sources=[reference_id(path) for path in paths], complete_fine_windows=count,
            sampled_windows=len(sample), sampling="uniform reservoir over complete fine-cell hours; seed 42")
    save_npz(output, mean=ref.mean, std=ref.std, clean=ref.clean, eta_sorted=ref.eta_sorted, metadata=json.dumps(metadata))
    return metadata


def save_npz(output, **arrays):
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(f".{output.name}.{uuid4()}.tmp")
    try:
        with pending.open("wb") as handle:
            np.savez_compressed(handle, **arrays)
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)


def build_fine_artifact(paths, reference, output, *, issued_at, source_run, product):
    ref, metadata = load_reference(reference)
    output.parent.mkdir(parents=True, exist_ok=True)
    with FineSources(paths, analysis=product == "analysis") as sources, TemporaryDirectory(dir=output.parent) as temporary:
        if metadata["preprocessing"] != sources.pipeline:
            raise ValueError("Fine input and reference preprocessing mismatch; rebuild the fixed reference")
        E = np.lib.format.open_memmap(Path(temporary) / "E.npy", mode="w+", dtype="float32",
            shape=(len(sources.times), 4, len(LATITUDES), len(LONGITUDES)))
        scores = np.lib.format.open_memmap(Path(temporary) / "scores.npy", mode="w+", dtype="float32",
            shape=(len(sources.times), len(LATITUDES), len(LONGITUDES)))
        valid = False
        try:
            for rows, tile in sources.tiles():
                E[:, :, rows, :] = tile
                scores[:, rows, :] = general_risk(tile, ref).score
                valid |= bool(np.isfinite(tile).all(axis=1).any())
            if not valid:
                raise ValueError("No complete fine-grid 78-hour exposure windows")
            info = dict(model_version=MODEL_VERSION, feature_version=FEATURE_VERSION,
                preprocessing=sources.pipeline, reference_id=reference_id(reference),
                provisional=metadata["provisional"], issued_at=str(issued_at), source_run=source_run,
                product=product, sources=[reference_id(path) for path in paths], storage="mmap-npz-v1")
            # Persist only serving hours, after computing exposure over the full
            # history. Warmup is not needed again by dashboard/map/learning.
            if product == "forecast":
                issued = pd.Timestamp(issued_at)
                issued = issued.tz_convert("UTC").tz_localize(None) if issued.tzinfo else issued
                first = int(sources.times.searchsorted(issued))
            else:
                first = 78
            if first >= len(sources.times):
                raise ValueError("No serving hours remain after exposure warmup")
            info["warmup_hours_dropped"] = first
            save_npz(output, times=sources.times[first:].values.astype("datetime64[ns]").astype(np.int64),
                E=E[first:], general_scores=scores[first:], source_rows=np.arange(len(LATITUDES)),
                source_columns=np.arange(len(LONGITUDES)), mean=ref.mean, std=ref.std,
                clean=ref.clean, eta_sorted=ref.eta_sorted, metadata=json.dumps(info))
        finally:
            E._mmap.close()
            scores._mmap.close()
