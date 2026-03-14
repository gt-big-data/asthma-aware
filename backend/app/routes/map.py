from fastapi import APIRouter, HTTPException

from app.config import CURRENT_MAP_FILE, FORECAST_MAP_FILE
from app.schemas.map import MapResponse
from app.services.data_loader import load_json_file

router = APIRouter()


@router.get("/atlanta/current", response_model=MapResponse)
def get_current_map():
    data = load_json_file(CURRENT_MAP_FILE)
    if data is None:
        raise HTTPException(status_code=404, detail="Current map data not found")
    return data


@router.get("/atlanta/forecast", response_model=MapResponse)
def get_forecast_map():
    data = load_json_file(FORECAST_MAP_FILE)
    if data is None:
        raise HTTPException(status_code=404, detail="Forecast map data not found")
    return data