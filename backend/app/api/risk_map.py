"""Public general-risk map; storage access stays inside the API service identity."""
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.dependencies import utc_now
from app.services.geos_dashboard import ForecastUnavailable, load_run
from app.services.risk_dashboard import resolve_artifact
from app.services.risk_map import MapForecast, map_forecast, map_image

router = APIRouter(prefix="/v1/map", tags=["Risk map"])


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
        return artifact
    except (ForecastUnavailable, OSError, ValueError):
        raise HTTPException(503, "No usable population risk forecast is available") from None


@router.get("/forecast", response_model=MapForecast)
def forecast(response: Response, now: datetime = Depends(utc_now), artifact=Depends(current_artifact)):
    try:
        result = map_forecast(artifact, now)
    except (ForecastUnavailable, ValueError):
        raise HTTPException(503, "No current or future risk frames are available") from None
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/frame")
def frame(response: Response, run_id: str = Query(pattern=r"^[a-f0-9]{64}$"),
          index: int = Query(ge=0), now: datetime = Depends(utc_now), artifact=Depends(current_artifact)):
    if artifact.digest != run_id:
        raise HTTPException(409, "Forecast changed; reload map metadata")
    try:
        metadata = map_forecast(artifact, now)
    except (ForecastUnavailable, ValueError):
        raise HTTPException(503, "No current or future risk frames are available") from None
    selected = next((item for item in metadata.frames if item.index == index), None)
    if selected is None:
        raise HTTPException(404, "Hour is outside the available forecast")
    response.headers["Cache-Control"] = "no-store"
    return {"run_id": run_id, "index": index, "valid_at": selected.valid_at,
            "available": selected.available, "image": map_image(artifact, index)}
