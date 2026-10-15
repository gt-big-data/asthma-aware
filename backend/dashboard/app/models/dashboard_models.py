"""Response schema for GET /dashboard."""

from typing import List, Literal

from pydantic import BaseModel, Field

RiskLevel = Literal["low", "moderate", "high"]


class DashboardLocation(BaseModel):
    lat: float
    lon: float
    grid_cell_id: str


class DashboardForecast(BaseModel):
    forecast_run_id: str
    valid_at: str


class DashboardRisk(BaseModel):
    score: int = Field(ge=0, le=100)
    level: RiskLevel


class Contributor(BaseModel):
    feature: str
    contribution: float


class HourlyRisk(BaseModel):
    timestamp: str
    risk: int = Field(ge=0, le=100)


class DashboardResponse(BaseModel):
    location: DashboardLocation
    forecast: DashboardForecast
    risk: DashboardRisk
    contributors: List[Contributor]
    hourly_risk: List[HourlyRisk]
