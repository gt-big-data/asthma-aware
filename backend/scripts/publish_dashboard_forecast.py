"""Publish a validated NASA run for the dashboard."""
import argparse
import logging
import os
import shutil
from datetime import datetime, timedelta, timezone
from math import isfinite
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pandas as pd
import xarray as xr

from app.config import Settings
from app.services.geos_dashboard import ForecastInput, PublishedForecast, load_run
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
            input_sources=[ForecastInput(
                variable=name,
                source_run=data[name].attrs.get("forecast_run") or data.attrs["forecast_run"],
                issued_at=pd.Timestamp(data[name].attrs.get("initialization_time") or data.attrs["initialization_time"]).to_pydatetime(),
                collection=data[name].attrs.get("source_collection") or None,
            ) for name in data.data_vars],
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
    parser.add_argument("--model-dir", type=Path, default=os.getenv("DOWNSCALE_MODEL_DIR") or None,
                        help="Generate model layers; defaults to DOWNSCALE_MODEL_DIR")
    parser.add_argument("--model-prefix", default=os.getenv("MODEL_GCS_PREFIX"), help="Versioned GCS checkpoint prefix; verifies the pinned manifest")
    parser.add_argument("--models", nargs="+", choices=("temperature", "dewpoint", "no2", "o3"),
                        help="Select checkpoints; defaults to available supported models")
    parser.add_argument("--model-threads", type=int, default=2, help="CPU threads for optional model inference")
    parser.add_argument("--no2-surface", choices=("native", "column-scaled"),
                        default=os.getenv("RISK_NO2_SURFACE", "").strip() or "native",
                        help="column-scaled: rescale NO2 by the learned column (needs o3 and no2 checkpoints "
                             "and a reference fitted the same way); defaults to RISK_NO2_SURFACE or native")
    args = parser.parse_args()
    if args.models and args.model_dir is None:
        parser.error("--models requires --model-dir")
    if args.model_threads < 1:
        parser.error("--model-threads must be positive")
    if args.no2_surface == "column-scaled" and args.model_dir is None:
        parser.error("--no2-surface column-scaled requires --model-dir")
    if args.model_prefix:
        if args.model_dir is None or not Settings.from_env().forecast_gcs_bucket:
            parser.error("--model-prefix requires --model-dir and FORECAST_GCS_BUCKET")
        from scripts.fetch_checkpoints import download as download_checkpoints
        from app.services.gcs_forecast import get_bucket
        download_checkpoints(get_bucket(Settings.from_env().forecast_gcs_bucket), args.model_prefix, args.model_dir, args.models)
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
    fine_active = False
    if args.model_dir is not None:
        from scripts.downscale_forecast import selected_sources
        fine_active = "o3" in selected_sources(args.model_dir, args.models)
        if args.no2_surface == "column-scaled" and not (fine_active and "no2" in selected_sources(args.model_dir, args.models)):
            raise ValueError("Column-scaled NO2 needs both the o3 and no2 checkpoints")
    bucket = Settings.from_env().forecast_gcs_bucket
    forecast = prepare_run(source)
    if not bucket:
        args.output.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=args.output.parent if not bucket else None) as temporary:
        layers = None
        risk_source = source
        if args.model_dir is not None:
            from scripts.prepare_fine_risk import prepare_layers
            layers = Path(temporary) / "downscaled.nc"
            shutil.copyfile(prepare_layers(source, args.model_dir, args.models), layers)
            if fine_active:
                from app.services.fine_risk import write_surface
                risk_source = write_surface(source, layers, Path(temporary) / "surface.nc", no2_mode=args.no2_surface)
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
                if fine_active:
                    from scripts.fetch_model_analysis import download
                else:
                    from scripts.fetch_risk_analysis import download
                first = pd.Timestamp(forecast.times[0]).tz_convert("UTC").tz_localize(None)
                window = (first - pd.Timedelta(hours=78 + 6 * 24), first)
                history = [download(*window, no2_column=True) if args.no2_surface == "column-scaled" else download(*window)]
            if fine_active:
                from scripts.prepare_fine_risk import prepare
                from app.services.fine_risk import build_fine_artifact
                history = [prepare(path, args.model_dir, no2_mode=args.no2_surface) for path in history]
                build_artifact = build_fine_artifact
            risk = Path(temporary) / "risk.npz"
            build_artifact(
                [*history, risk_source], reference, risk,
                issued_at=forecast.issued_at.isoformat(), source_run=forecast.source_run, product="forecast",
            )
            history_artifact = Path(temporary) / "history.npz"
            build_artifact(
                history, reference, history_artifact,
                issued_at=forecast.issued_at.isoformat(), source_run="historical-analysis", product="analysis",
            )
            forecast = forecast.model_copy(update={"risk_artifact": f"risk-{uuid4()}.npz"})
        if fine_active and reference is None:
            raise ValueError("Activating fine-grid risk requires a fixed downscaled reference")
        map_fields = None
        if risk is not None:
            from app.services.map_fields import write_map_fields
            map_fields = Path(temporary) / "map-fields.nc"
            map_digest = write_map_fields(source, layers, map_fields)
            forecast = forecast.model_copy(update={"map_artifact": f"map-fields-{uuid4()}.nc", "map_artifact_sha256": map_digest})
        if bucket:
            from app.services.gcs_forecast import publish_gcs

            publication = publish_gcs(bucket, source, forecast, layers=layers, risk=risk, history=history_artifact, map_fields=map_fields)
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
            if map_fields is not None:
                map_fields.replace(args.output.parent / forecast.map_artifact)
            write_local_run(forecast, args.output)
            print(f"Published GEOS-CF {forecast.source_run} to {args.output}")

    # Trends use an independent analysis pointer, so their freshness is not tied
    # to the forecast initialization or the ML/risk artifact's history window.
    try:
        from scripts.publish_environmental_trends import publish_latest
        analysis = publish_latest()
        print(f"Environmental trends analysis through {analysis.observed_through.isoformat()}")
    except Exception:
        logging.getLogger(__name__).exception("Environmental trends refresh failed; kept the last published analysis")


if __name__ == "__main__":
    main()
