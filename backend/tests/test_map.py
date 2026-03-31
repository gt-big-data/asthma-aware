from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_get_current_map():
    response = client.get("/map/current")
    assert response.status_code == 200

    data = response.json()
    assert data["city"] == "atlanta"
    assert "timestamp" in data
    assert "cells" in data
    assert isinstance(data["cells"], list)
    assert len(data["cells"]) > 0

    first = data["cells"][0]
    assert "row" in first
    assert "col" in first
    assert "lat" in first
    assert "lon" in first
    assert "so2" in first
    assert "ndvi" in first
    assert "no2" in first


def test_get_forecast_24h():
    response = client.get("/map/forecast/24h")
    assert response.status_code == 200

    data = response.json()
    assert data["city"] == "atlanta"
    assert data["forecast_horizon"] == "24h"
    assert "timestamp" in data
    assert "cells" in data
    assert isinstance(data["cells"], list)
    assert len(data["cells"]) > 0


def test_get_forecast_48h():
    response = client.get("/map/forecast/48h")
    assert response.status_code == 200

    data = response.json()
    assert data["city"] == "atlanta"
    assert data["forecast_horizon"] == "48h"
    assert "timestamp" in data
    assert "cells" in data
    assert isinstance(data["cells"], list)
    assert len(data["cells"]) > 0


def test_get_forecast_72h():
    response = client.get("/map/forecast/72h")
    assert response.status_code == 200

    data = response.json()
    assert data["city"] == "atlanta"
    assert data["forecast_horizon"] == "72h"
    assert "timestamp" in data
    assert "cells" in data
    assert isinstance(data["cells"], list)
    assert len(data["cells"]) > 0