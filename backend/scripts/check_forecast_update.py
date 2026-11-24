"""Probe NASA metadata and launch a pinned publisher only for a newer ready run."""
import argparse
import logging
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from google.api_core.exceptions import NotFound

from app.services.gcs_forecast import CURRENT, Publication, get_bucket
from ingestion.geos_cf import fetch_analysis as analysis, fetch_forecast as nasa

logger = logging.getLogger(__name__)
# Required by the deployed surface-risk pipeline and O3 checkpoint. Keep in sync
# when activating new models; no Torch import or checkpoint download in this probe.
REQUIRED_VARIABLES = frozenset({
    "o3", "no2", "so2", "pm25", "air_temperature", "specific_humidity",
    "surface_pressure", "pbl_height",
})


def current_publication(bucket):
    try:
        return Publication.model_validate_json(bucket.blob(CURRENT).download_as_bytes(timeout=10))
    except NotFound:
        return None


def ready_run(current, now):
    """Metadata readiness is a preflight; the publisher still validates actual data."""
    url = nasa.collection_url(nasa.AQC_COLLECTION)
    names, times, _ = nasa.remote_call(nasa._read_inventory, url, what="Checking latest NASA run", retries=2)
    candidate = nasa.infer_init_time(times)
    issued = candidate.tz_localize("UTC")
    if issued > now or issued + pd.Timedelta(hours=120) <= now:
        raise nasa.ValidationError("NASA latest run has an invalid initialization time")
    if current is not None and issued <= pd.Timestamp(current.issued_at):
        logger.info("forecast_check status=unchanged source_run=%s", nasa.run_id_for(candidate))
        return None
    expected = pd.date_range(candidate + pd.Timedelta(minutes=30), periods=120, freq="h").values
    specs = [s for s in nasa.VARIABLES if s.name in REQUIRED_VARIABLES]
    for collection in dict.fromkeys(s.collection for s in specs):
        inventory = None
        for latest in (True, False):
            try:
                if collection == nasa.AQC_COLLECTION and latest:
                    available_names, axis = names, times
                else:
                    available_names, axis, _ = nasa.remote_call(
                        nasa._read_inventory, nasa.collection_url(collection, None if latest else nasa.run_id_for(candidate)),
                        what=f"Checking readiness of {collection}", retries=2,
                    )
                if np.array_equal(axis, expected) and all(
                    nasa.parse_units(available_names.get(s.source_name, "")) == s.units
                    for s in specs if s.collection == collection
                ):
                    inventory = available_names
                    break
            except (OSError, OverflowError, nasa.ValidationError):
                continue
        if inventory is None:
            logger.info("forecast_check status=waiting source_run=%s collection=%s", nasa.run_id_for(candidate), collection)
            return None
        # Rolling risk needs analysis through the hour immediately before forecast.
        history_names, historical, _ = nasa.remote_call(nasa._read_inventory, analysis.analysis_url(collection),
                                            what=f"Checking analysis readiness of {collection}", retries=2)
        first_needed = candidate + pd.Timedelta(minutes=30) - pd.Timedelta(hours=222)
        last_needed = candidate - pd.Timedelta(minutes=30)
        needed = pd.date_range(first_needed, last_needed, freq="h").values
        if not np.isin(needed, historical).all() or not all(
            nasa.parse_units(history_names.get(s.source_name, "")) == s.units
            for s in specs if s.collection == collection
        ):
            logger.info("forecast_check status=waiting source_run=%s analysis_collection=%s", nasa.run_id_for(candidate), collection)
            return None
    return candidate


def launch_publisher(project, region, job, candidate):
    from google.auth import default
    from google.auth.transport.requests import AuthorizedSession
    credentials, _ = default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    url = f"https://run.googleapis.com/v2/projects/{project}/locations/{region}/jobs/{job}:run"
    with AuthorizedSession(credentials) as session:
        response = session.post(url, json={"overrides": {"containerOverrides": [{
            "env": [{"name": "FORECAST_RUN_DATE", "value": candidate.strftime("%Y-%m-%d")}],
        }]}}, timeout=30)
        response.raise_for_status()
        return response.json()["name"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", default=os.getenv("FORECAST_GCS_BUCKET"), required=not os.getenv("FORECAST_GCS_BUCKET"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", default="us-east1")
    parser.add_argument("--job", default="asthmaaware-forecast")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    bucket = get_bucket(args.bucket)
    candidate = ready_run(current_publication(bucket), datetime.now(timezone.utc))
    if candidate is None:
        return
    from app.services.forecast_publication_guard import lease_active
    if lease_active(bucket):
        logger.info("forecast_check status=busy source_run=%s", nasa.run_id_for(candidate))
        return
    operation = "dry-run" if args.dry_run else launch_publisher(args.project, args.region, args.job, candidate)
    logger.info("forecast_check status=%s source_run=%s operation=%s",
                "ready" if args.dry_run else "launched", nasa.run_id_for(candidate), operation)


if __name__ == "__main__":
    main()
