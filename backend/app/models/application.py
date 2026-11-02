"""V1 application API contracts. Unknown fields are rejected, including user IDs."""

from datetime import date, datetime
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ProfileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    user_id: UUID
    name: str | None
    timezone: str


class ProfilePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, max_length=120)
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value):
        if value is None:
            raise ValueError("timezone cannot be null")
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be a valid IANA timezone") from None
        return value


class SymptomWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    severity: Annotated[int, Field(strict=True, ge=1, le=5)]
    latitude: float = Field(ge=-90, le=90, allow_inf_nan=False)
    longitude: float = Field(ge=-180, le=180, allow_inf_nan=False)
    location_source: Literal["current", "selected"]
    expected_revision: Annotated[int, Field(strict=True, ge=0)] = 0


class SymptomResponse(BaseModel):
    id: UUID
    log_date: date
    severity: int
    latitude: float
    longitude: float
    location_source: Literal["current", "selected"]
    timezone: str
    revision: int
    finalizes_at: datetime
    status: Literal["provisional", "final"]
    exposure_status: Literal["pending", "matched"]
    created_at: datetime
    updated_at: datetime


class SymptomHistory(BaseModel):
    items: list[SymptomResponse]
    next_before: date | None
