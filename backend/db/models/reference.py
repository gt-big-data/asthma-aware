"""Reference (dimension) tables: regions, the model grid, variables, sources.

These tables are small, change rarely, and are populated by
``scripts/seed_reference_data.py``. Everything else in the schema points
at them.
"""

from __future__ import annotations

from typing import List, Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

# Categories a variable can belong to. Drives which table its measurements
# land in: GRIDDED -> grid_observations, everything else -> station_observations.
VARIABLE_KINDS = ("satellite", "ground", "weather", "pollen", "derived")


class Region(TimestampMixin, Base):
    """A study area and the raster grid defined over it.

    The grid dimensions live here rather than in code because the ML model
    is trained against a specific grid: if ``grid_rows``/``grid_cols`` ever
    change, every stored observation and the model itself are invalidated
    together, and having them in one row makes that explicit.
    """

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)

    min_lat: Mapped[float] = mapped_column(Float, nullable=False)
    max_lat: Mapped[float] = mapped_column(Float, nullable=False)
    min_lon: Mapped[float] = mapped_column(Float, nullable=False)
    max_lon: Mapped[float] = mapped_column(Float, nullable=False)

    grid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    grid_cols: Mapped[int] = mapped_column(Integer, nullable=False)

    bbox: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False), nullable=True
    )

    cells: Mapped[List["GridCell"]] = relationship(back_populates="region")

    __table_args__ = (
        CheckConstraint("max_lat > min_lat", name="lat_ordered"),
        CheckConstraint("max_lon > min_lon", name="lon_ordered"),
        CheckConstraint("grid_rows > 0 AND grid_cols > 0", name="grid_positive"),
    )

    def __repr__(self) -> str:
        return f"<Region {self.slug} {self.grid_rows}x{self.grid_cols}>"


class GridCell(Base):
    """One cell of a region's raster grid.

    Materialised once per region (56*96 = 5,376 rows for Atlanta) so that
    observations can carry a plain integer foreign key instead of
    repeating row/col/lat/lon on every measurement.

    Coordinate convention
    ---------------------
    ``lat``/``lon`` are the cell's **upper-left corner**, matching
    ``app/utils/geo.py:get_lat_lon_for_cell`` exactly::

        lat = max_lat - row * (max_lat - min_lat) / rows
        lon = min_lon + col * (max_lon - min_lon) / cols

    That is the convention the frontend heatmap already consumes, so it is
    preserved verbatim. ``center_lat``/``center_lon`` and the PostGIS
    ``centroid`` hold the true cell centre, which is what you want for
    distance and point-in-polygon work. Do not mix the two up.
    """

    __tablename__ = "grid_cells"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), nullable=False
    )

    row: Mapped[int] = mapped_column(Integer, nullable=False)
    col: Mapped[int] = mapped_column(Integer, nullable=False)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    center_lat: Mapped[float] = mapped_column(Float, nullable=False)
    center_lon: Mapped[float] = mapped_column(Float, nullable=False)

    # spatial_index=False throughout: GeoAlchemy2 would otherwise create
    # GiST indexes as a DDL side effect of creating the column, which
    # Alembic cannot see and therefore proposes dropping on every
    # autogenerate. Declared explicitly below instead.
    centroid: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POLYGON", srid=4326, spatial_index=False), nullable=True
    )

    region: Mapped["Region"] = relationship(back_populates="cells")

    __table_args__ = (
        UniqueConstraint("region_id", "row", "col", name="uq_grid_cells_region_row_col"),
        CheckConstraint("row >= 0 AND col >= 0", name="row_col_nonnegative"),
        # Loading a frame means fetching all cells for a region ordered by
        # (row, col); this index makes that an index-only scan.
        Index("ix_grid_cells_region_row_col", "region_id", "row", "col"),
        Index("ix_grid_cells_geom", "geom", postgresql_using="gist"),
        Index("ix_grid_cells_centroid", "centroid", postgresql_using="gist"),
    )

    def __repr__(self) -> str:
        return f"<GridCell r{self.row}c{self.col}>"


class Variable(TimestampMixin, Base):
    """A measurable quantity (no2, ndvi, pm25, temperature, ...).

    ``canonical_unit`` is the unit values are stored in. Collectors are
    responsible for converting into it before writing -- storing mixed
    units in one column is the single easiest way to silently corrupt a
    model's training set.
    """

    __tablename__ = "variables"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_unit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "kind IN " + str(VARIABLE_KINDS), name="kind_valid"
        ),
    )

    def __repr__(self) -> str:
        return f"<Variable {self.slug}>"


class DataSource(TimestampMixin, Base):
    """An upstream provider we pull from.

    Recorded so that every observation can be traced back to a dataset and
    its licence, which matters when this work gets written up.
    """

    __tablename__ = "data_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    license: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<DataSource {self.slug}>"
