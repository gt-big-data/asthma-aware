import datetime as dt

import pytest
from conftest import PINNED_TODAY, flat_series

from app.services import baseline_service, metrics_catalog
from app.services import body_response_service as service

HEART_RATE = metrics_catalog.get("resting_heart_rate")  # weight 0.20
COUGH = metrics_catalog.get("nocturnal_cough_count")  # weight 0.20
HRV = metrics_catalog.get("hrv_rmssd")  # weight 0.15
SKIN_TEMP = metrics_catalog.get("skin_temperature")  # weight 0.03


def reading(metric, deviation):
    """A reading with a given deviation, for testing the scoring directly."""
    return baseline_service.MetricReading(
        metric=metric,
        date=PINNED_TODAY,
        value=0.0,
        baseline=baseline_service.Baseline(median=0.0, spread=1.0, days_used=28),
        deviation=deviation,
    )


def pending_reading(metric):
    return baseline_service.MetricReading(
        metric=metric, date=PINNED_TODAY, value=0.0, baseline=None, deviation=None
    )


# --- catalog sanity ------------------------------------------------------


def test_catalog_weights_sum_to_one():
    assert metrics_catalog.TOTAL_WEIGHT == pytest.approx(1.0)


def test_every_metric_has_a_sane_definition():
    for metric in metrics_catalog.METRICS:
        assert metric.valid_min < metric.valid_max
        assert metric.min_spread > 0
        assert metric.weight > 0
        assert metric.adverse_direction in (
            metrics_catalog.HIGHER_IS_WORSE,
            metrics_catalog.LOWER_IS_WORSE,
        )


# --- weighted_adverse_deviation -----------------------------------------


def test_weighted_deviation_matches_a_hand_calculation():
    readings = [reading(HEART_RATE, 2.0), reading(HRV, 1.0)]
    # (0.20*2.0 + 0.15*1.0) / (0.20 + 0.15) = 0.55 / 0.35
    assert service.weighted_adverse_deviation(readings) == pytest.approx(0.55 / 0.35)


def test_missing_sensors_do_not_dilute_the_score():
    """A watch without an SpO2 sensor must not look healthier than one with."""
    only_heart_rate = [reading(HEART_RATE, 2.0)]
    assert service.weighted_adverse_deviation(only_heart_rate) == pytest.approx(2.0)


def test_pending_baselines_are_excluded_not_counted_as_zero():
    readings = [reading(HEART_RATE, 2.0), pending_reading(COUGH)]
    assert service.weighted_adverse_deviation(readings) == pytest.approx(2.0)


def test_no_usable_readings_gives_no_deviation():
    assert service.weighted_adverse_deviation([]) is None
    assert service.weighted_adverse_deviation([pending_reading(HEART_RATE)]) is None


def test_a_better_than_normal_day_gives_a_negative_deviation():
    assert service.weighted_adverse_deviation([reading(HEART_RATE, -1.5)]) < 0


# --- score and level ----------------------------------------------------


@pytest.mark.parametrize(
    "deviation, expected_score",
    [
        (0.0, 0),
        (1.5, 50),
        (3.0, 100),
        (4.5, 100),  # clamped
        (-2.0, 0),  # no credit for better than normal
    ],
)
def test_score_scale(deviation, expected_score):
    assert service.score_from_deviation(deviation) == expected_score


@pytest.mark.parametrize(
    "score, expected",
    [(0, "low"), (39, "low"), (40, "moderate"), (69, "moderate"), (70, "high"), (100, "high")],
)
def test_level_bands(score, expected):
    assert service.level_from_score(score) == expected


def test_level_bands_match_the_dashboard_endpoint():
    """low/moderate/high must mean the same thing across the whole app."""
    assert service.LEVEL_THRESHOLDS == ((70, "high"), (40, "moderate"), (0, "low"))


# --- contributors -------------------------------------------------------


def test_contributors_are_ranked_by_weighted_impact_and_sum_to_one():
    readings = [reading(COUGH, 3.0), reading(HEART_RATE, 1.0), reading(HRV, 2.0)]
    contributors = service.get_top_contributors(readings)

    # Impacts: cough 0.60, HRV 0.30, heart rate 0.20; total 1.10.
    assert [item["metric"] for item in contributors] == [
        "nocturnal_cough_count",
        "hrv_rmssd",
        "resting_heart_rate",
    ]
    assert contributors[0]["contribution"] == pytest.approx(0.55, abs=0.01)
    assert sum(item["contribution"] for item in contributors) == pytest.approx(1.0, abs=0.02)


def test_metrics_better_than_baseline_are_not_contributors():
    readings = [reading(HEART_RATE, 2.0), reading(HRV, -3.0)]
    contributors = service.get_top_contributors(readings)
    assert [item["metric"] for item in contributors] == ["resting_heart_rate"]


def test_an_all_good_day_has_no_contributors():
    assert service.get_top_contributors([reading(HEART_RATE, -1.0)]) == []


