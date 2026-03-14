from fastapi import APIRouter
from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/", response_model=HealthResponse)
def health_check():
    return HealthResponse(status="ok")