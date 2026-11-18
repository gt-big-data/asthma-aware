"""Serve general and personal risk on a shared grid and percentile scale."""
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.geos_dashboard import DashboardForecast, DailyRisk, ForecastUnavailable, OutsideForecastGrid
from app.services.personalization_model import PersonalModel, general_risk, personal_scores
from app.services.risk_artifacts import load_artifact


def resolve_artifact(settings, run):
    if run.risk_artifact is None:
        return None
    path = Path(run.risk_artifact)
    if settings.forecast_gcs_bucket:
        from app.services.gcs_forecast import cached_risk_file
        path = cached_risk_file(settings.forecast_gcs_bucket, run.risk_artifact)
    else:
        if path.name != str(path) or not path.name.endswith(".npz"):
            raise ValueError("Invalid risk artifact name")
        path = Path(settings.dashboard_forecast_path).parent / path
    return load_artifact(path)


def risk_dashboard(artifact, lat, lon, now, *, model=None, revision=None, pending=0, timezone_name="America/New_York"):
    try:
        E = artifact.point(lat, lon)
    except ValueError as error:
        raise OutsideForecastGrid() from error
    issued = pd.Timestamp(artifact.metadata["issued_at"])
    if issued.tzinfo is None:
        issued = issued.tz_localize("UTC")
    selected = (
        (artifact.times >= issued) & (artifact.times + timedelta(minutes=30) > now)
        & (artifact.times <= now + timedelta(hours=96))
    )
    if issued > now or not selected.any():
        raise ForecastUnavailable()
    E = E[selected]
    general = general_risk(E, artifact.ref).score[:, 0, 0]
    predictions = personal_scores(E, artifact.ref, model or PersonalModel())
    personal = predictions["score"][:, 0, 0]
    confidence = predictions["confidence"][:, 0, 0]
    days = []
    local_dates = np.array([t.tz_convert(ZoneInfo(timezone_name)).date() for t in artifact.times[selected]])
    today = now.astimezone(ZoneInfo(timezone_name)).date()
    for offset in range(4):
        day = today + timedelta(days=offset)
        valid = (local_dates == day) & np.isfinite(general) & np.isfinite(personal)
        days.append(DailyRisk(
            date=day, score=round(float(personal[valid].max())) if valid.any() else None,
            general_score=round(float(general[valid].max())) if valid.any() else None,
            available_hours=int(valid.sum()),
        ))
    finite_confidence = confidence[np.isfinite(confidence)]
    n_logs = model.n_logs if model else 0
    return DashboardForecast(
        source_run=artifact.metadata["source_run"], issued_at=issued.to_pydatetime(),
        stale=now - issued > timedelta(hours=24),
        placeholder=artifact.metadata["provisional"], grid_crs="EPSG:4326",
        grid_size_m=None, grid_step_degrees=0.01, timezone=timezone_name, days=days,
        personalization={
            "revision": revision, "matched_logs": n_logs, "pending_logs": pending,
            "still_learning": n_logs < 25 or not len(finite_confidence) or float(finite_confidence.mean()) < 0.5,
        },
    )
