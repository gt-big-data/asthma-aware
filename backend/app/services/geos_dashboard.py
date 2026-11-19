"""Read a locally published GEOS-CF run and resolve dashboard risk."""
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from math import floor, isfinite
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import AwareDatetime, BaseModel, Field, FiniteFloat, model_validator
from pyproj import Transformer
from pyproj.enums import TransformDirection

logger = logging.getLogger(__name__)
GEORGIA_TIME = ZoneInfo("America/New_York")
GRID_CRS = "EPSG:26916"
CELL_SIZE_METERS = 1000
SOURCE_STEP = 0.25


class ForecastInput(BaseModel):
    variable: str = Field(min_length=1, max_length=80)
    source_run: str | None = None
    issued_at: AwareDatetime | None = None
    collection: str | None = None


class PublishedForecast(BaseModel):
    schema_version: Literal[1] = 1
    risk_artifact: str | None = None
    map_artifact: str | None = None
    map_artifact_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    source_run: str
    issued_at: AwareDatetime
    times: list[AwareDatetime] = Field(min_length=1, max_length=120)
    latitudes: list[FiniteFloat] = Field(min_length=1)
    longitudes: list[FiniteFloat] = Field(min_length=1)
    pm25: list[list[FiniteFloat | None]]
    input_sources: list[ForecastInput] = Field(default_factory=list, max_length=64)

    @model_validator(mode="after")
    def validate_run(self):
        if bool(self.map_artifact) != bool(self.map_artifact_sha256):
            raise ValueError("Map artifacts require a checksum")
        if len(self.pm25) != len(self.times):
            raise ValueError("Expected one PM2.5 frame per valid time")
        cell_count = len(self.latitudes) * len(self.longitudes)
        if any(len(frame) != cell_count for frame in self.pm25):
            raise ValueError("PM2.5 frames must match the source grid")
        if any(b - a != timedelta(hours=1) for a, b in zip(self.times, self.times[1:])):
            raise ValueError("Forecast timestamps must be consecutive hourly midpoints")
        if self.times[0] < self.issued_at or self.times[-1] >= self.issued_at + timedelta(hours=120):
            raise ValueError("Forecast times must be within the 120-hour source run")
        for axis in (self.latitudes, self.longitudes):
            if any(abs(b - a - SOURCE_STEP) > 1e-6 for a, b in zip(axis, axis[1:])):
                raise ValueError("Expected an ascending 0.25-degree source grid")
        return self


class DailyRisk(BaseModel):
    date: date
    score: int | None
    general_score: int | None
    available_hours: int


class RiskDriver(BaseModel):
    pollutant: Literal["O3", "NO2", "SO2", "PM25"]
    exposure: FiniteFloat
    reference: FiniteFloat
    unit: str
    impact: FiniteFloat
    score_without: int


class HourlyRisk(BaseModel):
    valid_at: AwareDatetime
    score: int | None
    general_score: int | None
    drivers: list[RiskDriver] = Field(default_factory=list)


class ForecastDetails(BaseModel):
    forecast_start: AwareDatetime
    forecast_end: AwareDatetime
    source_remaining_hours: float
    displayed_through: AwareDatetime | None = None
    displayed_hours: int
    scored_hours: int
    input_sources: list[ForecastInput] = Field(default_factory=list)


class DashboardForecast(BaseModel):
    source_run: str
    issued_at: AwareDatetime
    stale: bool
    placeholder: bool = True
    timezone: str = "America/New_York"
    grid_crs: str = GRID_CRS
    grid_size_m: int | None = CELL_SIZE_METERS
    grid_step_degrees: float | None = None
    personalization: dict | None = None
    days: list[DailyRisk]
    hourly: list[HourlyRisk] = Field(default_factory=list)
    forecast_details: ForecastDetails | None = None


def with_forecast_details(result: DashboardForecast, run: PublishedForecast, now: datetime):
    """Report source coverage separately from the dashboard's capped visible horizon."""
    end = run.times[-1] + timedelta(minutes=30)
    details = ForecastDetails(
        forecast_start=run.times[0] - timedelta(minutes=30), forecast_end=end,
        source_remaining_hours=round(max(0, (end - now).total_seconds() / 3600), 2),
        displayed_through=result.hourly[-1].valid_at + timedelta(minutes=30) if result.hourly else None,
        displayed_hours=len(result.hourly), scored_hours=sum(hour.score is not None for hour in result.hourly),
        input_sources=run.input_sources,
    )
    return result.model_copy(update={"forecast_details": details})


