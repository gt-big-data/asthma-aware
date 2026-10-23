"""Assemble the /dashboard response from replaceable steps.

A user's personalized risk for one hour is

    y = g + w_u · z

where g is the general population symptom prediction, z the standardized
environmental features, and w_u the user's sensitivity weights. Each input
comes from its own function, so the mocks can be swapped for the real
components one at a time without changing the endpoint:

    resolve_grid_cell            mock Georgia grid        -> real forecast tile grid
    get_forecast_for_cell        mock_forecast.json       -> real forecast tiles
    get_user_personalization     mock personalization     -> real per-user weights
    calculate_personalized_risk  additive mathematical contract
    risk_score_from_severity     placeholder scale        -> model's real severity range
    get_top_contributors         correction-only demonstration (not full attribution)
"""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta, timezone
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.services import mock_data

# Supported geography: a lat/lon rectangle around Georgia. Small corners of
# neighbouring states fall inside it; a real state outline can replace it.
GEORGIA_BOUNDS = {"min_lat": 30.35, "max_lat": 35.00, "min_lon": -85.61, "max_lon": -80.84}

# Mock grid of 0.05-degree squares (~5.5 km). Row 0 is the northern edge,
# the same convention as the ConvLSTM grid in ingestion/grid_resample.py.
GRID_CELL_DEGREES = 0.05
GRID_ROWS = math.ceil(round((GEORGIA_BOUNDS["max_lat"] - GEORGIA_BOUNDS["min_lat"]) / GRID_CELL_DEGREES, 9))
GRID_COLS = math.ceil(round((GEORGIA_BOUNDS["max_lon"] - GEORGIA_BOUNDS["min_lon"]) / GRID_CELL_DEGREES, 9))
GRID_CELL_ID_PREFIX = "ga_"

# "Today" means the calendar day in Atlanta, so daylight saving is handled.
LOCAL_TZ = ZoneInfo("America/New_York")

# Placeholder risk scale until the model's real severity range is known:
# a prediction of SEVERITY_SCALE_MAX maps to a score of 100.
SEVERITY_SCALE_MAX = 4.0
# (minimum score, level), checked from the top down.
RISK_LEVEL_THRESHOLDS = ((70, "high"), (40, "moderate"), (0, "low"))

MAX_CONTRIBUTORS = 3


class LocationNotSupportedError(ValueError):
    """The coordinates are valid but outside the supported geography."""


def resolve_grid_cell(lat: float, lon: float) -> Optional[str]:
    """The id of the grid cell containing (lat, lon), or None outside Georgia."""
    bounds = GEORGIA_BOUNDS
    if not (bounds["min_lat"] <= lat <= bounds["max_lat"] and bounds["min_lon"] <= lon <= bounds["max_lon"]):
        return None

    # Rounding first stops a value like 24.9999999 (float error on an exact
    # cell edge) from flooring into the wrong cell. Points on the southern or
    # eastern boundary belong to the last row/column.
    row = min(math.floor(round((bounds["max_lat"] - lat) / GRID_CELL_DEGREES, 9)), GRID_ROWS - 1)
    col = min(math.floor(round((lon - bounds["min_lon"]) / GRID_CELL_DEGREES, 9)), GRID_COLS - 1)
    return f"{GRID_CELL_ID_PREFIX}{row * GRID_COLS + col:05d}"


def local_day_range(now: datetime) -> Tuple[datetime, datetime]:
    """Start (inclusive) and end (exclusive) of now's Atlanta calendar day, in UTC.

    Usually 24 hours apart; 23 or 25 on daylight-saving change days.
    """
    local_date = now.astimezone(LOCAL_TZ).date()
    start = datetime.combine(local_date, time.min, tzinfo=LOCAL_TZ)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=LOCAL_TZ)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def get_forecast_for_cell(grid_cell_id: str, time_range: Tuple[datetime, datetime]) -> List[Dict]:
    """Hourly forecast records for one cell over [start, end).

    Each record has a UTC ``timestamp``, the ``general_prediction`` g and the
    standardized ``features`` z.

    Mock: replays the cell's 24-hour profile from mock_forecast.json by local
    hour of day, using the default profile for cells not listed there.
    """
    fixture = mock_data.load_mock_forecast()
    profile = fixture["cells"].get(grid_cell_id, fixture["default"])
    by_local_hour = {record["hour"]: record for record in profile["hours"]}

    start, end = (moment.astimezone(timezone.utc) for moment in time_range)
    timestamp = start.replace(minute=0, second=0, microsecond=0)
    records = []
    while timestamp < end:
        source = by_local_hour[timestamp.astimezone(LOCAL_TZ).hour]
        records.append(
            {
                "timestamp": timestamp,
                "general_prediction": source["general_prediction"],
                "features": dict(source["features"]),
            }
        )
        timestamp += timedelta(hours=1)
    return records


