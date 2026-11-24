"""HTTP interface for GET /dashboard. All logic lives in dashboard_service."""

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.dependencies import utc_now, get_user_session
from app.auth import get_current_user_id as verified_user
from sqlalchemy.orm import Session
from uuid import UUID
from app.models.dashboard_models import DashboardResponse
from app.services import dashboard_service, geos_dashboard
from app.services.risk_artifacts import fine_preprocessing

router = APIRouter(tags=["Dashboard"])

DEMO_USER_ID = "demo-user"


def get_current_user_id(request: Request) -> str:
    """Explicit demo identity until verified Supabase authentication exists."""
    if not request.app.state.settings.dashboard_demo_enabled:
        raise HTTPException(
            status_code=503,
            detail="Dashboard providers and authentication are not configured. "
            "Set DASHBOARD_DEMO_ENABLED=true for local fixture data.",
        )
    return DEMO_USER_ID


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    response: Response,
    lat: float = Query(..., ge=-90, le=90, description="Latitude of the user's current location"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude of the user's current location"),
    user_id: str = Depends(get_current_user_id),
):
    response.headers["X-AsthmaAware-Data-Source"] = "demo"
    try:
        return dashboard_service.get_dashboard(lat, lon, user_id)
    except dashboard_service.LocationNotSupportedError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get("/v1/dashboard/risk", response_model=geos_dashboard.DashboardForecast)
def get_dashboard_risk(
    request: Request,
    response: Response,
    lat: float = Query(ge=-90, le=90),
    lon: float = Query(ge=-180, le=180),
    now: datetime = Depends(utc_now),
):
    """General population scores use public forecast data and require no user record."""
    settings = request.app.state.settings
    try:
        if settings.forecast_gcs_bucket:
            from app.services.gcs_forecast import load_gcs_run

            run = load_gcs_run(settings.forecast_gcs_bucket)
        else:
            run = geos_dashboard.load_run(Path(settings.dashboard_forecast_path))
        from app.services.risk_dashboard import resolve_artifact, risk_dashboard

        artifact = resolve_artifact(settings, run)
        result = risk_dashboard(artifact, lat, lon, now) if artifact else geos_dashboard.dashboard_forecast(run, lat, lon, now)
    except geos_dashboard.OutsideForecastGrid:
        raise HTTPException(404, "Location is outside the forecast grid") from None
    except (geos_dashboard.ForecastUnavailable, OSError, ValueError):
        raise HTTPException(503, "No usable GEOS-CF forecast is available") from None

    response.headers["Cache-Control"] = "no-store"
    response.headers["X-AsthmaAware-Data-Source"] = "geos-cf"
    return geos_dashboard.with_forecast_details(result, run, now)


@router.get("/v1/dashboard/personal-risk", response_model=geos_dashboard.DashboardForecast)
def get_personal_risk(
    request: Request, response: Response,
    lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180),
    user: UUID = Depends(verified_user),
    session: Session = Depends(get_user_session, scope="function"),
    now: datetime = Depends(utc_now),
):
    from app.services.risk_dashboard import resolve_artifact, risk_dashboard
    from db.repositories.personalization import rebuild_personalization
    from db.models.application import Profile

    settings = request.app.state.settings
    try:
        if settings.forecast_gcs_bucket:
            from app.services.gcs_forecast import load_gcs_run
            run = load_gcs_run(settings.forecast_gcs_bucket)
        else:
            run = geos_dashboard.load_run(Path(settings.dashboard_forecast_path))
        artifact = resolve_artifact(settings, run)
        if artifact is None:
            # Legacy artifacts still serve their explicit placeholder population score.
            result = geos_dashboard.dashboard_forecast(run, lat, lon, now)
        else:
            archive_dir = Path(settings.risk_archive_dir)
            if settings.forecast_gcs_bucket:
                from app.services.gcs_forecast import sync_history
                archive_dir = sync_history(settings.forecast_gcs_bucket,
                    pipeline=artifact.metadata["preprocessing"] if fine_preprocessing(artifact.metadata["preprocessing"]) else None)
            # Reject unavailable locations and expired forecasts before writing derived state.
            risk_dashboard(artifact, lat, lon, now)
            model, revision, pending = rebuild_personalization(session, user, artifact, archive_dir, now)
            result = risk_dashboard(
                artifact, lat, lon, now, model=model, revision=revision, pending=pending,
                timezone_name=session.get(Profile, user).timezone,
            )
    except geos_dashboard.OutsideForecastGrid:
        raise HTTPException(404, "Location is outside the forecast grid") from None
    except (geos_dashboard.ForecastUnavailable, OSError, ValueError):
        raise HTTPException(503, "Risk forecast or historical exposure is unavailable") from None
    response.headers["Cache-Control"] = "private, no-store"
    return geos_dashboard.with_forecast_details(result, run, now)


@router.get("/v1/dashboard/environmental-trends")
def get_environmental_trends(request: Request,response: Response,
    lat: float=Query(ge=-90,le=90,allow_inf_nan=False),lon: float=Query(ge=-180,le=180,allow_inf_nan=False),
    now: datetime=Depends(utc_now)):
    from app.services.environmental_trends import environmental_trends
    from google.api_core.exceptions import GoogleAPIError
    from google.auth.exceptions import GoogleAuthError
    try:
        result=environmental_trends(request.app.state.settings,lat,lon,now)
    except LookupError:
        raise HTTPException(404,"Location is outside Georgia or analysis coverage") from None
    except (OSError,ValueError,geos_dashboard.ForecastUnavailable,GoogleAPIError,GoogleAuthError):
        raise HTTPException(503,"Historical NASA analysis is unavailable") from None
    response.headers["Cache-Control"]="no-store"
    return result
