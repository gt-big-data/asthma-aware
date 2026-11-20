"""Authenticated one-hour personal maps using the dashboard scoring contract."""
import base64
import hashlib
import json
from pathlib import Path

import numpy as np
from sqlalchemy import select

from app.services.personalization_model import FEATURE_VERSION, MODEL_VERSION, PersonalModel, personal_scores
from app.services.risk_map import COLORS, MapLayer, colorize_frame, encode_png, map_forecast
from db.models.application import PersonalizationRevision
from db.repositories.personalization import rebuild_personalization

PERSONAL_LAYER = MapLayer(id="personal", label="Personalized asthma risk", unit="score / 100",
    minimum=0, maximum=100, colors=COLORS,
    description="Your asthma risk · learned from daily symptom reports",
    processing="Same personalized scoring model as your dashboard")


def prepare_model(session, user, artifact, settings, now):
    archive = Path(settings.risk_archive_dir)
    if settings.forecast_gcs_bucket:
        from app.services.gcs_forecast import sync_history
        pipeline = artifact.metadata["preprocessing"]
        archive = sync_history(settings.forecast_gcs_bucket,
            pipeline=pipeline if pipeline.startswith("surface-o3-unet-") else None)
    return rebuild_personalization(session, user, artifact, archive, now)


def saved_model(session, user, artifact):
    """Frames reuse the latest committed revision; only metadata rebuilds it."""
    row = session.scalar(select(PersonalizationRevision).where(
        PersonalizationRevision.user_id == user,
    ).order_by(PersonalizationRevision.revision.desc()).limit(1))
    if (row is None or row.population_model_version != MODEL_VERSION
            or row.feature_schema_version != FEATURE_VERSION
            or row.normalization_version != artifact.metadata["reference_id"]):
        raise ValueError("Reload personalized map metadata")
    return PersonalModel.from_dict(row.weights["state"]), row.revision


def personal_run_id(artifact, user, model, revision):
    # Account, exact learned state, revision and forecast all isolate image caches.
    state = json.dumps(model.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(f"personal-map-v1:{artifact.digest}:{user}:{revision}:{MODEL_VERSION}:{state}".encode()).hexdigest()


def personal_forecast(artifact, fields, now, user, model, revision, pending):
    result = map_forecast(artifact, now, fields)
    return result.model_copy(update=dict(run_id=personal_run_id(artifact, user, model, revision),
        layer=PERSONAL_LAYER, layers=[*result.layers, PERSONAL_LAYER],
        personalization=dict(matched_logs=model.n_logs, pending_logs=pending, revision=revision,
            still_learning=model.n_logs < 25)))


def personal_frame_scores(artifact, index, model):
    # Bound runtime memory to one hour, including for mmap-backed fine artifacts.
    score = personal_scores(artifact.E[index:index + 1], artifact.ref, model)["score"][0]
    return score[np.ix_(artifact.rows, artifact.columns)]


def personal_image(artifact, index, model):
    rgba = colorize_frame(personal_frame_scores(artifact, index, model), COLORS, 0, 100)
    # Deliberately bypass the process-wide public PNG cache and all shared storage.
    return "data:image/png;base64," + base64.b64encode(encode_png(rgba)).decode("ascii")