def get_user_personalization(user_id: str) -> Dict[str, float]:
    """The user's sensitivity weight w_u for each standardized feature.

    Mock: reads mock_user_personalization.json. A user with no stored weights
    gets none, so their risk equals the general population prediction.
    """
    return dict(mock_data.load_user_personalization()["users"].get(user_id, {}))


def calculate_personalized_risk(
    general_prediction: float,
    environmental_features: Dict[str, float],
    user_weights: Dict[str, float],
) -> float:
    """y = g + w_u · z. Features the user has no weight for contribute nothing."""
    adjustment = sum(
        user_weights.get(feature, 0.0) * value for feature, value in environmental_features.items()
    )
    return general_prediction + adjustment


def risk_score_from_severity(prediction: float) -> int:
    """Map a predicted severity onto a 0-100 risk score (placeholder linear scale)."""
    score = round(prediction / SEVERITY_SCALE_MAX * 100)
    return max(0, min(100, score))


def risk_level_from_score(score: int) -> str:
    for threshold, level in RISK_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return RISK_LEVEL_THRESHOLDS[-1][1]


def get_top_contributors(
    environmental_features: Dict[str, float],
    user_weights: Dict[str, float],
    limit: int = MAX_CONTRIBUTORS,
) -> List[Dict]:
    """Positive correction terms, as shares of the positive correction total.

    This excludes the population model's effects. It is not an explanation
    of the full prediction; the production attribution contract is unresolved.

    A feature's impact is w_f * z_f. Features that lower risk (negative
    impact) are not contributors, so only positive impacts are ranked, and
    each ``contribution`` is that feature's fraction of their sum.
    """
    impacts = {
        feature: user_weights.get(feature, 0.0) * value
        for feature, value in environmental_features.items()
    }
    increases = {feature: impact for feature, impact in impacts.items() if impact > 0}
    total = sum(increases.values())
    if total <= 0:
        return []

    ranked = sorted(increases.items(), key=lambda item: (-item[1], item[0]))[:limit]
    return [{"feature": feature, "contribution": round(impact / total, 2)} for feature, impact in ranked]


def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_dashboard(lat: float, lon: float, user_id: str, now: Optional[datetime] = None) -> Dict:
    """Build the full /dashboard response for one location and user.

    ``now`` defaults to the current time; tests pass a fixed value.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")

    grid_cell_id = resolve_grid_cell(lat, lon)
    if grid_cell_id is None:
        raise LocationNotSupportedError(
            f"Location ({lat}, {lon}) is outside the supported area (Georgia)."
        )

    day_start, day_end = local_day_range(now)
    forecast = get_forecast_for_cell(grid_cell_id, (day_start, day_end))
    user_weights = get_user_personalization(user_id)

    scored = [
        (
            record,
            risk_score_from_severity(
                calculate_personalized_risk(record["general_prediction"], record["features"], user_weights)
            ),
        )
        for record in forecast
    ]
    current_hour = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    current_record, current_score = next(
        (record, score) for record, score in scored if record["timestamp"] == current_hour
    )

    return {
        "location": {"lat": lat, "lon": lon, "grid_cell_id": grid_cell_id},
        "forecast": {
            # Mock run time: the start of today. The real value will be the
            # forecast pipeline run that produced the tiles.
            "forecast_run_id": _iso_utc(day_start),
            "valid_at": _iso_utc(current_hour),
        },
        "risk": {"score": current_score, "level": risk_level_from_score(current_score)},
        "contributors": get_top_contributors(current_record["features"], user_weights),
        "hourly_risk": [
            {"timestamp": _iso_utc(record["timestamp"]), "risk": score} for record, score in scored
        ],
    }
