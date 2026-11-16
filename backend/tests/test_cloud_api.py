"""Cloud deployment API tested against real local catalog code, fixture objects only."""
import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.auth import get_current_user_id
from app.config import Settings
from app.dependencies import utc_now
from app.services.forecast_delivery import DeliveryUnavailable, sign_delivery_url
from forecast_job.storage import MemoryStore
from forecast_pipeline.catalog import ForecastCatalog, create_immutable, publish_manifest, reference

INIT = datetime(2026, 11, 15, 9, tzinfo=timezone.utc)
NOW = INIT + timedelta(hours=2)


def fixture_manifest(store, product):
    name = f"{product}/20261115_09z/test-v1/fixture.json"
    ref = reference(name, create_immutable(store, name, b'{"test_fixture":true}'))
    manifest = {"schema_version": "nasa-geos-cf-raw-v1" if product == "raw" else "asthma-ml-output-v1",
                "product": product, "source": "NASA GEOS-CF v2", "run_id": "20261115_09z",
                "initialized_at": INIT.isoformat(), "valid_from": INIT.isoformat(),
                "valid_until": (INIT + timedelta(hours=24)).isoformat(), "artifact_version": "test-v1",
                "bounds": {"min_lat": 33.5, "max_lat": 34, "min_lon": -84.75, "max_lon": -84.25},
                "variables": {"test_fixture": "dimensionless"}, "files": [ref]}
    if product == "processed":
        raw = fixture_manifest(store, "raw")
        rawname = "raw/20261115_09z/test-v1/manifest.json"
        manifest.update(audience="environmental-public", contains_user_data=False,
                        model={"name": "test-fixture-only", "version": "test-v1", "input_contract": "nasa-geos-cf-raw-v1"},
                        input_raw_manifest=reference(rawname, store.read(rawname)))
    publish_manifest(store, manifest)
    return manifest


@pytest.fixture
def cloud_api():
    from app.cloud import create_app
    settings = Settings(forecast_delivery_base_url="https://cdn.example.com", forecast_cdn_key_name="test-key",
                        forecast_cdn_signing_key=base64.urlsafe_b64encode(b"0123456789abcdef").decode())
    app = create_app(settings)
    store = MemoryStore()
    app.state.catalogs = {"raw": ForecastCatalog(store), "processed": ForecastCatalog(store)}
    app.dependency_overrides[utc_now] = lambda: NOW
    with TestClient(app) as client:
        yield app, client, store


def test_production_api_has_no_sample_or_model_routes(cloud_api):
    app, client, store = cloud_api
    paths = set(app.openapi()["paths"])
    assert "/dashboard" not in paths and "/internal/forecast" not in paths
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/v1/forecasts/availability").status_code == 401
    app.dependency_overrides[get_current_user_id] = uuid4
    result = client.get("/v1/forecast?lat=33.75&lon=-84.39")
    assert result.status_code == 503
    assert "placeholder predictions" in result.json()["detail"]


def test_readiness_and_user_database_fail_closed_without_tls(cloud_api, monkeypatch):
    app, client, store = cloud_api
    monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/test")
    assert client.get("/ready").status_code == 503
    app.dependency_overrides[get_current_user_id] = uuid4
    assert client.get("/v1/users/me").status_code == 503
    monkeypatch.setenv("DATABASE_URL", "postgresql://test@localhost/test?sslmode=require")
    app.state.settings = Settings(supabase_url="https://test.supabase.co")
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json()["external_connectivity"] == "not_probed"


def test_absent_ml_outputs_are_pending_not_generated(cloud_api):
    app, client, store = cloud_api
    app.dependency_overrides[get_current_user_id] = uuid4
    fixture_manifest(store, "raw")
    result = client.get("/v1/forecasts/availability")
    assert result.status_code == 200, result.text
    assert result.json()["raw"]["status"] == "available"
    assert result.json()["processed"]["status"] == "pending"
    assert result.json()["ml_status"] == "pending"
    assert client.get("/v1/forecasts/metadata").status_code == 503
    assert client.get("/v1/forecasts/metadata?product=raw").status_code == 200
    assert client.get("/v1/forecasts/metadata?product=other").status_code == 422


def test_delivery_is_exact_manifest_member_and_short_lived(cloud_api):
    app, client, store = cloud_api
    app.dependency_overrides[get_current_user_id] = uuid4
    manifest = fixture_manifest(store, "processed")
    response = client.get("/v1/forecasts/delivery", params={"name": manifest["files"][0]["name"]})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["url"].startswith("https://cdn.example.com/processed/")
    assert f"Expires={int((NOW + timedelta(minutes=5)).timestamp())}" in result["url"]
    assert "Signature=" in result["url"]
    assert response.headers["Cache-Control"] == "private, no-store"
    assert client.get("/v1/forecasts/delivery", params={"name": "raw/20261115_09z/test-v1/fixture.json"}).status_code == 404
    assert client.get("/v1/forecasts/delivery", params={"name": "processed/other/secret.json"}).status_code == 404


def test_expired_product_and_corrupt_pointer_fail_closed(cloud_api):
    app, client, store = cloud_api
    app.dependency_overrides[get_current_user_id] = uuid4
    fixture_manifest(store, "processed")
    app.dependency_overrides[utc_now] = lambda: INIT + timedelta(hours=24)
    assert client.get("/v1/forecasts/metadata").status_code == 503
    pointer = store.read("processed/current.json")
    store.write("processed/current.json", b"corrupt", pointer.generation)
    assert client.get("/v1/forecasts/availability").status_code == 503


def test_signing_algorithm_and_configuration_no_secret_repr():
    key = base64.urlsafe_b64encode(b"0123456789abcdef").decode()
    settings = Settings(forecast_delivery_base_url="https://cdn.example.com", forecast_cdn_key_name="test-key", forecast_cdn_signing_key=key,
                        places_api_key="do-not-log-test-key")
    name = "processed/20261115_09z/v1/tile.png"
    result = sign_delivery_url(settings, name, now=NOW, valid_until=NOW + timedelta(seconds=20))
    unsigned, signature = result["url"].split("&Signature=")
    expected = base64.urlsafe_b64encode(hmac.new(b"0123456789abcdef", unsigned.encode(), hashlib.sha1).digest()).decode()
    assert hmac.compare_digest(signature, expected)
    assert f"Expires={int((NOW + timedelta(seconds=20)).timestamp())}" in unsigned
    assert key not in repr(settings) and "do-not-log-test-key" not in repr(settings)
    for bad in ("raw/secret.nc", "processed/../secret", "processed/x%2Fy", "https://evil.example/processed/a"):
        with pytest.raises(DeliveryUnavailable):
            sign_delivery_url(settings, bad, now=NOW, valid_until=NOW + timedelta(hours=1))
