import datetime as dt

import pytest
from conftest import PINNED_TODAY, flat_series, series

from app.services import baseline_service as service
from app.services import metrics_catalog

HEART_RATE = metrics_catalog.get("resting_heart_rate")  # higher is worse
HRV = metrics_catalog.get("hrv_rmssd")  # lower is worse
COUGH = metrics_catalog.get("nocturnal_cough_count")


# --- compute_baseline ----------------------------------------------------


def test_baseline_needs_a_minimum_history():
    short = flat_series(62.0, service.BASELINE_MIN_DAYS)  # includes as_of itself
    # The window excludes as_of, so this is one day short.
    assert service.compute_baseline(short, HEART_RATE, PINNED_TODAY) is None

    enough = flat_series(62.0, service.BASELINE_MIN_DAYS + 1)
    assert service.compute_baseline(enough, HEART_RATE, PINNED_TODAY) is not None


def test_baseline_excludes_the_day_being_judged():
    # 14 ordinary days, then a wild value today. If today leaked into the
    # window it would drag the median up and mask its own deviation.
    history = flat_series(60.0, 14, last=200.0)
    baseline = service.compute_baseline(history, HEART_RATE, PINNED_TODAY)

    assert baseline.median == 60.0
    assert baseline.days_used == 13


def test_baseline_median_and_spread_match_a_hand_calculation():
    # 9 values: median 64. Absolute deviations from 64 are
    # [4, 3, 2, 1, 0, 1, 2, 3, 4] -> median 2 -> spread 2 * 1.4826.
    history = series([60, 61, 62, 63, 64, 65, 66, 67, 68], end=PINNED_TODAY - dt.timedelta(days=1))
    baseline = service.compute_baseline(history, HEART_RATE, PINNED_TODAY)

    assert baseline.median == 64.0
    assert baseline.spread == pytest.approx(2 * service.MAD_TO_SIGMA)
    assert baseline.days_used == 9


def test_spread_falls_back_to_the_metric_floor_for_a_steady_user():
    # A perfectly constant series has a MAD of 0, which would make every
    # deviation infinite. The floor is the sensor's own noise level.
    baseline = service.compute_baseline(flat_series(62.0, 20), HEART_RATE, PINNED_TODAY)
    assert baseline.spread == HEART_RATE.min_spread

    # Same situation, different floor: a user who never coughs at night.
    cough_baseline = service.compute_baseline(flat_series(0.0, 20), COUGH, PINNED_TODAY)
    assert cough_baseline.spread == COUGH.min_spread


def test_baseline_window_does_not_reach_past_its_length():
    window = service.BASELINE_WINDOW_DAYS
    # Ten days that all fall before the window opens.
    old = series([90.0] * 10, end=PINNED_TODAY - dt.timedelta(days=window + 1))
    recent = flat_series(60.0, 10)
    baseline = service.compute_baseline({**old, **recent}, HEART_RATE, PINNED_TODAY)

    # Only the recent nine in-window days count, so the old 90s are ignored.
    assert baseline.median == 60.0
    assert baseline.days_used == 9


def test_baseline_window_boundary_is_inclusive_at_the_far_end():
    """The window is [as_of - 28 days, as_of): 28 days back is in, 29 is out."""
    window = service.BASELINE_WINDOW_DAYS
    recent = flat_series(60.0, 10)

    on_the_edge = {PINNED_TODAY - dt.timedelta(days=window): 90.0}
    just_outside = {PINNED_TODAY - dt.timedelta(days=window + 1): 90.0}

    assert service.compute_baseline({**recent, **on_the_edge}, HEART_RATE, PINNED_TODAY).days_used == 10
    assert service.compute_baseline({**recent, **just_outside}, HEART_RATE, PINNED_TODAY).days_used == 9


def test_gaps_shorten_the_history_rather_than_reaching_further_back():
    history = flat_series(60.0, 20)
    for days_ago in range(1, 6):
        del history[PINNED_TODAY - dt.timedelta(days=days_ago)]

    baseline = service.compute_baseline(history, HEART_RATE, PINNED_TODAY)
    assert baseline.days_used == 14


