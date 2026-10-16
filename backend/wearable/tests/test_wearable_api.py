"""Endpoint tests: the response contracts, through the real FastAPI app."""

import datetime as dt

from conftest import TEST_USER
from fastapi.testclient import TestClient

from app.api.wearable import get_current_user_id
from app.main import app
from app.services import ingest_service, metrics_catalog, sample_store, wearable_service

client = TestClient(app)


def iso(days_ago: int = 0) -> str:
    return (ingest_service.today_local() - dt.timedelta(days=days_ago)).isoformat()


# --- GET /health and /wearable/catalog ----------------------------------


def test_health_check():
    assert client.get("/health").json() == {"status": "ok"}


def test_catalog_lets_a_device_discover_what_to_send():
    response = client.get("/wearable/catalog")
    assert response.status_code == 200

    metrics = response.json()["metrics"]
    assert len(metrics) == len(metrics_catalog.METRICS)
    assert [item["metric"] for item in metrics] == metrics_catalog.slugs()

    for item in metrics:
        assert set(item) == {
            "metric",
            "display_name",
            "unit",
            "source",
            "adverse_direction",
            "valid_range",
            "weight",
            "description",
        }
        assert item["adverse_direction"] in {"higher", "lower"}
        assert item["valid_range"][0] < item["valid_range"][1]


# --- POST /wearable/samples ---------------------------------------------


