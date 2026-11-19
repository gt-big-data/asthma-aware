"""Produce experimental hourly GEOS-CF layers using the training pipeline."""
import hashlib
import json
import argparse
from pathlib import Path
from uuid import uuid4

import netCDF4
import numpy as np
import pandas as pd
import torch
import xarray as xr
from asthma_aware.data_pipeline import Grid, prepare_geos_inputs, time_features
from asthma_aware.source_models import SourceModelConfig, build_source_models
from asthma_aware.source_adapters import source_config as expand_config, BaselineBuilder, TimeMatcher

TARGETS = {
    "temperature": ("temperature_c", "degC"),
    "dewpoint": ("dewpoint_c", "degC"),
    "no2": ("tropospheric_no2_column", "mol/m2"),
    "o3": ("surface_o3_ppb", "ppb"),
}


def selected_sources(model_dir: Path, sources=None):
    names = list(sources) if sources is not None else [
        name for name in TARGETS if (model_dir / f"{name}_best.pt").is_file()
    ]
    if not names or len(set(names)) != len(names) or any(name not in TARGETS for name in names):
        raise ValueError("Select at least one supported, unique model source")
    for name in names:
        if not (model_dir / f"{name}_best.pt").is_file():
            raise FileNotFoundError(f"Missing checkpoint: {name}_best.pt")
    return names


def required_variables(model_dir: Path, sources=None):
    required = set()
    for name in selected_sources(model_dir, sources):
        checkpoint = torch.load(model_dir / f"{name}_best.pt", map_location="cpu", weights_only=True)
        cfg = expand_config(name, checkpoint["source_config"])
        required.update(cfg["predictors"])
        required.update(BaselineBuilder(cfg).variables)
    return required


def write_layers(source: Path, model_dir: Path, output: Path, *, sources=None, hours=None) -> None:
    """Write atomically; a failed model must not leave a consumable partial layer."""
    names = selected_sources(model_dir, sources)
    if hours is not None and hours < 1:
        raise ValueError("hours must be positive")
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(f".{output.name}.{uuid4()}.tmp")
    try:
        _write_layers(source, model_dir, pending, names, hours)
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)


