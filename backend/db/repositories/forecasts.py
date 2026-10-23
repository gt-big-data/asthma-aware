"""Persisting model forecasts.

Replaces the ``_forecast_cache`` module global in
``app/services/map_service.py``. That cache has two problems this fixes:
it is lost on every restart (so the first request after a deploy pays for
three ConvLSTM passes), and it is per-process (so with more than one
uvicorn worker, two users can be served forecasts generated from
different input data with no way to tell).

Stored forecasts are also the only way to ever score the model: you
cannot measure 72h forecast error if the 72h forecast was never written
down.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Sequence

import numpy as np
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.models import Forecast, GridCell, ModelRun, Region, Variable
from db.repositories import reference

_CHUNK_ROWS = 5000


def create_model_run(
    session: Session,
    *,
    region: Region,
    model_name: str,
    model_version: Optional[str] = None,
    model_checksum: Optional[str] = None,
    feature_order: Optional[Sequence[str]] = None,
    scaler_version: Optional[str] = None,
    input_start_date: Optional[dt.date] = None,
    input_end_date: Optional[dt.date] = None,
    notes: Optional[str] = None,
) -> ModelRun:
    run = ModelRun(
        region_id=region.id,
        model_name=model_name,
        model_version=model_version,
        model_checksum=model_checksum,
        feature_order=list(feature_order) if feature_order else None,
        scaler_version=scaler_version,
        input_start_date=input_start_date,
        input_end_date=input_end_date,
        ran_at=dt.datetime.now(dt.timezone.utc),
        notes=notes,
    )
    session.add(run)
    session.flush()
    return run


def save_forecast_frame(
    session: Session,
    *,
    model_run: ModelRun,
    region: Region,
    variables: Sequence[Variable],
    horizon: str,
    frame: np.ndarray,
    frame_scaled: Optional[np.ndarray] = None,
    target_date: Optional[dt.date] = None,
) -> int:
    """Store one ``(C, H, W)`` predicted frame.

    ``frame`` is in canonical units (post inverse-transform) and
    ``frame_scaled`` is the raw model output. Keeping both means a
    suspicious forecast can be diagnosed without re-running inference:
    if the scaled values look sane and the unscaled ones do not, the bug
    is in the scaler, not the model.
    """
    expected = (len(variables), region.grid_rows, region.grid_cols)
    if frame.shape != expected:
        raise ValueError(f"Expected frame of shape {expected}, got {frame.shape}")
    if frame_scaled is not None and frame_scaled.shape != expected:
        raise ValueError(
            f"Expected frame_scaled of shape {expected}, got {frame_scaled.shape}"
        )

    cell_ids = reference.get_ordered_cell_ids(session, region)

    payload: List[Dict[str, Any]] = []
    for channel, variable in enumerate(variables):
        flat = np.asarray(frame[channel], dtype=np.float64).ravel()
        flat_scaled = (
            np.asarray(frame_scaled[channel], dtype=np.float64).ravel()
            if frame_scaled is not None
            else None
        )
        for index, cell_id in enumerate(cell_ids):
            value = float(flat[index])
            scaled = float(flat_scaled[index]) if flat_scaled is not None else None
            payload.append(
                {
                    "model_run_id": model_run.id,
                    "grid_cell_id": cell_id,
                    "variable_id": variable.id,
                    "horizon": horizon,
                    "target_date": target_date,
                    "value": value if np.isfinite(value) else None,
                    "value_scaled": (
                        scaled if scaled is not None and np.isfinite(scaled) else None
                    ),
                }
            )

    written = 0
    for start in range(0, len(payload), _CHUNK_ROWS):
        chunk = payload[start : start + _CHUNK_ROWS]
        stmt = pg_insert(Forecast).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_forecasts_run_cell_variable_horizon",
            set_={
                "value": stmt.excluded.value,
                "value_scaled": stmt.excluded.value_scaled,
                "target_date": stmt.excluded.target_date,
            },
        )
        session.execute(stmt)
        written += len(chunk)
    return written


def latest_model_run(session: Session, region: Region) -> Optional[ModelRun]:
    return session.scalar(
        select(ModelRun)
        .where(ModelRun.region_id == region.id)
        .order_by(ModelRun.ran_at.desc())
        .limit(1)
    )


def load_forecast_frame(
    session: Session,
    *,
    model_run: ModelRun,
    region: Region,
    variables: Sequence[Variable],
    horizon: str,
) -> Optional[np.ndarray]:
    """Rebuild a ``(C, H, W)`` array from stored forecasts."""
    var_index = {v.id: i for i, v in enumerate(variables)}
    rows = session.execute(
        select(
            Forecast.variable_id,
            GridCell.row,
            GridCell.col,
            Forecast.value,
        )
        .join(GridCell, GridCell.id == Forecast.grid_cell_id)
        .where(
            Forecast.model_run_id == model_run.id,
            Forecast.horizon == horizon,
            Forecast.variable_id.in_(list(var_index)),
        )
    ).all()

    if not rows:
        return None

    out = np.full(
        (len(variables), region.grid_rows, region.grid_cols), np.nan, dtype=np.float32
    )
    for variable_id, r, c, value in rows:
        if value is not None:
            out[var_index[variable_id], r, c] = value
    return out


def load_forecast_cells(
    session: Session,
    *,
    model_run: ModelRun,
    region: Region,
    variables: Sequence[Variable],
    horizon: str,
) -> List[Dict[str, Any]]:
    """Forecast as a list of cell dicts, matching the map API response.

    Emits the same keys as ``app/services/array_parser.py`` -- row, col,
    lat, lon and one key per variable slug -- so a DB-backed route is a
    drop-in for the current one. ``lat``/``lon`` use the upper-left-corner
    convention the frontend already expects (see ``GridCell``).
    """
    var_index = {v.id: v.slug for v in variables}
    rows = session.execute(
        select(
            GridCell.row,
            GridCell.col,
            GridCell.lat,
            GridCell.lon,
            Forecast.variable_id,
            Forecast.value,
        )
        .join(GridCell, GridCell.id == Forecast.grid_cell_id)
        .where(
            Forecast.model_run_id == model_run.id,
            Forecast.horizon == horizon,
            Forecast.variable_id.in_(list(var_index)),
        )
        .order_by(GridCell.row, GridCell.col)
    ).all()

    cells: Dict[tuple, Dict[str, Any]] = {}
    for r, c, lat, lon, variable_id, value in rows:
        cell = cells.setdefault(
            (r, c), {"row": r, "col": c, "lat": lat, "lon": lon}
        )
        cell[var_index[variable_id]] = value
    return [cells[key] for key in sorted(cells)]
