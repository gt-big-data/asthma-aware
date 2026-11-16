"""Authenticated read-only real forecast access."""
from uuid import UUID
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from app.auth import get_current_user_id
from app.dependencies import utc_now
from app.services import live_forecast

router = APIRouter(prefix="/v1", tags=["Forecast"])


@router.get("/forecast")
def forecast(request: Request, response: Response, lat: float = Query(ge=-90, le=90, allow_inf_nan=False),
             lon: float = Query(ge=-180, le=180, allow_inf_nan=False),
             user_id: UUID = Depends(get_current_user_id), now: datetime = Depends(utc_now)):
    try:
        response.headers["X-AsthmaAware-Data-Source"] = "NASA GEOS-CF v2; placeholder-score"
        response.headers["Cache-Control"] = "private, no-store"
        return live_forecast.at_location(request.app.state.live_forecast_provider.get(), lat, lon, now)
    except live_forecast.OutsideCoverage as exc:
        raise HTTPException(404, str(exc)) from None
    except live_forecast.ForecastUnavailable:
        raise HTTPException(503, "Live forecast unavailable; no sample data substituted") from None
