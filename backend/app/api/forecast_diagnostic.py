"""Private deployment diagnostic. Cloud Run IAM provides developer authentication.

This exposes synthetic fixtures only; it is not a risk or personalization API.
"""

import logging

from fastapi import APIRouter, HTTPException, Query, Request, Response

from forecast_job.publication import available_forecast
from forecast_job.storage import GCSStore

router = APIRouter()
logger = logging.getLogger(__name__)


def get_store(request):
    settings = request.app.state.settings
    if settings.forecast_provider == 'disabled':
        raise HTTPException(503, 'Forecast provider is disabled')
    if settings.forecast_provider != 'gcs-sample':
        raise HTTPException(503, 'Unsupported forecast provider')
    # Tests inject an in-memory store; live failures never enable this fallback.
    if hasattr(request.app.state, 'forecast_store'):
        return request.app.state.forecast_store
    return GCSStore(settings.forecast_bucket)


@router.get('/internal/forecast', tags=['infrastructure diagnostics'])
def forecast(request: Request, response: Response,
             lat: float = Query(ge=-90, le=90), lon: float = Query(ge=-180, le=180)):
    try:
        store = get_store(request)
        payload = available_forecast(store, lat, lon)
    except HTTPException:
        raise
    except KeyError:
        raise HTTPException(503, 'Published forecast is incomplete or unavailable')
    except LookupError:
        raise HTTPException(404, 'Outside synthetic coverage')
    except Exception:
        logger.exception('forecast_read status=failed')
        raise HTTPException(503, 'Forecast is unavailable, invalid, or outside its validity window')
    response.headers['X-AsthmaAware-Data-Source'] = 'sample'
    response.headers['Cache-Control'] = 'no-store'
    return payload
