"""Match finalized logs and rebuild a user's Bayesian state under owner RLS."""
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import select

from app.services.personalization_model import (
    FEATURE_VERSION, MODEL_VERSION, PersonalModel, general_risk,
)
from app.services.risk_artifacts import load_artifact
from db.models.application import ExposureSnapshot, OutboxEvent, PersonalizationRevision, SymptomLog
from db.repositories.application import ensure_profile


def match_day(log, archives):
    start = datetime.combine(log.log_date, time.min, ZoneInfo(log.timezone)).astimezone(timezone.utc)
    end = log.finalizes_at
    for path in archives:
        artifact = load_artifact(path)
        if artifact.metadata["product"] != "analysis" or artifact.metadata["preprocessing"] != "native-surface-nearest-001deg-v1":
            continue
        try:
            values = artifact.day(log.latitude, log.longitude, start, end)
        except ValueError:
            continue
        if values is not None:
            return start, end, values, artifact
    return None


def rebuild_personalization(session, user_id, artifact, archive_dir: Path, now):
    ensure_profile(session, user_id, lock=True)
    logs = list(session.scalars(select(SymptomLog).where(
        SymptomLog.user_id == user_id, SymptomLog.finalizes_at <= now,
    ).order_by(SymptomLog.log_date)))
    reference_id = artifact.metadata["reference_id"]
    archives = None
    model = PersonalModel()
    inputs = []
    pending = 0
    snapshots = {(row.log_id, row.log_revision): row for row in session.scalars(
        select(ExposureSnapshot).where(ExposureSnapshot.user_id == user_id)
    )}
    for log in logs:
        snapshot = snapshots.get((log.id, log.revision))
        if snapshot is None:
            if archives is None:
                archives = sorted(archive_dir.glob("*.npz"), key=lambda path: path.stat().st_mtime_ns, reverse=True)
            matched = match_day(log, archives)
            if matched is None:
                pending += 1
                continue
            start, end, values, history = matched
            snapshot = ExposureSnapshot(
                user_id=user_id, log_id=log.id, log_revision=log.revision,
                window_start=start, window_end=end, feature_schema_version=FEATURE_VERSION,
                pipeline_version=history.metadata["preprocessing"],
                features={"exposure": values.tolist()},
                provenance={"artifact_sha256": history.digest, "reference_id": reference_id, "product": "analysis"},
            )
            session.add(snapshot)
        if snapshot.feature_schema_version != FEATURE_VERSION or snapshot.pipeline_version != artifact.metadata["preprocessing"]:
            pending += 1
            continue
        values = np.asarray(snapshot.features["exposure"], dtype=float)[None, :, None, None]
        z = artifact.ref.standardize(values)[0, :, 0, 0]
        eta = general_risk(values, artifact.ref).log_rr.item()
        model.update(z, eta, log.severity - 1)
        inputs.append({"log_id": str(log.id), "revision": log.revision})
    latest = session.scalar(select(PersonalizationRevision).where(
        PersonalizationRevision.user_id == user_id,
    ).order_by(PersonalizationRevision.revision.desc()).limit(1))
    if latest is None or latest.training_inputs != inputs or latest.normalization_version != reference_id or latest.population_model_version != MODEL_VERSION:
        revision = PersonalizationRevision(
            user_id=user_id, revision=1 if latest is None else latest.revision + 1,
            population_model_version=MODEL_VERSION, feature_schema_version=FEATURE_VERSION,
            normalization_version=reference_id, training_inputs=inputs,
            weights={"state": model.to_dict()},
        )
        session.add(revision)
    else:
        revision = latest
    session.flush()
    processed = {(entry["log_id"], entry["revision"]) for entry in inputs}
    active = {str(log.id): log.revision for log in logs}
    for event in session.scalars(select(OutboxEvent).where(
        OutboxEvent.user_id == user_id, OutboxEvent.dispatched_at.is_(None), OutboxEvent.available_at <= now,
    )):
        if ((str(event.log_id), event.log_revision) in processed
                or active.get(str(event.log_id), event.log_revision) > event.log_revision):
            event.dispatched_at = now
    session.flush()
    return model, revision.revision, pending
