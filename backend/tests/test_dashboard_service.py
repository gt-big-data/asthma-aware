from datetime import datetime, timezone

import pytest

from app.services import dashboard_service as service
from app.services import mock_data

DOWNTOWN_ATLANTA = (33.7490, -84.3880)
SAVANNAH = (32.08, -81.09)
MACON = (32.84, -83.63)  # inside Georgia, no cell-specific mock data
NEW_YORK = (40.7128, -74.0060)

# 20:30 UTC on 2026-10-15 is 16:30 EDT, an ordinary (non-DST-change) day.
PINNED_NOW = datetime(2026, 10, 15, 20, 30, tzinfo=timezone.utc)


# --- resolve_grid_cell ---------------------------------------------------


def test_spec_example_resolves_to_a_cell_with_its_own_mock_data():
    cell = service.resolve_grid_cell(*DOWNTOWN_ATLANTA)
    assert cell == "ga_02424"
    assert cell in mock_data.load_mock_forecast()["cells"]


def test_savannah_resolves_to_a_cell_with_its_own_mock_data():
    cell = service.resolve_grid_cell(*SAVANNAH)
    assert cell == "ga_05658"
    assert cell in mock_data.load_mock_forecast()["cells"]


def test_nearby_points_share_a_cell_and_lookup_is_repeatable():
    assert service.resolve_grid_cell(33.7490, -84.3880) == service.resolve_grid_cell(33.7491, -84.3879)
    assert service.resolve_grid_cell(*DOWNTOWN_ATLANTA) == service.resolve_grid_cell(*DOWNTOWN_ATLANTA)


def test_grid_corners():
    bounds = service.GEORGIA_BOUNDS
    last_id = f"ga_{service.GRID_ROWS * service.GRID_COLS - 1:05d}"
    assert service.resolve_grid_cell(bounds["max_lat"], bounds["min_lon"]) == "ga_00000"
    assert service.resolve_grid_cell(bounds["min_lat"], bounds["max_lon"]) == last_id


@pytest.mark.parametrize("lat, lon", [NEW_YORK, (30.34, -84.0), (33.7, -85.62), (35.01, -84.0), (33.7, -80.83)])
def test_outside_georgia_has_no_cell(lat, lon):
    assert service.resolve_grid_cell(lat, lon) is None


# --- calculate_personalized_risk -----------------------------------------


def test_personalized_risk_matches_hand_calculation():
    features = {"pm25": 0.5, "o3": -0.2, "pollen": 0.4}
    weights = {"pm25": 0.4, "o3": 0.2, "no2": 0.1}
    # 1.8 + 0.4*0.5 + 0.2*(-0.2); pollen has no weight, no2 has no value.
    assert service.calculate_personalized_risk(1.8, features, weights) == pytest.approx(1.96)


def test_no_weights_means_general_prediction():
    assert service.calculate_personalized_risk(1.8, {"pm25": 0.9}, {}) == 1.8


# --- risk_score_from_severity / risk_level_from_score ---------------------


@pytest.mark.parametrize("prediction, score", [(0.0, 0), (2.0, 50), (4.0, 100), (10.0, 100), (-3.0, 0)])
def test_score_is_scaled_and_clamped(prediction, score):
    assert service.risk_score_from_severity(prediction) == score


@pytest.mark.parametrize(
    "score, level",
    [(0, "low"), (39, "low"), (40, "moderate"), (67, "moderate"), (69, "moderate"), (70, "high"), (100, "high")],
)
def test_level_bands(score, level):
    assert service.risk_level_from_score(score) == level


# --- get_top_contributors -------------------------------------------------


def test_top_contributors_are_positive_ranked_shares():
    features = {"pm25": 1.0, "o3": 0.5, "no2": 0.2, "wind": -1.0, "humidity": 0.1}
    weights = {"pm25": 0.4, "o3": 0.4, "no2": 0.5, "wind": 0.3, "humidity": 0.1}
    # Impacts: pm25 0.4, o3 0.2, no2 0.1, humidity 0.01, wind -0.3 (lowers risk).
    # Shares are of the positive total 0.71.
    assert service.get_top_contributors(features, weights) == [
        {"feature": "pm25", "contribution": 0.56},
        {"feature": "o3", "contribution": 0.28},
        {"feature": "no2", "contribution": 0.14},
    ]


