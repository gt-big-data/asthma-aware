"""Shared test fixtures.

The database tests are split into two groups deliberately:

* Tests that need no database at all (scaler arithmetic, grid geometry,
  raster reading). These always run.
* Integration tests that need a real Postgres with PostGIS. These are
  skipped unless ``TEST_DATABASE_URL`` is set, because requiring a
  database to run the test suite would mean nobody runs it.

To run the integration tests::

    export TEST_DATABASE_URL='postgresql+psycopg://user:pass@host:5432/asthma_test'
    pytest tests/

Point this at a *throwaway* database. The fixtures create and drop the
whole schema.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "").strip()

requires_db = pytest.mark.skipif(
    not TEST_DATABASE_URL,
    reason="Set TEST_DATABASE_URL to a throwaway Postgres+PostGIS database to run",
)


@pytest.fixture(scope="session")
def engine():
    """Engine bound to the test database, with the schema created fresh."""
    if not TEST_DATABASE_URL:
        pytest.skip("TEST_DATABASE_URL not set")

    from sqlalchemy import create_engine, text

    from db.models import Base

    eng = create_engine(TEST_DATABASE_URL, future=True)
    with eng.begin() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture
def session(engine):
    """A session rolled back after each test, so tests cannot interfere.

    The outer transaction is never committed, so even repository code
    that calls ``session.commit()`` stays isolated inside the savepoint.
    """
    from sqlalchemy.orm import Session

    from db.repositories import reference

    connection = engine.connect()
    transaction = connection.begin()
    sess = Session(bind=connection, join_transaction_mode="create_savepoint")
    # The grid-cell lookup is cached per process; a test that rebuilds the
    # grid would otherwise see a stale map.
    reference.clear_cell_cache()
    try:
        yield sess
    finally:
        sess.close()
        transaction.rollback()
        connection.close()
        reference.clear_cell_cache()


@pytest.fixture
def region(session):
    """A small seeded region.

    Deliberately 4x6 rather than the real 56x96: the geometry and indexing
    logic is identical, and small grids make failures readable.
    """
    from scripts.seed_reference_data import compute_cell_rows

    from db.models import GridCell, Region
    from sqlalchemy import func

    reg = Region(
        slug="testville",
        name="Test Region",
        min_lat=33.0,
        max_lat=34.0,
        min_lon=-85.0,
        max_lon=-84.0,
        grid_rows=4,
        grid_cols=6,
        bbox=func.ST_GeomFromText(
            "POLYGON((-85 33,-84 33,-84 34,-85 34,-85 33))", 4326
        ),
    )
    session.add(reg)
    session.flush()

    rows = compute_cell_rows(
        min_lat=reg.min_lat,
        max_lat=reg.max_lat,
        min_lon=reg.min_lon,
        max_lon=reg.max_lon,
        grid_rows=reg.grid_rows,
        grid_cols=reg.grid_cols,
        region_id=reg.id,
    )
    session.execute(GridCell.__table__.insert(), rows)
    session.flush()
    return reg


@pytest.fixture
def variables(session):
    """Three variables in the model's channel order."""
    from db.repositories import reference

    return [
        reference.upsert_variable(
            session, slug, display_name=slug.upper(), kind="satellite",
            canonical_unit="mol/m^2",
        )
        for slug in ("so2", "ndvi", "no2")
    ]
