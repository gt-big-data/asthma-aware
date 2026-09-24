"""Stable, versioned normalisation constants.

Background
----------
``scripts/build_latest_sequence.py`` currently does this::

    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(arr.reshape(-1, arr.shape[-1])).reshape(arr.shape)

The scaler is fitted fresh on whatever is on disk, every run. So the day
a new observation arrives with a higher NO2 reading than anything seen
before, *every historical value shifts down* -- and the model, which was
trained under the old scaling, is now being fed inputs from a different
distribution. Nothing errors. The forecasts just quietly get worse.

The fix is to fit once, write the constants down, and reuse them::

    fit_minmax(session, region, variable, version="v1", activate=True)
    ...
    scaler = get_active(session, region, variable)
    x = scaler.transform(raw_array)
    raw_again = scaler.inverse_transform(model_output)

Retraining the model means writing a *new* version and activating it, so
old forecasts stay interpretable under the constants that produced them.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

import numpy as np
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from db.models import Region, ScalerParams, Variable
from db.repositories import grid


class ScalerNotFound(LookupError):
    def __init__(self, region_slug: str, variable_slug: str) -> None:
        super().__init__(
            f"No active scaler for variable {variable_slug!r} in region "
            f"{region_slug!r}. Fit one with:\n"
            f"  python scripts/fit_scalers.py --region {region_slug}"
        )


@dataclass(frozen=True)
class Scaler:
    """An immutable view of one variable's normalisation constants.

    Detached from the ORM deliberately: transform/inverse_transform get
    called inside tight loops and in code that has no session.
    """

    variable_slug: str
    method: str
    version: str
    fit_min: Optional[float]
    fit_max: Optional[float]
    fit_mean: Optional[float]
    fit_std: Optional[float]

    def transform(self, values: np.ndarray) -> np.ndarray:
        """Scale to the model's input range, preserving NaN."""
        arr = np.asarray(values, dtype=np.float32)
        if self.method == "minmax":
            span = (self.fit_max or 0.0) - (self.fit_min or 0.0)
            if span == 0:
                # A constant variable carries no information; map it to
                # zero rather than dividing by zero and producing inf.
                return np.zeros_like(arr)
            return (arr - self.fit_min) / span
        if self.method == "standard":
            std = self.fit_std or 0.0
            if std == 0:
                return np.zeros_like(arr)
            return (arr - (self.fit_mean or 0.0)) / std
        raise ValueError(f"Unknown scaler method {self.method!r}")

    def inverse_transform(self, values: np.ndarray) -> np.ndarray:
        """Map model output back into the variable's real unit."""
        arr = np.asarray(values, dtype=np.float32)
        if self.method == "minmax":
            span = (self.fit_max or 0.0) - (self.fit_min or 0.0)
            return arr * span + (self.fit_min or 0.0)
        if self.method == "standard":
            return arr * (self.fit_std or 0.0) + (self.fit_mean or 0.0)
        raise ValueError(f"Unknown scaler method {self.method!r}")


def _to_scaler(row: ScalerParams, variable_slug: str) -> Scaler:
    return Scaler(
        variable_slug=variable_slug,
        method=row.method,
        version=row.version,
        fit_min=row.fit_min,
        fit_max=row.fit_max,
        fit_mean=row.fit_mean,
        fit_std=row.fit_std,
    )


