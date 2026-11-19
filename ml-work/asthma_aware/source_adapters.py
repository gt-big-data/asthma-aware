"""Configurable label adapters, temporal matching, and physical baselines.

Imported lazily by data_pipeline after its shared spatial/IO utilities are defined.
Source names are used only in legacy configuration defaults, never for dispatch.
"""
import copy
import json
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import Resampling
import torch
from .data_pipeline import (
    MET, TARGETS, column_factor, dewpoint_c, indices, utc, regrid,
    satellite_day, weighted_pass_time, nearest_geos_time, station_pair,
)


def source_config(name, config):
    """Expand old source configurations; explicit fields enable new source names."""
    cfg = copy.deepcopy(config)
    defaults = {
        "no2": ("raster", "mol/m2", "assumed_noon_interval", {"method": "column", "variable": "tropcol_no2"}),
        "o3": ("station_table", "ppb", "hour_start_label_to_geos_center", {"method": "scaled_variable", "variable": "o3", "input_units": ["mol mol-1", "mol/mol"], "scale": 1e9}),
        "temperature": ("station_table", "degC", "interpolated_hourly_means_to_station_hour", {"method": "scaled_variable", "variable": "air_temperature", "input_units": ["k", "K"], "offset": -273.15}),
        "dewpoint": ("station_table", "degC", "interpolated_hourly_means_to_station_hour", {"method": "dewpoint"}),
    }
    if name in defaults:
        kind, unit, timing, baseline = defaults[name]
        cfg.setdefault("label_type", kind)
        cfg.setdefault("target_units", unit)
        cfg.setdefault("target_name", TARGETS[name])
        cfg.setdefault("timing_policy", timing)
        if "baseline" not in cfg:
            if "baseline_variable" in cfg and baseline["method"] != "dewpoint":
                baseline = dict(baseline, variable=cfg["baseline_variable"])
            cfg["baseline"] = baseline
        cfg.setdefault("predictors", ([name] if name in ("no2", "o3") else []) + MET)
        if name in ("temperature", "dewpoint"):
            cfg.setdefault("label_units", "degC")
        if kind == "station_table":
            cfg.setdefault("columns", {"value": "o3_ppb" if name == "o3" else name + "_c",
                "valid": name + "_valid", "latitude": name + "_latitude", "longitude": name + "_longitude"})
    for key in ("label_type", "target_units", "target_name", "timing_policy", "baseline", "predictors"):
        if key not in cfg:
            raise ValueError(f"{name}: missing source configuration {key}")
    return cfg


class BaselineBuilder:
    def __init__(self, cfg):
        self.spec = cfg["baseline"]
        self.method = self.spec["method"]
        if self.method not in ("column", "scaled_variable", "dewpoint"):
            raise ValueError(f"Unknown baseline method: {self.method}")
        self.variables = MET[:3] if self.method == "dewpoint" else [self.spec["variable"]]

    def validate(self, variables):
        for name in self.variables:
            if name not in variables:
                raise ValueError(f"Missing GEOS baseline variable: {name}")
        if self.method == "column":
            column_factor(variables[self.spec["variable"]]["units"])
        elif self.method == "scaled_variable":
            allowed = self.spec.get("input_units")
            if not allowed:
                raise ValueError("Scaled baselines require explicit input_units")
            if isinstance(allowed, str):
                allowed = [allowed]
            if variables[self.spec["variable"]]["units"] not in allowed:
                raise ValueError("Baseline input units do not match configured conversion")

    def build(self, fields, dataset):
        self.validate({k: {"units": dataset[k].attrs.get("units")} for k in self.variables})
        if self.method == "dewpoint":
            return dewpoint_c(*(fields[k] for k in self.variables))
        values = fields[self.spec["variable"]]
        if self.method == "column":
            return values * column_factor(dataset[self.spec["variable"]].attrs.get("units"))
        return values * self.spec.get("scale", 1) + self.spec.get("offset", 0)


