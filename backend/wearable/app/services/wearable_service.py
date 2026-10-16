"""Joins the store to the analysis, and assembles the endpoint payloads.

The services either side of this one are deliberately narrow:
``baseline_service`` and ``body_response_service`` are pure functions over
a history dictionary and know nothing about users or storage, which is
what makes them testable with a hand-written 30-day series. This module
is the only place that fetches.

"Up to date" is resolved here too. The newest day a user has data for is
usually yesterday or this morning, never reliably today -- a watch syncs
when it feels like it. So every read reports ``as_of``: the latest day
with data, capped at today so a device with a fast clock cannot make the
app display a future night. Clients show that date rather than assuming
the numbers are from today.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Optional

from app.services import (
    baseline_service,
    body_response_service,
    ingest_service,
    metrics_catalog,
    sample_store,
)

# Default and maximum span of GET /wearable/trends.
DEFAULT_TREND_DAYS = 14
MAX_TREND_DAYS = 90


class UnknownMetricError(KeyError):
    """The requested metric is not in the catalog."""


def _device_out(user_id: str) -> Dict:
    state = sample_store.get_store().device_state(user_id)
    last_sync = state.get("last_sync_at")
    return {
        "device_id": state.get("device_id"),
        "last_sync_at": (
            last_sync.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            if isinstance(last_sync, dt.datetime)
            else None
        ),
    }


def _empty_coverage() -> Dict:
    return {
        "metrics_reported": 0,
        "metrics_scored": 0,
        "metrics_expected": len(metrics_catalog.METRICS),
        "weight_available": 0.0,
        "baseline_ready": False,
        "sufficient": False,
    }


def resolve_as_of(
    history: Dict[str, Dict[dt.date, float]], now: Optional[dt.datetime] = None
) -> Optional[dt.date]:
    """The latest day with data, never later than today. ``None`` if no data."""
    return baseline_service.latest_date_with_data(
        history, not_after=ingest_service.today_local(now)
    )


def get_metrics_snapshot(user_id: str, now: Optional[dt.datetime] = None) -> Dict:
    """Latest health metrics for one user, each against their own baseline."""
    history = sample_store.get_store().history(user_id)
    as_of = resolve_as_of(history, now)

    if as_of is None:
        return {
            "user_id": user_id,
            "as_of": None,
            "device": _device_out(user_id),
            "metrics": [],
            "missing_metrics": metrics_catalog.slugs(),
            "coverage": _empty_coverage(),
        }

    snapshot = body_response_service.build_metrics_snapshot(history, as_of)
    return {"user_id": user_id, "device": _device_out(user_id), **snapshot}


def get_body_response(user_id: str, now: Optional[dt.datetime] = None) -> Dict:
    """The user's body-response score and what is driving it."""
    history = sample_store.get_store().history(user_id)
    as_of = resolve_as_of(history, now)

    if as_of is None:
        return {
            "user_id": user_id,
            "as_of": None,
            "device": _device_out(user_id),
            "body_response": None,
            "weighted_deviation": None,
            "contributors": [],
            "drift": [],
            "coverage": _empty_coverage(),
        }

    payload = body_response_service.build_body_response(history, as_of)
    return {"user_id": user_id, "device": _device_out(user_id), **payload}


def get_trends(
    user_id: str,
    metric_slug: str,
    days: int = DEFAULT_TREND_DAYS,
    now: Optional[dt.datetime] = None,
) -> Dict:
    """One metric's recent series with its baseline band, for plotting."""
    if not metrics_catalog.is_known(metric_slug):
        raise UnknownMetricError(metric_slug)

    metric = metrics_catalog.get(metric_slug)
    history = sample_store.get_store().history(user_id)
    # Anchored on the last day with data so the chart does not trail off
    # into empty days when a watch has not synced for a while.
    end = resolve_as_of(history, now) or ingest_service.today_local(now)
    start = end - dt.timedelta(days=max(days, 1) - 1)

    return {
        "user_id": user_id,
        "metric": metric.slug,
        "display_name": metric.display_name,
        "unit": metric.unit,
        "adverse_direction": metric.direction_label,
        "start_date": start,
        "end_date": end,
        "points": baseline_service.history_as_series(
            history.get(metric_slug, {}), metric, start, end
        ),
    }


def get_catalog() -> Dict:
    """Every metric the system accepts. Lets a device discover what to send."""
    return {
        "metrics": [
            {
                "metric": metric.slug,
                "display_name": metric.display_name,
                "unit": metric.unit,
                "source": metric.source,
                "adverse_direction": metric.direction_label,
                "valid_range": [metric.valid_min, metric.valid_max],
                "weight": metric.weight,
                "description": metric.description,
            }
            for metric in metrics_catalog.METRICS
        ]
    }


def sync_samples(
    user_id: str,
    device_id: Optional[str],
    samples,
    now: Optional[dt.datetime] = None,
) -> Dict:
    """Validate and store a device sync. ``samples`` are ``SampleIn`` models."""
    return ingest_service.ingest_samples(
        user_id,
        [(sample.metric, sample.date, sample.value) for sample in samples],
        device_id=device_id,
        now=now,
    )
