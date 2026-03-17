# Tests the map endpoints (currently outdated)

from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_get_current_map():
    response = client.get("/map/current")
    assert response.status_code == 200
    data = response.json()
    assert "city" in data
    assert "timestamp" in data
    assert "cells" in data


def test_get_forecast_map():
    response = client.get("/map/forecast")
    assert response.status_code == 200
    data = response.json()
    assert "city" in data
    assert "generated_at" in data
    assert "forecast" in data