# --- adverse_deviation ---------------------------------------------------


def test_positive_deviation_always_means_worse():
    baseline = service.Baseline(median=60.0, spread=2.0, days_used=28)

    # Higher heart rate is worse.
    assert service.adverse_deviation(66.0, baseline, HEART_RATE) == pytest.approx(3.0)
    assert service.adverse_deviation(54.0, baseline, HEART_RATE) == pytest.approx(-3.0)

    # Lower HRV is worse, so the sign flips.
    assert service.adverse_deviation(54.0, baseline, HRV) == pytest.approx(3.0)
    assert service.adverse_deviation(66.0, baseline, HRV) == pytest.approx(-3.0)


def test_deviation_is_zero_at_the_personal_median():
    baseline = service.Baseline(median=60.0, spread=2.0, days_used=28)
    assert service.adverse_deviation(60.0, baseline, HEART_RATE) == 0.0


def test_the_same_value_deviates_differently_for_two_users():
    """The point of the whole module: 72 bpm is not a number, it is a context."""
    steady = flat_series(62.0, 28, last=72.0)
    higher_resting = flat_series(71.0, 28, last=72.0)

    steady_reading = service.read_metric(steady, HEART_RATE, PINNED_TODAY)
    other_reading = service.read_metric(higher_resting, HEART_RATE, PINNED_TODAY)

    assert steady_reading.deviation > 5
    assert other_reading.deviation < 1
    assert steady_reading.status == service.STATUS_CONCERN
    assert other_reading.status == service.STATUS_TYPICAL


# --- read_metric and status ---------------------------------------------


def test_no_value_today_means_no_reading():
    history = flat_series(62.0, 20)
    del history[PINNED_TODAY]
    assert service.read_metric(history, HEART_RATE, PINNED_TODAY) is None


def test_a_new_user_sees_values_but_no_deviation():
    reading = service.read_metric(flat_series(62.0, 3), HEART_RATE, PINNED_TODAY)

    assert reading.value == 62.0
    assert reading.baseline is None
    assert reading.deviation is None
    assert reading.status == service.STATUS_PENDING


@pytest.mark.parametrize(
    "deviation, expected",
    [
        (0.0, service.STATUS_TYPICAL),
        (0.9, service.STATUS_TYPICAL),
        (1.0, service.STATUS_WATCH),
        (1.9, service.STATUS_WATCH),
        (2.0, service.STATUS_CONCERN),
        (5.0, service.STATUS_CONCERN),
        (-0.9, service.STATUS_TYPICAL),
        (-1.0, service.STATUS_FAVORABLE),
    ],
)
def test_status_bands(deviation, expected):
    reading = service.MetricReading(
        metric=HEART_RATE,
        date=PINNED_TODAY,
        value=0.0,
        baseline=service.Baseline(median=0.0, spread=1.0, days_used=28),
        deviation=deviation,
    )
    assert reading.status == expected


def test_read_all_metrics_returns_catalog_order_and_skips_absent_metrics():
    history = {
        "hrv_rmssd": flat_series(48.0, 20),
        "resting_heart_rate": flat_series(62.0, 20),
    }
    readings = service.read_all_metrics(history, PINNED_TODAY)

    # Catalog order puts resting heart rate (weight 0.20) before HRV (0.15).
    assert [reading.metric.slug for reading in readings] == [
        "resting_heart_rate",
        "hrv_rmssd",
    ]


# --- detect_drift --------------------------------------------------------


def _drifting_history(adverse_days: int) -> dict:
    """Flat at 60 bpm, then ``adverse_days`` days well above baseline."""
    history = flat_series(60.0, 28)
    for days_ago in range(adverse_days):
        history[PINNED_TODAY - dt.timedelta(days=days_ago)] = 70.0
    return history


