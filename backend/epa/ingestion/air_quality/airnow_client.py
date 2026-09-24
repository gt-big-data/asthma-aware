"""Client for the AirNow Air Quality Data API (https://www.airnowapi.org/aq/data/)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime

API_URL = "https://www.airnowapi.org/aq/data/"
DEFAULT_PARAMETERS = ("PM25", "OZONE", "NO2")
USER_AGENT = "AsthmaAware-ingestion/1.0"


class AirNowError(RuntimeError):
    pass


@dataclass(frozen=True)
class BoundingBox:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def as_param(self) -> str:
        return f"{self.min_lon},{self.min_lat},{self.max_lon},{self.max_lat}"

    def contains(self, latitude: float, longitude: float) -> bool:
        return (
            self.min_lat <= latitude <= self.max_lat
            and self.min_lon <= longitude <= self.max_lon
        )


def fetch_observations(
    api_key: str,
    bbox: BoundingBox,
    start: datetime,
    end: datetime,
    *,
    parameters: tuple[str, ...] = DEFAULT_PARAMETERS,
    monitor_type: int = 2,
    timeout: int = 60,
    retries: int = 3,
) -> tuple[bytes, dict]:
    """Return the raw response body and the request metadata (API key redacted)."""
    query = {
        "startDate": start.strftime("%Y-%m-%dT%H"),
        "endDate": end.strftime("%Y-%m-%dT%H"),
        "parameters": ",".join(parameters),
        "BBOX": bbox.as_param(),
        "dataType": "B",
        "format": "application/json",
        "verbose": "1",
        "monitorType": str(monitor_type),
        "includerawconcentrations": "0",
        "API_KEY": api_key,
    }
    url = API_URL + "?" + urllib.parse.urlencode(query)
    request_meta = {
        "endpoint": API_URL,
        "query": {k: v for k, v in query.items() if k != "API_KEY"},
    }

    body = _get_with_retries(url, timeout=timeout, retries=retries)
    _raise_for_service_error(body)
    return body, request_meta


def _get_with_retries(url: str, *, timeout: int, retries: int) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    last_error: Exception | None = None

    for attempt in range(retries):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError as error:
            if error.code not in (408, 429, 500, 502, 503, 504):
                detail = error.read().decode("utf-8", "replace")[:500]
                raise AirNowError(f"AirNow returned HTTP {error.code}: {detail}") from error
            last_error = error
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error

        if attempt < retries - 1:
            time.sleep(2**attempt)

    raise AirNowError(f"AirNow request failed after {retries} attempts: {last_error}")


def _raise_for_service_error(body: bytes) -> None:
    """AirNow reports some failures as a 200 response carrying a WebServiceError payload."""
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise AirNowError(f"AirNow returned a non-JSON response: {body[:200]!r}") from error

    if isinstance(payload, dict) and "WebServiceError" in payload:
        raise AirNowError(f"AirNow service error: {payload['WebServiceError']}")
    if isinstance(payload, list) and payload and isinstance(payload[0], dict):
        if "WebServiceError" in payload[0]:
            raise AirNowError(f"AirNow service error: {payload[0]['WebServiceError']}")
