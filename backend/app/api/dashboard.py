"""HTTP interface for GET /dashboard. All logic lives in dashboard_service."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.models.dashboard_models import DashboardResponse
from app.services import dashboard_service

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
