"""Deterministic sample payload; no network, database, or model imports."""

from datetime import datetime, timedelta, timezone


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Timestamp must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def generate_sample_forecast(run_id: str, generated_at: datetime, profile: str = "future") -> dict:
    """Same inputs produce the same payload; values are synthetic, not health risk."""
    timestamp = iso_utc(generated_at)
    start = generated_at.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start += timedelta(hours=1)
    hours = 3
    if profile == "available":
        # Explicit infrastructure fixture: covers now and the next daily refresh.
        start -= timedelta(hours=1)
        hours = 48
    elif profile == "expired":
        start -= timedelta(hours=5)
        generated_at = start
        timestamp = iso_utc(generated_at)
    elif profile != "future":
        raise ValueError("Unknown fixture profile")
    return {
        "schema_version": "1.0",
        "run_id": run_id,
        "source": "sample",
        "generated_at": timestamp,
        "valid_from": iso_utc(start),
        "valid_until": iso_utc(start + timedelta(hours=hours)),
        "bounds": {"min_lat": 33.7, "max_lat": 33.8, "min_lon": -84.4, "max_lon": -84.3},
        "variables": {"sample_index": {"unit": "dimensionless", "description": "Synthetic infrastructure test value"}},
        "forecasts": [
            {
                "valid_at": iso_utc(start + timedelta(hours=hour)),
                "cells": [
                    {"lat": 33.725, "lon": -84.375, "sample_index": 10 + hour},
                    {"lat": 33.775, "lon": -84.325, "sample_index": 20 + hour},
                ],
            }
            for hour in range(hours)
        ],
    }
