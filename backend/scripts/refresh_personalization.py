"""Process a user's finalized reports without opening the app."""
import argparse
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select, text

from app.config import Settings
from app.services.geos_dashboard import load_run
from app.services.risk_dashboard import resolve_artifact
from db.repositories.personalization import rebuild_personalization
from db.session import get_session_factory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", type=UUID, action="append", required=True)
    args = parser.parse_args()
    settings = Settings.from_env()
    archive = Path(settings.risk_archive_dir)
    if settings.forecast_gcs_bucket:
        from app.services.gcs_forecast import load_gcs_run, sync_history
        run = load_gcs_run(settings.forecast_gcs_bucket)
    else:
        run = load_run(Path(settings.dashboard_forecast_path))
    artifact = resolve_artifact(settings, run)
    if artifact is None:
        raise ValueError("Publish a risk artifact with a fixed reference first")
    if settings.forecast_gcs_bucket:
        archive = sync_history(settings.forecast_gcs_bucket,
            pipeline=artifact.metadata["preprocessing"] if artifact.metadata["preprocessing"].startswith("surface-o3-unet-") else None)
    for user_id in args.user_id:
        with get_session_factory()() as session, session.begin():
            session.execute(text("SET LOCAL ROLE asthmaaware_api"))
            session.execute(select(func.set_config("app.user_id", str(user_id), True)))
            model, revision, pending = rebuild_personalization(
                session, user_id, artifact, archive, datetime.now(timezone.utc),
            )
            print(f"revision={revision} matched_reports={model.n_logs} pending_reports={pending}")


if __name__ == "__main__":
    main()