class TimeMatcher:
    def __init__(self, cfg):
        self.policy = cfg["timing_policy"]
        allowed = {"assumed_noon_interval", "nearest_collection_within_1h", "weighted_pass_midpoints_within_1h",
                   "hour_start_label_to_geos_center", "interpolated_hourly_means_to_station_hour"}
        if self.policy not in allowed:
            raise ValueError(f"Unknown timing policy: {self.policy}")
        if cfg["label_type"] == "station_table" and self.policy not in {"hour_start_label_to_geos_center", "interpolated_hourly_means_to_station_hour"}:
            raise ValueError("Station tables require a station timing policy")
        if cfg["label_type"] == "raster" and self.policy in {"hour_start_label_to_geos_center", "interpolated_hourly_means_to_station_hour"}:
            raise ValueError("Rasters require a raster timing policy")

    def station_match(self, timestamp, lookup):
        if self.policy == "hour_start_label_to_geos_center":
            center = (timestamp + pd.Timedelta(minutes=30)).isoformat()
            refs = [lookup[center]] if center in lookup else None
            extra = {"interval_start": timestamp.isoformat(), "interval_end": (timestamp + pd.Timedelta(hours=1)).isoformat(), "geos_time": center}
        else:
            refs = station_pair(timestamp, lookup)
            extra = {}
        return {"target_time": timestamp.isoformat(), "geos": refs, "timing_method": self.policy, **extra} if refs else None

    def raster_match(self, observation, lookup):
        date = observation["date"]
        policy = self.policy
        known = observation.get("actual_acquisition_time")
        reported = None
        no_pass = False
        if policy == "nearest_collection_within_1h":
            tags = observation.get("tags", {})
            no_pass = tags.get("status", "").startswith("no satellite pass")
            if tags.get("status") == "observed" and tags.get("datetime"):
                reported = utc(tags["datetime"]).isoformat()
            # A sidecar is an explicit acquisition timestamp; rounded
            # raster timestamps remain separately identified as estimates.
            match_time = known or reported
            matched = nearest_geos_time(match_time, lookup) if match_time else None
        elif policy == "weighted_pass_midpoints_within_1h":
            pass_tags = observation.get("pass_tags", observation.get("tags", {}))
            no_pass = pass_tags.get("n_passes") == "0"
            reported = weighted_pass_time(date, pass_tags)
            match_time = known or reported
            matched = nearest_geos_time(match_time, lookup) if match_time else None
        elif policy == "assumed_noon_interval":
            noon = date + pd.Timedelta(hours=12, minutes=30)
            matched = noon.isoformat() if noon.isoformat() in lookup else None
        else:
            raise ValueError(f"Unknown satellite timing policy: {policy}")
        fallback = no_pass and not known
        if fallback:
            noon = date + pd.Timedelta(hours=12, minutes=30)
            matched = noon.isoformat() if noon.isoformat() in lookup else None
        metadata = {
            "timing_fallback_reason": "no_satellite_pass" if fallback else None,
            "actual_acquisition_time": known,
            "reported_collection_time": reported,
            "collection_time_precision": (
                "pixel_count_weighted_pass_midpoint" if policy == "weighted_pass_midpoints_within_1h"
                else "rounded_hour"
            ) if reported and not known else None,
            "timing_method": "assumed_noon_interval" if fallback else policy,
        }

        return matched, metadata


def label_factor(cfg, unit):
    if not unit:
        raise ValueError("Label units must be declared")
    if unit == cfg["target_units"]:
        return 1.0
    return column_factor(unit) / column_factor(cfg["target_units"])


class RasterLabels:
    def __init__(self, cfg):
        self.cfg = cfg

    def catalog(self, grid, lookup, report, matcher):
        sc = self.cfg
        catalog, records = [], []
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
                "Acquisition metadata retained; matching follows the configured policy"
            )
        if not Path(sc["labels"]).exists():
            report["blockers"].append("Satellite label file is missing")
        else:
            with rasterio.open(sc["labels"]) as src:
                if not src.crs:
                    raise ValueError("Satellite raster has no CRS")
                unit = sc.get("label_units") or src.tags().get("units")
                try:
                    label_factor(sc, unit)
                    sc["label_units"] = unit
                except ValueError as exc:
                    report["blockers"].append(str(exc))
                mask_band_tags = {}
                mask_path = sc.get("observation_mask")
                if not mask_path or not Path(mask_path).exists():
                    report["blockers"].append(
                        "Original-observation mask is required; filled labels are not observations"
                    )
                else:
                    with rasterio.open(mask_path) as mask:
                        mask_band_tags = {i: mask.tags(i) for i in mask.indexes}
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
                                    and satellite_day(mask.descriptions[band - 1])
                                    != satellite_day(src.descriptions[band - 1])
                                ):
                                    report["blockers"].append(
                                        "Mask band dates differ from label dates"
                                    )
                                    break
                dates = set()
                for band, description in enumerate(src.descriptions, 1):
                    if not description:
                        raise ValueError(f'{sc["labels"]} band {band} lacks date')
                    date = satellite_day(description)
                    if date.date() in dates:
                        raise ValueError(
                            "Duplicate satellite date; use an explicit orbit index instead"
                        )
                    dates.add(date.date())
                    matched, metadata = matcher.raster_match({
                        "date": date, "tags": src.tags(band),
                        "pass_tags": mask_band_tags.get(band, src.tags(band)),
                        "actual_acquisition_time": sidecar.get((str(Path(sc["labels"]).resolve()), band)),
                    }, lookup)
                    catalog.append({
                        "file": sc["labels"], "band": band,
                        "date": date.date().isoformat(), **metadata,
                    })
                    if matched is not None:
                        records.append({
                            "target_time": matched, "band": band,
                            "geos": [lookup[matched]], **metadata,
                            "time_offset_minutes": (
                                (utc(matched) - utc(metadata["actual_acquisition_time"] or metadata["reported_collection_time"])).total_seconds() / 60
                                if metadata["timing_method"] in ("nearest_collection_within_1h", "weighted_pass_midpoints_within_1h") else None
                            ),
                        })

        return catalog, records

    def load_targets(self, record, dataset):
        label = dataset.readers.open("tif", self.cfg["labels"])
        mask = dataset.readers.open("tif", self.cfg["observation_mask"])
        a = (
            label.read(record["band"], masked=True)
            .astype(np.float32)
            .filled(np.nan)
        )
        m = mask.read(record["band"], masked=True).filled(0) == 1
        a = np.where(m & np.isfinite(a), a, np.nan) * label_factor(self.cfg, self.cfg["label_units"])
        targets = regrid(
            a, label.transform, label.crs, dataset.grid, method=Resampling.nearest
        )[None]
        valid = np.isfinite(targets)
        extra = {}
        return targets, valid, extra


