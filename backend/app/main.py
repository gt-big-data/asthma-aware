# Starts the FastAPI application and includes the API routers for health and map endpoints.

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.api.map import router as map_router
from app.config import settings

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION
)

# Expo Web runs on a different origin (e.g. localhost:8081) than the API; browsers block
# cross-origin fetches unless the API sends Access-Control-Allow-Origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router)
app.include_router(map_router)