from fastapi import APIRouter, Query
from app.services.map_service import get_current_map_data, get_forecast_data

router = APIRouter(prefix="/map", tags=["Map"])


@router.get("/current")
def get_current_map(city: str = Query(default="atlanta")):
    return get_current_map_data(city)


@router.get("/forecast/24h")
def get_forecast_24h(city: str = Query(default="atlanta")):
    return get_forecast_data("24h", city)


@router.get("/forecast/48h")
def get_forecast_48h(city: str = Query(default="atlanta")):
    return get_forecast_data("48h", city)


@router.get("/forecast/72h")
def get_forecast_72h(city: str = Query(default="atlanta")):
    return get_forecast_data("72h", city)

@router.get("/socioeconomic")
def get_socioeconomic_data(city: str = Query(default="atlanta")):
    #TODO: Implement this endpoint to return socioeconomic data for the city
    return {"message": "Socioeconomic data endpoint not implemented yet"}