def test_contributors_are_capped():
    readings = [reading(metric, 1.0) for metric in metrics_catalog.METRICS]
    assert len(service.get_top_contributors(readings)) == service.MAX_CONTRIBUTORS


# --- coverage -----------------------------------------------------------


def test_coverage_reports_the_share_of_weight_behind_the_score():
    coverage = service.describe_coverage([reading(HEART_RATE, 1.0), reading(COUGH, 1.0)])

    assert coverage["metrics_reported"] == 2
    assert coverage["metrics_scored"] == 2
    assert coverage["metrics_expected"] == len(metrics_catalog.METRICS)
    assert coverage["weight_available"] == pytest.approx(0.40)
    assert coverage["baseline_ready"] is True
    assert coverage["sufficient"] is True


def test_one_low_weight_sensor_is_not_sufficient_coverage():
    coverage = service.describe_coverage([reading(SKIN_TEMP, 3.0)])
    assert coverage["weight_available"] == pytest.approx(0.03)
    assert coverage["sufficient"] is False


def test_full_catalog_is_full_coverage():
    readings = [reading(metric, 0.0) for metric in metrics_catalog.METRICS]
    assert service.describe_coverage(readings)["weight_available"] == pytest.approx(1.0)


def test_reported_but_unbaselined_metrics_count_as_reported_only():
    coverage = service.describe_coverage([pending_reading(HEART_RATE)])
    assert coverage["metrics_reported"] == 1
    assert coverage["metrics_scored"] == 0
    assert coverage["baseline_ready"] is False


# --- build_body_response ------------------------------------------------


def test_a_steady_user_scores_low():
    history = {
        "resting_heart_rate": flat_series(62.0, 28),
        "nocturnal_cough_count": flat_series(4.0, 28),
        "hrv_rmssd": flat_series(48.0, 28),
    }
    payload = service.build_body_response(history, PINNED_TODAY)

    assert payload["body_response"]["score"] == 0
    assert payload["body_response"]["level"] == "low"
    assert payload["contributors"] == []
    assert payload["drift"] == []


def test_a_deteriorating_user_scores_high_and_shows_drift():
    history = {}
    for slug, normal, bad in [
        ("resting_heart_rate", 62.0, 72.0),
        ("nocturnal_cough_count", 5.0, 30.0),
        ("respiratory_rate", 14.0, 18.0),
    ]:
        series_values = flat_series(normal, 28)
        for days_ago in range(3):
            series_values[PINNED_TODAY - dt.timedelta(days=days_ago)] = bad
        history[slug] = series_values

    payload = service.build_body_response(history, PINNED_TODAY)

    assert payload["body_response"]["level"] == "high"
    assert payload["weighted_deviation"] > 2.0
    assert {item["metric"] for item in payload["drift"]} == set(history)
    assert all(item["consecutive_days"] == 3 for item in payload["drift"])
    assert payload["contributors"]


def test_a_brand_new_user_gets_a_null_score_not_a_zero():
    """Zero would read as 'doing fine', which we cannot claim with no baseline."""
    payload = service.build_body_response(
        {"resting_heart_rate": flat_series(62.0, 3)}, PINNED_TODAY
    )

    assert payload["body_response"] is None
    assert payload["weighted_deviation"] is None
    assert payload["coverage"]["baseline_ready"] is False
    assert payload["coverage"]["metrics_reported"] == 1


def test_drift_direction_is_reported_for_falling_metrics():
    history = {"hrv_rmssd": flat_series(48.0, 28)}
    for days_ago in range(3):
        history["hrv_rmssd"][PINNED_TODAY - dt.timedelta(days=days_ago)] = 28.0

    payload = service.build_body_response(history, PINNED_TODAY)
    assert payload["drift"][0]["direction"] == "lower"


# --- build_metrics_snapshot ---------------------------------------------


def test_snapshot_names_the_metrics_that_did_not_report():
    history = {"resting_heart_rate": flat_series(62.0, 28)}
    snapshot = service.build_metrics_snapshot(history, PINNED_TODAY)

    assert [item["metric"] for item in snapshot["metrics"]] == ["resting_heart_rate"]
    assert "nocturnal_cough_count" in snapshot["missing_metrics"]
    assert len(snapshot["missing_metrics"]) == len(metrics_catalog.METRICS) - 1


def test_snapshot_carries_units_direction_and_baseline():
    history = {"hrv_rmssd": flat_series(48.0, 28, last=30.0)}
    item = service.build_metrics_snapshot(history, PINNED_TODAY)["metrics"][0]

    assert item["unit"] == "ms"
    assert item["adverse_direction"] == "lower"
    assert item["source"] == "watch"
    assert item["value"] == 30.0
    assert item["baseline"]["median"] == 48.0
    assert item["baseline"]["days_used"] == 27
    assert item["deviation"] > 2
    assert item["status"] == "concern"
