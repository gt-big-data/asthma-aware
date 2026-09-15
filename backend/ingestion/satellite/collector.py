"""Satellite air-quality ingestion workflow.

Retrieves NO2, SO2, CO, NDVI and AOD for the Atlanta area for one date (or a
range of dates) via ingestion.satellite.client, quality-filters each raster,
and writes GeoTIFFs plus a metadata.json describing them.

Usage:
    python -m ingestion.satellite.collector --date 2026-09-10
    python -m ingestion.satellite.collector --start-date 2026-09-01 --end-date 2026-09-10

Run from the backend/ directory so that `ingestion` resolves as a package.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import ee

from ingestion.satellite import client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

REGION_NAME = "atlanta"
ATLANTA_BBOX = {
    "min_lat": 33.39,
    "max_lat": 33.64,
    "min_lon": -84.550,
    "max_lon": -84.280,
}

DEFAULT_OUTPUT_DIR = "data/satellite"


def _build_metadata_entry(
    variable: str, filename: str, source: str, source_date: dt.date, unit: str, scale_meters: float, crs: str, qa_filtering: str
) -> Dict[str, Any]:
    return {
        "variable": variable,
        "file": filename,
        "source": source,
        "source_date": source_date.isoformat(),
        "unit": unit,
        "scale_meters": round(scale_meters, 3),
        "crs": crs,
        "qa_filtering": qa_filtering,
    }


def collect_for_date(date: dt.date, region: ee.Geometry, output_root: Path) -> Dict[str, Any]:
    out_dir = output_root / date.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    rasters: List[Dict[str, Any]] = []
    missing: List[Dict[str, str]] = []

    variable_specs = [
        ("no2", "no2.tif", "sentinel-5p-tropomi-l3-offl", client.get_no2_image, client.S5P_NO2_UNIT, client.S5P_NO2_UPSTREAM_QA),
        ("so2", "so2.tif", "sentinel-5p-tropomi-l3-offl", client.get_so2_image, client.S5P_SO2_UNIT, client.S5P_SO2_UPSTREAM_QA),
        ("co", "co.tif", "sentinel-5p-tropomi-l3-offl", client.get_co_image, client.S5P_CO_UNIT, client.S5P_CO_UPSTREAM_QA),
        ("aod", "aod.tif", "modis-maiac-mcd19a2", client.get_aod_image, client.MODIS_AOD_UNIT, client.MODIS_AOD_QA_DESCRIPTION),
    ]

    for variable, filename, source, fetch_fn, unit, qa_description in variable_specs:
        try:
            image = fetch_fn(date, region)
        except Exception as exc:  # noqa: BLE001 - log and continue with other variables
            logger.warning("Failed to fetch %s for %s: %s", variable, date, exc)
            missing.append({"variable": variable, "reason": str(exc)})
            continue

        if image is None:
            logger.warning("No %s observation found for %s over %s", variable, date, REGION_NAME)
            missing.append({"variable": variable, "reason": f"No observation available for {date.isoformat()}"})
            continue

        out_path = out_dir / filename
        scale_meters, crs = client.download_geotiff(image, region, str(out_path))
        rasters.append(
            _build_metadata_entry(variable, filename, source, date, unit, scale_meters, crs, qa_description)
        )
        logger.info("Wrote %s (%s, %.1fm, %s)", out_path, unit, scale_meters, crs)

    try:
        ndvi_image, ndvi_source_date = client.get_ndvi_image(date, region)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch ndvi for %s: %s", date, exc)
        ndvi_image, ndvi_source_date = None, None
        missing.append({"variable": "ndvi", "reason": str(exc)})

    if ndvi_image is None:
        if ndvi_source_date is None and not any(m["variable"] == "ndvi" for m in missing):
            logger.warning("No NDVI composite found within lookback window of %s", date)
            missing.append(
                {
                    "variable": "ndvi",
                    "reason": f"No MOD13Q1 composite found within {client.DEFAULT_NDVI_LOOKBACK_DAYS} days on/before {date.isoformat()}",
                }
            )
    else:
        out_path = out_dir / "ndvi.tif"
        scale_meters, crs = client.download_geotiff(ndvi_image, region, str(out_path))
        rasters.append(
            _build_metadata_entry(
                "ndvi", "ndvi.tif", "modis-mod13q1", ndvi_source_date, client.MODIS_NDVI_UNIT, scale_meters, crs, client.MODIS_NDVI_QA_DESCRIPTION
            )
        )
        logger.info("Wrote %s (ndvi, %.1fm, source_date=%s)", out_path, scale_meters, ndvi_source_date)

    metadata: Dict[str, Any] = {
        "requested_date": date.isoformat(),
        "region": REGION_NAME,
        "region_bounds": ATLANTA_BBOX,
        "generated_at": dt.datetime.utcnow().isoformat() + "Z",
        "rasters": rasters,
    }
    if missing:
        metadata["missing"] = missing

    metadata_path = out_dir / "metadata.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)

    return metadata


def _date_range(start: dt.date, end: dt.date):
    current = start
    while current <= end:
        yield current
        current += dt.timedelta(days=1)


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Ingest satellite air-quality rasters for the Atlanta area.")
    parser.add_argument("--date", type=str, help="Single date to retrieve, e.g. 2026-09-10")
    parser.add_argument("--start-date", type=str, help="Start of a date range (inclusive)")
    parser.add_argument("--end-date", type=str, help="End of a date range (inclusive)")
    parser.add_argument("--ee-project", type=str, default=None, help="Google Cloud project registered for Earth Engine (overrides EE_PROJECT_ID)")
    parser.add_argument("--output-dir", type=str, default=DEFAULT_OUTPUT_DIR, help="Root output directory (default: data/satellite)")
    args = parser.parse_args(argv)

    if args.date and (args.start_date or args.end_date):
        parser.error("--date cannot be combined with --start-date/--end-date")
    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start-date and --end-date must be provided together")
    if not args.date and not args.start_date:
        parser.error("Provide either --date or --start-date/--end-date")

    return args


def main(argv: Optional[List[str]] = None) -> None:
    args = parse_args(argv)
    client.initialize(args.ee_project)

    region = client.make_region(
        ATLANTA_BBOX["min_lon"], ATLANTA_BBOX["min_lat"], ATLANTA_BBOX["max_lon"], ATLANTA_BBOX["max_lat"]
    )
    output_root = Path(args.output_dir)

    if args.date:
        dates = [dt.date.fromisoformat(args.date)]
    else:
        dates = list(_date_range(dt.date.fromisoformat(args.start_date), dt.date.fromisoformat(args.end_date)))

    all_metadata = []
    for date in dates:
        logger.info("Collecting satellite data for %s over %s", date, REGION_NAME)
        metadata = collect_for_date(date, region, output_root)
        all_metadata.append(metadata)

    print(json.dumps(all_metadata if len(all_metadata) > 1 else all_metadata[0], indent=2))


if __name__ == "__main__":
    main()
