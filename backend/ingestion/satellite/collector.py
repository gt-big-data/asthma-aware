"""Satellite air-quality ingestion workflow.

Retrieves NO2, SO2, CO, NDVI and AOD for the Atlanta area for one date (or a
range of dates) via ingestion.satellite.client, quality-filters each raster,
and writes GeoTIFFs plus a metadata.json describing them.

When a database is configured (DATABASE_URL in backend/.env) the same data
is also written to Postgres: pixel values resampled onto the 56x96 model
grid, provenance for each GeoTIFF, and an issue row for every variable that
could not be fetched. The GeoTIFFs remain the native-resolution archive.
Pass --no-db to skip the database and behave exactly as before.

Usage:
    python -m ingestion.satellite.collector --date 2026-09-10
    python -m ingestion.satellite.collector --start-date 2026-09-01 --end-date 2026-09-10
    python -m ingestion.satellite.collector --date 2026-09-10 --no-db

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


class DbSink:
    """Adapter that writes what the collector fetches into Postgres.

    Holds the open session, the region row and the current ingestion run,
    so ``collect_for_date`` does not have to know about any of them.

    A failure to persist one variable is logged and recorded as an issue
    rather than aborting the run: losing the rest of a multi-day fetch
    because one raster would not resample is a bad trade, and Earth Engine
    calls are slow enough that re-running from scratch is expensive.
    """

    def __init__(self, session, run, region_row):
        self.session = session
        self.run = run
        self.region_row = region_row
        self.rows_written = 0

    def record_raster(self, **kwargs) -> None:
        from ingestion.satellite import db_writer

        variable_slug = kwargs.get("variable_slug")
        try:
            self.rows_written += db_writer.persist_raster(
                self.session, run=self.run, region=self.region_row, **kwargs
            )
            self.run.rows_written = self.rows_written
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to persist %s to the database: %s", variable_slug, exc)
            self.record_missing(
                variable_slug,
                kwargs.get("observation_date"),
                f"Downloaded but failed to persist: {exc}",
                kind="error",
            )

    def record_missing(self, variable_slug, observation_date, reason, kind="missing") -> None:
        from ingestion.satellite import db_writer

        try:
            db_writer.persist_missing(
                self.session,
                run=self.run,
                variable_slug=variable_slug,
                observation_date=observation_date,
                reason=reason,
                kind=kind,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("Failed to record issue for %s: %s", variable_slug, exc)


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


def collect_for_date(
    date: dt.date,
    region: ee.Geometry,
    output_root: Path,
    sink: Optional["DbSink"] = None,
) -> Dict[str, Any]:
    """Fetch every variable for one date.

    ``sink`` is an optional database writer. When absent the function
    behaves exactly as it did before the database existed: GeoTIFFs plus
    a metadata.json.
    """
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
            if sink:
                sink.record_missing(variable, date, str(exc), kind="error")
            continue

        if image is None:
            logger.warning("No %s observation found for %s over %s", variable, date, REGION_NAME)
            reason = f"No observation available for {date.isoformat()}"
            missing.append({"variable": variable, "reason": reason})
            if sink:
                sink.record_missing(variable, date, reason)
            continue

        out_path = out_dir / filename
        scale_meters, crs = client.download_geotiff(image, region, str(out_path))
        rasters.append(
            _build_metadata_entry(variable, filename, source, date, unit, scale_meters, crs, qa_description)
        )
        logger.info("Wrote %s (%s, %.1fm, %s)", out_path, unit, scale_meters, crs)
        if sink:
            sink.record_raster(
                variable_slug=variable,
                source_slug=source,
                observation_date=date,
                path=out_path,
                unit=unit,
                scale_meters=scale_meters,
                crs=crs,
                qa_filtering=qa_description,
            )

    try:
        ndvi_image, ndvi_source_date = client.get_ndvi_image(date, region)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to fetch ndvi for %s: %s", date, exc)
        ndvi_image, ndvi_source_date = None, None
        missing.append({"variable": "ndvi", "reason": str(exc)})
        if sink:
            sink.record_missing("ndvi", date, str(exc), kind="error")

    if ndvi_image is None:
        if ndvi_source_date is None and not any(m["variable"] == "ndvi" for m in missing):
            logger.warning("No NDVI composite found within lookback window of %s", date)
            reason = f"No MOD13Q1 composite found within {client.DEFAULT_NDVI_LOOKBACK_DAYS} days on/before {date.isoformat()}"
            missing.append({"variable": "ndvi", "reason": reason})
            if sink:
                sink.record_missing("ndvi", date, reason)
    else:
        out_path = out_dir / "ndvi.tif"
        scale_meters, crs = client.download_geotiff(ndvi_image, region, str(out_path))
        rasters.append(
            _build_metadata_entry(
                "ndvi", "ndvi.tif", "modis-mod13q1", ndvi_source_date, client.MODIS_NDVI_UNIT, scale_meters, crs, client.MODIS_NDVI_QA_DESCRIPTION
            )
        )
        logger.info("Wrote %s (ndvi, %.1fm, source_date=%s)", out_path, scale_meters, ndvi_source_date)
        if sink:
            # observation_date is the date we asked for; source_date is
            # when the 16-day composite was actually acquired. Both are
            # kept -- the model indexes on the former, provenance needs
            # the latter.
            sink.record_raster(
                variable_slug="ndvi",
                source_slug="modis-mod13q1",
                observation_date=date,
                path=out_path,
                unit=client.MODIS_NDVI_UNIT,
                scale_meters=scale_meters,
                crs=crs,
                qa_filtering=client.MODIS_NDVI_QA_DESCRIPTION,
                source_date=ndvi_source_date,
            )

    metadata: Dict[str, Any] = {
        "requested_date": date.isoformat(),
        "region": REGION_NAME,
        "region_bounds": ATLANTA_BBOX,
        # Format kept identical to the pre-database version (naive ISO + "Z")
        # so anything already parsing metadata.json keeps working;
        # datetime.utcnow() itself is deprecated from Python 3.12.
        "generated_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
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
    parser.add_argument("--no-db", action="store_true", help="Write GeoTIFFs and metadata.json only; skip the database")
    parser.add_argument("--region", type=str, default=REGION_NAME, help=f"Region slug to attribute observations to (default: {REGION_NAME})")
    args = parser.parse_args(argv)

    if args.date and (args.start_date or args.end_date):
        parser.error("--date cannot be combined with --start-date/--end-date")
    if bool(args.start_date) != bool(args.end_date):
        parser.error("--start-date and --end-date must be provided together")
    if not args.date and not args.start_date:
        parser.error("Provide either --date or --start-date/--end-date")

    return args


def _collect_all(dates, region, output_root, sink) -> List[Dict[str, Any]]:
    all_metadata = []
    for date in dates:
        logger.info("Collecting satellite data for %s over %s", date, REGION_NAME)
        all_metadata.append(collect_for_date(date, region, output_root, sink=sink))
    return all_metadata


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

    use_db = not args.no_db
    if use_db:
        from db import config as db_config

        if not db_config.is_configured():
            # Not fatal. Someone fetching rasters before the database is
            # set up should still get their GeoTIFFs.
            logger.warning(
                "DATABASE_URL is not set -- writing files only. "
                "See backend/db/README.md to set up Postgres, or pass "
                "--no-db to silence this."
            )
            use_db = False

    if not use_db:
        all_metadata = _collect_all(dates, region, output_root, sink=None)
    else:
        from db.repositories import ingestion as ingestion_repo
        from db.repositories import reference
        from db.session import session_scope

        with session_scope() as session:
            region_row = reference.get_region(session, args.region)
            source = reference.get_data_source(
                session, "sentinel-5p-tropomi-l3-offl"
            )
            with ingestion_repo.run_scope(
                session,
                "satellite",
                region=region_row,
                source=source,
                start=dates[0],
                end=dates[-1],
                params=vars(args),
            ) as run:
                sink = DbSink(session, run, region_row)
                all_metadata = _collect_all(dates, region, output_root, sink=sink)
                logger.info(
                    "Database: wrote %d observation rows across %d date(s)",
                    sink.rows_written,
                    len(dates),
                )

    print(json.dumps(all_metadata if len(all_metadata) > 1 else all_metadata[0], indent=2))


if __name__ == "__main__":
    main()
