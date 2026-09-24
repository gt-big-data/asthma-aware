"""Populate the reference tables. Safe to re-run.

Creates:
  * the Atlanta region and its 56x96 grid (5,376 grid_cells rows)
  * the variables every pipeline writes against
  * the upstream data sources we pull from

Run once after `alembic upgrade head`, and again whenever a variable or
source is added:

    python scripts/seed_reference_data.py

Everything here is an upsert, so re-running never duplicates or clobbers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List, Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import func, select  # noqa: E402

from app.constants import ATLANTA_BOUNDS  # noqa: E402
from db.models import GridCell, Region  # noqa: E402
from db.repositories import reference  # noqa: E402
from db.session import session_scope  # noqa: E402

# The grid the ConvLSTM was trained on. These are not free parameters:
# app/ml/base_convlstm.pt expects exactly (4, 3, 56, 96), and the raster
# stacks in raw_rasters/ are 96x56. Changing them invalidates the model.
GRID_ROWS = 56
GRID_COLS = 96

REGIONS = [
    {
        "slug": "atlanta",
        "name": "Atlanta Metro Study Area",
        "bounds": ATLANTA_BOUNDS,
        "grid_rows": GRID_ROWS,
        "grid_cols": GRID_COLS,
    }
]

# Slugs are the contract between pipelines. Agreeing on them here stops
# one collector writing "pm25" while another writes "pm2_5" and the two
# never joining.
VARIABLES = [
    # --- satellite (gridded; written to grid_observations) ---
    {
        "slug": "no2",
        "display_name": "Nitrogen dioxide (tropospheric column)",
        "kind": "satellite",
        "canonical_unit": "mol/m^2",
        "description": "Sentinel-5P TROPOMI L3 OFFL. Model input channel 3.",
    },
    {
        "slug": "so2",
        "display_name": "Sulphur dioxide (column)",
        "kind": "satellite",
        "canonical_unit": "mol/m^2",
        "description": "Sentinel-5P TROPOMI L3 OFFL. Model input channel 1.",
    },
    {
        "slug": "co",
        "display_name": "Carbon monoxide (column)",
        "kind": "satellite",
        "canonical_unit": "mol/m^2",
        "description": "Sentinel-5P TROPOMI L3 OFFL. Collected but not yet a model input.",
    },
    {
        "slug": "ndvi",
        "display_name": "Normalised difference vegetation index",
        "kind": "satellite",
        "canonical_unit": "ndvi",
        "description": (
            "MODIS MOD13Q1, a 16-day composite. Model input channel 2. "
            "Because it is a composite, observation_date and source_date "
            "usually differ -- see RasterFile.source_date."
        ),
    },
    {
        "slug": "aod",
        "display_name": "Aerosol optical depth (550nm)",
        "kind": "satellite",
        "canonical_unit": "dimensionless",
        "description": "MODIS MAIAC MCD19A2. Collected but not yet a model input.",
    },
    # --- ground air quality (point; written to station_observations) ---
    {
        "slug": "pm25",
        "display_name": "PM2.5",
        "kind": "ground",
        "canonical_unit": "ug/m^3",
        "description": "Fine particulate matter. For the air_quality pipeline.",
    },
    {
        "slug": "pm10",
        "display_name": "PM10",
        "kind": "ground",
        "canonical_unit": "ug/m^3",
        "description": "Coarse particulate matter. For the air_quality pipeline.",
    },
    {
        "slug": "ozone",
        "display_name": "Ozone",
        "kind": "ground",
        "canonical_unit": "ppb",
        "description": "Ground-level ozone. For the air_quality pipeline.",
    },
    {
        "slug": "aqi",
        "display_name": "Air Quality Index",
        "kind": "ground",
        "canonical_unit": "aqi",
        "description": "US EPA AQI. Already an index, so do not rescale.",
    },
    # --- weather (point; written to station_observations) ---
    {
        "slug": "temperature",
        "display_name": "Air temperature",
        "kind": "weather",
        "canonical_unit": "degC",
        "description": "For the nws pipeline. Convert F to C before writing.",
    },
    {
        "slug": "relative_humidity",
        "display_name": "Relative humidity",
        "kind": "weather",
        "canonical_unit": "percent",
        "description": "For the nws pipeline.",
    },
    {
        "slug": "wind_speed",
        "display_name": "Wind speed",
        "kind": "weather",
        "canonical_unit": "m/s",
        "description": "For the nws pipeline.",
    },
    {
        "slug": "wind_direction",
        "display_name": "Wind direction",
        "kind": "weather",
        "canonical_unit": "degrees",
        "description": "Meteorological convention: direction the wind comes from.",
    },
    {
        "slug": "precipitation",
        "display_name": "Precipitation",
        "kind": "weather",
        "canonical_unit": "mm",
        "description": "For the nws pipeline.",
    },
    # --- pollen (point; written to station_observations) ---
    {
        "slug": "pollen_tree",
        "display_name": "Tree pollen count",
        "kind": "pollen",
        "canonical_unit": "grains/m^3",
        "description": "For the pollen pipeline.",
    },
    {
        "slug": "pollen_grass",
        "display_name": "Grass pollen count",
        "kind": "pollen",
        "canonical_unit": "grains/m^3",
        "description": "For the pollen pipeline.",
    },
    {
        "slug": "pollen_weed",
        "display_name": "Weed pollen count",
        "kind": "pollen",
        "canonical_unit": "grains/m^3",
        "description": "For the pollen pipeline.",
    },
    {
        "slug": "pollen_mold",
        "display_name": "Mold spore count",
        "kind": "pollen",
        "canonical_unit": "spores/m^3",
        "description": "For the pollen pipeline.",
    },
]

DATA_SOURCES = [
    {
        "slug": "sentinel-5p-tropomi-l3-offl",
        "name": "Sentinel-5P TROPOMI L3 OFFL",
        "provider": "ESA / Copernicus, via Google Earth Engine",
        "url": "https://developers.google.com/earth-engine/datasets/catalog/COPERNICUS_S5P_OFFL_L3_NO2",
        "license": "Copernicus Sentinel data, free and open",
        "notes": (
            "Already-gridded daily mosaics. QA filtering is applied upstream by "
            "ESA/GEE before gridding (NO2 qa_value > 0.75, SO2/CO > 0.5); the L3 "
            "collections expose no per-pixel qa_value band of their own."
        ),
    },
    {
        "slug": "modis-mod13q1",
        "name": "MODIS/Terra MOD13Q1 Vegetation Indices",
        "provider": "NASA LP DAAC, via Google Earth Engine",
        "url": "https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MOD13Q1",
        "license": "Public domain (NASA)",
        "notes": "16-day composite at 250m. We keep SummaryQA 0 (Good) and 1 (Marginal).",
    },
    {
        "slug": "modis-maiac-mcd19a2",
        "name": "MODIS MAIAC MCD19A2 Aerosol Optical Depth",
        "provider": "NASA LP DAAC, via Google Earth Engine",
        "url": "https://developers.google.com/earth-engine/datasets/catalog/MODIS_061_MCD19A2_GRANULES",
        "license": "Public domain (NASA)",
        "notes": "Daily at 1km. Filtered to AOD_QA CloudMask=Clear and AdjacencyMask=Normal.",
    },
    {
        "slug": "us-census-acs5",
        "name": "US Census American Community Survey, 5-year estimates",
        "provider": "US Census Bureau",
        "url": "https://www.census.gov/data/developers/data-sets/acs-5year.html",
        "license": "Public domain",
        "notes": (
            "Published annually. Queried at ZIP Code Tabulation Area level. "
            "Sentinel values -666666666 / -222222222 / -999999999 mean "
            "'not available' and must be read as NULL, not as a number."
        ),
    },
]


def _region_bbox_wkt(bounds: Dict[str, float]) -> str:
    min_lat, max_lat = bounds["min_lat"], bounds["max_lat"]
    min_lon, max_lon = bounds["min_lon"], bounds["max_lon"]
    return (
        f"POLYGON(({min_lon} {min_lat},{max_lon} {min_lat},"
        f"{max_lon} {max_lat},{min_lon} {max_lat},{min_lon} {min_lat}))"
    )


def seed_region(session, spec: Dict) -> Region:
    bounds = spec["bounds"]
    region = reference.find_region(session, spec["slug"])
    fields = dict(
        name=spec["name"],
        min_lat=bounds["min_lat"],
        max_lat=bounds["max_lat"],
        min_lon=bounds["min_lon"],
        max_lon=bounds["max_lon"],
        grid_rows=spec["grid_rows"],
        grid_cols=spec["grid_cols"],
        bbox=func.ST_GeomFromText(_region_bbox_wkt(bounds), 4326),
    )
    if region is None:
        region = Region(slug=spec["slug"], **fields)
        session.add(region)
    else:
        # Refuse to silently resize an existing grid: every stored
        # observation is keyed to a grid_cell, and the model is trained
        # for this exact shape.
        if (
            region.grid_rows != spec["grid_rows"]
            or region.grid_cols != spec["grid_cols"]
        ):
            raise SystemExit(
                f"Region {region.slug!r} already exists as "
                f"{region.grid_rows}x{region.grid_cols} but the seed specifies "
                f"{spec['grid_rows']}x{spec['grid_cols']}.\n"
                f"Changing the grid invalidates every stored observation and "
                f"the trained model. If this is genuinely intended, drop the "
                f"region explicitly first."
            )
        for key, value in fields.items():
            setattr(region, key, value)
    session.flush()
    return region


def compute_cell_rows(
    *,
    min_lat: float,
    max_lat: float,
    min_lon: float,
    max_lon: float,
    grid_rows: int,
    grid_cols: int,
    region_id: Optional[int] = None,
) -> List[Dict]:
    """Build the grid_cells rows for a region. Pure, so it can be tested.

    The ``lat``/``lon`` returned are the cell's **upper-left corner**,
    computed identically to ``app/utils/geo.py:get_lat_lon_for_cell``.
    The frontend heatmap is already positioned against that convention,
    so any drift here would silently move every cell on the map.
    ``tests/test_grid_geometry.py`` asserts the two agree.
    """
    lat_step = (max_lat - min_lat) / grid_rows
    lon_step = (max_lon - min_lon) / grid_cols

    rows: List[Dict] = []
    for r in range(grid_rows):
        top = max_lat - r * lat_step
        bottom = top - lat_step
        center_lat = top - lat_step / 2.0
        for c in range(grid_cols):
            left = min_lon + c * lon_step
            right = left + lon_step
            center_lon = left + lon_step / 2.0
            rows.append(
                {
                    "region_id": region_id,
                    "row": r,
                    "col": c,
                    "lat": top,
                    "lon": left,
                    "center_lat": center_lat,
                    "center_lon": center_lon,
                    "centroid": f"SRID=4326;POINT({center_lon} {center_lat})",
                    "geom": (
                        f"SRID=4326;POLYGON(({left} {bottom},{right} {bottom},"
                        f"{right} {top},{left} {top},{left} {bottom}))"
                    ),
                }
            )
    return rows


def seed_grid_cells(session, region: Region) -> int:
    """Materialise the region's grid. Returns the number of cells created."""
    existing = session.scalar(
        select(func.count(GridCell.id)).where(GridCell.region_id == region.id)
    )
    expected = region.grid_rows * region.grid_cols
    if existing == expected:
        return 0
    if existing:
        raise SystemExit(
            f"Region {region.slug!r} has {existing} grid_cells but should have "
            f"{expected}. The grid is partially seeded; delete the region's "
            f"cells and re-run rather than leaving it inconsistent."
        )

    rows = compute_cell_rows(
        min_lat=region.min_lat,
        max_lat=region.max_lat,
        min_lon=region.min_lon,
        max_lon=region.max_lon,
        grid_rows=region.grid_rows,
        grid_cols=region.grid_cols,
        region_id=region.id,
    )
    session.execute(GridCell.__table__.insert(), rows)
    reference.clear_cell_cache(region.id)
    return len(rows)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--region",
        default=None,
        help="Only seed this region slug (default: all configured regions)",
    )
    args = parser.parse_args(argv)

    with session_scope() as session:
        for spec in VARIABLES:
            reference.upsert_variable(session, **spec)
        print(f"variables:     {len(VARIABLES)} upserted")

        for spec in DATA_SOURCES:
            reference.upsert_data_source(session, **spec)
        print(f"data sources:  {len(DATA_SOURCES)} upserted")

        for spec in REGIONS:
            if args.region and spec["slug"] != args.region:
                continue
            region = seed_region(session, spec)
            created = seed_grid_cells(session, region)
            total = region.grid_rows * region.grid_cols
            if created:
                print(
                    f"region {region.slug!r}: created ({region.grid_rows}x"
                    f"{region.grid_cols}) with {created} grid cells"
                )
            else:
                print(
                    f"region {region.slug!r}: already seeded "
                    f"({total} grid cells), updated metadata"
                )

    print("\nSeed complete.")


if __name__ == "__main__":
    main()
