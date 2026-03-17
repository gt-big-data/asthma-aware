# Defines the data models for API responses, including the structure of map cells and the overall response format for both current and forecasted map data.

from pydantic import BaseModel
from typing import List, Optional


class MapCell(BaseModel):
    row: int
    col: int
    lat: float
    lon: float
    so2: float
    no2: float
    ozone: float
    ndvi: float
    pollen: float


class MapResponse(BaseModel):
    city: str
    timestamp: str
    cells: List[MapCell]


class ForecastMapResponse(BaseModel):
    city: str
    forecast_horizon: str
    timestamp: str
    cells: List[MapCell]