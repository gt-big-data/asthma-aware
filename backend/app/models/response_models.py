from pydantic import BaseModel
from typing import List


class MapCell(BaseModel):
    row: int
    col: int
    lat: float
    lon: float
    so2: float
    ndvi: float
    no2: float


class MapResponse(BaseModel):
    city: str
    timestamp: str
    cells: List[MapCell]


class ForecastMapResponse(BaseModel):
    city: str
    forecast_horizon: str
    timestamp: str
    cells: List[MapCell]