from fastapi import APIRouter, Query
from app.services.map_service import (
    get_current_map_data,
    get_forecast_24h_data,
    get_forecast_48h_data,
    get_forecast_72h_data,
)

router = APIRouter(prefix="/map", tags=["Map"])


@router.get("/current")
def get_current_map(city: str = Query(default="atlanta")):
    return get_current_map_data(city)


@router.get("/forecast/24h")
def get_forecast_24h(city: str = Query(default="atlanta")):
    return get_forecast_24h_data(city)


@router.get("/forecast/48h")
def get_forecast_48h(city: str = Query(default="atlanta")):
    return get_forecast_48h_data(city)


@router.get("/forecast/72h")
def get_forecast_72h(city: str = Query(default="atlanta")):
    return get_forecast_72h_data(city)