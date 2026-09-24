"""The database grid must agree with the coordinate convention the API uses.

These tests need no database. They exist because a mismatch here is the
kind of bug that produces a map which looks entirely plausible and is
quietly in the wrong place.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.constants import ATLANTA_BOUNDS  # noqa: E402
from app.utils.geo import get_lat_lon_for_cell  # noqa: E402
from scripts.seed_reference_data import GRID_COLS, GRID_ROWS, compute_cell_rows  # noqa: E402


@pytest.fixture(scope="module")
def atlanta_cells():
    return compute_cell_rows(
        min_lat=ATLANTA_BOUNDS["min_lat"],
        max_lat=ATLANTA_BOUNDS["max_lat"],
        min_lon=ATLANTA_BOUNDS["min_lon"],
        max_lon=ATLANTA_BOUNDS["max_lon"],
        grid_rows=GRID_ROWS,
        grid_cols=GRID_COLS,
    )


def test_cell_count(atlanta_cells):
    assert len(atlanta_cells) == GRID_ROWS * GRID_COLS == 5376


def test_lat_lon_matches_existing_geo_helper(atlanta_cells):
    """db grid_cells.lat/lon must equal app/utils/geo.get_lat_lon_for_cell.

    This is the contract that keeps a DB-backed map route pixel-identical
    to the current file-backed one.
    """
    for cell in atlanta_cells:
        expected_lat, expected_lon = get_lat_lon_for_cell(
            row=cell["row"],
            col=cell["col"],
            rows=GRID_ROWS,
            cols=GRID_COLS,
            bounds=ATLANTA_BOUNDS,
        )
        assert cell["lat"] == pytest.approx(expected_lat, abs=1e-12)
        assert cell["lon"] == pytest.approx(expected_lon, abs=1e-12)


def test_row_zero_is_the_northern_edge(atlanta_cells):
    """Row 0 is max_lat. If this flips, every stored frame is upside down."""
    first = atlanta_cells[0]
    assert first["row"] == 0 and first["col"] == 0
    assert first["lat"] == pytest.approx(ATLANTA_BOUNDS["max_lat"])
    assert first["lon"] == pytest.approx(ATLANTA_BOUNDS["min_lon"])


def test_cells_are_row_major(atlanta_cells):
    """Ordering must match numpy's ravel(), which is what upsert_frame assumes."""
    for index, cell in enumerate(atlanta_cells):
        assert cell["row"] == index // GRID_COLS
        assert cell["col"] == index % GRID_COLS


def test_centroid_is_inside_its_own_cell(atlanta_cells):
    lat_step = (ATLANTA_BOUNDS["max_lat"] - ATLANTA_BOUNDS["min_lat"]) / GRID_ROWS
    lon_step = (ATLANTA_BOUNDS["max_lon"] - ATLANTA_BOUNDS["min_lon"]) / GRID_COLS
    for cell in atlanta_cells:
        # lat/lon is the upper-left corner, so the centre sits half a step
        # south and half a step east of it.
        assert cell["center_lat"] == pytest.approx(cell["lat"] - lat_step / 2)
        assert cell["center_lon"] == pytest.approx(cell["lon"] + lon_step / 2)


def test_grid_spans_the_full_bounding_box(atlanta_cells):
    lat_step = (ATLANTA_BOUNDS["max_lat"] - ATLANTA_BOUNDS["min_lat"]) / GRID_ROWS
    lon_step = (ATLANTA_BOUNDS["max_lon"] - ATLANTA_BOUNDS["min_lon"]) / GRID_COLS
    last = atlanta_cells[-1]
    # The last cell's lower-right corner should land exactly on the bbox corner.
    assert last["lat"] - lat_step == pytest.approx(ATLANTA_BOUNDS["min_lat"])
    assert last["lon"] + lon_step == pytest.approx(ATLANTA_BOUNDS["max_lon"])


def test_wkt_is_well_formed(atlanta_cells):
    cell = atlanta_cells[0]
    assert cell["centroid"].startswith("SRID=4326;POINT(")
    assert cell["geom"].startswith("SRID=4326;POLYGON((")
    # A closed ring repeats its first vertex.
    ring = cell["geom"].split("((")[1].rstrip("))").split(",")
    assert len(ring) == 5
    assert ring[0].strip() == ring[-1].strip()
