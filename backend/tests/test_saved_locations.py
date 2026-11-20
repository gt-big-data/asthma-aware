from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError

from app.config import Settings
from app.main import create_app
from app.services.location_catalog import catalog, location_coverage, search_cities
from db.models.application import SavedLocation
from tests.test_application_api import api, owned_session


def test_public_city_catalog_is_georgia_only_and_marks_actual_coverage():
    assert len(catalog()) > 600
    assert all(city_id.startswith("13") and len(city_id) == 7 for city_id in catalog())
    assert search_cities("atlanta")[0]["name"] == "Atlanta"
    assert search_cities("SAVANNAH")[0]["forecast_supported"] is False
    assert location_coverage(33.75, -84.39) == {"in_georgia": True, "forecast_supported": True}
    assert location_coverage(32.08, -81.1) == {"in_georgia": True, "forecast_supported": False}
    assert location_coverage(32.61, -85.48) == {"in_georgia": False, "forecast_supported": False}  # Auburn, AL, inside model rectangle.
    with TestClient(create_app(Settings())) as client:
        assert client.get("/v1/locations/cities?q=Atlanta").json()["items"][0]["id"] == "1304000"
        assert client.get("/v1/locations/cities/3651000").status_code == 404
        assert client.get("/v1/locations/cities?q=" + "a" * 81).status_code == 422
        assert client.get("/v1/locations/coverage?lat=nan&lon=-84").status_code == 422
        assert client.get("/v1/locations").status_code == 401
        assert client.put("/v1/locations/1304000", json={"label": "Home"}).status_code == 401
        assert client.delete("/v1/locations/1304000").status_code == 401


def test_saved_cities_roundtrip_rename_retry_and_delete(api):
    client, _ = api
    path = "/v1/locations/1304000"
    first = client.put(path, json={"label": " Home "})
    assert first.status_code == 200, first.text
    assert first.json()["label"] == "Home"
    assert first.json()["city_name"] == "Atlanta"
    assert first.json()["latitude"] == catalog()["1304000"]["latitude"]
    assert client.put(path, json={"label": "Home"}).status_code == 200
    assert len(client.get("/v1/locations").json()["items"]) == 1
    assert client.put(path, json={"label": "Work"}).json()["label"] == "Work"
    assert client.delete(path).status_code == 204
    assert client.delete(path).status_code == 204
    assert client.get("/v1/locations").json()["items"] == []


def test_unknown_states_and_client_coordinates_are_rejected(api):
    client, _ = api
    assert client.put("/v1/locations/3651000", json={"label": "New York"}).status_code == 422
    assert client.put("/v1/locations/1399999", json={}).status_code == 422
    for body in ({"label": "   "}, {"label": "a" * 81}, {"latitude": 40, "longitude": -74}, {"user_id": str(uuid4())}):
        assert client.put("/v1/locations/1304000", json=body).status_code == 422
    assert client.put("/v1/locations/1369000", json={}).json()["forecast_supported"] is False


def test_owner_isolation_enforced_by_api_and_forced_rls(api, engine):
    client, state = api
    owner = state["user"]
    assert client.put("/v1/locations/1304000", json={"label": "Home"}).status_code == 200
    other = uuid4()
    state["user"] = other
    assert client.get("/v1/locations").json()["items"] == []
    assert client.delete("/v1/locations/1304000").status_code == 204
    assert client.put("/v1/locations/1304000", json={"label": "Other"}).status_code == 200
    state["user"] = owner
    assert client.get("/v1/locations").json()["items"][0]["label"] == "Home"
    with owned_session(engine, owner) as session:
        assert [row.user_id for row in session.scalars(select(SavedLocation))] == [owner]
        flags = session.execute(text("SELECT relrowsecurity,relforcerowsecurity FROM pg_class WHERE oid='app.saved_locations'::regclass")).one()
        assert flags == (True, True)
        with pytest.raises(DBAPIError):
            session.execute(insert(SavedLocation).values(user_id=other, city_id="1349756", label="Forged", city_name="Marietta", latitude=33.95, longitude=-84.54))


def test_limit_allows_renaming_but_rejects_eleventh_city(api):
    client, _ = api
    cities = list(catalog())[:11]
    for city_id in cities[:10]:
        assert client.put(f"/v1/locations/{city_id}", json={}).status_code == 200
    assert client.put(f"/v1/locations/{cities[10]}", json={}).status_code == 422
    assert client.put(f"/v1/locations/{cities[0]}", json={"label": "Renamed"}).status_code == 200
    assert len(client.get("/v1/locations").json()["items"]) == 10
