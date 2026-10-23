"""Personal baselines, deviations from them, and multi-day drift.

This is the part that makes the watch data worth collecting. A resting
heart rate of 72 bpm means nothing on its own: it is unremarkable for one
person and five beats above normal for another. What carries information
is the distance from *that user's own* recent history, which is only
knowable because the watch measures them every day.

So every number the rest of the system uses is a deviation, in units of
the user's own day-to-day variability:

    deviation = adverse_direction * (value - median) / spread

expressed in the user's personal sigma. ``0`` is a typical day for them,
``+2`` is two of their own standard deviations in the worse direction.

Three deliberate choices
------------------------
**Median and MAD, not mean and standard deviation.** One bad night --
a fever, a late flight, a toddler -- shifts a 28-point mean noticeably
and inflates its standard deviation, which then hides the real drift it
should be revealing. The median and median absolute deviation barely
move for a handful of outliers, which is exactly the robustness this
needs given that the outliers are the events we are trying to detect.

**The baseline excludes the day being judged.** The window is the 28
days *before* the target date. Including the day would let an extreme
value pull the median toward itself and understate its own deviation --
the signal partly erasing itself.

**A minimum history before reporting anything.** With fewer than 7 days
the spread estimate is too noisy to call anything unusual, so the
baseline is reported as pending rather than guessed at. A new user sees
their metrics but no deviations for their first week.
"""

from __future__ import annotations

import datetime as dt
import statistics
from dataclasses import dataclass
from typing import Dict, List, Mapping, Optional

from app.services import metrics_catalog
from app.services.metrics_catalog import Metric

# Trailing days considered for a baseline. Four weeks: long enough for a
# stable median, short enough to follow real seasonal change in the user.
BASELINE_WINDOW_DAYS = 28

# Days of data required before any deviation is reported.
BASELINE_MIN_DAYS = 7

# Scales a median absolute deviation to a standard-deviation equivalent
# for normally distributed data, so deviations are readable as sigmas.
MAD_TO_SIGMA = 1.4826

# Deviation bands for the per-metric status flag, in personal sigmas.
STATUS_WATCH_SIGMA = 1.0
STATUS_CONCERN_SIGMA = 2.0
STATUS_FAVORABLE_SIGMA = -1.0

STATUS_TYPICAL = "typical"
STATUS_WATCH = "watch"
STATUS_CONCERN = "concern"
STATUS_FAVORABLE = "better_than_baseline"
STATUS_PENDING = "baseline_pending"

# Drift: a metric this far into the adverse side...
DRIFT_SIGMA = 1.0
# ...on this many consecutive days, counting back from the target date.
DRIFT_MIN_DAYS = 3
# How far back to look for a run. Bounds the work and matches the 1-5 day
# prediction horizon the published home-monitoring models achieve.
DRIFT_MAX_LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class Baseline:
    """One metric's personal normal, over the window before a given date."""

    median: float
    spread: float
    days_used: int
    window_days: int = BASELINE_WINDOW_DAYS


@dataclass(frozen=True)
class MetricReading:
    """A metric's value on one date, next to the user's baseline for it."""

    metric: Metric
    date: dt.date
    value: float
    baseline: Optional[Baseline]
    deviation: Optional[float]

    @property
    def status(self) -> str:
        if self.deviation is None:
            return STATUS_PENDING
        if self.deviation >= STATUS_CONCERN_SIGMA:
            return STATUS_CONCERN
        if self.deviation >= STATUS_WATCH_SIGMA:
            return STATUS_WATCH
        if self.deviation <= STATUS_FAVORABLE_SIGMA:
            return STATUS_FAVORABLE
        return STATUS_TYPICAL


@dataclass(frozen=True)
class Drift:
    """A metric that has stayed on the adverse side for several days."""

    metric: Metric
    consecutive_days: int
    deviation: float


def compute_baseline(
    history: Mapping[dt.date, float],
    metric: Metric,
    as_of: dt.date,
) -> Optional[Baseline]:
    """The user's normal for ``metric`` over the window *before* ``as_of``.

    ``history`` maps date to value; missing days are simply absent, which
    is the normal case (a watch left on the charger reports nothing). The
    window is counted in calendar days, not in samples, so a gap shortens
    the history used rather than silently reaching further back.

    Returns ``None`` when fewer than ``BASELINE_MIN_DAYS`` values fall in
    the window.
    """
    window_start = as_of - dt.timedelta(days=BASELINE_WINDOW_DAYS)
    values = [
        value
        for date, value in history.items()
        if window_start <= date < as_of
    ]
    if len(values) < BASELINE_MIN_DAYS:
        return None

    median = statistics.median(values)
    mad = statistics.median([abs(value - median) for value in values])
    # The floor also covers mad == 0, which is common for coarse metrics
    # (a cough count of 0 every night) and would otherwise divide by zero.
    spread = max(mad * MAD_TO_SIGMA, metric.min_spread)
    return Baseline(median=median, spread=spread, days_used=len(values))


