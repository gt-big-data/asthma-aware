"""The demo history is what anyone first sees, so it is worth pinning down."""

import datetime as dt

import pytest
from conftest import PINNED_TODAY

from app.services import baseline_service, body_response_service, demo_history
from app.services import metrics_catalog


def test_history_covers_every_catalog_metric():
    history = demo_history.generate_history(PINNED_TODAY)
    assert set(history) == set(metrics_catalog.slugs())


def test_history_is_long_enough_to_establish_a_baseline():
    history = demo_history.generate_history(PINNED_TODAY)
    assert demo_history.DEMO_HISTORY_DAYS > baseline_service.BASELINE_WINDOW_DAYS

    for slug, series in history.items():
        baseline = baseline_service.compute_baseline(
            series, metrics_catalog.get(slug), PINNED_TODAY
        )
        assert baseline is not None, slug
        assert baseline.days_used >= baseline_service.BASELINE_MIN_DAYS


def test_history_ends_on_the_requested_day():
    history = demo_history.generate_history(PINNED_TODAY)
    assert all(max(series) == PINNED_TODAY for series in history.values())
    oldest = PINNED_TODAY - dt.timedelta(days=demo_history.DEMO_HISTORY_DAYS - 1)
    assert min(history["resting_heart_rate"]) == oldest


def test_history_is_deterministic():
    assert demo_history.generate_history(PINNED_TODAY) == demo_history.generate_history(
        PINNED_TODAY
    )


def test_history_is_anchored_to_the_date_so_a_later_demo_is_still_current():
    """Values repeat by day-offset; only the dates move."""
    later = PINNED_TODAY + dt.timedelta(days=60)
    today_history = demo_history.generate_history(PINNED_TODAY)
    later_history = demo_history.generate_history(later)

    assert max(later_history["resting_heart_rate"]) == later
    assert (
        later_history["resting_heart_rate"][later]
        == today_history["resting_heart_rate"][PINNED_TODAY]
    )


def test_every_value_is_physiologically_plausible():
    history = demo_history.generate_history(PINNED_TODAY)
    for slug, series in history.items():
        for date, value in series.items():
            assert metrics_catalog.in_valid_range(slug, value), (slug, date, value)


def test_the_watch_off_wrist_days_are_the_only_gaps():
    history = demo_history.generate_history(PINNED_TODAY)
    expected = demo_history.DEMO_HISTORY_DAYS

    for slug, series in history.items():
        if slug == "skin_temperature":
            assert len(series) == expected - len(demo_history.SKIN_TEMPERATURE_GAPS)
        else:
            assert len(series) == expected, slug


def test_the_demo_user_shows_a_multi_day_drift():
    """The whole point of the fixture: a textbook pre-exacerbation buildup."""
    history = demo_history.generate_history(PINNED_TODAY)
    drifts = baseline_service.detect_drift(history, PINNED_TODAY)
    drifting = {drift.metric.slug for drift in drifts}

    assert "resting_heart_rate" in drifting
    assert "nocturnal_cough_count" in drifting
    assert all(
        drift.consecutive_days >= baseline_service.DRIFT_MIN_DAYS for drift in drifts
    )


def test_cough_is_the_loudest_signal_on_the_worst_day():
    payload = body_response_service.build_body_response(
        demo_history.generate_history(PINNED_TODAY), PINNED_TODAY
    )
    assert payload["contributors"][0]["metric"] == "nocturnal_cough_count"


def test_the_demo_user_lands_in_the_high_band_without_saturating():
    payload = body_response_service.build_body_response(
        demo_history.generate_history(PINNED_TODAY), PINNED_TODAY
    )

    assert payload["body_response"]["level"] == "high"
    # Not pegged at 100: a saturated score would hide the scale working.
    assert 70 <= payload["body_response"]["score"] < 100
    assert payload["coverage"]["weight_available"] == pytest.approx(1.0)


def test_four_weeks_ago_the_demo_user_was_fine():
    """The baseline period must look unremarkable, or the drift means nothing."""
    quiet_day = PINNED_TODAY - dt.timedelta(days=20)
    history = demo_history.generate_history(PINNED_TODAY)
    payload = body_response_service.build_body_response(history, quiet_day)

    assert payload["body_response"]["level"] == "low"
    assert payload["drift"] == []