def test_no_contributors_when_nothing_raises_risk():
    assert service.get_top_contributors({"pm25": 0.9}, {}) == []
    assert service.get_top_contributors({"wind": -1.0}, {"wind": 0.3}) == []


# --- get_forecast_for_cell ------------------------------------------------


def test_unlisted_georgia_cell_uses_default_profile():
    cell = service.resolve_grid_cell(*MACON)
    assert cell not in mock_data.load_mock_forecast()["cells"]

    records = service.get_forecast_for_cell(cell, service.local_day_range(PINNED_NOW))
    default_hours = mock_data.load_mock_forecast()["default"]["hours"]
    assert [r["general_prediction"] for r in records] == [h["general_prediction"] for h in default_hours]


# --- get_dashboard --------------------------------------------------------


def test_dashboard_with_pinned_clock():
    result = service.get_dashboard(*DOWNTOWN_ATLANTA, user_id="demo-user", now=PINNED_NOW)

    assert result["location"] == {"lat": 33.7490, "lon": -84.3880, "grid_cell_id": "ga_02424"}
    # The Atlanta day of 2026-10-15 runs 04:00Z to 03:00Z the next day (EDT, UTC-4).
    assert result["forecast"] == {"forecast_run_id": "2026-10-15T04:00:00Z", "valid_at": "2026-10-15T20:00:00Z"}

    hourly = result["hourly_risk"]
    assert len(hourly) == 24
    assert hourly[0]["timestamp"] == "2026-10-15T04:00:00Z"
    assert hourly[-1]["timestamp"] == "2026-10-16T03:00:00Z"

    # Current risk is the 16:00 local hour, computed independently from the fixtures.
    hour_16 = mock_data.load_mock_forecast()["cells"]["ga_02424"]["hours"][16]
    weights = mock_data.load_user_personalization()["users"]["demo-user"]
    expected = hour_16["general_prediction"] + sum(weights[f] * v for f, v in hour_16["features"].items())
    expected_score = round(expected / service.SEVERITY_SCALE_MAX * 100)

    assert result["risk"]["score"] == expected_score
    assert result["risk"]["level"] == service.risk_level_from_score(expected_score)
    assert {"timestamp": "2026-10-15T20:00:00Z", "risk": expected_score} in hourly

    contributors = result["contributors"]
    assert 1 <= len(contributors) <= 3
    shares = [c["contribution"] for c in contributors]
    assert shares == sorted(shares, reverse=True)


@pytest.mark.parametrize(
    "now, expected_hours",
    [
        (datetime(2026, 3, 8, 18, tzinfo=timezone.utc), 23),  # clocks spring forward
        (datetime(2026, 11, 1, 18, tzinfo=timezone.utc), 25),  # clocks fall back
    ],
)
def test_daylight_saving_days(now, expected_hours):
    result = service.get_dashboard(*DOWNTOWN_ATLANTA, user_id="demo-user", now=now)
    assert len(result["hourly_risk"]) == expected_hours


def test_user_without_personalization_gets_general_risk():
    result = service.get_dashboard(*DOWNTOWN_ATLANTA, user_id="unknown-user", now=PINNED_NOW)
    hour_16 = mock_data.load_mock_forecast()["cells"]["ga_02424"]["hours"][16]
    assert result["risk"]["score"] == service.risk_score_from_severity(hour_16["general_prediction"])
    assert result["contributors"] == []


def test_outside_georgia_raises():
    with pytest.raises(service.LocationNotSupportedError, match="outside the supported area"):
        service.get_dashboard(*NEW_YORK, user_id="demo-user", now=PINNED_NOW)


def test_naive_now_is_rejected():
    with pytest.raises(ValueError):
        service.get_dashboard(*DOWNTOWN_ATLANTA, user_id="demo-user", now=datetime(2026, 10, 15, 20))