def adverse_deviation(value: float, baseline: Baseline, metric: Metric) -> float:
    """How far ``value`` sits in the *worse* direction, in personal sigmas.

    Positive is worse for every metric, whichever way its numbers run.
    """
    return metric.adverse_direction * (value - baseline.median) / baseline.spread


def read_metric(
    history: Mapping[dt.date, float],
    metric: Metric,
    as_of: dt.date,
) -> Optional[MetricReading]:
    """``metric``'s reading for ``as_of``, or ``None`` if nothing was recorded.

    A reading with no baseline yet still carries its raw value, so a new
    user's app shows real numbers from day one.
    """
    value = history.get(as_of)
    if value is None:
        return None

    baseline = compute_baseline(history, metric, as_of)
    deviation = (
        None if baseline is None else adverse_deviation(value, baseline, metric)
    )
    return MetricReading(
        metric=metric, date=as_of, value=value, baseline=baseline, deviation=deviation
    )


def read_all_metrics(
    history_by_metric: Mapping[str, Mapping[dt.date, float]],
    as_of: dt.date,
) -> List[MetricReading]:
    """Readings for every catalog metric with data on ``as_of``.

    In catalog order, so the most informative metrics come first.
    """
    readings = []
    for metric in metrics_catalog.METRICS:
        reading = read_metric(history_by_metric.get(metric.slug, {}), metric, as_of)
        if reading is not None:
            readings.append(reading)
    return readings


def detect_drift(
    history_by_metric: Mapping[str, Mapping[dt.date, float]],
    as_of: dt.date,
) -> List[Drift]:
    """Metrics on the adverse side for ``DRIFT_MIN_DAYS`` consecutive days.

    This is the early-warning signal the system exists for: not "today is
    bad" but "today is the third bad day in a row". A single poor night is
    noise; a sustained drift away from a four-week baseline is the
    gradual deterioration the FACET study described.

    Each day is judged against *its own* trailing baseline, so a run that
    merely reflects a slowly shifting baseline does not register.
    Returns the longest-running drifts first.
    """
    drifts = []
    for metric in metrics_catalog.METRICS:
        history = history_by_metric.get(metric.slug, {})
        latest_deviation = None
        consecutive = 0

        for days_back in range(DRIFT_MAX_LOOKBACK_DAYS):
            date = as_of - dt.timedelta(days=days_back)
            reading = read_metric(history, metric, date)
            # A missing day or a pending baseline breaks the run: we have
            # no evidence either way, and treating "unknown" as "adverse"
            # would turn a charging night into a warning.
            if reading is None or reading.deviation is None:
                break
            if reading.deviation < DRIFT_SIGMA:
                break
            if latest_deviation is None:
                latest_deviation = reading.deviation
            consecutive += 1

        if consecutive >= DRIFT_MIN_DAYS and latest_deviation is not None:
            drifts.append(
                Drift(
                    metric=metric,
                    consecutive_days=consecutive,
                    deviation=latest_deviation,
                )
            )

    drifts.sort(key=lambda drift: (-drift.consecutive_days, -drift.deviation))
    return drifts


def latest_date_with_data(
    history_by_metric: Mapping[str, Mapping[dt.date, float]],
    not_after: Optional[dt.date] = None,
) -> Optional[dt.date]:
    """The most recent date any metric has a value for, at most ``not_after``.

    What "up to date" means in practice: the app should show the last
    night the watch actually synced, not today's empty slot.
    """
    dates = [
        date
        for history in history_by_metric.values()
        for date in history
        if not_after is None or date <= not_after
    ]
    return max(dates, default=None)


def history_as_series(
    history: Mapping[dt.date, float],
    metric: Metric,
    start: dt.date,
    end: dt.date,
) -> List[Dict]:
    """Per-day value, baseline and deviation over ``[start, end]``, in date order.

    For plotting a metric against its own baseline band. Days with no
    value are omitted rather than sent as nulls, so a chart can draw a
    gap where the watch was on its charger.

    Rounded to 2 decimals, matching the other endpoints: these are
    presentation figures, and full float precision in the JSON is noise.
    """
    series = []
    date = start
    while date <= end:
        reading = read_metric(history, metric, date)
        if reading is not None:
            baseline = reading.baseline
            series.append(
                {
                    "date": date,
                    "value": reading.value,
                    "baseline_median": (
                        None if baseline is None else round(baseline.median, 2)
                    ),
                    "baseline_spread": (
                        None if baseline is None else round(baseline.spread, 2)
                    ),
                    "deviation": (
                        None if reading.deviation is None else round(reading.deviation, 2)
                    ),
                    "status": reading.status,
                }
            )
        date += dt.timedelta(days=1)
    return series
