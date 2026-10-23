"""Request and response schemas for the wearable endpoints.

Dates are plain ISO dates (``2026-10-15``) because every metric is a
daily summary; instants -- sync times -- are UTC ISO 8601 with a ``Z``,
matching the dashboard endpoint.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

Level = Literal["low", "moderate", "high"]
Status = Literal[
    "typical", "watch", "concern", "better_than_baseline", "baseline_pending"
]
AdverseDirection = Literal["higher", "lower"]

# A pairing backfill can carry a year of history across nine metrics; the
# cap is well above that and only exists to bound one request's memory.
MAX_SAMPLES_PER_SYNC = 10000


# --- POST /wearable/samples ----------------------------------------------


class SampleIn(BaseModel):
    """One metric's value for one day.

    ``date`` is the local calendar date the measurement belongs to. For
    overnight metrics (sleep, resting heart rate, nocturnal cough) that is
    the **morning the night ended**, so a night's sleep from the 14th into
    the 15th is dated the 15th. Devices disagree on this, and getting it
    wrong shifts every overnight metric one day out of step with the
    daytime ones.
    """

    metric: str = Field(description="Metric slug, e.g. resting_heart_rate")
    date: dt.date
    value: float


class SyncRequest(BaseModel):
    device_id: Optional[str] = Field(
        default=None, max_length=128, description="Opaque device identifier"
    )
    samples: List[SampleIn] = Field(min_length=1, max_length=MAX_SAMPLES_PER_SYNC)


class RejectedSample(BaseModel):
    index: int = Field(description="Position in the request's samples array")
    metric: str
    date: str
    reason: str
    expected_range: Optional[List[float]] = None
    unit: Optional[str] = None


class SyncResponse(BaseModel):
    accepted: int
    rejected: List[RejectedSample]
    stored_dates: List[str]
    metrics_stored: List[str]
    last_sync_at: str


# --- shared read structures ----------------------------------------------


class BaselineOut(BaseModel):
    median: float
    spread: float = Field(description="Robust spread (1.4826 x MAD), one personal sigma")
    days_used: int
    window_days: int


class CoverageOut(BaseModel):
    metrics_reported: int
    metrics_scored: int = Field(description="Reported metrics that also have a baseline")
    metrics_expected: int
    weight_available: float = Field(
        ge=0, le=1, description="Share of total catalog weight behind the score"
    )
    baseline_ready: bool
    sufficient: bool = Field(description="False when too few metrics stand behind the score")


class DeviceOut(BaseModel):
    device_id: Optional[str]
    last_sync_at: Optional[str]


# --- GET /wearable/metrics -----------------------------------------------


class MetricReadingOut(BaseModel):
    metric: str
    display_name: str
    unit: str
    source: str
    adverse_direction: AdverseDirection
    value: float
    baseline: Optional[BaselineOut]
    deviation: Optional[float] = Field(
        default=None, description="Personal sigmas in the adverse direction; positive is worse"
    )
    status: Status
    description: str


class MetricsResponse(BaseModel):
    user_id: str
    as_of: Optional[dt.date] = Field(description="Most recent day with any data")
    device: DeviceOut
    metrics: List[MetricReadingOut]
    missing_metrics: List[str]
    coverage: CoverageOut


# --- GET /wearable/body-response -----------------------------------------


class BodyResponseScore(BaseModel):
    score: int = Field(ge=0, le=100)
    level: Level


class ContributorOut(BaseModel):
    metric: str
    display_name: str
    contribution: float = Field(ge=0, le=1)


class DriftOut(BaseModel):
    metric: str
    display_name: str
    consecutive_days: int
    deviation: float
    direction: AdverseDirection


class BodyResponseResponse(BaseModel):
    user_id: str
    as_of: Optional[dt.date]
    device: DeviceOut
    body_response: Optional[BodyResponseScore] = Field(
        description="Null until the user has enough history for a baseline"
    )
    weighted_deviation: Optional[float]
    contributors: List[ContributorOut]
    drift: List[DriftOut]
    coverage: CoverageOut


# --- GET /wearable/trends ------------------------------------------------


class TrendPoint(BaseModel):
    date: dt.date
    value: float
    baseline_median: Optional[float]
    baseline_spread: Optional[float]
    deviation: Optional[float]
    status: Status


class TrendsResponse(BaseModel):
    user_id: str
    metric: str
    display_name: str
    unit: str
    adverse_direction: AdverseDirection
    start_date: dt.date
    end_date: dt.date
    points: List[TrendPoint]


# --- GET /wearable/catalog -----------------------------------------------


class CatalogEntry(BaseModel):
    metric: str
    display_name: str
    unit: str
    source: str
    adverse_direction: AdverseDirection
    valid_range: List[float]
    weight: float
    description: str


class CatalogResponse(BaseModel):
    metrics: List[CatalogEntry]
