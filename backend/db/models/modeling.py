"""Model-facing tables: scaler parameters, model runs, stored forecasts.

The ConvLSTM in ``app/ml/`` consumes a normalised ``(4, 3, 56, 96)``
tensor and emits a ``(3, 56, 96)`` frame. These tables hold the two
things that pipeline currently has nowhere to put: the normalisation
constants, and the predictions.
"""

from __future__ import annotations

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
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

SCALER_METHODS = ("minmax", "standard")


class ScalerParams(TimestampMixin, Base):
    """Frozen normalisation constants for one variable in one region.

    This table exists to fix a real bug.
    ``scripts/build_latest_sequence.py`` calls ``MinMaxScaler.fit_transform``
    over the whole raster stack every time it runs, so the normalisation
    is re-derived from whatever data happens to be on disk that day. Each
    new observation shifts min/max, which shifts every historical value,
    which means the model is served inputs scaled differently from the
    ones it was trained on -- silently, with no error and no obviously
    wrong output.

    The fix is to fit once over an explicit date window, record the
    constants here, mark them active, and reuse them forever after. If the
    model is retrained, write a new ``version`` and flip ``is_active``;
    old forecasts remain interpretable because the constants that produced
    them are still on record.

    ``fit_start_date``/``fit_end_date`` document the window the constants
    came from, so a future retrain can reproduce them exactly.
    """

    __tablename__ = "scaler_params"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), nullable=False
    )
    variable_id: Mapped[int] = mapped_column(
        ForeignKey("variables.id", ondelete="CASCADE"), nullable=False
    )

    version: Mapped[str] = mapped_column(String(64), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False, default="minmax")

    # minmax: the observed bounds. standard: mean/std go in the same slots.
    fit_min: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_max: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_mean: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    fit_std: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    fit_start_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    fit_end_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    sample_count: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "region_id",
            "variable_id",
            "version",
            name="uq_scaler_params_region_variable_version",
        ),
        CheckConstraint("method IN " + str(SCALER_METHODS), name="method_valid"),
        Index("ix_scaler_params_active", "region_id", "variable_id", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<ScalerParams var={self.variable_id} {self.version}>"


class ModelRun(TimestampMixin, Base):
    """One inference pass that produced a set of forecasts.

    ``feature_order`` is stored per-run rather than read from
    ``app/constants.py`` at display time. The channel order (so2, ndvi,
    no2) is load-bearing -- the model's output channels are meaningless
    without it -- and a run made under a different order must stay
    readable after the constant changes.
    """

    __tablename__ = "model_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    region_id: Mapped[int] = mapped_column(
        ForeignKey("regions.id", ondelete="CASCADE"), nullable=False
    )

    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    model_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # sha256 of the .pt file, so a forecast can be tied to exact weights.
    model_checksum: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    feature_order: Mapped[Optional[list]] = mapped_column(JSONB, nullable=True)
    scaler_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # The window of observed frames fed in.
    input_start_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)
    input_end_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)

    ran_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    forecasts: Mapped[List["Forecast"]] = relationship(
        back_populates="model_run", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_model_runs_region_ran_at", "region_id", "ran_at"),)

    def __repr__(self) -> str:
        return f"<ModelRun {self.model_name} {self.ran_at}>"


class Forecast(Base):
    """A predicted value for one cell, variable and horizon.

    Persisting these replaces the module-level ``_forecast_cache`` global
    in ``app/services/map_service.py``, which is lost on every restart and
    not shared between workers -- so under more than one uvicorn worker,
    two users can currently get forecasts generated from different data.

    Both the scaled and unscaled values are kept: ``value`` is in the
    variable's canonical unit and is what the API serves, while
    ``value_scaled`` is the raw model output, which is what you want when
    debugging whether a problem is in the model or in the inverse
    transform.
    """

    __tablename__ = "forecasts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    model_run_id: Mapped[int] = mapped_column(
        ForeignKey("model_runs.id", ondelete="CASCADE"), nullable=False
    )
    grid_cell_id: Mapped[int] = mapped_column(
        ForeignKey("grid_cells.id", ondelete="CASCADE"), nullable=False
    )
    variable_id: Mapped[int] = mapped_column(
        ForeignKey("variables.id", ondelete="CASCADE"), nullable=False
    )

    horizon: Mapped[str] = mapped_column(String(16), nullable=False)
    target_date: Mapped[Optional[dt.date]] = mapped_column(Date, nullable=True)

    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    value_scaled: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    model_run: Mapped["ModelRun"] = relationship(back_populates="forecasts")

    __table_args__ = (
        UniqueConstraint(
            "model_run_id",
            "grid_cell_id",
            "variable_id",
            "horizon",
            name="uq_forecasts_run_cell_variable_horizon",
        ),
        Index("ix_forecasts_run_horizon", "model_run_id", "horizon"),
    )

    def __repr__(self) -> str:
        return f"<Forecast {self.horizon} cell={self.grid_cell_id}>"
