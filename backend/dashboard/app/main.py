"""FastAPI application for the AsthmaAware dashboard API.

Run from backend/dashboard/:
    uvicorn app.main:app --reload
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.dashboard import router as dashboard_router

app = FastAPI(
    title="AsthmaAware Dashboard API",
    version="0.1.0",
)

# The Expo web frontend runs on a different origin (e.g. localhost:8081);
# browsers block its requests unless the API allows cross-origin access.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(dashboard_router)
