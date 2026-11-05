"""DEMO DATA. A synthetic wearable history for the demo user.

Stands in for real device syncs until watches are actually paired, so the
read endpoints return something meaningful the moment the service starts.
It is generated rather than stored as a JSON fixture for two reasons: a
fixture of 35 days x 9 metrics is 300-odd unreviewable numbers, and dated
fixtures go stale -- this one is always anchored to the current date, so a
demo next month still shows "last night".

The shape of it is deliberate. Four weeks of ordinary days establish a
personal baseline, then the last three days ramp into a textbook
pre-exacerbation pattern: resting heart rate and nighttime respiratory
rate climbing, HRV and sleep quality falling, nocturnal cough rising
sharpest of all. That is the gradual multi-day buildup the FACET study
described, and it is what ``detect_drift`` should find.

Values are deterministic: the same date always produces the same history,
so tests and demos agree and nothing changes under you between two calls.
"""

from __future__ import annotations

import datetime as dt
import random
from typing import Dict

from app.services import metrics_catalog

DEMO_USER_ID = "demo-user"
DEMO_DEVICE_ID = "galaxy-watch-7-demo"

# Days of history generated, including the most recent day.
DEMO_HISTORY_DAYS = 35

# Per metric: the user's personal centre, the half-width of their ordinary
# day-to-day variation, and how far the flare pushes them at its peak
# (signed in the metric's own units -- negative where lower is worse).
DEMO_PROFILE: Dict[str, Dict[str, float]] = {
    "resting_heart_rate": {"center": 62.0, "noise": 2.0, "flare": 4.0, "decimals": 0},
    "nocturnal_cough_count": {"center": 6.0, "noise": 4.0, "flare": 10.0, "decimals": 0},
    "hrv_rmssd": {"center": 48.0, "noise": 6.0, "flare": -7.0, "decimals": 0},
    "respiratory_rate": {"center": 14.2, "noise": 0.7, "flare": 1.1, "decimals": 1},
    "spo2": {"center": 97.3, "noise": 0.6, "flare": -0.6, "decimals": 1},
    "sleep_efficiency": {"center": 89.0, "noise": 3.0, "flare": -4.0, "decimals": 0},
    "sleep_duration": {"center": 7.2, "noise": 0.7, "flare": -0.8, "decimals": 1},
    "steps": {"center": 7800.0, "noise": 1600.0, "flare": -1800.0, "decimals": 0},
    "skin_temperature": {"center": 33.4, "noise": 0.25, "flare": 0.3, "decimals": 1},
}

# Share of the full flare applied on each of the final days, most recent
# first. Three entries, so the drift is three days long.
FLARE_RAMP = (1.0, 0.75, 0.55)

# Days before the most recent one where the watch was off the wrist, so
# skin temperature is missing. Exercises the gap handling that real syncs
# will hit constantly, without breaking the drift run.
SKIN_TEMPERATURE_GAPS = (9, 17)


def _noise(metric_slug: str, days_ago: int) -> float:
    """Reproducible pseudo-random offset in [-1, 1] for one metric-day."""
    return random.Random(f"{metric_slug}:{days_ago}").uniform(-1.0, 1.0)


def _value(metric_slug: str, days_ago: int) -> float:
    profile = DEMO_PROFILE[metric_slug]
    value = profile["center"] + profile["noise"] * _noise(metric_slug, days_ago)

    if days_ago < len(FLARE_RAMP):
        value += profile["flare"] * FLARE_RAMP[days_ago]

    metric = metrics_catalog.get(metric_slug)
    value = min(max(value, metric.valid_min), metric.valid_max)

    decimals = int(profile["decimals"])
    return round(value, decimals) if decimals else float(round(value))


def generate_history(as_of: dt.date) -> Dict[str, Dict[dt.date, float]]:
    """``DEMO_HISTORY_DAYS`` of history per metric, ending on ``as_of``.

    Returns the same ``{metric_slug: {date: value}}`` shape the real store
    hands to the services.
    """
    history: Dict[str, Dict[dt.date, float]] = {}

    for metric_slug in DEMO_PROFILE:
        series: Dict[dt.date, float] = {}
        for days_ago in range(DEMO_HISTORY_DAYS):
            if metric_slug == "skin_temperature" and days_ago in SKIN_TEMPERATURE_GAPS:
                continue
            series[as_of - dt.timedelta(days=days_ago)] = _value(metric_slug, days_ago)
        history[metric_slug] = series

    return history
