"""Publish actual supplied ML output files; never execute model inference."""
import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


def worker(args):
    from forecast_pipeline.contracts import MAX_MANIFEST_BYTES
    from forecast_pipeline.ml_handoff import publish_ml_outputs
    from forecast_pipeline.storage import GCSArtifactStore
    if not 0 < args.manifest.stat().st_size <= MAX_MANIFEST_BYTES:
        raise ValueError("ML manifest exceeds size bound")
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    result = publish_ml_outputs(manifest, args.files_root, GCSArtifactStore(args.raw_bucket),
        GCSArtifactStore(args.processed_bucket), GCSArtifactStore(args.delivery_bucket))
    print(json.dumps({"event": "ml_artifact_publication_complete", "run_id": result["manifest"]["run_id"],
                      "promoted": result["promoted"], "model_executed": False}))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--files-root", type=Path, required=True)
    parser.add_argument("--raw-bucket", default=os.getenv("NASA_RAW_BUCKET", ""))
    parser.add_argument("--processed-bucket", default=os.getenv("FORECAST_PROCESSED_BUCKET", ""))
    parser.add_argument("--delivery-bucket", default=os.getenv("FORECAST_DELIVERY_BUCKET", ""))
    parser.add_argument("--deadline", type=int, choices=range(10, 241), default=240)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        try:
            worker(args)
            return 0
        except Exception as exc:
            print(json.dumps({"event": "ml_artifact_publication_failed", "error_type": type(exc).__name__}), file=sys.stderr)
            return 1
    arguments = list(argv) if argv is not None else sys.argv[1:]
    proc = subprocess.Popen([sys.executable, "-m", "scripts.publish_processed_forecast", *arguments, "--worker"],
        **({"start_new_session": True} if sys.platform != "win32" else {}))
    try:
        return proc.wait(timeout=args.deadline)
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        if sys.platform == "win32":
            try:
                result = subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10)
                if result.returncode:
                    proc.kill()
            except (OSError, subprocess.TimeoutExpired):
                proc.kill()
        else:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.wait(timeout=10)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