def test_three_adverse_days_in_a_row_is_a_drift():
    drifts = service.detect_drift(
        {"resting_heart_rate": _drifting_history(3)}, PINNED_TODAY
    )

    assert len(drifts) == 1
    assert drifts[0].metric.slug == "resting_heart_rate"
    assert drifts[0].consecutive_days == 3
    assert drifts[0].deviation > service.DRIFT_SIGMA


def test_two_adverse_days_is_not_yet_a_drift():
    assert service.detect_drift(
        {"resting_heart_rate": _drifting_history(2)}, PINNED_TODAY
    ) == []


def test_one_bad_night_is_not_a_drift():
    history = flat_series(60.0, 28, last=85.0)
    assert service.detect_drift({"resting_heart_rate": history}, PINNED_TODAY) == []


def test_a_steadily_high_user_does_not_drift():
    """A high baseline is not a deterioration; only departure from it is."""
    assert service.detect_drift(
        {"resting_heart_rate": flat_series(88.0, 28)}, PINNED_TODAY
    ) == []


def test_a_missing_day_breaks_the_run():
    history = _drifting_history(4)
    del history[PINNED_TODAY - dt.timedelta(days=1)]

    # Today is still adverse, but yesterday is unknown, so the run is 1.
    assert service.detect_drift({"resting_heart_rate": history}, PINNED_TODAY) == []


def test_drift_is_capped_by_the_lookback_window():
    drifts = service.detect_drift(
        {"resting_heart_rate": _drifting_history(14)}, PINNED_TODAY
    )
    assert drifts[0].consecutive_days == service.DRIFT_MAX_LOOKBACK_DAYS


def test_drift_detects_falling_metrics_too():
    history = flat_series(48.0, 28)
    for days_ago in range(3):
        history[PINNED_TODAY - dt.timedelta(days=days_ago)] = 30.0

    drifts = service.detect_drift({"hrv_rmssd": history}, PINNED_TODAY)
    assert [drift.metric.slug for drift in drifts] == ["hrv_rmssd"]


def test_longest_drift_comes_first():
    history = {
        "resting_heart_rate": _drifting_history(3),
        "respiratory_rate": {
            **flat_series(14.0, 28),
            **{PINNED_TODAY - dt.timedelta(days=d): 18.0 for d in range(5)},
        },
    }
    drifts = service.detect_drift(history, PINNED_TODAY)
    assert [drift.consecutive_days for drift in drifts] == [5, 3]


# --- latest_date_with_data and history_as_series ------------------------


def test_latest_date_is_the_newest_across_all_metrics():
    history = {
        "resting_heart_rate": series([60.0, 61.0], end=PINNED_TODAY - dt.timedelta(days=3)),
        "steps": series([8000.0], end=PINNED_TODAY - dt.timedelta(days=1)),
    }
    assert service.latest_date_with_data(history) == PINNED_TODAY - dt.timedelta(days=1)


def test_latest_date_ignores_future_dates_when_capped():
    history = {"resting_heart_rate": series([60.0], end=PINNED_TODAY + dt.timedelta(days=5))}

    assert service.latest_date_with_data(history) == PINNED_TODAY + dt.timedelta(days=5)
    assert service.latest_date_with_data(history, not_after=PINNED_TODAY) is None


def test_latest_date_of_nothing_is_none():
    assert service.latest_date_with_data({}) is None
    assert service.latest_date_with_data({"steps": {}}) is None


def test_series_covers_the_range_and_skips_missing_days():
    history = flat_series(60.0, 20)
    del history[PINNED_TODAY - dt.timedelta(days=2)]

    points = service.history_as_series(
        history, HEART_RATE, PINNED_TODAY - dt.timedelta(days=4), PINNED_TODAY
    )

    assert [point["date"] for point in points] == [
        PINNED_TODAY - dt.timedelta(days=4),
        PINNED_TODAY - dt.timedelta(days=3),
        PINNED_TODAY - dt.timedelta(days=1),
        PINNED_TODAY,
    ]
    assert all(point["baseline_median"] == 60.0 for point in points)
    assert all(point["deviation"] == 0.0 for point in points)
