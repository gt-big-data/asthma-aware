"""HTTP interface for GET /dashboard. All logic lives in dashboard_service."""

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models.dashboard_models import DashboardResponse
from app.services import dashboard_service

router = APIRouter(tags=["Dashboard"])

DEMO_USER_ID = "demo-user"


def get_current_user_id() -> str:
    """The signed-in user's id.

    Placeholder until authentication exists: everyone is the demo user. When
    auth is added, only this function changes. There is deliberately no
    user_id query parameter, which would let any caller read any user's risk.
    """
    return DEMO_USER_ID


@router.get("/dashboard", response_model=DashboardResponse)
def get_dashboard(
    lat: float = Query(..., ge=-90, le=90, description="Latitude of the user's current location"),
    lon: float = Query(..., ge=-180, le=180, description="Longitude of the user's current location"),
    user_id: str = Depends(get_current_user_id),
):
    try:
        return dashboard_service.get_dashboard(lat, lon, user_id)
    except dashboard_service.LocationNotSupportedError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
