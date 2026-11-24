"""Explicit model rollout using the current immutable NASA source, under a lease.

Run only during a controlled release with scheduled publication paused. Regular
publication continues to use --only-newer and live NASA ingestion.
"""
import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

from app.config import Settings
from app.services.gcs_forecast import CURRENT, Publication, get_bucket
from app.services.forecast_publication_guard import publication_lease


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--models", nargs="+", required=True)
    parser.add_argument("--analysis-lead-hours", type=float)
    parser.add_argument("--model-threads", type=int, default=4)
    args = parser.parse_args()
    bucket_name = Settings.from_env().forecast_gcs_bucket
    if not bucket_name:
        parser.error("Model activation requires FORECAST_GCS_BUCKET")
    bucket = get_bucket(bucket_name)
    with publication_lease(bucket) as acquired:
        if not acquired:
            raise RuntimeError("Another publisher holds the lease; wait for it to finish")
        pointer = Publication.model_validate_json(bucket.blob(CURRENT).download_as_bytes(timeout=10))
        print(f"Activating models using immutable source from publication {pointer.publication_id}", flush=True)
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "forecast.nc"
            bucket.blob(f"raw/geos-cf/{pointer.publication_id}/forecast.nc").download_to_filename(source, timeout=120)
            arguments = ["publish_dashboard_forecast", "--input", str(source),
                         "--model-dir", "data/models", "--models", *args.models,
                         "--model-threads", str(args.model_threads)]
            if args.analysis_lead_hours is not None:
                arguments += ["--analysis-lead-hours", str(args.analysis_lead_hours)]
            original = sys.argv
            try:
                sys.argv = arguments
                from scripts.publish_dashboard_forecast import main as publish
                publish()
            finally:
                sys.argv = original


if __name__ == "__main__":
    main()
