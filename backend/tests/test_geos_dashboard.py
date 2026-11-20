from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.config import Settings
from app.dependencies import utc_now
from app.main import create_app
from app.services.geos_dashboard import PublishedForecast, grid_projection, source_index
from pyproj.enums import TransformDirection


def test_dashboard_uses_remaining_geos_hours_and_preserves_missing_cells(tmp_path):
    issued = datetime(2026, 11, 10, 9, tzinfo=timezone.utc)
    now = issued + timedelta(hours=32)
    times = [issued + timedelta(minutes=30, hours=i) for i in range(120)]
    run = PublishedForecast(
        source_run="20261110_09z",
        issued_at=issued,
        times=times,
        latitudes=[33.75],
        longitudes=[-84.5, -84.25],
        pm25=[[40.0 if i < 32 else 8.0 if i < 60 else 16.0, None] for i in range(120)],
    )
    artifact = tmp_path / "current.json"
    artifact.write_text(run.model_dump_json(), encoding="utf-8")
    app = create_app(Settings(dashboard_forecast_path=str(artifact)))
    app.dependency_overrides[utc_now] = lambda: now

    with TestClient(app) as client:
        path = "/v1/dashboard/risk"
        response = client.get(path, params={"lat": 33.75, "lon": -84.5})
        assert response.status_code == 200
        result = response.json()
        assert result["stale"] and result["placeholder"]
        assert result["grid_size_m"] == 1000
        assert sum(day["available_hours"] for day in result["days"]) == 84
        assert [day["score"] for day in result["days"]] == [20, 40, 40, 40]
        assert result["days"][0]["general_score"] == 20
        assert len(result["hourly"]) == 88
        assert result["hourly"][0]["score"] == 20
        assert result["hourly"][0]["drivers"] == []  # Legacy PM-only publications have no four-pollutant explanation.
        missing = client.get(path, params={"lat": 33.75, "lon": -84.25}).json()
        assert len(missing["days"]) == 4
        assert all(day["score"] is None and day["available_hours"] == 0 for day in missing["days"])
        assert len(missing["hourly"]) == 88
        assert all(hour["score"] is None for hour in missing["hourly"])
        assert client.get(path, params={"lat": 40, "lon": -74}).status_code == 404

        now = issued + timedelta(hours=120)
        assert client.get(path, params={"lat": 33.75, "lon": -84.5}).status_code == 503

    # Two distinct 1 km cells inside the same NASA cell inherit the same value.
    projection = grid_projection()
    x, y = projection.transform(-84.5, 33.75)
    for offset in (0, 1000):
        lon, lat = projection.transform(x + offset, y, direction=TransformDirection.INVERSE)
        assert source_index(run, lat, lon) == 0