def fit_minmax(
    session: Session,
    region: Region,
    variable: Variable,
    *,
    version: str,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    activate: bool = False,
    notes: Optional[str] = None,
) -> ScalerParams:
    """Compute min/max over stored observations and record them.

    The aggregate runs in Postgres (see ``grid.value_bounds``), so this
    costs one query regardless of how many years of history exist -- no
    loading rasters into memory.

    NULL cells are excluded by SQL aggregate semantics, so cloud-masked
    pixels cannot drag the bounds. This is a genuine improvement over the
    current ``np.nan_to_num`` approach, which converts masked pixels to
    0.0 and then includes those zeros in the fit.
    """
    vmin, vmax, count = grid.value_bounds(
        session, region, variable, start=start, end=end
    )
    if count == 0:
        raise ValueError(
            f"No observations for {variable.slug!r} in region {region.slug!r}"
            + (f" between {start} and {end}" if start or end else "")
            + ". Ingest data before fitting a scaler."
        )

    params = session.scalar(
        select(ScalerParams).where(
            ScalerParams.region_id == region.id,
            ScalerParams.variable_id == variable.id,
            ScalerParams.version == version,
        )
    )
    fields = dict(
        method="minmax",
        fit_min=vmin,
        fit_max=vmax,
        fit_start_date=start,
        fit_end_date=end,
        sample_count=count,
        notes=notes,
    )
    if params is None:
        params = ScalerParams(
            region_id=region.id,
            variable_id=variable.id,
            version=version,
            **fields,
        )
        session.add(params)
    else:
        for key, value in fields.items():
            setattr(params, key, value)
    session.flush()

    if activate:
        activate_version(session, region, variable, version)
    return params


def activate_version(
    session: Session, region: Region, variable: Variable, version: str
) -> None:
    """Make one version the active scaler, deactivating the others.

    Done in two statements inside the caller's transaction, so there is
    never a moment where two versions are active or none is.
    """
    session.execute(
        update(ScalerParams)
        .where(
            ScalerParams.region_id == region.id,
            ScalerParams.variable_id == variable.id,
        )
        .values(is_active=False)
    )
    session.execute(
        update(ScalerParams)
        .where(
            ScalerParams.region_id == region.id,
            ScalerParams.variable_id == variable.id,
            ScalerParams.version == version,
        )
        .values(is_active=True)
    )
    session.flush()


def get_active(session: Session, region: Region, variable: Variable) -> Scaler:
    row = session.scalar(
        select(ScalerParams).where(
            ScalerParams.region_id == region.id,
            ScalerParams.variable_id == variable.id,
            ScalerParams.is_active.is_(True),
        )
    )
    if row is None:
        raise ScalerNotFound(region.slug, variable.slug)
    return _to_scaler(row, variable.slug)


def get_active_many(
    session: Session, region: Region, variables: Sequence[Variable]
) -> List[Scaler]:
    """Active scalers for several variables, in the order given.

    Order matches the channel order of the model tensor, so the result can
    be zipped straight against the channel axis.
    """
    return [get_active(session, region, v) for v in variables]


def transform_sequence(
    sequence: np.ndarray, scalers: Sequence[Scaler]
) -> np.ndarray:
    """Apply per-channel scalers to a ``(T, C, H, W)`` array.

    Raises when the channel count does not match the scaler count --
    which is the failure mode that would otherwise scale NO2 by NDVI's
    constants and produce plausible-looking nonsense.
    """
    if sequence.ndim != 4:
        raise ValueError(f"Expected a (T, C, H, W) array, got shape {sequence.shape}")
    if sequence.shape[1] != len(scalers):
        raise ValueError(
            f"Array has {sequence.shape[1]} channels but {len(scalers)} scalers "
            f"were given ({[s.variable_slug for s in scalers]}). These must "
            f"correspond one-to-one and in the same order."
        )
    out = np.empty_like(sequence, dtype=np.float32)
    for channel, scaler in enumerate(scalers):
        out[:, channel] = scaler.transform(sequence[:, channel])
    return out


def inverse_transform_frame(
    frame: np.ndarray, scalers: Sequence[Scaler]
) -> np.ndarray:
    """Invert per-channel scaling on a ``(C, H, W)`` model output frame."""
    if frame.ndim != 3:
        raise ValueError(f"Expected a (C, H, W) array, got shape {frame.shape}")
    if frame.shape[0] != len(scalers):
        raise ValueError(
            f"Frame has {frame.shape[0]} channels but {len(scalers)} scalers "
            f"were given ({[s.variable_slug for s in scalers]})."
        )
    out = np.empty_like(frame, dtype=np.float32)
    for channel, scaler in enumerate(scalers):
        out[channel] = scaler.inverse_transform(frame[channel])
    return out
