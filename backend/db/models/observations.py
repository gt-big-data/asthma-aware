"""Measurements.

Two shapes of measurement, two tables:

``grid_observations``
    Gridded data on a region's raster grid -- everything from the
    satellite pipeline. One row per (cell, variable, date).

``station_observations``
    Point data from a fixed monitoring site -- ground air quality, NWS
    weather, pollen counts. One row per (station, variable, timestamp).

Ground data is hourly and irregular, satellite data is daily and dense,
so forcing them into one table would mean a mostly-NULL column set and
an index that serves neither well.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class GridObservation(Base):
    """One variable's value in one grid cell on one date.

    Volume: 5,376 cells x 5 variables = ~27k rows/day, ~10M rows/year for
    Atlanta. Comfortably within what a single Postgres table handles with
    the indexes below; no partitioning needed at this scale.

    ``value`` is NULL for no-data rather than a sentinel. The satellite
    client writes -9999.0 into masked pixels on export (see its
    ``NODATA_VALUE`` comment) and the collector translates that to NULL on
    the way in. This is deliberate: -9999 silently poisons any mean,
    min/max or scaler fit that forgets to filter it, and the existing
    ``build_latest_sequence.py`` does exactly that via ``np.nan_to_num``.
    NULL cannot be averaged by accident.
    """

    __tablename__ = "grid_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    grid_cell_id: Mapped[int] = mapped_column(
        ForeignKey("grid_cells.id", ondelete="CASCADE"), nullable=False
    )
    variable_id: Mapped[int] = mapped_column(
        ForeignKey("variables.id", ondelete="CASCADE"), nullable=False
    )
    observation_date: Mapped[dt.date] = mapped_column(Date, nullable=False)

    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Actual acquisition date, when it differs from observation_date
    # (MODIS NDVI composites -- see RasterFile.source_date).
    source_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)

    raster_file_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("raster_files.id", ondelete="SET NULL"), nullable=True
    )
    run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        # The upsert target: re-running a collector for a date overwrites
        # rather than duplicating, so pipelines are safely re-runnable.
        UniqueConstraint(
            "grid_cell_id",
            "variable_id",
            "observation_date",
            name="uq_grid_observations_cell_variable_date",
        ),
        # Serves the dominant query: "every cell of variable V on date D",
        # i.e. loading one frame. Also covers date-range scans per variable.
        Index(
            "ix_grid_observations_variable_date",
            "variable_id",
            "observation_date",
        ),
        # Serves the ML team's other access pattern: one cell's full time
        # series, for inspecting a pixel or fitting per-cell statistics.
        Index(
            "ix_grid_observations_cell_variable_date",
            "grid_cell_id",
            "variable_id",
            "observation_date",
        ),
    )

    def __repr__(self) -> str:
        return f"<GridObservation cell={self.grid_cell_id} var={self.variable_id} {self.observation_date}>"


class Station(TimestampMixin, Base):
    """A fixed ground monitoring site.

    Used by the air quality, NWS weather and pollen pipelines. A station
    is identified by (source, external id) because the same physical site
    can appear in more than one upstream feed under different ids.
    """

    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("data_sources.id", ondelete="CASCADE"), nullable=False
    )
    region_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )

    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="POINT", srid=4326, spatial_index=False), nullable=True
    )
    elevation_m: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Whatever else the upstream feed hands back. Kept rather than dropped
    # so a later pipeline change does not require re-fetching history.
    extra: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "source_id", "external_id", name="uq_stations_source_external_id"
        ),
        Index("ix_stations_geom", "geom", postgresql_using="gist"),
    )

    def __repr__(self) -> str:
        return f"<Station {self.external_id} {self.name!r}>"


class StationObservation(Base):
    """One variable's value at one station at one instant.

    ``observed_at`` is timezone-aware and stored in UTC. Ground feeds
    report in local time with varying DST handling; normalising on the way
    in is the only way these join cleanly against daily satellite frames.
    """

    __tablename__ = "station_observations"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)

    station_id: Mapped[int] = mapped_column(
        ForeignKey("stations.id", ondelete="CASCADE"), nullable=False
    )
    variable_id: Mapped[int] = mapped_column(
        ForeignKey("variables.id", ondelete="CASCADE"), nullable=False
    )
    observed_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # Recorded per-row as well as on the Variable, because ground feeds
    # are not consistent about units between sites and we would rather
    # store the truth than assume it.
    unit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quality_flag: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )

    station: Mapped["Station"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "station_id",
            "variable_id",
            "observed_at",
            name="uq_station_observations_station_variable_time",
        ),
        Index(
            "ix_station_observations_variable_time", "variable_id", "observed_at"
        ),
    )

    def __repr__(self) -> str:
        return f"<StationObservation station={self.station_id} {self.observed_at}>"