class OutsideForecastGrid(Exception):
    pass


class ForecastUnavailable(Exception):
    pass


@lru_cache(maxsize=1)
def grid_projection() -> Transformer:
    return Transformer.from_crs("EPSG:4326", GRID_CRS, always_xy=True)


def source_index(run: PublishedForecast, lat: float, lon: float) -> int:
    def index(value: float, axis: list[float]) -> int:
        return floor((value - axis[0] + SOURCE_STEP / 2) / SOURCE_STEP)

    # Check coverage at the requested point and the center of its target cell.
    if not (0 <= index(lat, run.latitudes) < len(run.latitudes)
            and 0 <= index(lon, run.longitudes) < len(run.longitudes)):
        raise OutsideForecastGrid()
    projection = grid_projection()
    x, y = projection.transform(lon, lat)
    center_x = (floor(x / CELL_SIZE_METERS) + 0.5) * CELL_SIZE_METERS
    center_y = (floor(y / CELL_SIZE_METERS) + 0.5) * CELL_SIZE_METERS
    center_lon, center_lat = projection.transform(center_x, center_y, direction=TransformDirection.INVERSE)
    row, column = index(center_lat, run.latitudes), index(center_lon, run.longitudes)
    if not (0 <= row < len(run.latitudes) and 0 <= column < len(run.longitudes)):
        raise OutsideForecastGrid()
    return row * len(run.longitudes) + column


def general_population_risk(pm25: float | None) -> float | None:
    """Temporary linear PM2.5 baseline on a 0–100 scale."""
    if pm25 is None or not isfinite(pm25) or pm25 < 0:
        return None
    return min(100.0, pm25 * 2.5)


def placeholder_risk_score(general_risk: float | None) -> int | None:
    """The dashboard uses the population baseline until personal scoring exists."""
    return None if general_risk is None else round(general_risk)


@lru_cache(maxsize=2)
def read_run(path: Path, modified_ns: int, size: int) -> PublishedForecast:
    return PublishedForecast.model_validate_json(path.read_bytes())


def load_run(path: Path) -> PublishedForecast:
    try:
        info = path.stat()
        return read_run(path, info.st_mtime_ns, info.st_size)
    except (OSError, ValueError) as error:
        logger.warning("Could not read the published dashboard forecast: %s", type(error).__name__)
        raise ForecastUnavailable() from error


def dashboard_forecast(run: PublishedForecast, lat: float, lon: float, now: datetime) -> DashboardForecast:
    horizon = now + timedelta(hours=96)
    remaining = [
        (time, frame) for time, frame in zip(run.times, run.pm25)
        if time + timedelta(minutes=30) > now and time <= horizon
    ]
    if run.issued_at > now or not remaining:
        raise ForecastUnavailable()

    cell = source_index(run, lat, lon)
    today = now.astimezone(GEORGIA_TIME).date()
    days = {today + timedelta(days=offset): [] for offset in range(4)}
    for time, frame in remaining:
        day = time.astimezone(GEORGIA_TIME).date()
        baseline = general_population_risk(frame[cell])
        if day in days and baseline is not None:
            days[day].append(baseline)

    outlook = []
    for day, values in days.items():
        baseline = max(values, default=None)
        outlook.append(DailyRisk(
            date=day,
            score=placeholder_risk_score(baseline),
            general_score=None if baseline is None else round(baseline),
            available_hours=len(values),
        ))
    return DashboardForecast(
        source_run=run.source_run,
        issued_at=run.issued_at,
        stale=now - run.issued_at > timedelta(hours=24),
        days=outlook,
        hourly=[HourlyRisk(valid_at=time, score=placeholder_risk_score(general_population_risk(frame[cell])),
                           general_score=placeholder_risk_score(general_population_risk(frame[cell])))
                for time, frame in remaining],
    )
