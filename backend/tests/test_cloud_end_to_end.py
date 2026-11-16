"""Offline full contract flow: NASA fixture NetCDF → stores → signed delivery API.

No model is executed: the ML output is an explicitly test-only supplied file.
Real JWT crypto is exercised against local JWKS; no cloud/database API is called.
"""
import base64
import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient

from app import auth
from app.cloud import create_app
from app.config import Settings
from app.dependencies import utc_now
from forecast_job.storage import MemoryStore
from forecast_pipeline.catalog import ForecastCatalog, reference
from forecast_pipeline.contracts import digest
from forecast_pipeline.ml_handoff import publish_ml_outputs
from forecast_pipeline.pipeline import publish_raw
from tests.test_real_forecast_pipeline import raw, NOW


def test_real_contract_chain_with_offline_nasa_and_supplied_test_artifact(raw, tmp_path, monkeypatch):
    raw_store, staging, delivery = MemoryStore(), MemoryStore(), MemoryStore()
    raw_manifest = publish_raw(raw_store, raw[0], now=NOW)["manifest"]
    project = "https://test.supabase.co"
    signing_key = ec.generate_private_key(ec.SECP256R1())
    jwk = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(signing_key.public_key()))
    jwk.update(kid="e2e-local-key", alg="ES256", use="sig")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [jwk]})
    auth.get_jwks_client.cache_clear()
    real_now = datetime.now(timezone.utc)
    token = jwt.encode({"sub": str(uuid4()), "iss": project + "/auth/v1", "aud": "authenticated",
                        "role": "authenticated", "iat": real_now, "exp": real_now + timedelta(minutes=5)},
                       signing_key, algorithm="ES256", headers={"kid": "e2e-local-key"})
    app = create_app(Settings(supabase_url=project, forecast_delivery_base_url="https://cdn.example.com",
                             forecast_cdn_key_name="test-only", forecast_cdn_signing_key=base64.urlsafe_b64encode(b"0123456789abcdef").decode()))
    app.state.catalogs = {"raw": ForecastCatalog(raw_store), "processed": ForecastCatalog(delivery)}
    app.dependency_overrides[utc_now] = lambda: NOW
    headers = {"Authorization": "Bearer " + token}
    try:
        with TestClient(app) as client:
            pending = client.get("/v1/forecasts/availability", headers=headers)
            assert pending.status_code == 200, pending.text
            assert pending.json()["ml_status"] == "pending"
            assert client.get("/v1/forecasts/metadata", headers=headers).status_code == 503
            data = b'{"test_artifact_only":true}'
            path = tmp_path / "ml-fixture.json"
            path.write_bytes(data)
            raw_name = f"raw/{raw_manifest['run_id']}/{raw_manifest['artifact_version']}/manifest.json"
            name = f"processed/{raw_manifest['run_id']}/fixture-v1/tile.json"
            supplied = {**{key: raw_manifest[key] for key in ("source", "run_id", "initialized_at", "valid_from", "valid_until", "bounds")},
                        "schema_version": "asthma-ml-output-v1", "product": "processed", "artifact_version": "fixture-v1",
                        "variables": {"test_artifact_only": "dimensionless"}, "audience": "environmental-public", "contains_user_data": False,
                        "model": {"name": "fixture-not-a-model", "version": "fixture-v1", "input_contract": "nasa-geos-cf-raw-v1"},
                        "input_raw_manifest": reference(raw_name, raw_store.read(raw_name)),
                        "files": [{"name": name, "relative_path": path.name, "sha256": digest(data),
                                   "size_bytes": len(data), "content_type": "application/json"}]}
            assert publish_ml_outputs(supplied, tmp_path, raw_store, staging, delivery)["promoted"] is True
            published = client.get("/v1/forecasts/availability", headers=headers)
            assert published.json()["ml_status"] == "published"
            metadata = client.get("/v1/forecasts/metadata", headers=headers)
            assert metadata.json()["input_raw_manifest"]["name"] == raw_name
            signed = client.get("/v1/forecasts/delivery", params={"name": name}, headers=headers)
            assert signed.status_code == 200, signed.text
            assert "Signature=" in signed.json()["url"]
            assert ForecastCatalog(delivery).delivery(name, NOW) == (data, "application/json")
            assert client.get("/v1/forecast", headers=headers).status_code == 503
    finally:
        auth.get_jwks_client.cache_clear()
