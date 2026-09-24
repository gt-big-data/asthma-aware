"""Normalize AirNow observations into the AsthmaAware air-quality record schema."""

from __future__ import annotations

from datetime import datetime, timezone

from .airnow_client import BoundingBox

MISSING_VALUE = -999

POLLUTANTS = {
    "PM2.5": "pm25",
    "PM10": "pm10",
    "OZONE": "o3",
    "NO2": "no2",
    "SO2": "so2",
    "CO": "co",
}

UNITS = {
    "UG/M3": "ug/m3",
    "PPB": "ppb",
    "PPM": "ppm",
}


def normalize_observations(
    payload: list[dict],
    bbox: BoundingBox,
    retrieved_at: datetime,
) -> tuple[list[dict], dict]:
    """Return deduplicated records (one per station + pollutant + observation time) and counts."""
    retrieved_at_text = _to_iso_z(retrieved_at)
    records: dict[tuple[str, str, str], dict] = {}
    stats = {
        "observations_returned": len(payload),
        "outside_bounding_box": 0,
        "unmapped_pollutant": 0,
        "missing_measurement": 0,
        "duplicates_collapsed": 0,
    }

    for observation in payload:
        latitude = _to_float(observation.get("Latitude"))
        longitude = _to_float(observation.get("Longitude"))
        if latitude is None or longitude is None or not bbox.contains(latitude, longitude):
            stats["outside_bounding_box"] += 1
            continue

        pollutant = POLLUTANTS.get(str(observation.get("Parameter", "")).upper())
        if pollutant is None:
            stats["unmapped_pollutant"] += 1
            continue

        value = _to_measurement(observation.get("Value"))
        aqi = _to_measurement(observation.get("AQI"))
        if value is None and aqi is None:
            stats["missing_measurement"] += 1
            continue

        observed_at = _parse_observed_at(observation.get("UTC"))
        if observed_at is None:
            stats["missing_measurement"] += 1
            continue

        station_id = _station_id(observation, latitude, longitude)
        record = {
            "source": "airnow",
            "station_id": station_id,
            "station_name": observation.get("SiteName") or None,
            "latitude": latitude,
            "longitude": longitude,
            "observed_at": observed_at,
            "retrieved_at": retrieved_at_text,
            "pollutant": pollutant,
            "value": value,
            "unit": _unit(observation.get("Unit")),
            "aqi": int(aqi) if aqi is not None else None,
        }

        key = (station_id, pollutant, observed_at)
        if key in records:
            stats["duplicates_collapsed"] += 1
        records[key] = record

    ordered = sorted(
        records.values(),
        key=lambda r: (r["observed_at"], r["station_id"], r["pollutant"]),
    )
    stats["records_written"] = len(ordered)
    return ordered, stats


def _station_id(observation: dict, latitude: float, longitude: float) -> str:
    for field in ("FullAQSCode", "IntlAQSCode"):
        value = observation.get(field)
        if value:
            return str(value)
    return f"airnow-{latitude:.4f}-{longitude:.4f}"


def _parse_observed_at(raw: object) -> str | None:
    if not raw:
        return None
    text = str(raw).replace("Z", "")
    for pattern in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M", "%Y-%m-%dT%H"):
        try:
            parsed = datetime.strptime(text, pattern)
        except ValueError:
            continue
        return _to_iso_z(parsed.replace(tzinfo=timezone.utc))
    return None


def _to_iso_z(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_float(raw: object) -> float | None:
    try:
        return float(raw)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _to_measurement(raw: object) -> float | None:
    value = _to_float(raw)
    if value is None or value <= MISSING_VALUE:
        return None
    return value


def _unit(raw: object) -> str | None:
    if not raw:
        return None
    text = str(raw).strip()
    return UNITS.get(text.upper(), text.lower())
