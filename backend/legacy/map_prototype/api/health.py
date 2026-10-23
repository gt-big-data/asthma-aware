# Defines the API router for health check endpoints, allowing clients to verify that the server is running and responsive.

from fastapi import APIRouter

router = APIRouter(tags=["Health"])

@router.get("/health")
def health_check():
    return {"status": "ok"}