"""Validating what a watch sends before it reaches the store.

Devices are not trustworthy narrators. A dropped PPG contact reports a
heart rate of 0, a watch on a bedside table reports a skin temperature of
21 degC, and a phone whose clock reset reports last night as 1970. None of
these are errors the user can do anything about, and all of them are
destructive if stored: this system's whole output is a comparison against
a four-week median, so one impossible value distorts the user's baseline
for the next month. Filtering at the door is far cheaper than detecting
it later.

Partial acceptance
------------------
A sync carrying 50 samples with one bad value stores the other 49 and
reports what it dropped and why. Rejecting the whole batch would mean a
single faulty sensor silently costs the user every other metric, and
because syncs are retried with the same payload, it would cost them those
metrics permanently. The response therefore always lists rejections --
``accepted`` alone is not a success check.
"""

from __future__ import annotations

import datetime as dt
import math
from typing import Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from app.services import metrics_catalog, sample_store

# The app's reference timezone, matching dashboard_service.LOCAL_TZ.
LOCAL_TZ = ZoneInfo("America/New_York")

# A device in a timezone ahead of Atlanta can legitimately label last
# night with tomorrow's local date here, so one day of slack is allowed.
FUTURE_DATE_GRACE_DAYS = 1

# Anything older than this is a device clock problem, not history. Long
# enough to allow a genuine backfill of a year of watch data on pairing.
MAX_BACKFILL_DAYS = 400

REASON_UNKNOWN_METRIC = "unknown_metric"
REASON_NOT_A_NUMBER = "value_not_finite"
REASON_OUT_OF_RANGE = "value_out_of_plausible_range"
REASON_FUTURE_DATE = "date_in_the_future"
REASON_TOO_OLD = "date_too_far_in_the_past"


def today_local(now: Optional[dt.datetime] = None) -> dt.date:
    """The current calendar date in the app's reference timezone."""
    now = now or dt.datetime.now(dt.timezone.utc)
    return now.astimezone(LOCAL_TZ).date()


def validate_sample(
    metric_slug: str,
    date: dt.date,
    value: float,
    today: dt.date,
) -> Optional[str]:
    """``None`` if the sample is storable, otherwise the rejection reason."""
    if not metrics_catalog.is_known(metric_slug):
        return REASON_UNKNOWN_METRIC

    # NaN compares false against every bound, so it would slip past the
    # range check and then poison statistics.median downstream.
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return REASON_NOT_A_NUMBER

    if not metrics_catalog.in_valid_range(metric_slug, float(value)):
        return REASON_OUT_OF_RANGE

    if date > today + dt.timedelta(days=FUTURE_DATE_GRACE_DAYS):
        return REASON_FUTURE_DATE

    if date < today - dt.timedelta(days=MAX_BACKFILL_DAYS):
        return REASON_TOO_OLD

    return None


def _describe_rejection(index: int, metric_slug: str, date: dt.date, reason: str) -> Dict:
    detail = {
        "index": index,
        "metric": metric_slug,
        "date": date.isoformat(),
        "reason": reason,
    }
    if reason == REASON_OUT_OF_RANGE:
        metric = metrics_catalog.get(metric_slug)
        detail["expected_range"] = [metric.valid_min, metric.valid_max]
        detail["unit"] = metric.unit
    return detail


def ingest_samples(
    user_id: str,
    samples: Iterable[Tuple[str, dt.date, float]],
    device_id: Optional[str] = None,
    now: Optional[dt.datetime] = None,
) -> Dict:
    """Validate and store a sync for one user.

    ``samples`` are ``(metric_slug, date, value)`` triples in the order the
    device sent them, so rejection indices line up with the request.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    today = today_local(now)

    accepted: List[Tuple[str, dt.date, float]] = []
    rejected: List[Dict] = []

    for index, (metric_slug, date, value) in enumerate(samples):
        reason = validate_sample(metric_slug, date, value, today)
        if reason is None:
            accepted.append((metric_slug, date, float(value)))
        else:
            rejected.append(_describe_rejection(index, metric_slug, date, reason))

    store = sample_store.get_store()
    store.upsert_samples(
        user_id, accepted, device_id=device_id, synced_at=now
    )

    dates = sorted({date for _, date, _ in accepted})
    return {
        "accepted": len(accepted),
        "rejected": rejected,
        "stored_dates": [date.isoformat() for date in dates],
        "metrics_stored": sorted({metric for metric, _, _ in accepted}),
        "last_sync_at": now.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
