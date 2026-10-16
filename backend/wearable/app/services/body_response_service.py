"""The body-response score: one number for "how is this user doing today".

The write-up describes three layers of asthma risk. The environmental
model covers exposure (what the user is breathing) and the forecast
covers where it is going. This is the middle layer: whether the user's
own physiology is drifting away from their normal, which is what
distinguishes two people breathing the same air.

    body_response = sum(w_i * d_i) / sum(w_i)   over metrics with data

``d_i`` is the metric's adverse deviation in the user's personal sigmas
(see ``baseline_service``) and ``w_i`` its catalog weight. The division
renormalises over whatever the watch actually reported: a user whose
device has no SpO2 sensor gets a score built from the rest, not a score
quietly dragged toward zero by a missing term. That does mean a score
from two metrics is a weaker claim than one from nine, so every response
carries a ``coverage`` block saying how much of the catalog weight was
available. A client showing the score without the coverage is
overstating it.

Scale
-----
Deviations are unbounded, scores are not. ``BODY_RESPONSE_SIGMA_MAX``
sets the top of the scale: a weighted adverse deviation of +3 personal
sigmas maps to 100. A typical day for the user maps to 0, and days
*better* than their baseline also map to 0 -- there is no credit below
normal, the score only measures departure in the worse direction.

The thresholds and the level bands are placeholders in the same sense as
the dashboard's ``SEVERITY_SCALE_MAX``: named constants to be refitted
once there is labelled exacerbation data (AAMOS-00) to calibrate against.
The level bands match the dashboard's on purpose, so ``low`` /
``moderate`` / ``high`` mean the same thing wherever the app shows them.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, List, Mapping, Optional

from app.services import baseline_service, metrics_catalog
from app.services.baseline_service import Drift, MetricReading

# A weighted adverse deviation of this many personal sigmas scores 100.
BODY_RESPONSE_SIGMA_MAX = 3.0

# (minimum score, level), checked from the top down. Same bands as the
# dashboard's RISK_LEVEL_THRESHOLDS.
LEVEL_THRESHOLDS = ((70, "high"), (40, "moderate"), (0, "low"))

MAX_CONTRIBUTORS = 3

# Below this share of the catalog's total weight, the score is reported
# but flagged as thin: too few sensors reporting to stand behind it.
MIN_COVERAGE_WEIGHT = 0.40


def weighted_adverse_deviation(readings: List[MetricReading]) -> Optional[float]:
    """The weight-renormalised mean adverse deviation across ``readings``.

    Readings whose baseline is still pending are skipped -- they have a
    value but no personal normal to compare it against. Returns ``None``
    when nothing usable is left.
    """
    usable = [reading for reading in readings if reading.deviation is not None]
    total_weight = sum(reading.metric.weight for reading in usable)
    if total_weight <= 0:
        return None

    weighted = sum(
        reading.metric.weight * reading.deviation for reading in usable
    )
    return weighted / total_weight


def score_from_deviation(deviation: float) -> int:
    """Map a weighted adverse deviation onto 0-100."""
    score = round(deviation / BODY_RESPONSE_SIGMA_MAX * 100)
    return max(0, min(100, score))


def level_from_score(score: int) -> str:
    for threshold, level in LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return LEVEL_THRESHOLDS[-1][1]


def get_top_contributors(
    readings: List[MetricReading], limit: int = MAX_CONTRIBUTORS
) -> List[Dict]:
    """The metrics pushing the score up most, as shares of the total rise.

    A metric's impact is ``w_i * d_i``. Metrics on the better-than-normal
    side have negative impact and are not "behind" an elevated score, so
    only positive impacts are ranked -- the same rule the dashboard
    applies to environmental contributors.
    """
    impacts = {
        reading.metric: reading.metric.weight * reading.deviation
        for reading in readings
        if reading.deviation is not None
    }
    increases = {metric: impact for metric, impact in impacts.items() if impact > 0}
    total = sum(increases.values())
    if total <= 0:
        return []

    ranked = sorted(increases.items(), key=lambda item: (-item[1], item[0].slug))[:limit]
    return [
        {
            "metric": metric.slug,
            "display_name": metric.display_name,
            "contribution": round(impact / total, 2),
        }
        for metric, impact in ranked
    ]


def describe_coverage(readings: List[MetricReading]) -> Dict:
    """How much of the catalog stood behind the score.

    ``weight_available`` is the share of total catalog weight whose
    metrics had both a value and an established baseline; it is the
    honest measure of how much the score rests on, since a missing
    resting heart rate costs far more than a missing skin temperature.
    """
    with_baseline = [reading for reading in readings if reading.deviation is not None]
    weight_available = sum(reading.metric.weight for reading in with_baseline)
    share = weight_available / metrics_catalog.TOTAL_WEIGHT

    return {
        "metrics_reported": len(readings),
        "metrics_scored": len(with_baseline),
        "metrics_expected": len(metrics_catalog.METRICS),
        "weight_available": round(share, 2),
        "baseline_ready": bool(with_baseline),
        "sufficient": share >= MIN_COVERAGE_WEIGHT,
    }


def describe_drift(drifts: List[Drift]) -> List[Dict]:
    return [
        {
            "metric": drift.metric.slug,
            "display_name": drift.metric.display_name,
            "consecutive_days": drift.consecutive_days,
            "deviation": round(drift.deviation, 2),
            "direction": drift.metric.direction_label,
        }
        for drift in drifts
    ]


def describe_reading(reading: MetricReading) -> Dict:
    """One metric, formatted for the API."""
    baseline = reading.baseline
    return {
        "metric": reading.metric.slug,
        "display_name": reading.metric.display_name,
        "unit": reading.metric.unit,
        "source": reading.metric.source,
        "adverse_direction": reading.metric.direction_label,
        "value": reading.value,
        "baseline": (
            None
            if baseline is None
            else {
                "median": round(baseline.median, 2),
                "spread": round(baseline.spread, 2),
                "days_used": baseline.days_used,
                "window_days": baseline.window_days,
            }
        ),
        "deviation": None if reading.deviation is None else round(reading.deviation, 2),
        "status": reading.status,
        "description": reading.metric.description,
    }


def build_body_response(
    history_by_metric: Mapping[str, Mapping[dt.date, float]],
    as_of: dt.date,
) -> Dict:
    """Score, level, contributors, drift and coverage for one user and day.

    This is the payload the dashboard's personalization layer consumes.
    """
    readings = baseline_service.read_all_metrics(history_by_metric, as_of)
    deviation = weighted_adverse_deviation(readings)

    if deviation is None:
        # No baseline anywhere yet: a brand-new user in their first week.
        # Returning a score of 0 here would read as "doing fine", which is
        # a claim we cannot make, so the score is explicitly null.
        return {
            "as_of": as_of,
            "body_response": None,
            "weighted_deviation": None,
            "contributors": [],
            "drift": [],
            "coverage": describe_coverage(readings),
        }

    score = score_from_deviation(deviation)
    return {
        "as_of": as_of,
        "body_response": {"score": score, "level": level_from_score(score)},
        "weighted_deviation": round(deviation, 2),
        "contributors": get_top_contributors(readings),
        "drift": describe_drift(baseline_service.detect_drift(history_by_metric, as_of)),
        "coverage": describe_coverage(readings),
    }


def build_metrics_snapshot(
    history_by_metric: Mapping[str, Mapping[dt.date, float]],
    as_of: dt.date,
) -> Dict:
    """Every metric's latest reading, with baselines and statuses.

    The "up to date health data" view: what the watch last reported and
    how each number compares with this user's own normal.
    """
    readings = baseline_service.read_all_metrics(history_by_metric, as_of)
    reported = {reading.metric.slug for reading in readings}

    return {
        "as_of": as_of,
        "metrics": [describe_reading(reading) for reading in readings],
        # Named so the client can say "no SpO2 last night" rather than
        # silently omitting a tile the user expects to see.
        "missing_metrics": [
            slug for slug in metrics_catalog.slugs() if slug not in reported
        ],
        "coverage": describe_coverage(readings),
    }
