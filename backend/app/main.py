# Starts the FastAPI application and includes the API routers for health and map endpoints.

from fastapi import FastAPI
from app.api.health import router as health_router
from app.api.map import router as map_router
from app.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION
)

app.include_router(health_router)
app.include_router(map_router)