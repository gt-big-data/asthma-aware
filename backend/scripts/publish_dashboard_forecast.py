"""Publish a validated NASA run for the dashboard."""
import argparse
import os
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pandas as pd
import xarray as xr

from app.config import Settings
from app.services.geos_dashboard import PublishedForecast, load_run
from ingestion.geos_cf.fetch_forecast import region_area, run, validate_forecast


def prepare_run(source: Path) -> PublishedForecast:
    with xr.open_dataset(source) as data:
        from ingestion.geos_cf.fetch_forecast import VARIABLES
        required = [spec for spec in VARIABLES if spec.name in {"pm25", "o3", "no2", "so2"}]
        validate_forecast(data, 120, required)
        run_data = PublishedForecast(
            source_run=data.attrs["forecast_run"],
            issued_at=pd.Timestamp(data.attrs["initialization_time"]).to_pydatetime(),
            times=[pd.Timestamp(value, tz="UTC").to_pydatetime() for value in data.time.values],
            latitudes=data.lat.values.tolist(),
            longitudes=data.lon.values.tolist(),
            pm25=[
                [float(value) if isfinite(value) and value >= 0 else None for value in frame]
                for frame in data.pm25.transpose("time", "lat", "lon").values.reshape(120, -1)
            ],
        )

    now = datetime.now(timezone.utc)
    if run_data.issued_at > now or run_data.times[-1] + timedelta(minutes=30) <= now:
        raise ValueError("The forecast run has no usable current or future hours")
    if not any(value is not None for frame in run_data.pm25 for value in frame):
        raise ValueError("The forecast run has no usable PM2.5 values")
    return run_data


def publish(source: Path, output: Path) -> PublishedForecast:
    run_data = prepare_run(source)
    write_local_run(run_data, output)
    return run_data


def write_local_run(run_data: PublishedForecast, output: Path) -> None:
    if output.exists() and load_run(output).issued_at > run_data.issued_at:
        raise ValueError("The dashboard already has a newer forecast")
    output.parent.mkdir(parents=True, exist_ok=True)
    pending = output.with_name(f".{output.name}.{uuid4()}.tmp")
    try:
        pending.write_text(run_data.model_dump_json(), encoding="utf-8")
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Reuse an existing GEOS-CF forecast NetCDF")
    parser.add_argument("--output", type=Path, default=Path("data/dashboard/current.json"))
    parser.add_argument("--reference", type=Path, default=os.getenv("RISK_REFERENCE_PATH") or None, help="Fixed historical reference NPZ")
    parser.add_argument("--history", type=Path, nargs="+", help="NASA analysis files covering preceding hours")
    parser.add_argument("--model-dir", type=Path, help="Generate experimental layers using checkpoints in this directory")
    parser.add_argument("--models", nargs="+", choices=("temperature", "dewpoint", "no2", "o3"),
                        help="Select checkpoints; defaults to available supported models")
    parser.add_argument("--model-threads", type=int, default=2, help="CPU threads for optional model inference")
    args = parser.parse_args()
    if args.models and args.model_dir is None:
        parser.error("--models requires --model-dir")
    if args.model_threads < 1:
        parser.error("--model-threads must be positive")
    reference_object = os.getenv("RISK_REFERENCE_OBJECT", "").strip()
    if args.input is not None:
        source = args.input
    elif args.model_dir is not None or args.reference is not None or reference_object:
        from scripts.fetch_risk_forecast import download
        source = download()
    else:
        source = run(region_area("georgia"), Path("data/geos_cf"))
    if args.model_dir is not None:
        import torch
        from scripts.downscale_forecast import required_variables
        from scripts.fetch_model_forecast import enrich
        torch.set_num_threads(args.model_threads)
        source = enrich(source, required_variables(args.model_dir, args.models), Path("data/geos_cf"))
    bucket = Settings.from_env().forecast_gcs_bucket
    forecast = prepare_run(source)
    if not bucket:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent if not bucket else None) as temporary:
        risk = None
        history_artifact = None
        reference = args.reference
        if reference is None and reference_object:
            if not bucket:
                raise ValueError("RISK_REFERENCE_OBJECT requires FORECAST_GCS_BUCKET")
            from app.services.gcs_forecast import get_bucket
            reference = Path(temporary) / "reference.npz"
            get_bucket(bucket).blob(reference_object).download_to_filename(reference, timeout=30)
        if reference is not None:
            from app.services.risk_artifacts import build_artifact

            history = args.history
            if history is None:
                from scripts.fetch_risk_analysis import download
                first = pd.Timestamp(forecast.times[0]).tz_convert("UTC").tz_localize(None)
                history = [download(first - pd.Timedelta(hours=78 + 6 * 24), first)]
            risk = Path(temporary) / "risk.npz"
            build_artifact(
                [*history, source], reference, risk,
                issued_at=forecast.issued_at.isoformat(), source_run=forecast.source_run, product="forecast",
            )
            history_artifact = Path(temporary) / "history.npz"
            build_artifact(
                history, reference, history_artifact,
                issued_at=forecast.issued_at.isoformat(), source_run="historical-analysis", product="analysis",
            )
            forecast = forecast.model_copy(update={"risk_artifact": f"risk-{uuid4()}.npz"})
        layers = None
        if args.model_dir is not None:
            from scripts.downscale_forecast import write_layers

            layers = Path(temporary) / "downscaled.nc"
            write_layers(source, args.model_dir, layers, sources=args.models)
        if bucket:
            from app.services.gcs_forecast import publish_gcs

            publication = publish_gcs(bucket, source, forecast, layers=layers, risk=risk, history=history_artifact)
            print(f"Current forecast issued at {publication.issued_at}: gs://{bucket}/{publication.artifact}")
        else:
            if layers is not None:
                output_layers = args.output.parent / f"downscaled-{uuid4()}.nc"
                layers.replace(output_layers)
                print(f"Generated experimental layers at {output_layers}")
            if history_artifact is not None:
                archive = Path(Settings.from_env().risk_archive_dir)
                archive.mkdir(parents=True, exist_ok=True)
                history_artifact.replace(archive / f"analysis-{uuid4()}.npz")
            if risk is not None:
                risk.replace(args.output.parent / forecast.risk_artifact)
            write_local_run(forecast, args.output)
            print(f"Published GEOS-CF {forecast.source_run} to {args.output}")


if __name__ == "__main__":
    main()
