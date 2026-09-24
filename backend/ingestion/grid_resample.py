"""Resample a downloaded GeoTIFF onto a region's canonical model grid.

The satellite pipeline downloads each variable at its own native
resolution -- ~1113m for Sentinel-5P, 250m for MODIS NDVI, 1km for MAIAC
AOD. The ConvLSTM, though, consumes a single fixed 56x96 grid over the
Atlanta bounding box. Something has to put them on a common grid, and
until now that was done offline by the ML team (hence the filename
``SO2_stack_reprojected_ndvi_dims2.tif``).

This module does it in-pipeline and reproducibly.

Note that 56x96 over the Atlanta bbox is roughly 490m per cell, so this
is not uniformly a downsample: NDVI at 250m is being *coarsened*, while
Sentinel-5P at 1113m is being *refined*. Neither creates information --
refining just replicates the source pixel across the cells it covers.

Do not point this at ``raw_rasters/*.tif``
---------------------------------------------
Those files are already on the 56x96 model grid, and their embedded CRS
is mis-tagged: the data is MODIS *spherical* sinusoidal (R=6371007.181)
but the WKT declares the WGS84 *ellipsoid*. Reprojecting according to the
declared CRS lands the data ~17km north of where it belongs and drops
~60% of the cells, all without raising anything.

Verified against ``NO2_reprojected_stack.tif``::

    as declared (WGS84 ellipsoid):  lat 33.5397 .. 33.7921   (+0.15 deg, ~17km off)
    as MODIS sphere (correct):      lat 33.3882 .. 33.6400   (matches ATLANTA_BOUNDS)

Use ``read_band_stack`` for those files -- they need no warping, only
NoData normalisation. This module's ``resample_to_grid`` is for the
GeoTIFFs the satellite collector downloads, which Earth Engine exports in
genuine EPSG:4326 and which therefore do need warping.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import rasterio
from rasterio.enums import Resampling
from rasterio.transform import from_bounds
from rasterio.warp import reproject, transform_bounds

logger = logging.getLogger(__name__)

# Below this fraction of the target grid being covered by the source
# footprint, assume something is wrong with the CRS rather than that the
# data is genuinely partial.
MIN_FOOTPRINT_OVERLAP = 0.5

# What a masked/absent pixel becomes in the returned array. The database
# layer turns NaN into NULL.
FILL = np.nan

# Sentinel the satellite client bakes into masked pixels before export.
# Kept in sync with ingestion/satellite/client.py:NODATA_VALUE.
SATELLITE_NODATA = -9999.0

RESAMPLING_METHODS = {
    "nearest": Resampling.nearest,
    "bilinear": Resampling.bilinear,
    "average": Resampling.average,
    "cubic": Resampling.cubic,
}


def target_transform(bounds: Dict[str, float], rows: int, cols: int):
    """The affine transform of the model grid.

    Row 0 is the *northern* edge, matching both the GeoTIFF convention
    and ``app/utils/geo.py`` (where ``lat = max_lat - row * step``). If
    this were flipped, every stored frame would be upside down relative
    to the frontend heatmap -- and it would still look plausible.
    """
    return from_bounds(
        bounds["min_lon"],
        bounds["min_lat"],
        bounds["max_lon"],
        bounds["max_lat"],
        cols,
        rows,
    )


def _check_footprint_overlap(path: Path, src, bounds: Dict[str, float]) -> None:
    """Warn when a source barely overlaps the target grid.

    A mis-declared CRS produces a silently wrong result -- the warp
    succeeds, the array has the right shape, and the numbers are simply
    in the wrong places. A footprint check is the cheapest way to notice.
    """
    if src.crs is None:
        logger.warning(
            "%s has no CRS; assuming it is already on the target grid.", path.name
        )
        return

    try:
        left, bottom, right, top = transform_bounds(
            src.crs, "EPSG:4326", *src.bounds, densify_pts=32
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics only, never fatal
        logger.debug("Could not reproject bounds of %s: %s", path.name, exc)
        return

    overlap_lon = max(
        0.0, min(right, bounds["max_lon"]) - max(left, bounds["min_lon"])
    )
    overlap_lat = max(
        0.0, min(top, bounds["max_lat"]) - max(bottom, bounds["min_lat"])
    )
    target_area = (bounds["max_lon"] - bounds["min_lon"]) * (
        bounds["max_lat"] - bounds["min_lat"]
    )
    fraction = (overlap_lon * overlap_lat) / target_area if target_area else 0.0

    if fraction < MIN_FOOTPRINT_OVERLAP:
        logger.warning(
            "%s covers only %.0f%% of the target grid after reprojection "
            "(source footprint lat %.4f..%.4f lon %.4f..%.4f vs target lat "
            "%.4f..%.4f lon %.4f..%.4f). If this file came from raw_rasters/, "
            "its CRS is mis-tagged (MODIS sphere data labelled as the WGS84 "
            "ellipsoid) -- read it with read_band_stack instead of warping it.",
            path.name,
            100.0 * fraction,
            bottom,
            top,
            left,
            right,
            bounds["min_lat"],
            bounds["max_lat"],
            bounds["min_lon"],
            bounds["max_lon"],
        )


def resample_to_grid(
    path: Path,
    bounds: Dict[str, float],
    rows: int,
    cols: int,
    *,
    band: int = 1,
    method: str = "nearest",
    src_nodata: Optional[float] = SATELLITE_NODATA,
) -> np.ndarray:
    """Read one band of a GeoTIFF onto a ``(rows, cols)`` array.

    Masked source pixels, and any part of the target grid the source does
    not cover, come back as NaN -- never 0, and never the -9999 sentinel.

    ``method`` defaults to nearest-neighbour to match the rest of the
    pipeline: ``client.download_geotiff`` deliberately avoids
    interpolating physical quantities, and averaging concentrations across
    a partly-masked neighbourhood would blend real readings with fill.
    Use ``average`` only when coarsening a dense, fully-valid field.
    """
    if method not in RESAMPLING_METHODS:
        raise ValueError(
            f"Unknown resampling method {method!r}. "
            f"Choose one of: {', '.join(sorted(RESAMPLING_METHODS))}"
        )

    destination = np.full((rows, cols), FILL, dtype=np.float32)
    dst_transform = target_transform(bounds, rows, cols)

    with rasterio.open(path) as src:
        if band > src.count:
            raise ValueError(
                f"{path} has {src.count} band(s); band {band} was requested."
            )
        _check_footprint_overlap(path, src, bounds)
        source = src.read(band).astype(np.float32)

        # Prefer the file's own NoData tag; fall back to the caller's
        # value. download_geotiff sets this tag explicitly because Earth
        # Engine does not do so reliably.
        nodata = src.nodata if src.nodata is not None else src_nodata
        if nodata is not None:
            source = np.where(np.isclose(source, nodata), np.nan, source)

        reproject(
            source=source,
            destination=destination,
            src_transform=src.transform,
            src_crs=src.crs,
            dst_transform=dst_transform,
            dst_crs="EPSG:4326",
            # NaN on both sides so masked pixels stay masked through the
            # warp instead of being resampled into their neighbours.
            src_nodata=np.nan,
            dst_nodata=np.nan,
            resampling=RESAMPLING_METHODS[method],
        )

    return destination


def read_band_stack(path: Path, *, nodata: Optional[float] = None) -> np.ndarray:
    """Read every band of a raster as ``(bands, height, width)`` float32.

    Used by the backfill script for the pre-existing ``raw_rasters/``
    stacks, which are already on the 56x96 grid and so need no warping --
    only NoData normalisation.
    """
    with rasterio.open(path) as src:
        stack = src.read().astype(np.float32)
        effective_nodata = src.nodata if src.nodata is not None else nodata
    if effective_nodata is not None:
        stack = np.where(np.isclose(stack, effective_nodata), np.nan, stack)
    return stack


def raster_shape(path: Path) -> Tuple[int, int, int]:
    """``(bands, height, width)`` without reading the pixel data."""
    with rasterio.open(path) as src:
        return src.count, src.height, src.width
