"""Collect recent AirNow air-quality observations for a bounded study area.

Usage:
    python -m ingestion.air_quality.collector --hours 24
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .airnow_client import AirNowError, BoundingBox, fetch_observations
from .normalize import normalize_observations

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGION_FILE = PROJECT_ROOT / "regions" / "atlanta-metro.json"
DEFAULT_DATA_DIR = PROJECT_ROOT / "data"
RAW_SUBDIR = Path("raw") / "air_quality" / "airnow"
NORMALIZED_SUBDIR = Path("normalized") / "air_quality" / "airnow"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)

    api_key = args.api_key or os.environ.get("AIRNOW_API_KEY")
    if not api_key:
        print(
            "No AirNow API key. Set AIRNOW_API_KEY or pass --api-key. "
            "Request a free key at https://docs.airnowapi.org/account/request/",
            file=sys.stderr,
        )
        return 2

    try:
        region, bbox = load_region(args.region_file)
    except (OSError, ValueError) as error:
        print(f"Invalid region file {args.region_file}: {error}", file=sys.stderr)
        return 2

    retrieved_at = datetime.now(timezone.utc)
    start, end = collection_window(args.hours, retrieved_at)

    try:
        raw_body, request_meta = fetch_observations(api_key, bbox, start, end)
    except AirNowError as error:
        print(str(error), file=sys.stderr)
        return 1

    stamp = retrieved_at.strftime("%Y%m%dT%H%M%SZ")
    raw_dir = args.data_dir / RAW_SUBDIR
    normalized_dir = args.data_dir / NORMALIZED_SUBDIR
    raw_dir.mkdir(parents=True, exist_ok=True)
    normalized_dir.mkdir(parents=True, exist_ok=True)

    raw_path = raw_dir / f"{region}_{stamp}.json"
    raw_path.write_bytes(raw_body)
    (raw_dir / f"{region}_{stamp}.meta.json").write_text(
        json.dumps(
            {
                "source": "airnow",
                "region": region,
                "bounding_box": dataclasses.asdict(bbox),
                "window_start_utc": start.strftime("%Y-%m-%dT%H:00:00Z"),
                "window_end_utc": end.strftime("%Y-%m-%dT%H:00:00Z"),
                "retrieved_at": retrieved_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "request": request_meta,
                "raw_file": raw_path.name,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    records, stats = normalize_observations(json.loads(raw_body), bbox, retrieved_at)
    normalized_path = normalized_dir / f"{region}_{stamp}.json"
    normalized_path.write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")

    stations = {record["station_id"] for record in records}
    print(f"region={region} window={args.hours}h bbox={bbox.as_param()}")
    print(f"raw        -> {raw_path}")
    print(f"normalized -> {normalized_path}")
    print(
        f"{stats['records_written']} records from {len(stations)} stations "
        f"({stats['observations_returned']} observations returned, "
        f"{stats['outside_bounding_box']} outside bbox, "
        f"{stats['missing_measurement']} missing values, "
        f"{stats['duplicates_collapsed']} duplicates collapsed)"
    )
    return 0


def collection_window(hours: int, now: datetime) -> tuple[datetime, datetime]:
    """Return the first and last UTC hour of a window holding exactly `hours` hourly slots.

    AirNow treats both startDate and endDate as inclusive, so the window ends at the
    current UTC hour and starts `hours - 1` hours before it.
    """
    end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return end - timedelta(hours=hours - 1), end


def load_region(path: Path) -> tuple[str, BoundingBox]:
    spec = json.loads(path.read_text(encoding="utf-8"))
    region = str(spec.get("region") or "region").strip().lower().replace(" ", "-")

    bounds = {}
    for field in ("min_lat", "max_lat", "min_lon", "max_lon"):
        if field not in spec:
            raise ValueError(f"missing required field {field!r}")
        bounds[field] = float(spec[field])

    if bounds["min_lat"] >= bounds["max_lat"] or bounds["min_lon"] >= bounds["max_lon"]:
        raise ValueError("min_lat/min_lon must be smaller than max_lat/max_lon")
    if not (-90 <= bounds["min_lat"] and bounds["max_lat"] <= 90):
        raise ValueError("latitudes must be within [-90, 90]")
    if not (-180 <= bounds["min_lon"] and bounds["max_lon"] <= 180):
        raise ValueError("longitudes must be within [-180, 180]")

    return region, BoundingBox(**bounds)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m ingestion.air_quality.collector",
        description="Collect recent AirNow PM2.5 / O3 / NO2 observations for a study area.",
    )
    parser.add_argument(
        "--hours",
        type=_positive_hours,
        default=24,
        help="size of the collection window ending now, in hours (default: 24)",
    )
    parser.add_argument(
        "--region-file",
        type=Path,
        default=DEFAULT_REGION_FILE,
        help=f"JSON bounding box to collect (default: {DEFAULT_REGION_FILE})",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"root of the data archive (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--api-key",
        help="AirNow API key (defaults to the AIRNOW_API_KEY environment variable)",
    )
    return parser.parse_args(argv)


def _positive_hours(raw: str) -> int:
    hours = int(raw)
    if hours < 1:
        raise argparse.ArgumentTypeError("--hours must be at least 1")
    return hours


if __name__ == "__main__":
    raise SystemExit(main())
