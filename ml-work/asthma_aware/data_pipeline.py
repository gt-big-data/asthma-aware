"""Independent observation-supervised datasets for GEOS-CF hourly analyses.

Preparation creates a reproducible catalog; it never invents acquisition times,
forecast initialization times, observation masks, or compatible column baselines.
See README for the JSON configuration and preparation CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import calendar
import copy
import hashlib
import json
import math
import os
import random
import re

import numpy as np
import pandas as pd
import rasterio
from rasterio.transform import Affine, from_bounds, rowcol, array_bounds
from rasterio.warp import (
    reproject,
    Resampling,
    transform as transform_points,
    transform_bounds,
)
import torch
from torch.utils.data import Dataset, DataLoader
import xarray as xr

MET = ["air_temperature", "specific_humidity", "surface_pressure", "pbl_height"]
TARGETS = {
    "no2": "tropospheric_no2_column",
    "o3": "surface_o3_ppb",
    "temperature": "temperature_c",
    "dewpoint": "dewpoint_c",
}
COLUMN_UNITS = {
    "mol/m2": 1.0,
    "mol m-2": 1.0,
    "umol/m2": 1e-6,
    "1.0e15 molec cm-2": 1e19 / 6.02214076e23,
}
EXPECTED_UNITS = {
    "air_temperature": {"k", "K"},
    "specific_humidity": {"kg kg-1"},
    "surface_pressure": {"pa", "Pa"},
    "pbl_height": {"m"},
    "o3": {"mol mol-1", "mol/mol"},
}
TIME_CONVENTION = "hourly means stamped at the center of the hour"


@dataclass(frozen=True)
class Grid:
    crs: str = "EPSG:4326"
    width: int = 408
    height: int = 420
    transform: tuple = (0.01, 0.0, -85.60, 0.0, -0.01, 34.98)

    @property
    def affine(self):
        return Affine(*self.transform)

    def validate(self):
        if self.width < 1 or self.height < 1:
            raise ValueError("Grid dimensions must be positive")
        a = self.affine
        if a.a <= 0 or a.e >= 0 or a.b or a.d:
            raise ValueError("Output grid must be north-up without rotation")
        rasterio.crs.CRS.from_user_input(self.crs)

    def as_dict(self):
        return dict(
            crs=self.crs,
            width=self.width,
            height=self.height,
            transform=list(self.transform),
        )


def utc(value):
    return (
        pd.Timestamp(value).tz_localize("UTC")
        if pd.Timestamp(value).tzinfo is None
        else pd.Timestamp(value).tz_convert("UTC")
    )


def column_factor(unit):
    if unit not in COLUMN_UNITS:
        raise ValueError(
            f"Unsupported column units: {unit!r}; surface mole fractions are not columns"
        )
    return COLUMN_UNITS[unit]


def dewpoint_c(t_kelvin, q, p):
    """Magnus over liquid water: e=q*p/(0.622+0.378*q), Td=243.04*l/(17.625-l).

    e is converted Pa -> hPa; l=ln(e/6.1094). This is a liquid-water dew point,
    including below freezing, not frost point. Invalid/supersaturated inputs fail.
    """
    if not all(np.isfinite(x).all() for x in (t_kelvin, q, p)):
        raise ValueError("Non-finite dew-point inputs")
    if np.any(
        (t_kelvin < 173.15) | (t_kelvin > 343.15) | (q <= 0) | (q >= 1) | (p <= 0)
    ):
        raise ValueError("Invalid physical dew-point inputs")
    e = q * p / (0.622 + 0.378 * q)
    l = np.log(e / 610.94)
    td = 243.04 * l / (17.625 - l)
    if not np.isfinite(td).all() or np.any(td > t_kelvin - 273.15 + 0.5):
        raise ValueError("Invalid or supersaturated dew-point inputs")
    return td


def indices(grid, lat, lon):
    lat, lon = np.asarray(lat, dtype=float), np.asarray(lon, dtype=float)
    finite = np.isfinite(lat) & np.isfinite(lon) & (abs(lat) <= 90) & (abs(lon) <= 180)
    x, y = transform_points(
        "EPSG:4326",
        grid.crs,
        np.where(finite, lon, 0).tolist(),
        np.where(finite, lat, 0).tolist(),
    )
    rr, cc = rowcol(grid.affine, x, y)
    rr, cc = np.asarray(rr, dtype=np.int64), np.asarray(cc, dtype=np.int64)
    valid = finite & (rr >= 0) & (rr < grid.height) & (cc >= 0) & (cc < grid.width)
    return np.where(valid, rr, 0), np.where(valid, cc, 0), valid


def time_features(value):
    t = utc(value)
    hour = t.hour + t.minute / 60 + t.second / 3600
    phase = (t.dayofyear - 1 + hour / 24) / (366 if calendar.isleap(t.year) else 365)
    return np.array(
        [
            math.sin(2 * math.pi * hour / 24),
            math.cos(2 * math.pi * hour / 24),
            math.sin(2 * math.pi * phase),
            math.cos(2 * math.pi * phase),
        ],
        dtype=np.float32,
    )


def station_pair(target, lookup):
    t = utc(target)
    if t.minute or t.second or t.microsecond:
        raise ValueError("Station target must be a round UTC hour")
    left, right = t - pd.Timedelta(minutes=30), t + pd.Timedelta(minutes=30)
    if left.isoformat() not in lookup or right.isoformat() not in lookup:
        return None
    return [lookup[left.isoformat()], lookup[right.isoformat()]]


def assign_splits(records):
    dates = sorted({r.get("observation_date", r["target_time"][:10]) for r in records})
    n = len(dates)
    a, b = int(n * 0.7), int(n * 0.85)
    mapping = {
        d: "train" if i < a else "validation" if i < b else "test"
        for i, d in enumerate(dates)
    }
    for r in records:
        r["split"] = mapping[r.get("observation_date", r["target_time"][:10])]
    return mapping


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    manifests = cfg.get("geos_manifests", [cfg["geos_manifest"]] if cfg.get("geos_manifest") else [])
    if manifests:
        cfg["geos_files"], cfg["geos_valid_times"] = [], []
        for name in manifests:
            manifest_path = (path.parent / name).resolve()
            manifest = json.loads(manifest_path.read_text())
            if not manifest.get("files"):
                raise ValueError(f"Forecast manifest contains no downloaded runs: {manifest_path}")
            if manifest.get("failures") and not cfg.get("allow_partial_forecasts", False):
                raise ValueError("Forecast archive is incomplete; retry or explicitly set allow_partial_forecasts")
            cfg["geos_files"].extend(str((manifest_path.parent / f).resolve()) for f in manifest["files"])
            cfg["geos_valid_times"].extend(manifest["valid_times"])
        cfg["required_product"] = "forecast"
    for key in ["geos_files"]:
        cfg[key] = [str((path.parent / p).resolve()) for p in cfg[key]]
    for source in cfg["sources"].values():
        for key in ["labels", "observation_mask", "acquisition_sidecar"]:
            if source.get(key):
                source[key] = str((path.parent / source[key]).resolve())
    return cfg


def _inspect_geos(paths):
    lookup, inventories = {}, []
    for path in paths:
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            if (
                ds.attrs.get("product") not in ("analysis", "forecast")
                or ds.attrs.get("time_convention") != TIME_CONVENTION
            ):
                raise ValueError(f"{path}: expected hourly-mean analysis or forecast provenance")
            for axis in ["time", "lat", "lon"]:
                if axis not in ds.coords or ds[axis].ndim != 1:
                    raise ValueError(f"{path}: expected one-dimensional {axis}")
            for axis in ["lat", "lon"]:
                values = np.asarray(ds[axis], dtype=float)
                if (
                    len(values) < 2
                    or not np.isfinite(values).all()
                    or not (np.all(np.diff(values) > 0) or np.all(np.diff(values) < 0))
                    or not np.allclose(np.diff(values), np.diff(values)[0])
                ):
                    raise ValueError(f"{path}: {axis} must be regular and monotonic")
            ts = pd.to_datetime(ds.time.values, utc=True)
            if (
                not ts.is_monotonic_increasing
                or ts.has_duplicates
                or any(t.minute != 30 or t.second != 0 for t in ts)
            ):
                raise ValueError(f"{path}: invalid center-of-hour time axis")
            for i, t in enumerate(ts):
                if t.isoformat() in lookup:
                    raise ValueError(f"Duplicate GEOS timestamp: {t}")
                lookup[t.isoformat()] = {
                    "path": path,
                    "index": i,
                    "time": t.isoformat(),
                }
            vars = {
                k: {
                    "dims": list(v.dims),
                    "units": v.attrs.get("units"),
                    "long_name": v.attrs.get("long_name", ""),
                }
                for k, v in ds.data_vars.items()
            }
            meta_path = Path(path).with_suffix(".metadata.json")
            if meta_path.exists():
                meta = json.loads(meta_path.read_text())
                for key, actual in [
                    ("hours", len(ts)),
                    ("product", ds.attrs["product"]),
                    ("time_convention", TIME_CONVENTION),
                ]:
                    if meta.get(key) != actual:
                        raise ValueError(f"{meta_path}: {key} disagrees with NetCDF")
                for key, actual in [
                    ("valid_time_start", ts[0]),
                    ("valid_time_end", ts[-1]),
                ]:
                    if utc(meta[key]) != actual:
                        raise ValueError(f"{meta_path}: {key} disagrees with NetCDF")
                for k, unit in meta.get("units", {}).items():
                    if k not in vars or vars[k]["units"] != unit:
                        raise ValueError(f"{meta_path}: unit disagreement for {k}")
            inventories.append(
                {
                    "path": path,
                    "hours": len(ts),
                    "start": ts[0].isoformat(),
                    "end": ts[-1].isoformat(),
                    "shape": [ds.sizes["lat"], ds.sizes["lon"]],
                    "variables": vars,
                    "metadata_cross_checked": meta_path.exists(),
                }
            )
    return lookup, inventories


class ForecastLookup(dict):
    """Mapping of run IDs to per-run valid-time indexes; never combines runs."""


def inspect_geos(paths):
    analyses, runs, inventories = {}, {}, []
    products = set()
    reference_grid = None
    for path in paths:
        lookup, inventory = _inspect_geos([path])
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            product = ds.attrs["product"]
            products.add(product)
            grid = (ds.lat.values.copy(), ds.lon.values.copy())
            if reference_grid is not None and not all(np.array_equal(a,b) for a,b in zip(grid,reference_grid)):
                raise ValueError("GEOS files must use identical latitude/longitude coordinates")
            reference_grid = grid
            inventory[0]["product"] = product
            if product == "forecast":
                init = utc(ds.attrs["initialization_time"])
                if "forecast_reference_time" in ds.coords and utc(np.asarray(ds.forecast_reference_time.values).reshape(-1)[0]) != init:
                    raise ValueError("Forecast reference coordinate disagrees with initialization metadata")
                sidecar = Path(path).with_suffix(".metadata.json")
                if sidecar.exists():
                    metadata = json.loads(sidecar.read_text())
                    if utc(metadata["initialization_time"]) != init:
                        raise ValueError("Forecast sidecar initialization disagrees with NetCDF")
                run = ds.attrs.get("forecast_run", init.isoformat())
                version = ds.attrs.get("source_version", "unknown")
                run_key = f"{version}:{run}"
                if run_key in runs:
                    raise ValueError(f"Duplicate forecast run: {run_key}")
                if "lead_time" not in ds.coords:
                    raise ValueError("Forecast requires lead_time coordinate")
                expected = ds.time.values - init.tz_localize(None).to_datetime64()
                if not np.array_equal(ds.lead_time.values.astype("timedelta64[ns]"), expected.astype("timedelta64[ns]")):
                    raise ValueError("Forecast lead_time disagrees with initialization/valid time")
                for t, ref in lookup.items():
                    lead = (utc(t) - init).total_seconds()/3600
                    if not 0 < lead < 72:
                        raise ValueError("Forecast inputs must contain centers within the 0–72 hour horizon")
                    ref.update(product=product, forecast_run=run_key,
                               initialization_time=init.isoformat(), lead_time_hours=lead,
                               source_version=version,
                               available_at=ds.attrs.get("available_at") or None)
                runs[run_key] = lookup
                inventory[0].update(initialization_time=init.isoformat(), forecast_run=run_key)
            else:
                if set(analyses) & set(lookup):
                    raise ValueError("Duplicate GEOS analysis timestamp")
                analyses.update(lookup)
        inventories.extend(inventory)
    if len(products) > 1:
        raise ValueError("Do not mix analysis and forecast products in one preparation")
    return (ForecastLookup(runs) if runs else analyses), inventories


class Readers:
    """Process-local bounded handles; never pickle or share open readers."""

    def __init__(self):
        self.pid = os.getpid()
        self.handles = {}

    def open(self, kind, path):
        if self.pid != os.getpid():
            self.close()
            self.pid = os.getpid()
        key = (kind, path)
        if key not in self.handles:
            if len(self.handles) >= 4:
                self.handles.pop(next(iter(self.handles))).close()
            self.handles[key] = (
                xr.open_dataset(path, engine="h5netcdf")
                if kind == "nc"
                else rasterio.open(path)
            )
        return self.handles[key]

    def close(self):
        for h in self.handles.values():
            h.close()
        self.handles.clear()

    def __getstate__(self):
        return {"pid": os.getpid(), "handles": {}}


def regrid(
    array,
    source_transform,
    source_crs,
    grid,
    height=None,
    width=None,
    method=Resampling.bilinear,
):
    h, w = height or grid.height, width or grid.width
    bounds = array_bounds(grid.height, grid.width, grid.affine)
    transform = from_bounds(*bounds, w, h)
    dest = np.full((h, w), np.nan, dtype=np.float32)
    reproject(
        np.asarray(array, dtype=np.float32),
        dest,
        src_transform=source_transform,
        src_crs=source_crs,
        dst_transform=transform,
        dst_crs=grid.crs,
        src_nodata=np.nan,
        dst_nodata=np.nan,
        resampling=method,
    )
    return dest


def geos_fields(ref, names, readers, grid, coarse_shape):
    ds = readers.open("nc", ref["path"])
    lat = np.asarray(ds.lat)
    lon = np.asarray(ds.lon)
    dy, dx = abs(lat[1] - lat[0]), abs(lon[1] - lon[0])
    source_bounds = (
        min(lon) - dx / 2,
        min(lat) - dy / 2,
        max(lon) + dx / 2,
        max(lat) + dy / 2,
    )
    target_bounds = transform_bounds(
        grid.crs,
        "EPSG:4326",
        *array_bounds(grid.height, grid.width, grid.affine),
        densify_pts=21,
    )
    if any(
        [
            target_bounds[0] < source_bounds[0] - 1e-8,
            target_bounds[1] < source_bounds[1] - 1e-8,
            target_bounds[2] > source_bounds[2] + 1e-8,
            target_bounds[3] > source_bounds[3] + 1e-8,
        ]
    ):
        raise ValueError("GEOS cell footprints do not cover output grid")
    source_transform = from_bounds(*source_bounds, len(lon), len(lat))
    result = {}
    for name in names:
        da = ds[name]
        if set(da.dims) != {"time", "lat", "lon"}:
            raise ValueError(f"{name}: expected time/lat/lon dimensions")
        unit = da.attrs.get("units")
        if name in EXPECTED_UNITS and unit not in EXPECTED_UNITS[name]:
            raise ValueError(f"{name}: invalid units {unit}")
        a = np.asarray(
            da.isel(time=ref["index"]).transpose("lat", "lon"), dtype=np.float32
        )
        if lat[0] < lat[-1]:
            a = a[::-1, :]
        if lon[0] > lon[-1]:
            a = a[:, ::-1]
        if not np.isfinite(a).all():
            raise ValueError(f"Non-finite GEOS input: {name}")
        result[name] = regrid(a, source_transform, "EPSG:4326", grid, *coarse_shape)
        if not np.isfinite(result[name]).all():
            raise ValueError(f"Incomplete regridded GEOS input: {name}")
    return result


def prepare_geos_inputs(source, channels, cfg, grid, readers, references):
    """Shared training/inference preprocessing, including metadata-driven baselines."""
    from .source_adapters import source_config, BaselineBuilder
    cfg = source_config(source, cfg)
    baseline_builder = BaselineBuilder(cfg)
    needed = list(dict.fromkeys(channels + baseline_builder.variables))
    fields = [
        geos_fields(
            ref,
            needed,
            readers,
            grid,
            cfg.get("coarse_shape", [18, 17]),
        )
        for ref in references
    ]
    f = {k: sum(x[k] for x in fields) / len(fields) for k in needed}
    forecast = np.stack([f[k] for k in channels])
    units = readers.open("nc", references[0]["path"])
    baseline = baseline_builder.build(f, units)
    bounds = array_bounds(grid.height, grid.width, grid.affine)
    baseline = regrid(
        baseline,
        from_bounds(*bounds, baseline.shape[1], baseline.shape[0]),
        grid.crs,
        grid,
    )[None]
    if not np.isfinite(baseline).all():
        raise ValueError("Non-finite baseline")
    return forecast, baseline


class SourceDataset(Dataset):
    """One source, one split; prepared records have already passed input validation."""

    def __init__(
        self, bundle, source, split="train", normalization=None, allow_unready=False
    ):
        self.bundle = bundle
        self.source = source
        from .source_adapters import source_config, make_label_adapter, BaselineBuilder
        self.cfg = source_config(source, bundle["config"]["sources"][source])
        self.label_adapter = make_label_adapter(self.cfg)
        self.baseline_builder = BaselineBuilder(self.cfg)
        if not allow_unready:
            own_paths = set(bundle["config"]["geos_files"]) | {
                self.cfg[k]
                for k in ["labels", "observation_mask", "acquisition_sidecar"]
                if self.cfg.get(k)
            }
            for path, info in bundle.get("fingerprints", {}).items():
                if path not in own_paths:
                    continue
                stat = Path(path).stat()
                if stat.st_size != info["size"] or stat.st_mtime_ns != info["mtime_ns"]:
                    raise ValueError(
                        f"Input changed since preparation; rebuild catalog: {path}"
                    )
        self.report = bundle["reports"][source]
        if not allow_unready and not self.report["ready"]:
            raise ValueError(
                f"{source} is not ready: " + "; ".join(self.report["blockers"])
            )
        self.records = [
            r for r in bundle["indexes"][source] if split is None or r["split"] == split
        ]
        if not self.records and not allow_unready:
            raise ValueError(f"{source}: no {split} samples")
        self.grid = Grid(**self.cfg.get("grid", bundle["config"].get("grid", {})))
        self.grid.validate()
        self.readers = Readers()
        self.station_data = None
        self.normalization = normalization
        self.channels = self.cfg["predictors"]
        self.station_ids = self.report.get("station_ids", [])
        self.split = split
        self.product = bundle.get("product", "analysis")
        self.time_channels = 5 if self.product == "forecast" else 4
        if normalization is not None and normalization["signature"] != signature(
            bundle, source
        ):
            raise ValueError(
                "Normalization belongs to a different source/configuration/index"
            )

    def __len__(self):
        return len(self.records)

    def _raw(self, record):
        return prepare_geos_inputs(self.source, self.channels, self.cfg, self.grid, self.readers, record["geos"])

    def __getitem__(self, i):
        record = self.records[i]
        forecast, baseline = self._raw(record)
        targets, valid, extra = self.label_adapter.load_targets(record, self)
        targets = np.where(valid, targets, 0).astype(np.float32)
        if self.normalization:
            n = self.normalization
            forecast = (
                forecast - np.array(n["input_mean"], dtype=np.float32)[:, None, None]
            ) / np.array(n["input_std"], dtype=np.float32)[:, None, None]
            baseline = (baseline - n["target_mean"]) / n["target_std"]
            targets = np.where(valid, (targets - n["target_mean"]) / n["target_std"], 0)
        features = time_features(record["target_time"])
        provenance = {}
        if self.product == "forecast":
            features = np.append(features, np.float32(record["lead_time_hours"] / 72)).astype(np.float32)
            provenance = {"lead_time_hours": np.float32(record["lead_time_hours"]),
                          "initialization_time": record["initialization_time"],
                          "forecast_run": record["forecast_run"]}
        return {
            **provenance,
            "forecast": torch.from_numpy(forecast.astype(np.float32)),
            "baseline": torch.from_numpy(baseline.astype(np.float32)),
            "time_features": torch.from_numpy(features),
            "targets": torch.from_numpy(targets.astype(np.float32)),
            "valid": torch.from_numpy(valid),
            "source": self.source,
            "target_time": record["target_time"],
            "product": self.product,
            "timing_method": record["timing_method"],
            **extra,
        }

    def close(self):
        self.readers.close()


def signature(bundle, source):
    cfg = bundle["config"]["sources"][source]
    own_paths = set(bundle["config"]["geos_files"]) | {
        cfg[k]
        for k in ["labels", "observation_mask", "acquisition_sidecar"]
        if cfg.get(k)
    }
    data = {
        "product": bundle.get("product", "analysis"),
        "source_config": cfg,
        "grid": cfg.get("grid", bundle["config"].get("grid", {})),
        "source": source,
        "index": bundle["indexes"][source],
        "station_ids": bundle["reports"][source].get("station_ids", []),
        "fingerprints": {
            k: v for k, v in bundle.get("fingerprints", {}).items() if k in own_paths
        },
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


def fit_normalization(dataset):
    if dataset.split != "train" or dataset.normalization is not None:
        raise ValueError("Fit normalization on an unnormalized training dataset only")
    count = 0
    mean = None
    m2 = None
    tc = 0
    tm = 0.0
    t2 = 0.0
    for sample in dataset:
        x = (
            sample["forecast"]
            .numpy()
            .reshape(len(dataset.channels), -1)
            .astype(np.float64)
        )
        n = x.shape[1]
        mu = x.mean(1)
        ss = ((x - mu[:, None]) ** 2).sum(1)
        if mean is None:
            mean = mu
            m2 = ss
            count = n
        else:
            delta = mu - mean
            m2 += ss + delta**2 * count * n / (count + n)
            mean += delta * n / (count + n)
            count += n
        y = sample["targets"][sample["valid"]].numpy().astype(np.float64)
        if len(y):
            n = len(y)
            mu = float(y.mean())
            ss = float(((y - mu) ** 2).sum())
            delta = mu - tm
            t2 += ss + delta**2 * tc * n / (tc + n)
            tm += delta * n / (tc + n)
            tc += n
    if not count or not tc:
        raise ValueError(
            "No training inputs or valid training labels for normalization"
        )
    std = np.sqrt(m2 / count)
    std = np.where(std > 0, std, 1.0)
    ts = math.sqrt(t2 / tc)
    return dict(
        signature=signature(dataset.bundle, dataset.source),
        source=dataset.source,
        input_mean=mean.tolist(),
        input_std=std.tolist(),
        target_mean=tm,
        target_std=ts if ts > 0 else 1.0,
        training_samples=len(dataset),
        target_observations=tc,
    )


def _seed_worker(worker):
    seed = torch.initial_seed() % 2**32
    np.random.seed(seed)
    random.seed(seed)


def make_loader(
    bundle, source, split, normalization, *, batch_size=1, num_workers=0, seed=42
):
    if normalization is None:
        raise ValueError("Pass training-fitted normalization explicitly")
    dataset = SourceDataset(bundle, source, split, normalization)
    return DataLoader(
        dataset,
        batch_size=batch_size,
        shuffle=split == "train",
        num_workers=num_workers,
        generator=torch.Generator().manual_seed(seed),
        worker_init_fn=_seed_worker,
    )


def masked_loss(prediction, batch):
    if "rows" in batch:
        b = torch.arange(prediction.shape[0], device=prediction.device)[:, None]
        selected = prediction[b, 0, batch["rows"], batch["cols"]].unsqueeze(-1)
    else:
        selected = prediction
    target = batch["targets"]
    valid = batch["valid"] & torch.isfinite(target)
    if selected.shape != target.shape:
        raise ValueError("Prediction and target shapes disagree")
    if not valid.any():
        return None
    if not torch.isfinite(selected[valid]).all():
        raise ValueError("Non-finite supervised prediction")
    return (selected[valid] - target[valid]).square().mean()


def weighted_pass_time(date, tags):
    """Pixel-count-weighted pass midpoints, not per-pixel acquisition times."""
    count = int(tags.get("n_passes", "0"))
    if count == 0:
        return None
    passes = re.findall(
        r"(\d{2}:\d{2}:\d{2})-(\d{2}:\d{2}:\d{2}) UTC \(orbit \d+, (\d+) px\)",
        tags.get("passes_utc", ""),
    )
    if len(passes) != count:
        raise ValueError("Incomplete NO2 pass-time metadata")
    total, weight = 0.0, 0
    date = utc(date).normalize()
    for start, end, pixels in passes:
        start = date + pd.to_timedelta(start)
        end = date + pd.to_timedelta(end)
        if end < start:
            end += pd.Timedelta(days=1)
        pixels = int(pixels)
        total += ((start - date).total_seconds() + (end - date).total_seconds()) / 2 * pixels
        weight += pixels
    return (date + pd.Timedelta(seconds=total / weight)).isoformat() if weight else None


def nearest_geos_time(timestamp, lookup, tolerance_minutes=60):
    """Nearest analysis center within inclusive tolerance; ties choose earlier time."""
    if not lookup:
        return None
    timestamp = utc(timestamp)
    candidate = min(lookup, key=lambda t: (abs(utc(t) - timestamp), utc(t)))
    if abs(utc(candidate) - timestamp) > pd.Timedelta(minutes=tolerance_minutes):
        return None
    return candidate


def satellite_day(description):
    """Read compact dates or ISO timestamps without changing assumed-noon timing."""
    if len(description) == 8 and description.isdigit():
        return pd.to_datetime(description, format="%Y%m%d", utc=True).normalize()
    return pd.to_datetime(description, utc=True).normalize()


def resolve_satellite_labels(cfg):
    """Resolve local-first satellite labels and observation masks before fingerprinting."""
    cfg = copy.deepcopy(cfg)
    errors = {}
    for source, sc in cfg["sources"].items():
        if not sc:
            continue
        remote = sc.get("huggingface")
        if not remote:
            continue
        for field, remote_key in (("labels", "filename"), ("observation_mask", "mask_filename")):
            local = sc.get(field)
            if not local or Path(local).is_file():
                continue
            try:
                from huggingface_hub import hf_hub_download

                cached = hf_hub_download(
                    repo_id=remote["repo_id"],
                    repo_type="dataset",
                    filename=remote.get(remote_key, Path(local).name),
                    revision=remote.get("revision", "main"),
                )
                sc[field] = str(Path(cached).absolute())
            except Exception as exc:
                message = (
                    f"Local satellite {field} missing and Hugging Face fallback failed "
                    f"for {remote.get('repo_id')}: {exc}. Install huggingface_hub, "
                    "check network/access, or provide the local TIFF and prepare again."
                )
                errors[source] = errors.get(source, "") + message + " "
    return cfg, errors


def build_catalog(cfg):
    """Discover actual dates and preflight all candidate tensors; never train."""
    from .source_adapters import source_config, make_label_adapter, TimeMatcher, BaselineBuilder
    cfg = copy.deepcopy(cfg)
    cfg["sources"] = {name: source_config(name, sc) for name, sc in cfg["sources"].items()}
    cfg, download_errors = resolve_satellite_labels(cfg)
    lookup, geo_inventory = inspect_geos(cfg["geos_files"])
    product = "forecast" if isinstance(lookup, ForecastLookup) else "analysis"
    if cfg.get("required_product", product) != product:
        raise ValueError("GEOS product does not match required_product")
    if cfg.get("geos_valid_times"):
        allowed = {utc(t).isoformat() for t in cfg["geos_valid_times"]}
        if product == "forecast":
            lookup = ForecastLookup({run: {t: ref for t, ref in times.items() if t in allowed}
                                     for run, times in lookup.items()})
        else:
            lookup = {t: ref for t, ref in lookup.items() if t in allowed}
    bundle = {
        "product": product,
        "config": cfg,
        "geos_inventory": geo_inventory,
        "catalogs": {},
        "indexes": {},
        "reports": {},
    }
    fingerprints = {}
    paths = set(cfg["geos_files"])
    for sc in cfg["sources"].values():
        paths.update(
            sc[k]
            for k in ["labels", "observation_mask", "acquisition_sidecar"]
            if sc.get(k)
        )
    for path in sorted(paths):
        p = Path(path)
        if p.exists():
            fingerprints[path] = {
                "size": p.stat().st_size,
                "mtime_ns": p.stat().st_mtime_ns,
            }
    bundle["fingerprints"] = fingerprints
    for source, sc in cfg["sources"].items():
        adapter = make_label_adapter(sc)
        matcher = TimeMatcher(sc)
        baseline = BaselineBuilder(sc)
        grid = Grid(**sc.get("grid", cfg.get("grid", {})))
        grid.validate()
        report = {
            "ready": False,
            "blockers": [],
            "warnings": [],
            "excluded_samples": [],
            "target": sc["target_name"],
            "target_units": sc["target_units"],
            "grid": grid.as_dict(),
        }
        if source in download_errors:
            report["blockers"].append(download_errors[source])
        if product == "forecast":
            catalog_by_key, records = {}, []
            for run, times in lookup.items():
                run_catalog, run_records = adapter.catalog(grid, times, report, matcher)
                dates_by_band = {item.get("band"): item["date"] for item in run_catalog}
                for item in run_catalog:
                    catalog_by_key[json.dumps(item, sort_keys=True)] = item
                for record in run_records:
                    refs = record["geos"]
                    if len({ref["forecast_run"] for ref in refs}) != 1:
                        raise ValueError("A sample cannot mix forecast runs")
                    ref = refs[0]
                    record.update(forecast_run=run, initialization_time=ref["initialization_time"],
                                  source_version=ref["source_version"], available_at=ref["available_at"],
                                  lead_time_hours=sum(x["lead_time_hours"] for x in refs)/len(refs),
                                  product="forecast",
                                  observation_date=dates_by_band[record["band"]] if "band" in record else record["target_time"][:10])
                    records.append(record)
            catalog = list(catalog_by_key.values())
            records.sort(key=lambda rec: (rec["target_time"], rec["initialization_time"]))
        else:
            catalog, records = adapter.catalog(grid, lookup, report, matcher)
        report["product"] = product
        if baseline.method == "dewpoint":
            report["derivation"] = "Magnus liquid-water Td: e=q*p/(0.622+0.378*q); l=ln(e/610.94); Td=243.04*l/(17.625-l), degC"
        report["label_entries"] = len(catalog)
        report["temporal_matches"] = len(records)
        if not records:
            report["blockers"].append("No matching GEOS times for this source")
        # Only assess files that would be used for this source; missing dates in
        # another source never prevent a ready source from running.
        used = {ref["path"] for r in records for ref in r["geos"]}
        for inventory in geo_inventory:
            if inventory["path"] not in used:
                continue
            variables = inventory["variables"]
            needed = sc["predictors"] + baseline.variables
            missing = set(needed) - variables.keys()
            if missing:
                report["blockers"].append(
                    "Missing GEOS variables: " + ", ".join(sorted(missing))
                )
            try:
                baseline.validate(variables)
            except ValueError as exc:
                report["blockers"].append(str(exc))
        bundle["catalogs"][source] = catalog
        bundle["indexes"][source] = records
        bundle["reports"][source] = report
        if not report["blockers"]:
            ds = SourceDataset(bundle, source, split=None, allow_unready=True)
            kept = []
            try:
                for i, r in enumerate(records):
                    try:
                        sample = ds[i]
                        if not sample["valid"].any():
                            raise ValueError("No valid supervision")
                        kept.append(r)
                    except (ValueError, KeyError, rasterio.errors.RasterioError) as exc:
                        report["excluded_samples"].append(
                            {"target_time": r["target_time"], "reason": str(exc)}
                        )
            finally:
                ds.close()
            bundle["indexes"][source] = kept
            if not kept:
                report["blockers"].append(
                    "No usable samples after spatial/input/label validation"
                )
        else:
            # Temporal candidate indexes remain inspectable, never training-ready.
            report["warnings"].append(
                "Candidate samples are not input/label validated while blockers remain"
            )
        report["split_dates"] = assign_splits(bundle["indexes"][source])
        report["split_counts"] = {
            split: sum(r["split"] == split for r in bundle["indexes"][source])
            for split in ["train", "validation", "test"]
        }
        if not report["split_counts"]["train"]:
            report["blockers"].append("No training dates after chronological split")
        report["blockers"] = list(dict.fromkeys(report["blockers"]))
        report["ready"] = not report["blockers"]
    return bundle


def write_bundle(bundle, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    (output / "prepared.json").write_text(json.dumps(bundle, indent=2) + "\n")
    (output / "readiness.json").write_text(
        json.dumps(bundle["reports"], indent=2) + "\n"
    )
    for source in bundle["indexes"]:
        (output / f"{source}_catalog.json").write_text(
            json.dumps(bundle["catalogs"][source], indent=2) + "\n"
        )
        (output / f"{source}_samples.json").write_text(
            json.dumps(bundle["indexes"][source], indent=2) + "\n"
        )
