"""Check usable published coverage after allowing the NASA publication window."""
import os
from datetime import datetime, timedelta, timezone
from app.services.gcs_forecast import load_gcs_run, get_bucket

MAX_AGE_HOURS = 40


def check(run, bucket, now):
    age = now - run.issued_at
    if age < timedelta(0) or age > timedelta(hours=MAX_AGE_HOURS):
        raise RuntimeError(f"Published NASA forecast has invalid age or is more than {MAX_AGE_HOURS} hours old")
    if not run.risk_artifact or not run.map_artifact:
        raise RuntimeError("Published forecast lacks risk or map fields")
    if not all(bucket.blob(key).exists(timeout=10) for key in (run.risk_artifact, run.map_artifact)):
        raise RuntimeError("Published forecast references missing objects")
    if not any(valid + timedelta(minutes=30) > now for valid in run.times):
        raise RuntimeError("Published forecast has no remaining predictions")


def main():
    name = os.environ["FORECAST_GCS_BUCKET"]
    run = load_gcs_run(name)
    check(run, get_bucket(name), datetime.now(timezone.utc))
    print("freshness_probe status=success run_id=" + run.source_run)


if __name__ == "__main__":
    main()
