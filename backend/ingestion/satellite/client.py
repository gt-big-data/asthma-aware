"""Google Earth Engine API access for satellite air-quality variables.

This module is the only place in the ingestion pipeline that talks to the
Earth Engine API. It knows nothing about local file layout, CLI arguments,
or date-range iteration -- that orchestration lives in collector.py.

Datasets used (see each catalog page for full docs):
  NO2 / SO2 / CO -- Sentinel-5P/TROPOMI OFFL L3 (COPERNICUS/S5P/OFFL/L3_*)
    These are already-gridded daily mosaics. ESA/GEE apply qa_value
    filtering *upstream*, before gridding, at fixed thresholds documented
    on each dataset's catalog page:
      NO2: qa_value > 0.75   SO2: qa_value > 0.5   CO: qa_value > 0.5
    The L3 collections do not expose a per-pixel qa_value band of their
    own, so there is nothing further for us to threshold -- we just record
    the upstream threshold in metadata for transparency.
  NDVI -- MODIS/Terra MOD13Q1 (MODIS/061/MOD13Q1), 16-day composite.
    Quality comes from the SummaryQA (pixel reliability) band:
      0 = Good, 1 = Marginal, 2 = Snow/Ice, 3 = Cloudy, -1 = Fill.
    We keep Good + Marginal (0-1) and mask everything else.
  AOD -- MODIS Terra+Aqua MAIAC MCD19A2 (MODIS/061/MCD19A2_GRANULES), daily.
    Quality comes from the AOD_QA bitmask (MAIAC User's Guide):
      bits 0-2 = Cloud Mask (001 = Clear)
      bits 4-5 = Adjacency Mask (00 = Normal/clear)
    Recommended "best quality" filter: CloudMask == Clear AND
    AdjacencyMask == Normal/clear.
"""

from __future__ import annotations

import datetime as dt
import os
from typing import List, Optional, Tuple

import ee
import httpx
import rasterio

S5P_NO2_COLLECTION = "COPERNICUS/S5P/OFFL/L3_NO2"
S5P_NO2_BAND = "tropospheric_NO2_column_number_density"
S5P_NO2_UNIT = "mol/m^2"
S5P_NO2_UPSTREAM_QA = "qa_value > 0.75 (applied upstream by ESA/GEE before L3 gridding)"

S5P_SO2_COLLECTION = "COPERNICUS/S5P/OFFL/L3_SO2"
S5P_SO2_BAND = "SO2_column_number_density"
S5P_SO2_UNIT = "mol/m^2"
S5P_SO2_UPSTREAM_QA = "qa_value > 0.5 (applied upstream by ESA/GEE before L3 gridding)"

S5P_CO_COLLECTION = "COPERNICUS/S5P/OFFL/L3_CO"
S5P_CO_BAND = "CO_column_number_density"
S5P_CO_UNIT = "mol/m^2"
S5P_CO_UPSTREAM_QA = "qa_value > 0.5 (applied upstream by ESA/GEE before L3 gridding)"

MODIS_NDVI_COLLECTION = "MODIS/061/MOD13Q1"
MODIS_NDVI_BAND = "NDVI"
MODIS_NDVI_QA_BAND = "SummaryQA"
MODIS_NDVI_SCALE_FACTOR = 0.0001
MODIS_NDVI_UNIT = "ndvi"
MODIS_NDVI_GOOD_QA_MAX = 1  # keep SummaryQA 0 (Good) and 1 (Marginal)
MODIS_NDVI_QA_DESCRIPTION = "SummaryQA <= 1 (Good or Marginal; Snow/Ice, Cloudy and Fill masked out)"
DEFAULT_NDVI_LOOKBACK_DAYS = 32  # ~2 compositing cycles

