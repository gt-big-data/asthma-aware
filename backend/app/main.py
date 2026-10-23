"""Single API entry point. Run from backend/: uvicorn app.main:app --reload."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dashboard import router as dashboard_router
from app.api.health import router as health_router
from app.config import Settings

def create_app(settings: Settings | None = None) -> FastAPI:
    application = FastAPI(title="AsthmaAware API", version="0.1.0")
    application.state.settings = settings or Settings.from_env()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=list(application.state.settings.cors_origins),
        allow_methods=["GET"],
        allow_headers=["Authorization", "Content-Type"],
        expose_headers=["X-AsthmaAware-Data-Source"],
    )
    application.include_router(dashboard_router)
    application.include_router(health_router)
    return application


app = create_app()
