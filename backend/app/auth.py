"""Supabase access-token verification. Demo dashboard identities never enter V1."""

from functools import lru_cache
from urllib.parse import urlparse
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient, PyJWKClientConnectionError

bearer = HTTPBearer(auto_error=False)
ALGORITHMS = ("ES256", "RS256")


@lru_cache(maxsize=8)
def get_jwks_client(issuer: str) -> PyJWKClient:
    return PyJWKClient(issuer + "/.well-known/jwks.json", timeout=5, lifespan=300)


def verify_access_token(token: str, supabase_url: str) -> UUID:
    if not supabase_url:
        raise HTTPException(503, "Supabase authentication is not configured")
    parsed = urlparse(supabase_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise HTTPException(503, "SUPABASE_URL must be an HTTPS project origin")
    issuer = supabase_url.rstrip("/") + "/auth/v1"
    try:
        header = jwt.get_unverified_header(token)
        if header.get("alg") not in ALGORITHMS or not isinstance(header.get("kid"), str):
            raise jwt.InvalidTokenError("Unsupported signing key")
        key = get_jwks_client(issuer).get_signing_key_from_jwt(token)
        claims = jwt.decode(
            token, key.key, algorithms=list(ALGORITHMS),
            audience="authenticated", issuer=issuer,
            options={"require": ["exp", "iat", "sub", "iss", "aud", "role"]},
        )
        if claims["role"] != "authenticated":
            raise jwt.InvalidTokenError("Not a user access token")
        return UUID(claims["sub"])
    except PyJWKClientConnectionError:
        raise HTTPException(503, "Authentication keys are temporarily unavailable") from None
    except (jwt.PyJWTError, ValueError, TypeError, KeyError):
        raise HTTPException(401, "Invalid or expired access token", headers={"WWW-Authenticate": "Bearer"}) from None


def get_current_user_id(
    request: Request, credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
) -> UUID:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(401, "Bearer access token required", headers={"WWW-Authenticate": "Bearer"})
    return verify_access_token(credentials.credentials, request.app.state.settings.supabase_url)