def _write_layers(source, model_dir, output, names, hours):
    with xr.open_dataset(source) as data, netCDF4.Dataset(output, "w") as result:
        # Inference only opens the downloaded NetCDF. Training labels are unnecessary.
        class ForecastReader:
            def open(self, kind, path):
                return data

        reader = ForecastReader()
        times = pd.DatetimeIndex(data.time.values)[:hours]
        if not len(times):
            raise ValueError("Forecast has no hours")
        analysis = data.attrs.get("product") == "analysis"
        issued_at = pd.Timestamp(data.attrs.get("initialization_time", data.attrs.get("downloaded_at", "1970-01-01")))
        result.product = "analysis" if analysis else "forecast"
        with source.open("rb") as handle:
            result.source_sha256 = hashlib.file_digest(handle, "sha256").hexdigest()
        result.source_run = data.attrs.get("forecast_run", "historical-analysis")
        result.issued_at = issued_at.isoformat()
        result.experimental = 1
        result.forecast_skill_validated = 0
        result.Conventions = "CF-1.8"
        result.createDimension("time", len(times))
        time = result.createVariable("time", "f8", ("time",))
        time.units = "hours since 1970-01-01 00:00:00 UTC"
        time.standard_name = "time"
        time[:] = (times - pd.Timestamp("1970-01-01")) / pd.Timedelta(hours=1)

        grid = None
        for name in names:
            target, units = TARGETS[name]
            checkpoint_path = model_dir / f"{name}_best.pt"
            with checkpoint_path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
                handle.seek(0)
                checkpoint = torch.load(handle, map_location="cpu", weights_only=True)
            if (checkpoint["source"], checkpoint["target"], checkpoint["target_units"]) != (name, target, units):
                raise ValueError(f"Unexpected checkpoint source, target or units for {name}")
            config = SourceModelConfig(**checkpoint["model_config"])
            if tuple(config.target_names) != (target,):
                raise ValueError(f"Unexpected output channels for {name}")
            source_config = expand_config(name, checkpoint["source_config"])
            TimeMatcher(source_config)  # Reject unsupported or inconsistent timing policies.
            if (source_config["target_name"], source_config["target_units"]) != (target, units):
                raise ValueError(f"Source configuration target mismatch for {name}")
            channels = source_config["predictors"]
            norm = checkpoint["normalization"]
            if norm.get("signature") != checkpoint["data_signature"] or norm.get("source") != name:
                raise ValueError(f"Normalization provenance mismatch for {name}")
            means = np.asarray(norm["input_mean"], dtype=np.float32)
            stds = np.asarray(norm["input_std"], dtype=np.float32)
            if (len(channels) != config.forecast_channels or means.shape != (len(channels),)
                    or stds.shape != means.shape or not np.isfinite(means).all()
                    or not np.isfinite(stds).all() or np.any(stds <= 0)
                    or not np.isfinite([norm["target_mean"], norm["target_std"]]).all()
                    or norm["target_std"] <= 0):
                raise ValueError(f"Invalid checkpoint normalization for {name}")

            model_grid = Grid(**checkpoint["grid"])
            model_grid.validate()
            if grid is None:
                grid = model_grid
                if grid.crs != "EPSG:4326" or not np.allclose(
                    (grid.affine.a, grid.affine.b, grid.affine.d, grid.affine.e), (0.01, 0, 0, -0.01)
                ):
                    raise ValueError("Expected a north-up 0.01-degree grid")
                write_grid(result, grid)
            elif model_grid != grid:
                raise ValueError(f"{name} does not use the same output grid")

            model = build_source_models({name: config})[name].eval()
            model.load_state_dict(checkpoint["model_state"], strict=True)
            variable = result.createVariable(
                target, "f4", ("time", "lat", "lon"), zlib=True, complevel=2,
                chunksizes=(1, grid.height, grid.width), fill_value=np.float32(np.nan),
            )
            variable.units = units
            variable.checkpoint_sha256 = digest
            variable.training_data_signature = checkpoint["data_signature"]
            variable.training_product = checkpoint["product"]
            variable.checkpoint_epoch = checkpoint["epoch"]
            variable.predictors = json.dumps(channels)
            variable.timing_policy = source_config["timing_policy"]
            variable.forecast_skill_validated = int(checkpoint.get("forecast_skill_validated", False))
            variable.time_representation = "center of hourly interval"
            missing_cells = 0
            missing_hours = 0
            for index, valid_at in enumerate(times):
                if analysis and any(not np.isfinite(data[key].isel(time=index)).all()
                                    for key in dict.fromkeys(channels + BaselineBuilder(source_config).variables)):
                    variable[index] = np.nan
                    missing_hours += 1
                    continue
                inputs, baseline = prepare_geos_inputs(
                    name, channels, source_config, grid, reader, [{"path": source, "index": index}]
                )
                inputs = (inputs - means[:, None, None]) / stds[:, None, None]
                baseline = (baseline - norm["target_mean"]) / norm["target_std"]
                # Ozone station labels name the hour's start; NASA names its center.
                feature_time = valid_at
                if source_config["timing_policy"] == "hour_start_label_to_geos_center":
                    feature_time -= pd.Timedelta(minutes=30)
                with torch.inference_mode():
                    prediction = model(
                        torch.from_numpy(inputs[None]),
                        baseline=torch.from_numpy(baseline[None]),
                        time_features=torch.from_numpy(time_features(feature_time)[None]),
                    )[0, 0].numpy()
                prediction = prediction * norm["target_std"] + norm["target_mean"]
                if not np.isfinite(prediction).all():
                    raise ValueError(f"{name} produced non-finite values at {valid_at}")
                # Negative concentrations/columns are physically invalid.
                if name in {"no2", "o3"}:
                    missing_cells += int(np.count_nonzero(prediction < 0))
                    prediction = np.where(prediction >= 0, prediction, np.nan)
                    if not np.isfinite(prediction).any():
                        raise ValueError(f"{name} produced no usable cells at {valid_at}")
                variable[index] = prediction
                if (index + 1) % 24 == 0:
                    print(f"Downscaling {name}: {index + 1}/{len(times)} hours", flush=True)
            variable.masked_negative_cells = missing_cells
            variable.missing_input_hours = missing_hours
            del model, checkpoint
            print(f"Generated {target} for {len(times)} hours", flush=True)


def write_grid(result: netCDF4.Dataset, grid: Grid) -> None:
    transform = grid.affine
    for name, size, first, step, units in (
        ("lat", grid.height, transform.f, transform.e, "degrees_north"),
        ("lon", grid.width, transform.c, transform.a, "degrees_east"),
    ):
        result.createDimension(name, size)
        coordinates = result.createVariable(name, "f8", (name,))
        coordinates.standard_name = {"lat": "latitude", "lon": "longitude"}[name]
        coordinates.units = units
        coordinates[:] = first + (np.arange(size) + 0.5) * step


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--model-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--models", nargs="+", choices=tuple(TARGETS))
    parser.add_argument("--hours", type=int, help="Limit inference for a smoke test; publisher always writes all hours")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.threads < 1:
        parser.error("--threads must be positive")
    torch.set_num_threads(args.threads)
    write_layers(args.input, args.model_dir, args.output, sources=args.models, hours=args.hours)
