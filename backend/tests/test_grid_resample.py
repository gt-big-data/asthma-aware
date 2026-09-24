"""Raster reading and resampling. No database needed.

Runs against the real stacks in raw_rasters/, including the regression
test for the mis-tagged CRS that would otherwise shift the whole dataset
~17km north without failing.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.constants import ATLANTA_BOUNDS  # noqa: E402
from ingestion import grid_resample  # noqa: E402

RASTER_DIR = BACKEND_DIR / "raw_rasters"
NO2 = RASTER_DIR / "NO2_reprojected_stack.tif"
SO2 = RASTER_DIR / "SO2_stack_reprojected_ndvi_dims2.tif"
NDVI = RASTER_DIR / "ndvi_stack.tif"

requires_rasters = pytest.mark.skipif(
    not NO2.exists(), reason="raw_rasters/ not present in this checkout"
)

GRID_ROWS, GRID_COLS = 56, 96


@requires_rasters
def test_raw_rasters_are_already_on_the_model_grid():
    for path in (NO2, SO2, NDVI):
        bands, height, width = grid_resample.raster_shape(path)
        assert (height, width) == (GRID_ROWS, GRID_COLS), path.name
        assert bands >= 1


@requires_rasters
def test_read_band_stack_shape_and_dtype():
    stack = grid_resample.read_band_stack(NO2)
    assert stack.ndim == 3
    assert stack.shape[1:] == (GRID_ROWS, GRID_COLS)
    assert stack.dtype == np.float32


@requires_rasters
def test_read_band_stack_preserves_existing_nan_mask():
    """Masked pixels must survive as NaN, not become 0.

    build_latest_sequence.py runs np.nan_to_num over these, turning
    masked pixels into real-looking 0.0 readings that then skew any
    scaler fitted on them. The database path keeps them as NaN -> NULL.
    """
    stack = grid_resample.read_band_stack(NO2)
    band = stack[0]
    assert np.isnan(band).any(), "expected some masked pixels in band 1"
    assert np.isfinite(band).any(), "expected some valid pixels in band 1"


@requires_rasters
def test_ndvi_scale_factor_produces_physical_values():
    """MODIS NDVI is int16 scaled by 1e-4; scaled values must land in [-1, 1]."""
    stack = grid_resample.read_band_stack(NDVI) * 1e-4
    finite = stack[np.isfinite(stack)]
    assert finite.size > 0
    assert finite.min() >= -1.0
    assert finite.max() <= 1.0


@requires_rasters
def test_warping_a_mistagged_raster_warns(caplog):
    """Regression test for the raw_rasters CRS trap.

    These files carry MODIS *spherical* sinusoidal data tagged with the
    WGS84 *ellipsoid*. Honouring the declared CRS moves the data ~17km
    north and silently drops most of the grid. The warp still succeeds
    and returns a correctly-shaped array, so only the warning catches it.
    """
    import logging

    with caplog.at_level(logging.WARNING, logger="ingestion.grid_resample"):
        out = grid_resample.resample_to_grid(NO2, ATLANTA_BOUNDS, GRID_ROWS, GRID_COLS)

    assert out.shape == (GRID_ROWS, GRID_COLS)
    assert any("covers only" in record.message for record in caplog.records), (
        "expected a low-overlap warning when warping the mis-tagged raster"
    )


def test_target_transform_puts_row_zero_at_the_north():
    transform = grid_resample.target_transform(ATLANTA_BOUNDS, GRID_ROWS, GRID_COLS)

    # Origin (c, f) is the upper-left corner of pixel (0, 0).
    assert transform.f == pytest.approx(ATLANTA_BOUNDS["max_lat"])
    assert transform.c == pytest.approx(ATLANTA_BOUNDS["min_lon"])

    # A north-up raster has a negative y pixel size, so latitude decreases
    # as the row index grows. A positive `e` here would mean every stored
    # frame is vertically mirrored.
    assert transform.e < 0

    assert transform.f + GRID_ROWS * transform.e == pytest.approx(
        ATLANTA_BOUNDS["min_lat"]
    )
    assert transform.c + GRID_COLS * transform.a == pytest.approx(
        ATLANTA_BOUNDS["max_lon"]
    )


def test_unknown_resampling_method_is_rejected():
    with pytest.raises(ValueError, match="Unknown resampling method"):
        grid_resample.resample_to_grid(
            NO2, ATLANTA_BOUNDS, GRID_ROWS, GRID_COLS, method="magic"
        )
