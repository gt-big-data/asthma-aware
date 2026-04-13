from fastapi.testclient import TestClient
from app.main import app
from app.services import socioeconomic_service

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


def test_get_socioeconomic_data_uses_sample_fallback(monkeypatch):
    def mock_fetch():
        raise socioeconomic_service.HTTPException(status_code=502, detail="boom")

    socioeconomic_service.get_socioeconomic_data.cache_clear()
    monkeypatch.setattr(socioeconomic_service, "_fetch_census_rows", mock_fetch)

    response = client.get("/map/socioeconomic")
    assert response.status_code == 200

    data = response.json()
    assert data["city"] == "atlanta"
    assert data["dataset_year"] == 2023
    assert data["source"] == "sample_data_fallback"
    assert isinstance(data["zipcodes"], list)
    assert len(data["zipcodes"]) == len(socioeconomic_service.SUPPORTED_CITY_ZCTAS["atlanta"])

    first = data["zipcodes"][0]
    assert "zipcode" in first
    assert "median_age" in first
    assert "children_under_18_rate" in first
    assert "seniors_65_plus_rate" in first
    assert "population_density" in first
    assert "median_household_income" in first
    assert "median_gross_rent" in first
    assert "median_home_value" in first
    assert "poverty_rate" in first
    assert "bachelor_degree_or_higher_rate" in first
    assert "no_vehicle_households_rate" in first
    assert "severe_rent_burden_rate" in first
    assert "overcrowded_housing_rate" in first
    assert "unemployment_rate" in first

    socioeconomic_service.get_socioeconomic_data.cache_clear()
