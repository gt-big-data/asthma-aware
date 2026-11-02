"""Real JWT signature/claim verification with a local JWKS fixture, no network."""

import json
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import auth
from app.config import Settings
from app.main import create_app

PROJECT = "https://example.supabase.co"


@pytest.fixture
def signing(monkeypatch):
    key = ec.generate_private_key(ec.SECP256R1())
    public = json.loads(jwt.algorithms.ECAlgorithm.to_jwk(key.public_key()))
    public.update(kid="test-key", alg="ES256", use="sig")
    auth.get_jwks_client.cache_clear()
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: {"keys": [public]})

    def token(**changes):
        now = datetime.now(timezone.utc)
        claims = dict(sub=str(uuid4()), iss=PROJECT + "/auth/v1", aud="authenticated",
                      role="authenticated", iat=now, exp=now + timedelta(minutes=5))
        claims.update(changes)
        return jwt.encode(claims, key, algorithm="ES256", headers={"kid": "test-key"}), claims

    yield token
    auth.get_jwks_client.cache_clear()


def test_valid_signed_user_access_token(signing):
    token, claims = signing()
    assert str(auth.verify_access_token(token, PROJECT)) == claims["sub"]


@pytest.mark.parametrize("changes", [
    {"aud": "wrong"}, {"iss": "https://other.supabase.co/auth/v1"},
    {"role": "service_role"}, {"sub": "not-a-uuid"},
    {"exp": datetime.now(timezone.utc) - timedelta(minutes=1)},
    {"iat": datetime.now(timezone.utc) + timedelta(hours=1)},
])
def test_invalid_claims_are_rejected(signing, changes):
    token, _ = signing(**changes)
    with pytest.raises(HTTPException) as failure:
        auth.verify_access_token(token, PROJECT)
    assert failure.value.status_code == 401


def test_modified_signature_is_rejected(signing):
    token, _ = signing()
    header, payload, signature = token.split(".")
    signature = ("A" if signature[0] != "A" else "B") + signature[1:]
    with pytest.raises(HTTPException) as failure:
        auth.verify_access_token(".".join((header, payload, signature)), PROJECT)
    assert failure.value.status_code == 401


def test_symmetric_and_unsigned_tokens_are_rejected_before_key_fetch(monkeypatch):
    monkeypatch.setattr(auth, "get_jwks_client", lambda issuer: pytest.fail("must not fetch keys"))
    for algorithm, key in [("HS256", "local-test-secret" * 3), ("none", None)]:
        token = jwt.encode({"sub": str(uuid4())}, key, algorithm=algorithm, headers={"kid": "test-key"})
        with pytest.raises(HTTPException) as failure:
            auth.verify_access_token(token, PROJECT)
        assert failure.value.status_code == 401


def test_key_service_failure_is_unavailable_not_an_authenticated_user(monkeypatch):
    def unavailable(*args):
        raise jwt.PyJWKClientConnectionError("offline")
    key = ec.generate_private_key(ec.SECP256R1())
    token = jwt.encode({}, key, algorithm="ES256", headers={"kid": "key"})
    monkeypatch.setattr(auth, "get_jwks_client", unavailable)
    with pytest.raises(HTTPException) as failure:
        auth.verify_access_token(token, PROJECT)
    assert failure.value.status_code == 503


def test_v1_never_accepts_demo_identity_or_unconfigured_auth():
    with TestClient(create_app(Settings(dashboard_demo_enabled=True))) as client:
        assert client.get("/v1/users/me").status_code == 401
        response = client.get("/v1/users/me", headers={"Authorization": "Bearer any-token"})
        assert response.status_code == 503
