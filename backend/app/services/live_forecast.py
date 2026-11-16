"""Compact native-cell forecasts with value-preserving display downscaling.

No network acquisition, trained model, symptom prediction, or personal data here.
"""
from __future__ import annotations

import json
import math
import os
import tempfile
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

MAX_BYTES = 16 * 1024 * 1024
SCALES = {"pm25": 50.0, "no2": 100.0, "o3": 100.0}
UNITS = {"pm25": "ug m-3", "no2": "ppb", "o3": "ppb"}
GEORGIA = (30.36, 35.0, -85.61, -80.84)


class ForecastUnavailable(ValueError):
    pass


class OutsideCoverage(ValueError):
    pass


def moment(value):
    if not isinstance(value, str):
        raise ForecastUnavailable("Invalid forecast timestamp")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ForecastUnavailable("Forecast timestamps must include a timezone")
    return result.astimezone(timezone.utc)


def score(values):
    """Arbitrary engineering scale; equal weights, not a medical model or AQI."""
    if not isinstance(values, dict) or set(values) != set(SCALES):
        raise ForecastUnavailable("All three pollutant inputs are required")
    if any(type(values[k]) not in (int, float) or not math.isfinite(values[k]) or values[k] < 0 for k in SCALES):
        raise ForecastUnavailable("Invalid pollutant concentration")
    return round(100 * sum(min(values[k] / scale, 1) for k, scale in SCALES.items()) / len(SCALES))


def validate(payload):
    try:
        if payload["schema_version"] != "nasa-placeholder-v1" or payload["source"] != "NASA GEOS-CF v2":
            raise ForecastUnavailable("Unsupported forecast contract")
        if payload["units"] != UNITS or not isinstance(payload["run_id"], str):
            raise ForecastUnavailable("Missing forecast provenance or units")
        initialized = moment(payload["initialized_at"])
        if not re.fullmatch(r"\d{8}_09z", payload["run_id"]) or payload["run_id"] != initialized.strftime("%Y%m%d_%Hz"):
            raise ForecastUnavailable("Run identity does not match initialization")
        if initialized.hour != 9 or initialized.minute or initialized.second or initialized.microsecond:
            raise ForecastUnavailable("Unexpected GEOS-CF initialization time")
        if not isinstance(payload["cells"], list) or not 1 <= len(payload["cells"]) <= 1000:
            raise ForecastUnavailable("Invalid forecast coverage")
        seen = set()
        processing = payload.get("processing")
        if processing is not None:
            from forecast_pipeline.models import MODEL_VERSIONS
            if processing != MODEL_VERSIONS:
                raise ForecastUnavailable("Unsupported model pipeline version")
        reference_times = None
        for cell in payload["cells"]:
            lat, lon = cell["lat"], cell["lon"]
            if type(lat) not in (int, float) or type(lon) not in (int, float):
                raise ForecastUnavailable("Invalid native coordinates")
            # Native centers can lie just beyond the Georgia rectangle.
            if not (30.25 <= lat <= 35.0 and -85.5 <= lon <= -80.75) or (lat, lon) in seen:
                raise ForecastUnavailable("Invalid or duplicate native cell")
            if lat * 4 != round(lat * 4) or lon * 4 != round(lon * 4):
                raise ForecastUnavailable("Expected native 0.25 degree grid centers")
            seen.add((lat, lon))
            if not isinstance(cell["hours"], list) or not 1 <= len(cell["hours"]) <= 120:
                raise ForecastUnavailable("Invalid forecast horizon")
            times = [moment(row["timestamp"]) for row in cell["hours"]]
            if reference_times is not None and times != reference_times:
                raise ForecastUnavailable("Native cells must share one forecast time axis")
            reference_times = times
            if times[0] < initialized or times[-1] >= initialized + timedelta(hours=120):
                raise ForecastUnavailable("Hours outside the NASA forecast run")
            if any(t.minute or t.second or t.microsecond for t in times):
                raise ForecastUnavailable("Expected start-of-hour timestamps")
            if any(b - a != timedelta(hours=1) for a, b in zip(times, times[1:])):
                raise ForecastUnavailable("Forecast hours must be contiguous and unique")
            for row in cell["hours"]:
                expected_score = score(row["values"])
                if processing is not None and (type(row.get("score")) is not int or row["score"] != expected_score):
                    raise ForecastUnavailable("Stored score does not match the declared placeholder model")
            if processing is not None and cell.get("grid") != native_grid(lat, lon):
                raise ForecastUnavailable("Stored downscaling grid does not match the native cell")
        if len(json.dumps(payload, allow_nan=False).encode()) > MAX_BYTES:
            raise ForecastUnavailable("Forecast exceeds artifact size limit")
    except (KeyError, TypeError, AttributeError, OverflowError, ValueError) as exc:
        raise ForecastUnavailable("Invalid forecast artifact") from exc
    return payload


