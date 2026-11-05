"""FastAPI application for the AsthmaAware wearable API.

Run from backend/wearable/:
    uvicorn app.main:app --reload --port 8100
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.wearable import router as wearable_router

app = FastAPI(
    title="AsthmaAware Wearable API",
    version="0.1.0",
    description=(
        "Daily health metrics from a user's smartwatch and phone, compared "
        "against that user's own baseline."
    ),
)

# The Expo web frontend runs on a different origin (e.g. localhost:8081);
# browsers block its requests unless the API allows cross-origin access.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(wearable_router)


@app.get("/health", tags=["Health"])
def health_check():
    return {"status": "ok"}
