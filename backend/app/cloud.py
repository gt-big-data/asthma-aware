"""Deployment entrypoint. No sample data, model algorithms or inference imports."""
import json
import logging
import os
import time
from uuid import uuid4

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.engine import make_url
from urllib.parse import urlsplit

from app.api.application import router as application_router
from app.api.forecasts import router as forecast_router
from app.api.health import router as health_router
from app.auth import get_current_user_id
from app.config import Settings
from app.dependencies import get_user_session

logger = logging.getLogger("asthmaaware.http")


def database_is_configured():
    try:
        url = make_url(os.getenv("DATABASE_URL", ""))
        return url.get_backend_name() == "postgresql" and url.query.get("sslmode") in ("require", "verify-ca", "verify-full")
    except Exception:
        return False


def cloud_user_session(user_id=Depends(get_current_user_id)):
    if not database_is_configured():
        raise HTTPException(503, "A TLS application database connection is required")
    yield from get_user_session(user_id)


def create_app(settings=None):
    app = FastAPI(title="AsthmaAware cloud backend", version="1.0.0")
    app.state.settings = settings or Settings.from_env()
    app.dependency_overrides[get_user_session] = cloud_user_session
    origins = list(app.state.settings.cors_origins)
    if "*" in origins:
        raise ValueError("Use explicit application CORS origins")
    app.add_middleware(CORSMiddleware, allow_origins=origins,
                       allow_methods=["GET", "POST", "PATCH", "PUT"],
                       allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
                       expose_headers=["X-Request-ID"], allow_credentials=False)

    @app.middleware("http")
    async def request_log(request, call_next):
        started = time.monotonic()
        request_id = str(uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        # Do not log query strings, authorization, bodies, or signed URLs.
        route = request.scope.get("route")
        logger.info(json.dumps({"event": "http_request", "request_id": request_id,
                               "method": request.method, "route": getattr(route, "path", "unmatched"),
                               "status": response.status_code,
                               "elapsed_ms": round((time.monotonic() - started) * 1000, 2)}))
        return response

    @app.exception_handler(Exception)
    async def failed(request, exc):
        logger.error(json.dumps({"event": "request_failed", "error_type": type(exc).__name__}))
        return JSONResponse({"detail": "Service temporarily unavailable"}, status_code=503,
                            headers={"Cache-Control": "no-store"})

    app.include_router(health_router)
    app.include_router(application_router)
    app.include_router(forecast_router)
    # Added routers are imported only when present during staged local integration.
    from app.api.personalization import router as task_router
    from app.api.places import router as places_router
    app.include_router(task_router)
    app.include_router(places_router)

    @app.get("/ready", tags=["Operations"])
    def readiness():
        origin = urlsplit(app.state.settings.supabase_url)
        configured = (origin.scheme == "https" and bool(origin.hostname) and origin.username is None
                      and origin.password is None and not origin.query and not origin.fragment
                      and origin.path in ("", "/") and database_is_configured())
        if not configured:
            raise HTTPException(503, "Required authentication/database configuration is missing")
        return {"status": "configured", "external_connectivity": "not_probed"}

    @app.get("/v1/forecast", tags=["Compatibility"])
    def legacy_forecast(user=Depends(get_current_user_id)):
        raise HTTPException(503, "ML forecast output is not connected. Use /v1/forecasts/availability; no placeholder predictions are generated.")

    return app


app = create_app()
