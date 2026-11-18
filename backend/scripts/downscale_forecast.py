"""Produce experimental hourly GEOS-CF layers using the training pipeline."""
import hashlib
import json
from pathlib import Path

import netCDF4
import numpy as np
import pandas as pd
import torch
import xarray as xr
from asthma_aware.data_pipeline import Grid, prepare_geos_inputs, time_features
from asthma_aware.source_models import SourceModelConfig, build_source_models

TARGETS = {
    "temperature": ("temperature_c", "degC"),
    "dewpoint": ("dewpoint_c", "degC"),
    "no2": ("tropospheric_no2_column", "mol/m2"),
}


def write_layers(source: Path, model_dir: Path, output: Path) -> None:
    with xr.open_dataset(source) as data, netCDF4.Dataset(output, "w") as result:
        # Inference only opens the downloaded NetCDF. Training labels are unnecessary.
        class ForecastReader:
            def open(self, kind, path):
                return data

        reader = ForecastReader()
        times = pd.DatetimeIndex(data.time.values)
        issued_at = pd.Timestamp(data.attrs["initialization_time"])
        result.source_run = data.attrs["forecast_run"]
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
        for name, (target, units) in TARGETS.items():
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
            source_config = checkpoint["source_config"]
            channels = source_config["predictors"]
            norm = checkpoint["normalization"]
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
                    (grid.affine.a, grid.affine.e), (0.01, -0.01)
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
            missing_cells = 0
            for index, valid_at in enumerate(times):
                inputs, baseline = prepare_geos_inputs(
                    name, channels, source_config, grid, reader, [{"path": source, "index": index}]
                )
                inputs = (inputs - means[:, None, None]) / stds[:, None, None]
                baseline = (baseline - norm["target_mean"]) / norm["target_std"]
                with torch.inference_mode():
                    prediction = model(
                        torch.from_numpy(inputs[None]),
                        baseline=torch.from_numpy(baseline[None]),
                        time_features=torch.from_numpy(time_features(valid_at)[None]),
                    )[0, 0].numpy()
                prediction = prediction * norm["target_std"] + norm["target_mean"]
                if not np.isfinite(prediction).all():
                    raise ValueError(f"{name} produced non-finite values at {valid_at}")
                # A negative column is physically invalid. Keep it missing for consumers.
                if name == "no2":
                    missing_cells += int(np.count_nonzero(prediction < 0))
                    prediction = np.where(prediction >= 0, prediction, np.nan)
                    if not np.isfinite(prediction).any():
                        raise ValueError(f"{name} produced no usable cells at {valid_at}")
                variable[index] = prediction
            variable.masked_negative_cells = missing_cells
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
