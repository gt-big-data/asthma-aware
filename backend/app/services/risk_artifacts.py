"""Versioned exposure artifacts on the 0.01-degree serving grid."""
import hashlib
import json
import shutil
import re
from tempfile import gettempdir
from uuid import uuid4
from zipfile import ZipFile
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

from app.services.personalization_model import (
    FEATURE_VERSION, MODEL_VERSION, ReferenceStats, exposure, general_risk, personal_scores,
)

CHANNELS = ("o3", "no2", "so2", "pm25")
LATITUDES = 34.975 - 0.01 * np.arange(420)
LONGITUDES = -85.595 + 0.01 * np.arange(408)
PREPROCESSING = "native-surface-nearest-001deg-v1"
FINE_PREFIX = "surface-o3-unet-nearest-001deg-v1:"
FINE_NO2_PREFIX = "surface-o3-no2-unet-001deg-v1:"


def supported_preprocessing(value):
    return isinstance(value, str) and (value == PREPROCESSING or any(
        re.fullmatch(re.escape(prefix) + "[0-9a-f]{64}", value) for prefix in (FINE_PREFIX, FINE_NO2_PREFIX)))


def fine_preprocessing(value):
    return supported_preprocessing(value) and value != PREPROCESSING


def read_surface(path: Path) -> xr.Dataset:
    with xr.open_dataset(path) as data:
        variables = {}
        for name in CHANNELS:
            if name not in data:
                raise ValueError(f"{path.name} has no {name}; redownload with SO2 enabled")
            expected = "ug m-3" if name == "pm25" else "mol mol-1"
            if data[name].attrs.get("units") != expected:
                raise ValueError(f"{name} must have units {expected}")
            values = data[name].transpose("time", "lat", "lon").reset_coords(drop=True).load()
            variables[name] = values.where(np.isfinite(values) & (values >= 0)) * (1 if name == "pm25" else 1e9)
        result = xr.Dataset(variables).sortby("lat").sortby("lon")
    for axis in ("lat", "lon"):
        if len(result[axis]) < 2 or not np.allclose(np.diff(result[axis]), 0.25):
            raise ValueError("Expected NASA's 0.25-degree source grid")
    times = pd.DatetimeIndex(result.time.values)
    if not times.is_unique or not times.is_monotonic_increasing or any(times.minute != 30):
        raise ValueError("Expected unique ascending hourly midpoint timestamps")
    return result


