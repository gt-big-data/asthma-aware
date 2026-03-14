from typing import Dict, List, Literal, Optional
from pydantic import BaseModel


class Center(BaseModel):
    lat: float
    lng: float


class CellFactors(BaseModel):
    aqi: Optional[float] = None
    pollen: Optional[float] = None
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    smoke: Optional[float] = None


class MapCell(BaseModel):
    cell_id: str
    center: Center
    polygon: List[List[float]]
    risk_score: float
    risk_level: Literal["low", "medium", "high"]
    factors: CellFactors
    explanation: str


class MapResponse(BaseModel):
    city: str
    generated_at: str
    cells: List[MapCell]