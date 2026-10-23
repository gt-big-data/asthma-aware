"""Persist downloaded satellite rasters into Postgres.

Sits between ``collector.py`` (which knows about Earth Engine, dates and
files) and ``db.repositories`` (which knows about tables). Keeping it
separate means the collector still works with the database switched off
-- it just writes GeoTIFFs and metadata.json as it always did.

For each variable on each date this records three things:

  * the pixel values, resampled onto the 56x96 model grid
  * provenance for the archived GeoTIFF (unit, scale, CRS, QA filter)
  * an issue row when the variable could not be fetched at all
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
from pathlib import Path
from typing import Dict, Optional

import numpy as np
from sqlalchemy.orm import Session

from db.models import IngestionRun, Region
from db.repositories import grid as grid_repo
from db.repositories import ingestion as ingestion_repo
from db.repositories import reference
from ingestion import grid_resample

logger = logging.getLogger(__name__)

# Which resampling method suits each variable when moving to the model
# grid. NDVI is a dense, fully-valid field being coarsened from 250m, so
# averaging is both safe and more faithful. The Sentinel-5P products are
# being *refined* from ~1113m and are frequently partly masked, so
# nearest-neighbour avoids inventing blends across the mask edge.
RESAMPLING_BY_VARIABLE = {
    "ndvi": "average",
    "no2": "nearest",
    "so2": "nearest",
    "co": "nearest",
    "aod": "nearest",
}


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def persist_raster(
    session: Session,
    *,
    run: IngestionRun,
    region: Region,
    variable_slug: str,
    source_slug: str,
    observation_date: dt.date,
    path: Path,
    unit: Optional[str],
    scale_meters: Optional[float],
    crs: Optional[str],
    qa_filtering: Optional[str],
    source_date: Optional[dt.date] = None,
    nodata_value: float = grid_resample.SATELLITE_NODATA,
) -> int:
    """Record one GeoTIFF and its values. Returns rows written.

    ``source_date`` defaults to ``observation_date``; NDVI passes the
    composite's real acquisition date instead, and that difference is
    preserved all the way down to the individual observation rows.
    """
    variable = reference.get_variable(session, variable_slug)
    source = reference.get_data_source(session, source_slug)

    values = grid_resample.resample_to_grid(
        path,
        bounds={
            "min_lat": region.min_lat,
            "max_lat": region.max_lat,
            "min_lon": region.min_lon,
            "max_lon": region.max_lon,
        },
        rows=region.grid_rows,
        cols=region.grid_cols,
        method=RESAMPLING_BY_VARIABLE.get(variable_slug, "nearest"),
        src_nodata=nodata_value,
    )

    raster_file = ingestion_repo.record_raster_file(
        session,
        region=region,
        variable=variable,
        observation_date=observation_date,
        path=str(path),
        run=run,
        source=source,
        source_date=source_date or observation_date,
        unit=unit,
        scale_meters=scale_meters,
        crs=crs,
        qa_filtering=qa_filtering,
        nodata_value=nodata_value,
        size_bytes=path.stat().st_size if path.exists() else None,
        checksum_sha256=_file_digest(path) if path.exists() else None,
    )

    written = grid_repo.upsert_frame(
        session,
        region,
        variable,
        observation_date,
        values,
        source_date=source_date or observation_date,
        raster_file_id=raster_file.id,
        run_id=run.id,
    )

    valid = int(np.count_nonzero(~np.isnan(values)))
    total = region.grid_rows * region.grid_cols
    logger.info(
        "  db: %s %s -> %d cells (%d/%d valid, %.0f%% coverage)",
        variable_slug,
        observation_date,
        written,
        valid,
        total,
        100.0 * valid / total,
    )
    return written


def persist_missing(
    session: Session,
    *,
    run: IngestionRun,
    variable_slug: str,
    observation_date: dt.date,
    reason: str,
    kind: str = "missing",
) -> None:
    """Record that a variable was unavailable for a date.

    Deliberately not silent: a gap in the frame history is the thing most
    likely to break the sequence builder, and knowing *why* it is there
    determines whether you backfill or fix a credential.
    """
    try:
        variable = reference.get_variable(session, variable_slug)
    except reference.UnknownReference:
        variable = None
    ingestion_repo.record_issue(
        session,
        run,
        reason,
        variable=variable,
        observation_date=observation_date,
        kind=kind,
    )
