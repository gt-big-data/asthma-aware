"""Ingestion bookkeeping: runs, per-variable issues, archived raster files.

Together these replace the ``metadata.json`` the satellite collector
currently writes next to each day's GeoTIFFs. That file already records
source, unit, scale, CRS, QA filtering and a ``missing`` list with
reasons -- all genuinely useful provenance that is currently invisible to
anyone who did not run the collector themselves.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
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

RUN_STATUSES = ("running", "success", "partial", "failed")
ISSUE_KINDS = ("missing", "error", "qa_rejected", "skipped")


class IngestionRun(Base):
    """One invocation of one collector.

    Every row written by a pipeline carries this run's id, so "where did
    this number come from and when" is always answerable, and a bad run
    can be deleted wholesale.

    ``status`` is ``partial`` when some variables succeeded and others did
    not -- which is the normal case for satellite data, where a given day
    may simply have no usable overpass.
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pipeline: Mapped[str] = mapped_column(String(64), nullable=False)
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )
    region_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )

    requested_start: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    requested_end: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Whatever the CLI was invoked with, so a run is reproducible.
    params: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    issues: Mapped[List["IngestionIssue"]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )
    raster_files: Mapped[List["RasterFile"]] = relationship(back_populates="run")

    __table_args__ = (
        CheckConstraint("status IN " + str(RUN_STATUSES), name="status_valid"),
        Index("ix_ingestion_runs_pipeline_started", "pipeline", "started_at"),
    )

    def __repr__(self) -> str:
        return f"<IngestionRun {self.pipeline} {self.status}>"


class IngestionIssue(Base):
    """Something a run could not fetch, and why.

    This is the ``missing`` array from metadata.json, promoted to real
    rows. It matters more than it looks: the ConvLSTM needs four
    *consecutive* frames, so knowing exactly which days are absent -- and
    whether that is "no satellite overpass" or "our API key expired" --
    is the difference between a gap you can interpolate and a bug you
    need to fix.
    """

    __tablename__ = "ingestion_issues"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="CASCADE"), nullable=False
    )
    variable_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("variables.id", ondelete="SET NULL"), nullable=True
    )
    observation_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="missing")
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    run: Mapped["IngestionRun"] = relationship(back_populates="issues")

    __table_args__ = (
        CheckConstraint("kind IN " + str(ISSUE_KINDS), name="kind_valid"),
        Index("ix_ingestion_issues_variable_date", "variable_id", "observation_date"),
    )


class RasterFile(TimestampMixin, Base):
    """Provenance for one archived GeoTIFF.

    The pixel values also land in ``grid_observations`` resampled onto the
    56x96 model grid, but the GeoTIFF keeps the native resolution
    (~1113m for Sentinel-5P, 250m for MODIS) so the downsample is never
    lossy in a way we cannot undo.

    ``source_date`` is not redundant with ``observation_date``: MODIS NDVI
    is a 16-day composite, so a frame requested for the 20th may actually
    be imagery from the 5th. The satellite client already tracks this and
    it must not be lost.
    """

    __tablename__ = "raster_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), nullable=False
    )
    variable_id: Mapped[int] = mapped_column(
        ForeignKey("variables.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("data_sources.id", ondelete="SET NULL"), nullable=True
    )

    observation_date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    source_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)

    path: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    scale_meters: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    crs: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    qa_filtering: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    nodata_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    checksum_sha256: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    run: Mapped[Optional["IngestionRun"]] = relationship(back_populates="raster_files")

    __table_args__ = (
        UniqueConstraint(
            "region_id",
            "variable_id",
            "observation_date",
            name="uq_raster_files_region_variable_date",
        ),
        Index("ix_raster_files_variable_date", "variable_id", "observation_date"),
    )

    def __repr__(self) -> str:
        return f"<RasterFile {self.path}>"
