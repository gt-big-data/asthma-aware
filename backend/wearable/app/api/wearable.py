"""HTTP interface for the wearable endpoints. All logic lives in the services.

Four reads and one write:

    POST /wearable/samples        a device syncs daily metrics
    GET  /wearable/metrics        latest metrics vs the user's own baseline
    GET  /wearable/body-response  one score for how their body is doing
    GET  /wearable/trends         one metric's recent series, for charting
    GET  /wearable/catalog        which metrics the system accepts
"""

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.models.wearable_models import (
    BodyResponseResponse,
    CatalogResponse,
    MetricsResponse,
    SyncRequest,
    SyncResponse,
    TrendsResponse,
)
from app.services import wearable_service

router = APIRouter(prefix="/wearable", tags=["Wearable"])

DEMO_USER_ID = "demo-user"


def get_current_user_id() -> str:
    """The signed-in user's id.

    Placeholder until authentication exists: everyone is the demo user.
    When auth is added, only this function changes.

    As on /dashboard there is deliberately no ``user_id`` parameter, and
    here the reason is sharper: these are a named person's heart rate,
    sleep and cough recordings. A query parameter would let any caller
    read -- or, on the write endpoint, forge -- another patient's health
    history. The user id comes from the credential or not at all.
    """
    return DEMO_USER_ID


@router.post("/samples", response_model=SyncResponse)
def sync_samples(
    request: SyncRequest,
    user_id: str = Depends(get_current_user_id),
):
    """Store a batch of daily metrics from the user's watch or phone.

    Idempotent per ``(metric, date)``: re-sending a day overwrites it, so
    a device can safely retry a sync or correct a value it revised later.

    Returns ``200`` with per-sample results even when some samples were
    rejected, so one bad sensor reading cannot cost the user the rest of
    the batch. Check ``rejected``, not just the status code.
    """
    return wearable_service.sync_samples(user_id, request.device_id, request.samples)


@router.get("/metrics", response_model=MetricsResponse)
def get_metrics(user_id: str = Depends(get_current_user_id)):
    """The user's most recent health metrics, each next to their own baseline.

    ``as_of`` is the latest day with data, which is normally last night
    rather than today -- clients should display it instead of implying the
    numbers are current.
    """
    return wearable_service.get_metrics_snapshot(user_id)


@router.get("/body-response", response_model=BodyResponseResponse)
def get_body_response(user_id: str = Depends(get_current_user_id)):
    """How far the user's physiology has drifted from their own normal.

    ``body_response`` is null while the user has too little history for a
    baseline; ``coverage`` says how much of the metric catalog the score
    rests on, and ``drift`` lists metrics adverse for several days running.
    """
    return wearable_service.get_body_response(user_id)


@router.get("/trends/{metric}", response_model=TrendsResponse)
def get_trends(
    metric: str = Path(description="Metric slug, e.g. resting_heart_rate"),
    days: int = Query(
        default=wearable_service.DEFAULT_TREND_DAYS,
        ge=1,
        le=wearable_service.MAX_TREND_DAYS,
        description="Days of history ending at the latest day with data",
    ),
    user_id: str = Depends(get_current_user_id),
):
    """One metric's recent values with the baseline band around them."""
    try:
        return wearable_service.get_trends(user_id, metric, days)
    except wearable_service.UnknownMetricError as error:
        raise HTTPException(
            status_code=404, detail=f"Unknown metric '{metric}'. See GET /wearable/catalog."
        ) from error


@router.get("/catalog", response_model=CatalogResponse)
def get_catalog():
    """Every metric the system accepts, with units and plausible ranges."""
    return wearable_service.get_catalog()
