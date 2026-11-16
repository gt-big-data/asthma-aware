"""Real artifact contract, value-preserving grid, caching and authenticated API.

The pollution values are representative fixtures, not a live NASA download.
"""
import copy
import json
import math
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.config import Settings
from app.dependencies import utc_now
from app.main import create_app
from app.services import live_forecast as live

INIT = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
NOW = INIT + timedelta(hours=3, minutes=20)


def artifact():
    return {"schema_version": "nasa-placeholder-v1", "source": "NASA GEOS-CF v2",
            "run_id": "20261115_09z", "initialized_at": INIT.isoformat(),
            "acquired_at": NOW.isoformat(), "units": dict(live.UNITS),
            "cells": [{"lat": 33.75, "lon": -84.5, "hours": [
                {"timestamp": (INIT + timedelta(hours=i)).isoformat(),
                 "values": {"pm25": 25.0, "no2": 50.0, "o3": 50.0}}
                for i in range(48)]}]}


def test_equal_weight_normalization_and_clipping():
    assert live.score({"pm25": 25, "no2": 50, "o3": 50}) == 50
    assert live.score({"pm25": 0, "no2": 0, "o3": 0}) == 0
    assert live.score({"pm25": 1000, "no2": 1000, "o3": 1000}) == 100
    assert live.score({"pm25": 50, "no2": 0, "o3": 0}) == 33


@pytest.mark.parametrize("bad", [None, True, "25", -1, float("nan"), float("inf")])
def test_missing_or_invalid_concentrations_never_become_zero(bad):
    values = {"pm25": bad, "no2": 0, "o3": 0}
    with pytest.raises(live.ForecastUnavailable):
        live.score(values)
    with pytest.raises(live.ForecastUnavailable):
        live.score({"no2": 0, "o3": 0})


@pytest.mark.parametrize("change", [
    lambda p: p.update(source="sample"),
    lambda p: p.update(run_id="wrong-run"),
    lambda p: p.update(initialized_at="2026-11-15T09:00:00"),
    lambda p: p.update(units={"pm25": "ppb", "no2": "ppb", "o3": "ppb"}),
    lambda p: p.update(cells=[]),
    lambda p: p["cells"].append(copy.deepcopy(p["cells"][0])),
    lambda p: p["cells"][0].update(lat=33.76),
    lambda p: p["cells"][0].update(lat=True),
    lambda p: p["cells"][0].update(hours=[]),
    lambda p: p["cells"][0]["hours"][0].update(timestamp="not-a-time"),
    lambda p: p["cells"][0]["hours"][0].update(timestamp="2026-11-15T09:30:00Z"),
    lambda p: p["cells"][0]["hours"][0].update(timestamp="2026-11-15T08:00:00Z"),
    lambda p: p["cells"][0]["hours"][0]["values"].update(pm25=None),
    lambda p: p["cells"][0]["hours"].pop(5),
    lambda p: p["cells"].append({**copy.deepcopy(p["cells"][0]), "lat": 34, "hours": p["cells"][0]["hours"][1:]}),
])
def test_corrupt_or_incompatible_artifacts_are_rejected(change):
    payload = artifact()
    change(payload)
    with pytest.raises(live.ForecastUnavailable):
        live.validate(payload)


def test_subpixels_tile_native_footprint_and_preserve_values():
    payload = artifact()
    before = copy.deepcopy(payload)
    first = live.at_location(payload, 33.70, -84.48, NOW)
    second = live.at_location(payload, 33.80, -84.40, NOW)
    assert first["location"]["pixel_id"] != second["location"]["pixel_id"]
    assert first["hourly"] == second["hourly"]
    assert first["hourly"][0]["values"] == payload["cells"][0]["hours"][0]["values"]
    assert first["hourly"][0]["score"] == 50
    assert first["placeholder"] is True
    assert payload == before
    grid = first["grid"]
    assert grid["rows"] * grid["cols"] == 672
    assert grid["south"] + grid["rows"] * grid["lat_step"] == pytest.approx(grid["north"])
    assert grid["west"] + grid["cols"] * grid["lon_step"] == pytest.approx(grid["east"])
    assert grid["lat_step"] * 111.32 <= 1
    assert grid["lon_step"] * 111.32 * math.cos(math.radians(33.75)) <= 1


