"""Metadata and delivery only: no model execution or fabricated predictions."""
import logging
import threading
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from app.auth import get_current_user_id
from app.dependencies import utc_now
from app.services.forecast_delivery import DeliveryUnavailable, sign_delivery_url

router = APIRouter(prefix="/v1/forecasts", tags=["Forecast products"])
logger = logging.getLogger(__name__)
_catalog_lock = threading.Lock()


def get_catalog(request: Request, product="processed"):
    # Tests inject catalog objects; configured local mode reads actual files,
    # never generates fixtures. Client initialization is lazy to avoid ADC calls
    # during app startup or health probes.
    settings = request.app.state.settings
    with _catalog_lock:
        if not hasattr(request.app.state, "catalogs"):
            request.app.state.catalogs = {}
        if product not in request.app.state.catalogs:
            from forecast_pipeline.catalog import ForecastCatalog
            from forecast_pipeline.storage import GCSArtifactStore, LocalArtifactStore
            if settings.forecast_catalog_directory:
                store = LocalArtifactStore(settings.forecast_catalog_directory)
            else:
                bucket = settings.forecast_raw_bucket if product == "raw" else (settings.forecast_delivery_bucket or settings.forecast_processed_bucket)
                if not bucket:
                    raise HTTPException(503, "Forecast storage is not configured")
                store = GCSArtifactStore(bucket)
            request.app.state.catalogs[product] = ForecastCatalog(store)
        return request.app.state.catalogs[product]


def catalog_call(fn):
    from forecast_pipeline.catalog import ProductUnavailable, CatalogError
    try:
        return fn()
    except HTTPException:
        raise
    except ProductUnavailable:
        raise HTTPException(503, "Forecast product is pending or unavailable") from None
    except (CatalogError, OSError, KeyError, ValueError):
        raise HTTPException(503, "Forecast catalog is unavailable or invalid") from None
    except Exception as exc:
        logger.warning("forecast_catalog_failed error_type=%s", type(exc).__name__)
        raise HTTPException(503, "Forecast storage is unavailable") from None


@router.get("/availability")
def availability(request: Request, response: Response,
                 user: UUID = Depends(get_current_user_id), now: datetime = Depends(utc_now)):
    response.headers["Cache-Control"] = "private, no-store"
    from forecast_pipeline.contracts import ProductUnavailable
    def state(product):
        try:
            item = get_catalog(request, product).metadata(product=product, now=now)
        except ProductUnavailable:
            return {"status": "pending" if product == "processed" else "unavailable"}
        return {"status": "available", **{key: item[key] for key in ("run_id", "valid_from", "valid_until")}}
    # Each bucket has exactly one relevant pointer; do not probe both product
    # namespaces in both buckets (four reads instead of two).
    raw = catalog_call(lambda: state("raw"))
    processed = catalog_call(lambda: state("processed"))
    return {"raw": raw, "processed": processed,
            "ml_status": "published" if processed["status"] == "available" else "pending"}


@router.get("/metadata")
def metadata(request: Request, response: Response, product: str = Query("processed", pattern="^(raw|processed)$"),
             user: UUID = Depends(get_current_user_id), now: datetime = Depends(utc_now)):
    response.headers["Cache-Control"] = "private, no-store"
    return catalog_call(lambda: get_catalog(request, product).metadata(product=product, now=now))


@router.get("/delivery")
def delivery(request: Request, response: Response, name: str = Query(min_length=1, max_length=1024),
             user: UUID = Depends(get_current_user_id), now: datetime = Depends(utc_now)):
    response.headers["Cache-Control"] = "private, no-store"
    manifest = catalog_call(lambda: get_catalog(request).metadata(product="processed", now=now))
    # Exact membership, not user-supplied bucket names/prefixes. Never issue
    # signed URLs for raw inputs or personal records.
    if name not in {entry["name"] for entry in manifest["files"]}:
        raise HTTPException(404, "Object is not in the current processed forecast")
    try:
        valid_until = datetime.fromisoformat(manifest["valid_until"].replace("Z", "+00:00"))
        result = sign_delivery_url(request.app.state.settings, name, now=now, valid_until=valid_until)
    except (DeliveryUnavailable, ValueError):
        raise HTTPException(503, "Signed forecast delivery is not configured or unavailable") from None
    return {**result, "name": name, "run_id": manifest["run_id"]}