MODIS_AOD_COLLECTION = "MODIS/061/MCD19A2_GRANULES"
MODIS_AOD_BAND = "Optical_Depth_047"
MODIS_AOD_QA_BAND = "AOD_QA"
MODIS_AOD_SCALE_FACTOR = 0.001
MODIS_AOD_UNIT = "aod (dimensionless)"
MODIS_AOD_QA_DESCRIPTION = "AOD_QA CloudMask == Clear (bits 0-2 == 1) AND AdjacencyMask == Normal/clear (bits 4-5 == 0)"
_CLOUD_MASK_BITS = 0b111
_CLOUD_MASK_SHIFT = 0
_CLOUD_MASK_CLEAR = 1
_ADJACENCY_MASK_BITS = 0b11
_ADJACENCY_MASK_SHIFT = 4
_ADJACENCY_MASK_CLEAR = 0

OUTPUT_CRS = "EPSG:4326"

# Sentinel value written into masked pixels before export. Earth Engine's
# getDownloadURL does not reliably respect ee.Image masks: MODIS products
# need genuine resampling to reach EPSG:4326 and preserve the mask, but
# Sentinel-5P L3 is already gridded in plain lat/lon, so requesting the same
# CRS/scale hits a code path that silently drops the mask and leaks the
# underlying (invalid) values instead. Baking a sentinel into the pixel
# values ourselves -- rather than relying on Earth Engine's mask-to-NoData
# export behavior -- sidesteps that inconsistency entirely. -9999 sits well
# outside the physical range of every variable this module produces.
NODATA_VALUE = -9999.0

_initialized = False


def initialize(project_id: Optional[str] = None) -> None:
    """Initialize the Earth Engine API. Call once before any other function.

    Assumes `earthengine authenticate` has already been run interactively
    on this machine (personal-account auth). `project_id` falls back to the
    EE_PROJECT_ID environment variable when not passed explicitly.
    """
    global _initialized
    if _initialized:
        return
    project_id = project_id or os.environ.get("EE_PROJECT_ID")
    if not project_id:
        raise RuntimeError(
            "No Earth Engine project configured. Pass --ee-project or set "
            "the EE_PROJECT_ID environment variable to the Google Cloud "
            "project you registered for Earth Engine access."
        )
    ee.Initialize(project=project_id)
    _initialized = True


def make_region(min_lon: float, min_lat: float, max_lon: float, max_lat: float) -> ee.Geometry:
    return ee.Geometry.Rectangle([min_lon, min_lat, max_lon, max_lat])


def _day_window(date: dt.date) -> Tuple[str, str]:
    start = date.isoformat()
    end = (date + dt.timedelta(days=1)).isoformat()
    return start, end


def _collection_for_day(collection_id: str, date: dt.date, region: ee.Geometry) -> ee.ImageCollection:
    start, end = _day_window(date)
    return (
        ee.ImageCollection(collection_id)
        .filterDate(start, end)
        .filterBounds(region)
        .sort("system:time_start")
    )


def _native_projection(collection: ee.ImageCollection, band: str) -> ee.Projection:
    """The true pixel grid of the collection's images.

    ee.ImageCollection.mosaic() does not inherit its inputs' projection --
    the result defaults to ungridded EPSG:4326 at ~111km/pixel unless we
    explicitly reproject it back onto the source data's real grid.
    """
    return ee.Image(collection.first()).select(band).projection()


def _get_s5p_l3_image(collection_id: str, band: str, date: dt.date, region: ee.Geometry) -> Optional[ee.Image]:
    collection = _collection_for_day(collection_id, date, region)
    if collection.size().getInfo() == 0:
        return None
    native_projection = _native_projection(collection, band)
    return collection.select(band).mosaic().reproject(native_projection)


def get_no2_image(date: dt.date, region: ee.Geometry) -> Optional[ee.Image]:
    return _get_s5p_l3_image(S5P_NO2_COLLECTION, S5P_NO2_BAND, date, region)


def get_so2_image(date: dt.date, region: ee.Geometry) -> Optional[ee.Image]:
    return _get_s5p_l3_image(S5P_SO2_COLLECTION, S5P_SO2_BAND, date, region)


def get_co_image(date: dt.date, region: ee.Geometry) -> Optional[ee.Image]:
    return _get_s5p_l3_image(S5P_CO_COLLECTION, S5P_CO_BAND, date, region)


