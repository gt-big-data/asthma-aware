from fastapi import APIRouter, Query

from app.models.response_models import (
    ForecastMapResponse,
    MapResponse,
    SocioeconomicResponse,
)
from app.services.map_service import get_current_map_data, get_forecast_data
from app.services.socioeconomic_service import get_socioeconomic_data as get_socioeconomic_service_data

router = APIRouter(prefix="/map", tags=["Map"])


@router.get("/current", response_model=MapResponse)
def get_current_map(city: str = Query(default="atlanta")):
    return get_current_map_data(city)


@router.get("/forecast/24h", response_model=ForecastMapResponse)
def get_forecast_24h(city: str = Query(default="atlanta")):
    return get_forecast_data("24h", city)


@router.get("/forecast/48h", response_model=ForecastMapResponse)
def get_forecast_48h(city: str = Query(default="atlanta")):
    return get_forecast_data("48h", city)


@router.get("/forecast/72h", response_model=ForecastMapResponse)
def get_forecast_72h(city: str = Query(default="atlanta")):
    return get_forecast_data("72h", city)


@router.get("/socioeconomic", response_model=SocioeconomicResponse)
def get_socioeconomic_data(city: str = Query(default="atlanta")):
    return get_socioeconomic_service_data(city)
