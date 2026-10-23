from pydantic import BaseModel
from typing import List, Optional


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


class SocioeconomicRecord(BaseModel):
    zipcode: str
    area_sq_miles: Optional[float] = None
    population: Optional[int] = None
    population_density: Optional[float] = None
    median_age: Optional[float] = None
    children_under_18_rate: Optional[float] = None
    seniors_65_plus_rate: Optional[float] = None
    median_housing_age: Optional[int] = None
    median_household_income: Optional[int] = None
    median_gross_rent: Optional[int] = None
    median_home_value: Optional[int] = None
    poverty_rate: Optional[float] = None
    bachelor_degree_or_higher_rate: Optional[float] = None
    no_vehicle_households_rate: Optional[float] = None
    severe_rent_burden_rate: Optional[float] = None
    overcrowded_housing_rate: Optional[float] = None
    unemployment_rate: Optional[float] = None


class SocioeconomicResponse(BaseModel):
    city: str
    dataset_year: int
    source: str
    zipcodes: List[SocioeconomicRecord]
