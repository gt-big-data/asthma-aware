"""Independent observation-supervised datasets for GEOS-CF hourly analyses.

Preparation creates a reproducible catalog; it never invents acquisition times,
forecast initialization times, observation masks, or compatible column baselines.
See README for the JSON configuration and preparation CLI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import calendar
import hashlib
import json
import math
import os
import random

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
    "o3": "total_o3_column",
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
    dates = sorted({r["target_time"][:10] for r in records})
    n = len(dates)
    a, b = int(n * 0.7), int(n * 0.85)
    mapping = {
        d: "train" if i < a else "validation" if i < b else "test"
        for i, d in enumerate(dates)
    }
    for r in records:
        r["split"] = mapping[r["target_time"][:10]]
    return mapping


def load_config(path):
    path = Path(path).resolve()
    cfg = json.loads(path.read_text())
    for key in ["geos_files"]:
        cfg[key] = [str((path.parent / p).resolve()) for p in cfg[key]]
    for source in cfg["sources"].values():
        for key in ["labels", "observation_mask", "acquisition_sidecar"]:
            if source.get(key):
                source[key] = str((path.parent / source[key]).resolve())
    return cfg


def inspect_geos(paths):
    lookup, inventories = {}, []
    for path in paths:
        with xr.open_dataset(path, engine="h5netcdf") as ds:
            if (
                ds.attrs.get("product") != "analysis"
                or ds.attrs.get("time_convention") != TIME_CONVENTION
            ):
                raise ValueError(f"{path}: expected hourly-mean analysis provenance")
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
                    ("product", "analysis"),
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


class SourceDataset(Dataset):
    """One source, one split; prepared records have already passed input validation."""

    def __init__(
        self, bundle, source, split="train", normalization=None, allow_unready=False
    ):
        self.bundle = bundle
        self.source = source
        self.cfg = bundle["config"]["sources"][source]
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
        if normalization is not None and normalization["signature"] != signature(
            bundle, source
        ):
            raise ValueError(
                "Normalization belongs to a different source/configuration/index"
            )

    def __len__(self):
        return len(self.records)

    def _raw(self, record):
        needed = list(
            dict.fromkeys(
                self.channels
                + (
                    MET[:3]
                    if self.source == "dewpoint"
                    else [self.cfg["baseline_variable"]]
                )
            )
        )
        fields = [
            geos_fields(
                ref,
                needed,
                self.readers,
                self.grid,
                self.cfg.get("coarse_shape", [18, 17]),
            )
            for ref in record["geos"]
        ]
        f = {k: sum(x[k] for x in fields) / len(fields) for k in needed}
        forecast = np.stack([f[k] for k in self.channels])
        units = self.readers.open("nc", record["geos"][0]["path"])
        if self.source in ("no2", "o3"):
            var = self.cfg["baseline_variable"]
            if (
                self.source == "o3"
                and self.cfg.get("baseline_quantity") != "total_o3_column"
            ):
                raise ValueError(
                    "O3 requires an explicitly declared total-column baseline"
                )
            baseline = f[var] * column_factor(units[var].attrs.get("units"))
        elif self.source == "temperature":
            baseline = f["air_temperature"] - 273.15
        else:
            baseline = dewpoint_c(
                f["air_temperature"], f["specific_humidity"], f["surface_pressure"]
            )
        bounds = array_bounds(self.grid.height, self.grid.width, self.grid.affine)
        baseline = regrid(
            baseline,
            from_bounds(*bounds, baseline.shape[1], baseline.shape[0]),
            self.grid.crs,
            self.grid,
        )[None]
        if not np.isfinite(baseline).all():
            raise ValueError("Non-finite baseline")
        return forecast, baseline

    def __getitem__(self, i):
        record = self.records[i]
        forecast, baseline = self._raw(record)
        if self.source in ("no2", "o3"):
            label = self.readers.open("tif", self.cfg["labels"])
            mask = self.readers.open("tif", self.cfg["observation_mask"])
            a = (
                label.read(record["band"], masked=True)
                .astype(np.float32)
                .filled(np.nan)
            )
            m = mask.read(record["band"], masked=True).filled(0) == 1
            a = np.where(m & np.isfinite(a), a, np.nan) * column_factor(
                self.cfg["label_units"]
            )
            targets = regrid(
                a, label.transform, label.crs, self.grid, method=Resampling.nearest
            )[None]
            valid = np.isfinite(targets)
            extra = {}
        else:
            if self.station_data is None:
                df = pd.read_parquet(self.cfg["labels"])
                df["target_time_utc"] = pd.to_datetime(df.target_time_utc, utc=True)
                self.station_data = df.set_index(["target_time_utc", "station_id"])
            idx = pd.MultiIndex.from_product(
                [[utc(record["target_time"])], self.station_ids],
                names=["target_time_utc", "station_id"],
            )
            obs = self.station_data.reindex(idx)
            p = self.source
            values = pd.to_numeric(obs[p + "_c"], errors="coerce").to_numpy(
                dtype=np.float32
            )
            rr, cc, inside = indices(
                self.grid, obs[p + "_latitude"], obs[p + "_longitude"]
            )
            valid = (
                obs[p + "_valid"].fillna(False).to_numpy(dtype=bool)
                & inside
                & np.isfinite(values)
            )[:, None]
            targets = values[:, None]
            extra = {"rows": torch.from_numpy(rr), "cols": torch.from_numpy(cc)}
        targets = np.where(valid, targets, 0).astype(np.float32)
        if self.normalization:
            n = self.normalization
            forecast = (
                forecast - np.array(n["input_mean"], dtype=np.float32)[:, None, None]
            ) / np.array(n["input_std"], dtype=np.float32)[:, None, None]
            baseline = (baseline - n["target_mean"]) / n["target_std"]
            targets = np.where(valid, (targets - n["target_mean"]) / n["target_std"], 0)
        return {
            "forecast": torch.from_numpy(forecast.astype(np.float32)),
            "baseline": torch.from_numpy(baseline.astype(np.float32)),
            "time_features": torch.from_numpy(time_features(record["target_time"])),
            "targets": torch.from_numpy(targets.astype(np.float32)),
            "valid": torch.from_numpy(valid),
            "source": self.source,
            "target_time": record["target_time"],
            "product": "analysis",
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


def build_catalog(cfg):
    """Discover actual dates and preflight all candidate tensors; never train."""
    lookup, geo_inventory = inspect_geos(cfg["geos_files"])
    bundle = {
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
        if source not in TARGETS:
            raise ValueError(f"Unknown source {source}")
        sc.setdefault(
            "predictors",
            (["no2"] if source == "no2" else ["o3"] if source == "o3" else []) + MET,
        )
        sc.setdefault(
            "baseline_variable",
            {
                "no2": "tropcol_no2",
                "o3": "totalcol_o3",
                "temperature": "air_temperature",
                "dewpoint": "derived",
            }[source],
        )
        grid = Grid(**sc.get("grid", cfg.get("grid", {})))
        grid.validate()
        report = {
            "ready": False,
            "blockers": [],
            "warnings": [],
            "excluded_samples": [],
            "target": TARGETS[source],
            "target_units": "mol/m2" if source in ("no2", "o3") else "degC",
            "grid": grid.as_dict(),
        }
        catalog = []
        records = []
        if source in ("no2", "o3"):
            sidecar = {}
            if sc.get("acquisition_sidecar"):
                for entry in json.loads(Path(sc["acquisition_sidecar"]).read_text()):
                    key = (
                        str(
                            (
                                Path(sc["acquisition_sidecar"]).parent / entry["file"]
                            ).resolve()
                        ),
                        int(entry["band"]),
                    )
                    if key in sidecar:
                        raise ValueError("Duplicate acquisition sidecar key")
                    sidecar[key] = utc(entry["acquisition_time"]).isoformat()
                report["warnings"].append(
                    "Acquisition metadata retained; assumed-noon matching remains active"
                )
            if not Path(sc["labels"]).exists():
                report["blockers"].append("Satellite label file is missing")
            else:
                with rasterio.open(sc["labels"]) as src:
                    if not src.crs:
                        raise ValueError("Satellite raster has no CRS")
                    unit = sc.get("label_units") or src.tags().get("units")
                    try:
                        column_factor(unit)
                        sc["label_units"] = unit
                    except ValueError as exc:
                        report["blockers"].append(str(exc))
                    mask_path = sc.get("observation_mask")
                    if not mask_path or not Path(mask_path).exists():
                        report["blockers"].append(
                            "Original-observation mask is required; filled labels are not observations"
                        )
                    else:
                        with rasterio.open(mask_path) as mask:
                            if (mask.crs, mask.transform, mask.shape, mask.count) != (
                                src.crs,
                                src.transform,
                                src.shape,
                                src.count,
                            ):
                                report["blockers"].append(
                                    "Observation mask grid/band count differs from labels"
                                )
                            else:
                                for band in range(1, mask.count + 1):
                                    vals = mask.read(band, masked=True).compressed()
                                    if not np.isin(vals, [0, 1]).all():
                                        report["blockers"].append(
                                            "Observation mask must contain only 0/1"
                                        )
                                        break
                                    if (
                                        mask.descriptions[band - 1]
                                        and mask.descriptions[band - 1]
                                        != src.descriptions[band - 1]
                                    ):
                                        report["blockers"].append(
                                            "Mask band dates differ from label dates"
                                        )
                                        break
                    dates = set()
                    for band, description in enumerate(src.descriptions, 1):
                        if not description:
                            raise ValueError(f'{sc["labels"]} band {band} lacks date')
                        fmt = (
                            "%Y%m%d"
                            if len(description) == 8 and description.isdigit()
                            else "%Y-%m-%d"
                        )
                        date = pd.to_datetime(description, format=fmt, utc=True)
                        if date.date() in dates:
                            raise ValueError(
                                "Duplicate satellite date; use an explicit orbit index instead"
                            )
                        dates.add(date.date())
                        noon = date + pd.Timedelta(hours=12, minutes=30)
                        known = sidecar.get((str(Path(sc["labels"]).resolve()), band))
                        catalog.append(
                            {
                                "file": sc["labels"],
                                "band": band,
                                "date": date.date().isoformat(),
                                "actual_acquisition_time": known,
                                "timing_method": "assumed_noon_interval",
                            }
                        )
                        if noon.isoformat() in lookup:
                            records.append(
                                {
                                    "target_time": noon.isoformat(),
                                    "band": band,
                                    "geos": [lookup[noon.isoformat()]],
                                    "actual_acquisition_time": known,
                                    "timing_method": "assumed_noon_interval",
                                }
                            )
        else:
            if not Path(sc["labels"]).exists():
                report["blockers"].append("Station label file is missing")
            else:
                df = pd.read_parquet(sc["labels"])
                required = [
                    "station_id",
                    "target_time_utc",
                    source + "_c",
                    source + "_valid",
                    source + "_latitude",
                    source + "_longitude",
                ]
                if any(k not in df for k in required):
                    raise ValueError(f"{source}: missing station columns")
                df["target_time_utc"] = pd.to_datetime(df.target_time_utc, utc=True)
                if df.duplicated(["station_id", "target_time_utc"]).any():
                    raise ValueError("Duplicate station/hour labels")
                if not df[source + "_valid"].dropna().isin([True, False]).all():
                    raise ValueError("Invalid station mask")
                rr, cc, inside = indices(
                    grid, df[source + "_latitude"], df[source + "_longitude"]
                )
                valid = (
                    df[source + "_valid"].fillna(False).to_numpy(dtype=bool)
                    & inside
                    & np.isfinite(pd.to_numeric(df[source + "_c"], errors="coerce"))
                )
                report["station_ids"] = sorted(
                    df.loc[valid, "station_id"].unique().tolist()
                )
                for timestamp in sorted(df.loc[valid, "target_time_utc"].unique()):
                    timestamp = utc(timestamp)
                    catalog.append(
                        {
                            "target_time": timestamp.isoformat(),
                            "date": timestamp.date().isoformat(),
                        }
                    )
                    refs = station_pair(timestamp, lookup)
                    if refs:
                        records.append(
                            {
                                "target_time": timestamp.isoformat(),
                                "geos": refs,
                                "timing_method": "interpolated_hourly_means_to_station_hour",
                            }
                        )
                if source == "dewpoint":
                    report["derivation"] = (
                        "Magnus liquid-water Td: e=q*p/(0.622+0.378*q); l=ln(e/610.94); Td=243.04*l/(17.625-l), degC"
                    )
        report["label_entries"] = len(catalog)
        report["temporal_matches"] = len(records)
        if not records:
            report["blockers"].append("No matching GEOS times for this source")
        if source == "o3" and sc.get("baseline_quantity") != "total_o3_column":
            report["blockers"].append(
                "Explicit total_o3_column baseline quantity declaration is required"
            )
        # Only assess files that would be used for this source; missing dates in
        # another source never prevent a ready source from running.
        used = {ref["path"] for r in records for ref in r["geos"]}
        for inventory in geo_inventory:
            if inventory["path"] not in used:
                continue
            variables = inventory["variables"]
            needed = sc["predictors"] + (
                MET[:3] if source == "dewpoint" else [sc["baseline_variable"]]
            )
            missing = set(needed) - variables.keys()
            if missing:
                report["blockers"].append(
                    "Missing GEOS variables: " + ", ".join(sorted(missing))
                )
            if source in ("no2", "o3") and sc["baseline_variable"] in variables:
                try:
                    column_factor(variables[sc["baseline_variable"]]["units"])
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