def test_current_hour_and_exclusive_expiry():
    data = live.at_location(artifact(), 33.75, -84.39, NOW)
    assert live.moment(data["hourly"][0]["timestamp"]) == INIT + timedelta(hours=3)
    with pytest.raises(live.ForecastUnavailable):
        live.at_location(artifact(), 33.75, -84.39, INIT + timedelta(hours=48))
    with pytest.raises(live.ForecastUnavailable):
        live.at_location(artifact(), 33.75, -84.39, INIT - timedelta(seconds=1))
    payload = artifact()
    payload["cells"][0]["hours"] = payload["cells"][0]["hours"][4:]
    with pytest.raises(live.ForecastUnavailable):
        live.at_location(payload, 33.75, -84.39, NOW)


@pytest.mark.parametrize("point", [(34, -84.5), (40, -84.5), (33.75, float("nan")), (33.625, -84.5)])
def test_unsupported_locations_and_native_boundary_snap(point):
    with pytest.raises(live.OutsideCoverage):
        live.at_location(artifact(), *point, NOW)


def test_invalid_publication_keeps_previous_complete_file(tmp_path):
    path = tmp_path / "forecast.json"
    live.publish_file(artifact(), path)
    previous = path.read_bytes()
    broken = artifact()
    broken["cells"][0]["hours"][0]["values"]["pm25"] = float("nan")
    with pytest.raises(live.ForecastUnavailable):
        live.publish_file(broken, path)
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("tmp*"))


def test_identical_local_publication_is_not_rewritten(tmp_path):
    path = tmp_path / "forecast.json"
    assert live.publish_file(artifact(), path) is True
    original = path.stat().st_mtime_ns
    assert live.publish_file(artifact(), path) is False
    assert path.stat().st_mtime_ns == original


def test_file_cache_reloads_atomic_replacement_and_fails_closed(tmp_path):
    path = tmp_path / "forecast.json"
    live.publish_file(artifact(), path)
    provider = live.Provider(Settings(live_forecast_store="file", live_forecast_path=str(path)))
    first = provider.get()
    assert provider.get() is first
    changed = artifact()
    changed["cells"][0]["hours"][0]["values"]["pm25"] = 40
    live.publish_file(changed, path)
    assert provider.get() is not first
    path.write_text("corrupt")
    with pytest.raises(live.ForecastUnavailable):
        provider.get()


def test_database_cache_single_load_ttl_and_failure(monkeypatch):
    from db.repositories import live_forecast as repository
    calls = []
    def read():
        calls.append(1)
        return artifact()
    monkeypatch.setattr(repository, "read_latest", read)
    clock = [0]
    provider = live.Provider(Settings(live_forecast_store="database"), clock=lambda: clock[0])
    first = provider.get()
    assert provider.get() is first and len(calls) == 1
    clock[0] = 30
    assert provider.get() is not first and len(calls) == 2
    clock[0] = 60
    monkeypatch.setattr(repository, "read_latest", lambda: (_ for _ in ()).throw(OSError("offline")))
    with pytest.raises(live.ForecastUnavailable):
        provider.get()


def test_authenticated_api_full_contract_and_errors(tmp_path):
    path = tmp_path / "forecast.json"
    live.publish_file(artifact(), path)
    app = create_app(Settings(live_forecast_store="file", live_forecast_path=str(path)))
    app.dependency_overrides[utc_now] = lambda: NOW
    with TestClient(app) as client:
        assert client.get("/v1/forecast?lat=33.75&lon=-84.39").status_code == 401
        app.dependency_overrides[get_current_user_id] = uuid4
        response = client.get("/v1/forecast?lat=33.75&lon=-84.39")
        assert response.status_code == 200, response.text
        assert response.json()["source"] == "NASA GEOS-CF v2"
        assert response.json()["hourly"][0]["score"] == 50
        assert response.headers["Cache-Control"] == "private, no-store"
        assert "placeholder" in response.headers["X-AsthmaAware-Data-Source"]
        assert client.get("/v1/forecast?lat=34&lon=-84.39").status_code == 404
        assert client.get("/v1/forecast?lat=91&lon=-84.39").status_code == 422
        assert client.get("/v1/forecast?lat=nan&lon=-84.39").status_code == 422
        path.unlink()
        assert client.get("/v1/forecast?lat=33.75&lon=-84.39").status_code == 503
        assert client.get("/health").status_code == 200


def test_disabled_database_and_file_errors_do_not_return_mock_data():
    for settings in (Settings(), Settings(live_forecast_store="file", live_forecast_path="missing-file.json")):
        app = create_app(settings)
        app.dependency_overrides[get_current_user_id] = uuid4
        with TestClient(app) as client:
            assert client.get("/v1/forecast?lat=33.75&lon=-84.39").status_code == 503
