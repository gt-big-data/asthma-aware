from fastapi.testclient import TestClient

from app.api.dashboard import get_current_user_id
from app.main import app

client = TestClient(app)


def test_spec_example_returns_full_contract():
    response = client.get("/dashboard", params={"lat": 33.7490, "lon": -84.3880})
    assert response.status_code == 200

    data = response.json()
    assert set(data) == {"location", "forecast", "risk", "contributors", "hourly_risk"}
    assert data["location"] == {"lat": 33.749, "lon": -84.388, "grid_cell_id": "ga_02424"}
    assert set(data["forecast"]) == {"forecast_run_id", "valid_at"}
    assert 0 <= data["risk"]["score"] <= 100
    assert data["risk"]["level"] in {"low", "moderate", "high"}

    assert 1 <= len(data["contributors"]) <= 3
    for contributor in data["contributors"]:
        assert set(contributor) == {"feature", "contribution"}

    assert len(data["hourly_risk"]) in {23, 24, 25}
    for hour in data["hourly_risk"]:
        assert set(hour) == {"timestamp", "risk"}
        assert hour["timestamp"].endswith("Z")
    assert {"timestamp": data["forecast"]["valid_at"], "risk": data["risk"]["score"]} in data["hourly_risk"]


def test_invalid_coordinates_are_rejected():
    assert client.get("/dashboard", params={"lat": 91, "lon": -84.388}).status_code == 422
    assert client.get("/dashboard", params={"lat": 33.749, "lon": -181}).status_code == 422
    assert client.get("/dashboard", params={"lat": 33.749}).status_code == 422
    assert client.get("/dashboard", params={"lat": "abc", "lon": -84.388}).status_code == 422


def test_location_outside_georgia_is_404():
    response = client.get("/dashboard", params={"lat": 40.7128, "lon": -74.0060})
    assert response.status_code == 404
    assert "outside the supported area" in response.json()["detail"]


def test_user_comes_from_dependency_not_query():
    app.dependency_overrides[get_current_user_id] = lambda: "unknown-user"
    try:
        response = client.get("/dashboard", params={"lat": 33.7490, "lon": -84.3880})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["contributors"] == []