def get_aod_image(date: dt.date, region: ee.Geometry) -> Optional[ee.Image]:
    collection = _collection_for_day(MODIS_AOD_COLLECTION, date, region)
    if collection.size().getInfo() == 0:
        return None

    native_projection = _native_projection(collection, MODIS_AOD_BAND)
    aod = collection.select(MODIS_AOD_BAND).mosaic().reproject(native_projection).multiply(MODIS_AOD_SCALE_FACTOR)
    qa = collection.select(MODIS_AOD_QA_BAND).mosaic().reproject(native_projection).toInt()

    cloud_mask = qa.rightShift(_CLOUD_MASK_SHIFT).bitwiseAnd(_CLOUD_MASK_BITS).eq(_CLOUD_MASK_CLEAR)
    adjacency_mask = qa.rightShift(_ADJACENCY_MASK_SHIFT).bitwiseAnd(_ADJACENCY_MASK_BITS).eq(_ADJACENCY_MASK_CLEAR)

    return aod.updateMask(cloud_mask.And(adjacency_mask))


def get_ndvi_image(
    max_date: dt.date, region: ee.Geometry, lookback_days: int = DEFAULT_NDVI_LOOKBACK_DAYS
) -> Tuple[Optional[ee.Image], Optional[dt.date]]:
    """Return the most recent MOD13Q1 NDVI composite on or before max_date.

    MOD13Q1 is a 16-day composite, so we search backwards up to
    `lookback_days` for the most recent available image and report which
    calendar date it actually came from (system:time_start).
    """
    start = (max_date - dt.timedelta(days=lookback_days)).isoformat()
    end = (max_date + dt.timedelta(days=1)).isoformat()
    collection = (
        ee.ImageCollection(MODIS_NDVI_COLLECTION)
        .filterDate(start, end)
        .filterBounds(region)
        .sort("system:time_start", False)
    )
    if collection.size().getInfo() == 0:
        return None, None

    latest = ee.Image(collection.first())
    source_date_millis = latest.get("system:time_start").getInfo()
    source_date = dt.datetime.utcfromtimestamp(source_date_millis / 1000).date()

    ndvi = latest.select(MODIS_NDVI_BAND).multiply(MODIS_NDVI_SCALE_FACTOR)
    qa = latest.select(MODIS_NDVI_QA_BAND)
    ndvi = ndvi.updateMask(qa.gte(0).And(qa.lte(MODIS_NDVI_GOOD_QA_MAX)))
    return ndvi, source_date


def download_geotiff(image: ee.Image, region: ee.Geometry, out_path: str) -> Tuple[float, str]:
    """Download `image` clipped to `region` as a GeoTIFF at its native scale.

    Reprojects to OUTPUT_CRS (EPSG:4326) using nearest-neighbor sampling
    (Earth Engine's default -- no interpolation/blending of physical
    values) at the image's own native nominal scale. Masked pixels are
    written as NODATA_VALUE and tagged as NoData -- never substituted
    with, or confusable with, a real 0 reading.

    Returns (scale_meters, crs) actually used, for metadata purposes.
    """
    scale = image.projection().nominalScale().getInfo()
    # sameFootprint=False bakes NODATA_VALUE into previously-masked pixels
    # and drops the EE-level mask entirely, so no export code path is left
    # with a mask to (possibly) mishandle -- see NODATA_VALUE for why.
    sentinel_image = image.unmask(NODATA_VALUE, sameFootprint=False)
    band_names: List[str] = sentinel_image.bandNames().getInfo()

    url = sentinel_image.getDownloadURL(
        {
            "region": region,
            "scale": scale,
            "crs": OUTPUT_CRS,
            "format": "GEO_TIFF",
            "bands": band_names,
        }
    )
    response = httpx.get(url, timeout=120)
    response.raise_for_status()
    with open(out_path, "wb") as f:
        f.write(response.content)

    # Earth Engine does not reliably write a matching NoData tag itself
    # (see NODATA_VALUE), so set it explicitly ourselves.
    with rasterio.open(out_path, "r+") as dst:
        dst.nodata = NODATA_VALUE

    return scale, OUTPUT_CRS
