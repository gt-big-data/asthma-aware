"""Single API entry point. Run from backend/: uvicorn app.main:app --reload."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.api.application import router as application_router
from app.api.risk_map import router as risk_map_router
from app.api.saved_locations import router as saved_locations_router
from app.config import Settings

def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="AsthmaAware API", version="0.1.0")
    application.state.settings = settings or Settings.from_env()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(application.state.settings.cors_origins),
        allow_methods=["GET", "PATCH", "PUT", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-AsthmaAware-Data-Source"],
    )
    application.include_router(dashboard_router)
    application.include_router(health_router)
    application.include_router(application_router)
    application.include_router(risk_map_router)
    application.include_router(saved_locations_router)
    return application


app = create_app()