def publish_file(payload, path):
    validate(payload)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # This adapter is for a single local batch writer. Database mode provides
    # transactional concurrency control for shared deployments.
    if path.exists() and path.stat().st_size <= MAX_BYTES:
        try:
            previous = validate(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            previous = None
        if previous is not None:
            if previous == payload:
                return False
            if moment(previous["initialized_at"]) > moment(payload["initialized_at"]):
                raise ForecastUnavailable("Refusing to replace a newer local forecast")
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(payload, stream, allow_nan=False, separators=(",", ":"))
        os.replace(name, path)
        return True
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def load(settings):
    try:
        if settings.live_forecast_store == "file":
            path = Path(settings.live_forecast_path)
            if path.stat().st_size > MAX_BYTES:
                raise ForecastUnavailable("Forecast artifact is too large")
            return validate(json.loads(path.read_text(encoding="utf-8")))
        if settings.live_forecast_store == "database":
            from db.repositories.live_forecast import read_latest
            return validate(read_latest())
    except Exception as exc:
        raise ForecastUnavailable("Published forecast is unavailable") from exc
    raise ForecastUnavailable("Live forecasts are not configured")


@dataclass(frozen=True)
class Snapshot:
    payload: dict
    cells: dict
    initialized: datetime

    @classmethod
    def build(cls, payload):
        validate(payload)
        cells = {}
        for cell in payload["cells"]:
            # New publications contain batch model results. Legacy v1 artifacts
            # remain readable, explicitly labeled as compatibility calculations.
            hours = [{**h, "score": h["score"] if "processing" in payload else score(h["values"])} for h in cell["hours"]]
            cells[(cell["lat"], cell["lon"])] = {**cell, "hours": hours}
        return cls(payload, cells, moment(payload["initialized_at"]))


class Provider:
    """One bounded snapshot per API process; single-flight load, 30s maximum TTL.

    File replacements invalidate immediately by stat signature. Database reads
    reuse one validated snapshot for 30s. Freshness is still checked per request.
    Errors never serve a previously cached snapshot past its TTL.
    """
    def __init__(self, settings, clock=time.monotonic):
        self.settings = settings
        self.lock = threading.Lock()
        self.snapshot = None
        self.signature = None
        self.loaded_at = float("-inf")
        self.clock = clock

    def get(self):
        with self.lock:
            signature = None
            if self.settings.live_forecast_store == "file":
                try:
                    stat = Path(self.settings.live_forecast_path).stat()
                    signature = (stat.st_mtime_ns, stat.st_size, stat.st_ino)
                except OSError:
                    raise ForecastUnavailable("Published forecast is unavailable") from None
            if self.snapshot is None or signature != self.signature or self.clock() - self.loaded_at >= 30:
                candidate = Snapshot.build(load(self.settings))
                self.snapshot, self.signature, self.loaded_at = candidate, signature, self.clock()
            return self.snapshot


def native_grid(lat, lon):
    """Approximate 1 km cells; split native footprint evenly, no edge overshoot.

    Native GEOS-CF cells are 0.25 degrees, not exact 25 km squares. Rounding up
    rows/columns gives cells at most approximately 1 km along either axis.
    """
    rows = math.ceil(.25 * 111.32)
    cols = math.ceil(.25 * 111.32 * math.cos(math.radians(lat)))
    return {"south": lat - .125, "north": lat + .125,
            "west": lon - .125, "east": lon + .125,
            "rows": rows, "cols": cols, "lat_step": .25 / rows, "lon_step": .25 / cols}


def at_location(payload, lat, lon, now=None):
    snapshot = payload if isinstance(payload, Snapshot) else Snapshot.build(payload)
    payload = snapshot.payload
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ForecastUnavailable("Current time must be timezone aware")
    if not (math.isfinite(lat) and math.isfinite(lon) and GEORGIA[0] <= lat <= GEORGIA[1] and GEORGIA[2] <= lon <= GEORGIA[3]):
        raise OutsideCoverage("Location is outside Georgia")
    initialized = snapshot.initialized
    if initialized > now or now - initialized > timedelta(hours=48):
        raise ForecastUnavailable("NASA forecast run is stale or future dated")
    # Match the existing ingestion point snap convention, including tie-to-even.
    center = (round(lat * 4) / 4, round(lon * 4) / 4)
    cell = snapshot.cells.get(center)
    if cell is None:
        raise OutsideCoverage("No published NASA cell covers this location")
    hours = [row for row in cell["hours"] if moment(row["timestamp"]) + timedelta(hours=1) > now]
    if not hours or moment(hours[0]["timestamp"]) > now:
        raise ForecastUnavailable("Forecast does not cover the current time")
    # Local equirectangular 1 km display grid within the native footprint.
    # Materializing identical subpixels in Postgres would waste storage.
    grid = native_grid(cell["lat"], cell["lon"])
    row = min(grid["rows"] - 1, max(0, math.floor((grid["north"] - lat) / grid["lat_step"])))
    col = min(grid["cols"] - 1, max(0, math.floor((lon - grid["west"]) / grid["lon_step"])))
    return {
        "source": payload["source"], "run_id": payload["run_id"],
        "initialized_at": payload["initialized_at"],
        "method": "native-cell-copy; equal-weight-v1", "placeholder": True,
        "processing": payload.get("processing", {"pipeline_version": "legacy-read-adapter", "models_are_placeholders": True}),
        "notice": "Development environmental score; not a medical prediction or AQI.",
        "location": {"lat": lat, "lon": lon, "pixel_id": f"{cell['lat']}:{cell['lon']}:{row}:{col}",
                     "native_lat": cell["lat"], "native_lon": cell["lon"], "display_resolution_km": 1},
        "units": UNITS,
        "grid": grid,
        "valid_until": (moment(hours[-1]["timestamp"]) + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "hourly": hours,
    }
