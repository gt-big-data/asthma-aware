"""Public forecast layers and authenticated, owner-scoped personalized risk maps."""
from datetime import datetime
from pathlib import Path
from typing import Literal
from uuid import UUID
from sqlalchemy.orm import Session
from app.auth import get_current_user_id
from app.dependencies import get_user_session
from app.services.personal_risk_map import (
    prepare_model, saved_model, personal_forecast, personal_image, personal_run_id,
)

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.dependencies import utc_now
from app.services.geos_dashboard import ForecastUnavailable, load_run
from app.services.risk_dashboard import resolve_artifact
from app.services.risk_map import MapForecast, LayerUnavailable, map_forecast, map_image
from app.services.map_fields import resolve_map_fields
from google.api_core.exceptions import GoogleAPIError
from google.auth.exceptions import GoogleAuthError

router = APIRouter(prefix="/v1/map", tags=["Risk map"])
LayerId = Literal["risk", "o3", "no2", "so2", "pm25", "temperature", "pm10", "co"]


def current_artifact(request: Request):
    settings = request.app.state.settings
    try:
        if settings.forecast_gcs_bucket:
            from app.services.gcs_forecast import load_gcs_run
            run = load_gcs_run(settings.forecast_gcs_bucket)
        else:
            run = load_run(Path(settings.dashboard_forecast_path))
        artifact = resolve_artifact(settings, run)
        if artifact is None:
            raise ForecastUnavailable("Population risk artifact has not been published")
        from pandas import Timestamp
        issued = Timestamp(artifact.metadata["issued_at"])
        issued = issued.tz_localize("UTC") if issued.tzinfo is None else issued
        if artifact.metadata["source_run"] != run.source_run or issued.to_pydatetime() != run.issued_at:
            raise ForecastUnavailable("Risk artifact and publication differ")
        return artifact, resolve_map_fields(settings, run)
    except (ForecastUnavailable, OSError, ValueError, GoogleAPIError, GoogleAuthError):
        raise HTTPException(503, "No usable population risk forecast is available") from None


@router.get("/forecast", response_model=MapForecast)
def forecast(response: Response, layer: LayerId = "risk", now: datetime = Depends(utc_now), context=Depends(current_artifact)):
    artifact, fields = context
    try:
        result = map_forecast(artifact, now, fields, layer)
    except LayerUnavailable:
        raise HTTPException(404, "This layer is not available in the current publication") from None
    except (ForecastUnavailable, ValueError):
        raise HTTPException(503, "No current or future risk frames are available") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/frame")
def frame(response: Response, run_id: str = Query(pattern=r"^[a-f0-9]{64}$"),
          index: int = Query(ge=0), layer: LayerId = "risk", now: datetime = Depends(utc_now), context=Depends(current_artifact)):
    artifact, fields = context
    try:
        metadata = map_forecast(artifact, now, fields, layer)
    except LayerUnavailable:
        raise HTTPException(404, "This layer is not available in the current publication") from None
    except (ForecastUnavailable, ValueError):
        raise HTTPException(503, "No current or future risk frames are available") from None
    if metadata.run_id != run_id:
        raise HTTPException(409, "Forecast or layer changed; reload map metadata")
    selected = next((item for item in metadata.frames if item.index == index), None)
    if selected is None:
        raise HTTPException(404, "Hour is outside the available forecast")
    response.headers["Cache-Control"] = "no-store"
    try:
        image = map_image(artifact, index, fields, layer)
    except (OSError, ValueError):
        raise HTTPException(503, "Could not read the requested map field") from None
    return {"run_id": run_id, "layer": layer, "index": index, "valid_at": selected.valid_at,
            "available": selected.available, "image": image}


# Separate authenticated routes keep public-layer requests independent of user DB
# access and prevent accidental anonymous fallback for a personal map.


@router.get("/personal/forecast", response_model=MapForecast)
def personal_metadata(request: Request, response: Response,
    user: UUID = Depends(get_current_user_id),
    session: Session = Depends(get_user_session, scope="function"),
    now: datetime = Depends(utc_now), context=Depends(current_artifact)):
    artifact, fields = context
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    try:
        # Validate forecast availability before rebuilding derived user state.
        map_forecast(artifact, now, fields)
        model, revision, pending = prepare_model(session, user, artifact, request.app.state.settings, now)
        return personal_forecast(artifact, fields, now, user, model, revision, pending)
    except (ForecastUnavailable, OSError, ValueError, GoogleAPIError, GoogleAuthError):
        raise HTTPException(503, "Personalized risk forecast or historical exposure is unavailable") from None


@router.get("/personal/frame")
def personal_frame(response: Response, run_id: str = Query(pattern=r"^[a-f0-9]{64}$"), index: int = Query(ge=0),
    user: UUID = Depends(get_current_user_id),
    session: Session = Depends(get_user_session, scope="function"),
    now: datetime = Depends(utc_now), context=Depends(current_artifact)):
    artifact, fields = context
    response.headers["Cache-Control"] = "private, no-store"
    response.headers["Vary"] = "Authorization"
    try:
        metadata = map_forecast(artifact, now, fields)
    except (ForecastUnavailable, ValueError):
        raise HTTPException(503, "No current or future risk frames are available") from None
    try:
        model, revision = saved_model(session, user, artifact)
        if personal_run_id(artifact, user, model, revision) != run_id:
            raise ValueError("Personal model changed")
    except (ValueError, KeyError, TypeError):
        raise HTTPException(409, "Personal model or forecast changed; reload map metadata") from None
    selected = next((item for item in metadata.frames if item.index == index), None)
    if selected is None:
        raise HTTPException(404, "Hour is outside the available forecast")
    try:
        image = personal_image(artifact, index, model)
    except (OSError, ValueError):
        raise HTTPException(503, "Could not render personalized risk") from None
    return dict(run_id=run_id, layer="personal", index=index, valid_at=selected.valid_at,
        available=selected.available, image=image)