class StationTableLabels:
    def __init__(self, cfg):
        self.cfg = cfg

    def catalog(self, grid, lookup, report, matcher):
        sc, columns = self.cfg, self.cfg["columns"]
        if not Path(sc["labels"]).exists():
            report["blockers"].append("Station label file is missing")
            return [], []
        if sc.get("label_units") != sc["target_units"]:
            report["blockers"].append("Station label units must match target_units")
        df = pd.read_parquet(sc["labels"])
        required = ["station_id", "target_time_utc", *columns.values()]
        if any(k not in df for k in required):
            raise ValueError("Missing configured station columns")
        df["target_time_utc"] = pd.to_datetime(df.target_time_utc, utc=True)
        if df.target_time_utc.isna().any() or (df.target_time_utc != df.target_time_utc.dt.floor("h")).any():
            raise ValueError("Station timestamps must be exact UTC hour starts")
        if df.duplicated(["station_id", "target_time_utc"]).any():
            raise ValueError("Duplicate station/hour labels")
        if not df[columns["valid"]].dropna().isin([True, False]).all():
            raise ValueError("Invalid station mask")
        _, _, inside = indices(grid, df[columns["latitude"]], df[columns["longitude"]])
        valid = df[columns["valid"]].fillna(False).to_numpy(dtype=bool) & inside & np.isfinite(pd.to_numeric(df[columns["value"]], errors="coerce"))
        report["station_ids"] = sorted(df.loc[valid, "station_id"].unique().tolist())
        catalog, records = [], []
        for timestamp in sorted(df.loc[valid, "target_time_utc"].unique()):
            timestamp = utc(timestamp)
            catalog.append({"target_time": timestamp.isoformat(), "date": timestamp.date().isoformat()})
            record = matcher.station_match(timestamp, lookup)
            if record:
                records.append(record)
        if matcher.policy == "hour_start_label_to_geos_center":
            report["observation_time_convention"] = "one-hour average starting at target_time_utc"
        return catalog, records

    def load_targets(self, record, dataset):
        if dataset.station_data is None:
            df = pd.read_parquet(self.cfg["labels"])
            df["target_time_utc"] = pd.to_datetime(df.target_time_utc, utc=True)
            dataset.station_data = df.set_index(["target_time_utc", "station_id"])
        idx = pd.MultiIndex.from_product(
            [[utc(record["target_time"])], dataset.station_ids],
            names=["target_time_utc", "station_id"],
        )
        obs = dataset.station_data.reindex(idx)
        columns = self.cfg["columns"]
        values = pd.to_numeric(obs[columns["value"]], errors="coerce").to_numpy(
            dtype=np.float32
        )
        rr, cc, inside = indices(
            dataset.grid, obs[columns["latitude"]], obs[columns["longitude"]]
        )
        valid = (
            obs[columns["valid"]].fillna(False).to_numpy(dtype=bool)
            & inside
            & np.isfinite(values)
        )[:, None]
        targets = values[:, None]
        extra = {"rows": torch.from_numpy(rr), "cols": torch.from_numpy(cc)}

        return targets, valid, extra


LABEL_ADAPTERS = {"raster": RasterLabels, "station_table": StationTableLabels}


def make_label_adapter(cfg):
    try:
        adapter = LABEL_ADAPTERS[cfg["label_type"]]
    except KeyError:
        raise ValueError(f"Unknown label type: {cfg.get('label_type')}") from None
    return adapter(cfg)
