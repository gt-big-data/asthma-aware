"""Process liveness; deliberately independent of external services."""

from fastapi import APIRouter

router = APIRouter(tags=["Operations"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
