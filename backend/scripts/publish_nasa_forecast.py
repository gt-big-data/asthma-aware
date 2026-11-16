"""Bounded NASA raw forecast ingestion; no ML, scores or synthetic fallback.

Run from backend/: python -m scripts.publish_nasa_forecast --help
Cloud Run uses NASA_RAW_BUCKET and NASA_OUTPUT_DIR with attached ADC identity.
The normal entry point enforces a whole-process deadline beneath Run's 300s cap.
"""
import argparse
from datetime import date
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def parse_bbox(value):
    try:
        numbers = [float(v) for v in value.split(",")]
        if len(numbers) != 4:
            raise ValueError()
        return dict(zip(("min_lat", "max_lat", "min_lon", "max_lon"), numbers))
    except ValueError:
        raise argparse.ArgumentTypeError("Use MIN_LAT,MAX_LAT,MIN_LON,MAX_LON") from None


def worker(args):
    from forecast_pipeline.pipeline import ingest, publish_raw, validate_raw
    from forecast_pipeline.storage import GCSArtifactStore, LocalArtifactStore
    store = GCSArtifactStore(args.gcs_bucket) if args.gcs_bucket else LocalArtifactStore(Path(args.output_dir) / "catalog")
    if args.input:
        path = args.input
        metadata = validate_raw(path)
    else:
        path, metadata = ingest(args.output_dir, bbox=args.bbox, hours=args.hours, run_date=args.run, store=store)
    result = publish_raw(store, path, metadata)
    print(json.dumps({"event": "nasa_ingestion_complete", "status": "success", "source": "NASA GEOS-CF v2",
        "run_id": metadata["forecast_run"], "promoted": result["promoted"],
        "product": "raw", "ml_status": "pending", "storage": "gcs" if args.gcs_bucket else "local"}))
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Existing real NASA NetCDF plus its metadata sidecar")
    parser.add_argument("--run", type=date.fromisoformat, help="Pin a NASA 09Z run date")
    parser.add_argument("--bbox", type=parse_bbox, help="Atlanta-area MIN_LAT,MAX_LAT,MIN_LON,MAX_LON")
    parser.add_argument("--hours", type=int, choices=range(1, 121), default=48)
    parser.add_argument("--deadline", type=int, choices=range(10, 241), default=240)
    parser.add_argument("--output-dir", default=os.getenv("NASA_OUTPUT_DIR", "data/nasa" if os.name == "nt" else "/tmp/nasa"))
    parser.add_argument("--gcs-bucket", default=os.getenv("NASA_RAW_BUCKET", ""))
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        try:
            worker(args)
            return 0
        except Exception as exc:
            # Do not dump provider responses, data arrays, credentials or URLs.
            print(json.dumps({"event": "nasa_ingestion_failed", "status": "failed",
                "error_type": type(exc).__name__, "ml_status": "pending"}), file=sys.stderr)
            return 1
    arguments = list(argv) if argv is not None else sys.argv[1:]
    proc = subprocess.Popen([sys.executable, "-m", "scripts.publish_nasa_forecast", *arguments, "--worker"],
                            **({"start_new_session": True} if sys.platform != "win32" else {}))
    try:
        return proc.wait(timeout=args.deadline)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if sys.platform == "win32":
            try:
                killed = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
                if killed.returncode:
                    proc.kill()
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=10)
        print(json.dumps({"event": "nasa_ingestion_failed", "status": "deadline_exceeded"}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