def test_a_watch_sync_is_accepted_and_readable_back():
    response = client.post(
        "/wearable/samples",
        json={
            "device_id": "galaxy-watch-7",
            "samples": [
                {"metric": "resting_heart_rate", "date": iso(), "value": 64},
                {"metric": "spo2", "date": iso(), "value": 97.1},
            ],
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["accepted"] == 2
    assert body["rejected"] == []
    assert body["stored_dates"] == [iso()]
    assert body["metrics_stored"] == ["resting_heart_rate", "spo2"]
    assert body["last_sync_at"].endswith("Z")

    metrics = client.get("/wearable/metrics").json()
    assert metrics["as_of"] == iso()
    assert metrics["device"]["device_id"] == "galaxy-watch-7"
    assert {item["metric"] for item in metrics["metrics"]} == {"resting_heart_rate", "spo2"}


def test_a_partially_bad_sync_is_200_with_the_rejections_listed():
    response = client.post(
        "/wearable/samples",
        json={
            "samples": [
                {"metric": "resting_heart_rate", "date": iso(), "value": 64},
                {"metric": "resting_heart_rate", "date": iso(1), "value": 0},
                {"metric": "not_a_metric", "date": iso(), "value": 1},
            ]
        },
    )
    assert response.status_code == 200

    body = response.json()
    assert body["accepted"] == 1
    assert [item["index"] for item in body["rejected"]] == [1, 2]
    assert body["rejected"][0]["reason"] == "value_out_of_plausible_range"
    assert body["rejected"][0]["expected_range"] == [30.0, 150.0]
    assert body["rejected"][1]["reason"] == "unknown_metric"


def test_a_malformed_sync_is_rejected_outright():
    assert client.post("/wearable/samples", json={"samples": []}).status_code == 422
    assert client.post("/wearable/samples", json={}).status_code == 422
    assert (
        client.post(
            "/wearable/samples",
            json={"samples": [{"metric": "spo2", "date": "not-a-date", "value": 97}]},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/wearable/samples",
            json={"samples": [{"metric": "spo2", "date": iso(), "value": "high"}]},
        ).status_code
        == 422
    )


def test_resyncing_is_idempotent_over_the_api():
    payload = {
        "device_id": "watch",
        "samples": [{"metric": "steps", "date": iso(), "value": 8200}],
    }
    client.post("/wearable/samples", json=payload)
    client.post("/wearable/samples", json=payload)

    series = client.get("/wearable/trends/steps", params={"days": 7}).json()["points"]
    assert len(series) == 1
    assert series[0]["value"] == 8200.0


# --- GET /wearable/metrics ----------------------------------------------


def test_metrics_returns_the_full_contract(live_demo_store):
    today = live_demo_store
    response = client.get("/wearable/metrics")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {
        "user_id",
        "as_of",
        "device",
        "metrics",
        "missing_metrics",
        "coverage",
    }
    assert body["user_id"] == "demo-user"
    assert body["as_of"] == today.isoformat()
    assert body["missing_metrics"] == []
    assert len(body["metrics"]) == len(metrics_catalog.METRICS)

    for item in body["metrics"]:
        assert set(item) == {
            "metric",
            "display_name",
            "unit",
            "source",
            "adverse_direction",
            "value",
            "baseline",
            "deviation",
            "status",
            "description",
        }
        assert set(item["baseline"]) == {"median", "spread", "days_used", "window_days"}
        assert item["status"] in {
            "typical",
            "watch",
            "concern",
            "better_than_baseline",
            "baseline_pending",
        }

    assert body["coverage"]["weight_available"] == 1.0
    assert body["coverage"]["sufficient"] is True


def test_metrics_for_a_user_with_no_data_is_empty_not_an_error():
    body = client.get("/wearable/metrics").json()

    assert body["as_of"] is None
    assert body["metrics"] == []
    assert body["missing_metrics"] == metrics_catalog.slugs()
    assert body["coverage"]["baseline_ready"] is False
    assert body["device"] == {"device_id": None, "last_sync_at": None}


def test_a_brand_new_user_sees_values_with_a_pending_baseline():
    client.post(
        "/wearable/samples",
        json={"samples": [{"metric": "resting_heart_rate", "date": iso(), "value": 64}]},
    )
    item = client.get("/wearable/metrics").json()["metrics"][0]

    assert item["value"] == 64.0
    assert item["baseline"] is None
    assert item["deviation"] is None
    assert item["status"] == "baseline_pending"


def test_as_of_is_the_last_day_that_synced_not_today():
    client.post(
        "/wearable/samples",
        json={"samples": [{"metric": "steps", "date": iso(3), "value": 8000}]},
    )
    assert client.get("/wearable/metrics").json()["as_of"] == iso(3)


def test_a_device_with_a_fast_clock_cannot_show_a_future_night():
    """A tomorrow-dated sample is storable (timezone slack) but is not 'latest'."""
    tomorrow = (ingest_service.today_local() + dt.timedelta(days=1)).isoformat()
    client.post(
        "/wearable/samples",
        json={
            "samples": [
                {"metric": "steps", "date": tomorrow, "value": 100},
                {"metric": "steps", "date": iso(), "value": 8000},
            ]
        },
    )
    body = client.get("/wearable/metrics").json()

    assert body["as_of"] == iso()
    assert body["metrics"][0]["value"] == 8000.0


# --- GET /wearable/body-response ----------------------------------------


def test_body_response_returns_the_full_contract(live_demo_store):
    response = client.get("/wearable/body-response")
    assert response.status_code == 200

    body = response.json()
    assert set(body) == {
        "user_id",
        "as_of",
        "device",
        "body_response",
        "weighted_deviation",
        "contributors",
        "drift",
        "coverage",
    }
    assert body["as_of"] == live_demo_store.isoformat()
    assert 0 <= body["body_response"]["score"] <= 100
    assert body["body_response"]["level"] in {"low", "moderate", "high"}

    for item in body["contributors"]:
        assert set(item) == {"metric", "display_name", "contribution"}
        assert 0 <= item["contribution"] <= 1

    for item in body["drift"]:
        assert set(item) == {
            "metric",
            "display_name",
            "consecutive_days",
            "deviation",
            "direction",
        }
        assert item["consecutive_days"] >= 3


def test_the_demo_user_reads_as_a_building_exacerbation(live_demo_store):
    body = client.get("/wearable/body-response").json()

    assert body["body_response"]["level"] == "high"
    assert body["contributors"][0]["metric"] == "nocturnal_cough_count"
    drifting = {item["metric"] for item in body["drift"]}
    assert {"resting_heart_rate", "nocturnal_cough_count"} <= drifting


def test_body_response_is_null_without_a_baseline():
    client.post(
        "/wearable/samples",
        json={"samples": [{"metric": "resting_heart_rate", "date": iso(), "value": 64}]},
    )
    body = client.get("/wearable/body-response").json()

    assert body["body_response"] is None
    assert body["weighted_deviation"] is None
    assert body["coverage"]["baseline_ready"] is False


def test_body_response_for_a_user_with_no_data():
    body = client.get("/wearable/body-response").json()
    assert body["as_of"] is None
    assert body["body_response"] is None
    assert body["drift"] == []


# --- GET /wearable/trends -----------------------------------------------


def test_trends_returns_a_plottable_series(live_demo_store):
    response = client.get("/wearable/trends/resting_heart_rate", params={"days": 10})
    assert response.status_code == 200

    body = response.json()
    assert body["metric"] == "resting_heart_rate"
    assert body["unit"] == "bpm"
    assert body["adverse_direction"] == "higher"
    assert body["end_date"] == live_demo_store.isoformat()
    assert len(body["points"]) == 10

    dates = [point["date"] for point in body["points"]]
    assert dates == sorted(dates)
    for point in body["points"]:
        assert set(point) == {
            "date",
            "value",
            "baseline_median",
            "baseline_spread",
            "deviation",
            "status",
        }
        # Presentation figures, rounded like every other endpoint's.
        assert point["deviation"] == round(point["deviation"], 2)
        assert point["baseline_spread"] == round(point["baseline_spread"], 2)


def test_trends_defaults_to_two_weeks(live_demo_store):
    body = client.get("/wearable/trends/steps").json()
    assert len(body["points"]) == wearable_service.DEFAULT_TREND_DAYS


def test_trends_skips_days_the_watch_was_off(live_demo_store):
    """Skin temperature has deliberate gaps in the demo history."""
    body = client.get("/wearable/trends/skin_temperature", params={"days": 30}).json()
    assert len(body["points"]) < 30


def test_an_unknown_metric_is_404():
    response = client.get("/wearable/trends/blood_pressure")
    assert response.status_code == 404
    assert "Unknown metric" in response.json()["detail"]


def test_the_trend_window_is_bounded():
    assert client.get("/wearable/trends/steps", params={"days": 0}).status_code == 422
    assert client.get("/wearable/trends/steps", params={"days": 1000}).status_code == 422
    assert (
        client.get(
            "/wearable/trends/steps", params={"days": wearable_service.MAX_TREND_DAYS}
        ).status_code
        == 200
    )


# --- the user never comes from the request ------------------------------


def test_reads_are_scoped_to_the_authenticated_user():
    """A patient's heart rate and cough history must not be addressable by id."""
    sample_store.reset_store(
        history={
            "demo-user": {"steps": {ingest_service.today_local(): 8000.0}},
            "someone-else": {"steps": {ingest_service.today_local(): 2000.0}},
        }
    )

    # No user_id parameter exists, and supplying one changes nothing.
    body = client.get("/wearable/metrics", params={"user_id": "someone-else"}).json()
    assert body["user_id"] == "demo-user"
    assert body["metrics"][0]["value"] == 8000.0


def test_writes_land_on_the_authenticated_user():
    app.dependency_overrides[get_current_user_id] = lambda: TEST_USER
    try:
        client.post(
            "/wearable/samples",
            json={"samples": [{"metric": "steps", "date": iso(), "value": 1234}]},
        )
        assert client.get("/wearable/metrics").json()["user_id"] == TEST_USER
    finally:
        app.dependency_overrides.clear()

    # The demo user never saw that sample.
    assert sample_store.get_store().history("demo-user") == {}
