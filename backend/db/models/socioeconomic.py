"""Census ACS socioeconomic data, by ZIP Code Tabulation Area.

Areal rather than gridded, and updated once a year rather than daily, so
it gets its own pair of tables instead of sharing the grid schema.

This replaces the current behaviour in
``app/services/socioeconomic_service.py``, which fetches the *entire*
national ZCTA table from the Census API on every cold ``lru_cache`` miss
and then throws away all but ~21 rows -- a multi-megabyte request on
every cold start, and a hard dependency on the Census API being up at
request time.
"""

from __future__ import annotations

import datetime as dt
from typing import Optional

from geoalchemy2 import Geometry
from sqlalchemy import (
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin


class Zcta(TimestampMixin, Base):
    """A ZIP Code Tabulation Area.

    ZCTAs are Census constructs and do not map one-to-one onto USPS ZIP
    codes -- the existing service notes this and is deliberately
    ZCTA-first. Same convention here.

    ``geom`` is nullable because the boundary shapefile is a separate
    download from the ACS tables; populate it later to enable
    point-in-polygon joins (e.g. attributing a monitoring station or a
    grid cell to a ZCTA).
    """

    __tablename__ = "zctas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zcta: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    region_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("regions.id", ondelete="SET NULL"), nullable=True
    )

    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    area_sq_miles: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    geom: Mapped[Optional[object]] = mapped_column(
        Geometry(geometry_type="MULTIPOLYGON", srid=4326, spatial_index=False),
        nullable=True,
    )

    __table_args__ = (Index("ix_zctas_geom", "geom", postgresql_using="gist"),)

    def __repr__(self) -> str:
        return f"<Zcta {self.zcta}>"


class SocioeconomicRecord(TimestampMixin, Base):
    """One ZCTA's derived ACS indicators for one dataset year.

    Columns mirror ``SocioeconomicRecord`` in
    ``app/models/response_models.py`` one-for-one, so the API response can
    be built straight off a row without a translation layer.

    ``raw`` keeps the untransformed ACS variables for the row. The derived
    rates here are lossy (a rate of NULL cannot tell you whether the
    numerator or the denominator was missing), and re-deriving from raw
    beats re-fetching a year-old vintage from the Census API.
    """

    __tablename__ = "socioeconomic_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    zcta_id: Mapped[int] = mapped_column(
        ForeignKey("zctas.id", ondelete="CASCADE"), nullable=False
    )
    dataset_year: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(128), nullable=False)
    run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ingestion_runs.id", ondelete="SET NULL"), nullable=True
    )
    fetched_at: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)

    population: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    population_density: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    median_age: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    children_under_18_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    seniors_65_plus_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    median_housing_age: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    median_household_income: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    median_gross_rent: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    median_home_value: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    poverty_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    bachelor_degree_or_higher_rate: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    no_vehicle_households_rate: Mapped[Optional[float]] = mapped_column(
        Float, nullable=True
    )
    severe_rent_burden_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    overcrowded_housing_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unemployment_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    raw: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    zcta: Mapped["Zcta"] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "zcta_id", "dataset_year", name="uq_socioeconomic_records_zcta_year"
        ),
        Index("ix_socioeconomic_records_year", "dataset_year"),
    )

    def __repr__(self) -> str:
        return f"<SocioeconomicRecord zcta={self.zcta_id} {self.dataset_year}>"