def assemble(paths: list[Path], end=None) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Later files win overlaps. Missing hours are explicit and never filled with zero."""
    if not paths:
        raise ValueError("At least one NASA source file is required")
    datasets = [read_surface(path) for path in paths]
    first = datasets[0]
    for dataset in datasets[1:]:
        if not np.array_equal(first.lat, dataset.lat) or not np.array_equal(first.lon, dataset.lon):
            raise ValueError("NASA files must have the same source grid")
    joined = xr.concat(datasets, dim="time")
    joined = joined.isel(time=~joined.indexes["time"].duplicated(keep="last")).sortby("time")
    if end is not None:
        joined = joined.sel(time=slice(None, end))
    timeline = pd.date_range(joined.time.values[0], joined.time.values[-1], freq="h")
    joined = joined.reindex(time=timeline)
    cube = np.stack([joined[name].values for name in CHANNELS], axis=1)
    return timeline.values, cube, first.lat.values, first.lon.values


def grid_mapping(latitudes, longitudes):
    rows = np.floor((LATITUDES - latitudes[0] + 0.125) / 0.25).astype(int)
    columns = np.floor((LONGITUDES - longitudes[0] + 0.125) / 0.25).astype(int)
    if rows.min() < 0 or rows.max() >= len(latitudes) or columns.min() < 0 or columns.max() >= len(longitudes):
        raise ValueError("NASA source does not cover the 0.01-degree model footprint")
    return rows, columns


def reference_id(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def load_reference(path: Path):
    with np.load(path, allow_pickle=False) as saved:
        ref = ReferenceStats(*(saved[key] for key in ("mean", "std", "clean", "eta_sorted")))
        metadata = json.loads(str(saved["metadata"]))
    if not supported_preprocessing(metadata["preprocessing"]):
        raise ValueError("Reference preprocessing mismatch")
    if any(a.shape != (4,) or not np.isfinite(a).all() for a in (ref.mean, ref.std, ref.clean)):
        raise ValueError("Invalid reference channel statistics")
    if np.any(ref.std <= 0) or not len(ref.eta_sorted) or not np.isfinite(ref.eta_sorted).all() or np.any(np.diff(ref.eta_sorted) < 0):
        raise ValueError("Invalid reference normalization or distribution")
    return ref, metadata


def fit_reference(paths: list[Path], output: Path, *, provisional: bool):
    for path in paths:
        with xr.open_dataset(path) as source:
            if source.attrs.get("product") != "analysis":
                raise ValueError("Reference calibration requires historical analysis")
    times, cube, latitudes, longitudes = assemble(paths)
    E = exposure(cube)
    rows, columns = grid_mapping(latitudes, longitudes)
    # Sample actual 0.01-degree cells without materializing a year-long fine grid.
    rng = np.random.default_rng(42)
    eligible = np.argwhere(np.isfinite(E).all(axis=1))
    if len(eligible) < 2:
        raise ValueError("Not enough complete 78-hour exposure windows")
    weights = np.bincount(rows, minlength=len(latitudes))[:, None] * np.bincount(columns, minlength=len(longitudes))[None, :]
    probabilities = weights[eligible[:, 1], eligible[:, 2]].astype(float)
    probabilities /= probabilities.sum()
    indices = eligible[rng.choice(len(eligible), min(200000, len(eligible)), replace=True, p=probabilities)]
    sample = E[indices[:, 0], :, indices[:, 1], indices[:, 2]][:, :, None, None]
    ref = ReferenceStats.fit(sample)
    metadata = {
        "preprocessing": PREPROCESSING, "provisional": provisional,
        "first_time": str(times[0]), "last_time": str(times[-1]),
        "sources": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths],
        "complete_source_windows": len(eligible),
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, mean=ref.mean, std=ref.std, clean=ref.clean,
                        eta_sorted=ref.eta_sorted, metadata=json.dumps(metadata))
    return metadata


def build_artifact(paths: list[Path], reference: Path, output: Path, *, issued_at, source_run, product):
    if product == "analysis":
        for path in paths:
            with xr.open_dataset(path) as source:
                if source.attrs.get("product") != "analysis":
                    raise ValueError("Training archives must contain NASA analysis, not forecasts")
    times, cube, latitudes, longitudes = assemble(paths)
    E = exposure(cube).astype(np.float32)
    rows, columns = grid_mapping(latitudes, longitudes)
    ref, ref_metadata = load_reference(reference)
    if ref_metadata["preprocessing"] != PREPROCESSING:
        raise ValueError("Native sources cannot use a downscaled reference")
    if not np.isfinite(E).all(axis=1).any():
        raise ValueError("No valid risk windows; at least 78 preceding hours are required")
    metadata = {
        "model_version": MODEL_VERSION, "feature_version": FEATURE_VERSION,
        "preprocessing": PREPROCESSING, "reference_id": reference_id(reference),
        "provisional": ref_metadata["provisional"], "issued_at": str(issued_at),
        "source_run": source_run, "product": product,
        "sources": [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        output, times=times.astype("datetime64[ns]").astype(np.int64), E=E,
        general_scores=general_risk(E, ref).score.astype(np.float32),
        source_rows=rows, source_columns=columns,
        mean=ref.mean, std=ref.std, clean=ref.clean, eta_sorted=ref.eta_sorted,
        metadata=json.dumps(metadata),
    )


class RiskArtifact:
    def __init__(self, path: Path):
        self.digest = reference_id(path)
        with np.load(path, allow_pickle=False) as data:
            self.metadata = json.loads(str(data["metadata"]))
            self.times = pd.DatetimeIndex(data["times"].astype("datetime64[ns]")).tz_localize("UTC")
            mapped = self.metadata.get("storage") == "mmap-npz-v1"
            self.E = mapped_array(path, "E", self.digest) if mapped else data["E"]
            self.rows, self.columns = data["source_rows"], data["source_columns"]
            self.ref = ReferenceStats(*(data[key] for key in ("mean", "std", "clean", "eta_sorted")))
            # Older published runs remain readable. New publishers persist this cube.
            self.general_scores = (mapped_array(path, "general_scores", self.digest) if mapped else data["general_scores"]) if "general_scores" in data else None
        if any(name not in self.metadata for name in (
            "model_version", "feature_version", "preprocessing", "reference_id",
            "provisional", "issued_at", "source_run", "product",
        )):
            raise ValueError("Risk artifact metadata is incomplete")
        if self.ref.mean.shape != (4,) or self.ref.std.shape != (4,) or self.ref.clean.shape != (4,):
            raise ValueError("Risk artifact needs four pollutant reference statistics")
        if not np.isfinite(self.ref.mean).all() or not np.isfinite(self.ref.std).all() or np.any(self.ref.std <= 0):
            raise ValueError("Invalid risk reference normalization")
        if not len(self.ref.eta_sorted) or not np.isfinite(self.ref.eta_sorted).all() or np.any(np.diff(self.ref.eta_sorted) < 0):
            raise ValueError("Invalid risk reference percentiles")
        if (self.metadata["model_version"] != MODEL_VERSION
                or self.metadata["feature_version"] != FEATURE_VERSION
                or not supported_preprocessing(self.metadata["preprocessing"])):
            raise ValueError("Incompatible risk artifact")
        if self.E.ndim != 4 or self.E.shape[:2] != (len(self.times), 4) or len(self.rows) != 420 or len(self.columns) != 408:
            raise ValueError("Invalid risk artifact dimensions")
        if (self.rows.min() < 0 or self.rows.max() >= self.E.shape[2]
                or self.columns.min() < 0 or self.columns.max() >= self.E.shape[3]):
            raise ValueError("Risk artifact grid mapping is invalid")
        if fine_preprocessing(self.metadata["preprocessing"]) and (
                self.E.shape[2:] != (420, 408)
                or not np.array_equal(self.rows, np.arange(420))
                or not np.array_equal(self.columns, np.arange(408))):
            raise ValueError("Fine artifacts require individual 0.01-degree exposure cells")
        if not self.times.is_unique or not self.times.is_monotonic_increasing or not np.all(np.diff(self.times.asi8) == 3600000000000):
            raise ValueError("Risk artifact timeline is not consecutive hourly")
        if self.general_scores is None:
            self.general_scores = general_risk(self.E, self.ref).score.astype(np.float32)
        if self.general_scores.shape != (len(self.times), *self.E.shape[2:]):
            raise ValueError("Invalid general score dimensions")
        for scores in self.general_scores:
            finite_scores = scores[np.isfinite(scores)]
            if np.isinf(scores).any() or np.any((finite_scores < 0) | (finite_scores > 100)):
                raise ValueError("Invalid general scores")

    def point(self, lat: float, lon: float):
        row = int(np.floor((34.98 - lat) / 0.01))
        column = int(np.floor((lon + 85.6) / 0.01))
        if not 0 <= row < len(self.rows) or not 0 <= column < len(self.columns):
            raise ValueError("Location is outside the 0.01-degree model footprint")
        return self.E[:, :, self.rows[row], self.columns[column]][:, :, None, None]

    def day(self, lat, lon, start, end):
        E = self.point(lat, lon)
        selected = (self.times >= start) & (self.times < end)
        expected = int((end - start).total_seconds() / 3600)
        values = E[selected]
        if len(values) != expected or not np.isfinite(values).all():
            return None
        return values.mean(axis=0)[:, 0, 0].astype(float)


def mapped_array(path, name, digest):
    """Decode immutable fine cubes once to local disk, rather than keeping GB in RAM."""
    directory = Path(gettempdir()) / "asthmaaware-risk-arrays" / digest
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{name}.npy"
    if not output.exists():
        pending = directory / f".{uuid4()}.tmp"
        try:
            with ZipFile(path) as archive, archive.open(f"{name}.npy") as source, pending.open("wb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
            pending.replace(output)
        finally:
            pending.unlink(missing_ok=True)
    return np.load(output, mmap_mode="r", allow_pickle=False)


def prune_array_cache(keep):
    root = Path(gettempdir()) / "asthmaaware-risk-arrays"
    if root.exists():
        directories = sorted(root.iterdir(), key=lambda path: path.stat().st_mtime_ns, reverse=True)
        for directory in directories[3:]:
            if directory.name != keep:
                # Linux mappings remain valid after unlink. Windows retains any
                # directory still mapped by another request until a later pass.
                shutil.rmtree(directory, ignore_errors=True)


@lru_cache(maxsize=2)
def cached_artifact(path: Path, modified: int, size: int):
    result = RiskArtifact(path)
    if result.metadata.get("storage") == "mmap-npz-v1":
        prune_array_cache(result.digest)
    return result


def load_artifact(path: Path):
    info = path.stat()
    return cached_artifact(path, info.st_mtime_ns, info.st_size)
