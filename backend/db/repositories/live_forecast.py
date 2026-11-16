"""Bounded TLS Postgres publication, separate read and write identities."""
import json
import os
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.services.live_forecast import ForecastUnavailable, moment, validate, MAX_BYTES
from db.config import get_database_url, _normalize_url


@contextmanager
def connection(writer=False):
    url = os.getenv("FORECAST_PUBLISH_DATABASE_URL", "") if writer else get_database_url()
    if not url:
        raise ForecastUnavailable("Forecast database credentials are not configured")
    parsed = make_url(_normalize_url(url))
    if parsed.get_backend_name() != "postgresql" or parsed.query.get("sslmode") not in ("require", "verify-ca", "verify-full"):
        raise ForecastUnavailable("Forecast database requires TLS")
    engine = create_engine(parsed, poolclass=NullPool,
                           connect_args={"connect_timeout": 10, "options": "-c statement_timeout=10000"})
    try:
        with engine.begin() as conn:
            role = "asthmaaware_forecast_writer" if writer else "asthmaaware_api"
            conn.execute(text(f"SET LOCAL ROLE {role}"))
            yield conn
    finally:
        engine.dispose()


def read_latest():
    with connection() as conn:
        payload = conn.scalar(text("SELECT payload FROM forecast.current WHERE singleton AND octet_length(payload::text) <= :limit"), {"limit": MAX_BYTES})
        if payload is None:
            raise ForecastUnavailable("No forecast has been published")
        return payload


def publish(payload):
    validate(payload)
    with connection(writer=True) as conn:
        # Transactional replacement; overlapping older runs cannot regress current.
        changed = conn.execute(text("""
          INSERT INTO forecast.current (singleton, initialized_at, payload)
          VALUES (true, :initialized, CAST(:payload AS jsonb))
          ON CONFLICT (singleton) DO UPDATE
          SET initialized_at=EXCLUDED.initialized_at, payload=EXCLUDED.payload, published_at=now()
          WHERE EXCLUDED.initialized_at > forecast.current.initialized_at
          RETURNING singleton
        """), {"initialized": moment(payload["initialized_at"]),
               "payload": json.dumps(payload, allow_nan=False)}).scalar()
        if changed is None:
            existing = conn.scalar(text("SELECT payload FROM forecast.current WHERE singleton"))
            if existing and moment(existing["initialized_at"]) == moment(payload["initialized_at"]):
                # Acquisition timestamps can differ on an otherwise identical
                # re-download. Do not rewrite the row for this metadata alone.
                fields = ("schema_version", "source", "run_id", "initialized_at", "units", "cells")
                if any(existing[k] != payload[k] for k in fields):
                    raise ForecastUnavailable("This NASA run already has a different published snapshot")
        return changed is not None
