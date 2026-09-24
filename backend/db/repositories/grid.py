"""Reading and writing gridded observations as numpy arrays.

This is the module the ML and ingestion code actually uses. It hides the
fact that a "frame" is 5,376 rows in Postgres and presents it as the
``(rows, cols)`` array everyone already thinks in.

The two functions that matter:

``upsert_frame``    numpy array -> database, idempotently
``load_sequence``   database -> the ``(T, C, 56, 96)`` tensor the ConvLSTM wants

Missing data is ``NULL`` in the database and ``np.nan`` in arrays -- never
0 and never -9999. See ``GridObservation.value`` for why.
"""

from __future__ import annotations

import datetime as dt
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
from sqlalchemy import Select, and_, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.models import GridCell, GridObservation, Region, Variable
from db.repositories import reference

# Rows per INSERT statement. Postgres caps a statement at 65535 bind
# parameters; at ~7 parameters per row, 5,000 rows stays well under that
# while keeping the number of round trips low.
_CHUNK_ROWS = 5000


def _as_nullable(value: float) -> Optional[float]:
    """NaN/inf -> None, so no-data never enters the database as a number."""
    if value is None:
        return None
    fvalue = float(value)
    if not np.isfinite(fvalue):
        return None
    return fvalue


def upsert_frame(
    session: Session,
    region: Region,
    variable: Variable,
    observation_date: dt.date,
    values: np.ndarray,
    *,
    source_date: Optional[dt.date] = None,
    raster_file_id: Optional[int] = None,
    run_id: Optional[int] = None,
    nodata_value: Optional[float] = None,
) -> int:
    """Write one ``(rows, cols)`` array for one variable and date.

    Re-running the same ingest overwrites in place rather than
    duplicating, so a collector can safely be re-run over a date range it
    has already covered -- which is the normal way to recover from a
    partial failure.

    ``nodata_value`` (e.g. the satellite pipeline's -9999.0) is treated as
    missing in addition to NaN/inf.

    Returns the number of rows written.
    """
    expected = (region.grid_rows, region.grid_cols)
    if values.shape != expected:
        raise ValueError(
            f"Expected array of shape {expected} for region {region.slug!r}, "
            f"got {values.shape}"
        )

    cell_ids = reference.get_ordered_cell_ids(session, region)
    flat = np.asarray(values, dtype=np.float64).ravel()

    if nodata_value is not None:
        flat = np.where(np.isclose(flat, nodata_value), np.nan, flat)

    payload = [
        {
            "grid_cell_id": cell_id,
            "variable_id": variable.id,
            "observation_date": observation_date,
            "value": _as_nullable(value),
            "source_date": source_date,
            "raster_file_id": raster_file_id,
            "run_id": run_id,
        }
        for cell_id, value in zip(cell_ids, flat)
    ]

    written = 0
    for start in range(0, len(payload), _CHUNK_ROWS):
        chunk = payload[start : start + _CHUNK_ROWS]
        stmt = pg_insert(GridObservation).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_grid_observations_cell_variable_date",
            set_={
                "value": stmt.excluded.value,
                "source_date": stmt.excluded.source_date,
                "raster_file_id": stmt.excluded.raster_file_id,
                "run_id": stmt.excluded.run_id,
            },
        )
        session.execute(stmt)
        written += len(chunk)

    return written


def load_frame(
    session: Session,
    region: Region,
    variable: Variable,
    observation_date: dt.date,
) -> Optional[np.ndarray]:
    """Return one ``(rows, cols)`` float32 array, or None if the date is absent.

    Cells with no row, and cells whose value is NULL, both come back as
    ``np.nan``. "Absent" means *no rows at all* for that variable/date --
    distinct from a frame that exists but is partly masked.
    """
    rows = session.execute(
        select(GridCell.row, GridCell.col, GridObservation.value)
        .join(GridObservation, GridObservation.grid_cell_id == GridCell.id)
        .where(
            GridCell.region_id == region.id,
            GridObservation.variable_id == variable.id,
            GridObservation.observation_date == observation_date,
        )
    ).all()

    if not rows:
        return None

    frame = np.full((region.grid_rows, region.grid_cols), np.nan, dtype=np.float32)
    for r, c, value in rows:
        if value is not None:
            frame[r, c] = value
    return frame


def load_sequence(
    session: Session,
    region: Region,
    variables: Sequence[Variable],
    dates: Sequence[dt.date],
) -> np.ndarray:
    """Return a ``(len(dates), len(variables), rows, cols)`` array.

    ``variables`` order is the channel order -- for the current model that
    is ``[so2, ndvi, no2]`` from ``app.constants.FEATURE_ORDER``. Getting
    it wrong silently feeds the model transposed channels, so pass the
    constant rather than hardcoding a list.

    Dates with no data are all-NaN planes rather than an error, so the
    caller can decide whether a gap is fatal. Use ``complete_dates`` first
    if you need to guarantee no gaps.
    """
    n_t, n_c = len(dates), len(variables)
    out = np.full(
        (n_t, n_c, region.grid_rows, region.grid_cols), np.nan, dtype=np.float32
    )
    if n_t == 0 or n_c == 0:
        return out

    date_index = {d: i for i, d in enumerate(dates)}
    var_index = {v.id: i for i, v in enumerate(variables)}

    # One query for the whole cube rather than T*C per-frame queries.
    rows = session.execute(
        select(
            GridObservation.observation_date,
            GridObservation.variable_id,
            GridCell.row,
            GridCell.col,
            GridObservation.value,
        )
        .join(GridCell, GridCell.id == GridObservation.grid_cell_id)
        .where(
            GridCell.region_id == region.id,
            GridObservation.variable_id.in_(list(var_index)),
            GridObservation.observation_date.in_(list(dates)),
        )
    ).all()

    for obs_date, variable_id, r, c, value in rows:
        if value is None:
            continue
        out[date_index[obs_date], var_index[variable_id], r, c] = value

    return out


def available_dates(
    session: Session,
    region: Region,
    variable: Variable,
    *,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
) -> List[dt.date]:
    """Every date this variable has data for, ascending."""
    stmt = (
        select(GridObservation.observation_date)
        .join(GridCell, GridCell.id == GridObservation.grid_cell_id)
        .where(
            GridCell.region_id == region.id,
            GridObservation.variable_id == variable.id,
        )
        .distinct()
        .order_by(GridObservation.observation_date)
    )
    if start is not None:
        stmt = stmt.where(GridObservation.observation_date >= start)
    if end is not None:
        stmt = stmt.where(GridObservation.observation_date <= end)
    return list(session.scalars(stmt).all())


def complete_dates(
    session: Session,
    region: Region,
    variables: Sequence[Variable],
    *,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    min_coverage: float = 1.0,
) -> List[dt.date]:
    """Dates where *every* requested variable has enough non-NULL cells.

    The ConvLSTM needs four consecutive complete frames, so "which dates
    are actually usable" is a question the sequence builder has to ask
    before every run. Answering it in SQL beats loading candidate frames
    and checking them in Python.

    ``min_coverage`` is the fraction of the grid that must be non-NULL for
    a frame to count. Satellite data is frequently partly cloud-masked, so
    demanding 1.0 can legitimately return nothing; 0.5-0.8 is a more
    realistic threshold for Sentinel-5P.
    """
    if not variables:
        return []

    total_cells = region.grid_rows * region.grid_cols
    required = int(np.ceil(total_cells * min_coverage))

    stmt = (
        select(
            GridObservation.observation_date,
            GridObservation.variable_id,
            func.count(GridObservation.value).label("present"),
        )
        .join(GridCell, GridCell.id == GridObservation.grid_cell_id)
        .where(
            GridCell.region_id == region.id,
            GridObservation.variable_id.in_([v.id for v in variables]),
        )
        .group_by(GridObservation.observation_date, GridObservation.variable_id)
        .having(func.count(GridObservation.value) >= required)
    )
    if start is not None:
        stmt = stmt.where(GridObservation.observation_date >= start)
    if end is not None:
        stmt = stmt.where(GridObservation.observation_date <= end)

    per_date: Dict[dt.date, set] = {}
    for obs_date, variable_id, _present in session.execute(stmt).all():
        per_date.setdefault(obs_date, set()).add(variable_id)

    wanted = {v.id for v in variables}
    return sorted(d for d, got in per_date.items() if got >= wanted)


def latest_complete_dates(
    session: Session,
    region: Region,
    variables: Sequence[Variable],
    count: int,
    *,
    require_consecutive: bool = False,
    min_coverage: float = 1.0,
) -> List[dt.date]:
    """The most recent ``count`` usable dates, ascending.

    With ``require_consecutive=True`` the returned dates must be adjacent
    calendar days. That matters because the model was trained on a daily
    cadence: feeding it four frames spanning three weeks is not an error
    the code can detect, but it is not a forecast either.

    Raises ValueError when there are not enough usable dates, rather than
    silently returning a short sequence.
    """
    dates = complete_dates(
        session, region, variables, min_coverage=min_coverage
    )
    if len(dates) < count:
        raise ValueError(
            f"Need {count} complete frames for region {region.slug!r} covering "
            f"{[v.slug for v in variables]}, but only {len(dates)} exist. "
            f"Run the satellite collector for more dates, or lower min_coverage."
        )

    window = dates[-count:]
    if require_consecutive:
        expected = [
            window[-1] - dt.timedelta(days=i) for i in range(count - 1, -1, -1)
        ]
        if window != expected:
            raise ValueError(
                f"The {count} most recent complete frames are not consecutive "
                f"days: {[d.isoformat() for d in window]}. The model expects a "
                f"daily cadence. Backfill the gap or pass "
                f"require_consecutive=False if you accept the discontinuity."
            )
    return window


def value_bounds(
    session: Session,
    region: Region,
    variable: Variable,
    *,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
) -> Tuple[Optional[float], Optional[float], int]:
    """``(min, max, non_null_count)`` for a variable over a date window.

    This is what makes stable normalisation possible: the scaler can be
    fitted with one aggregate query over all history instead of loading
    every raster into memory. NULLs are excluded by SQL's aggregate
    semantics, so masked pixels cannot skew the bounds.
    """
    stmt = (
        select(
            func.min(GridObservation.value),
            func.max(GridObservation.value),
            func.count(GridObservation.value),
        )
        .join(GridCell, GridCell.id == GridObservation.grid_cell_id)
        .where(
            GridCell.region_id == region.id,
            GridObservation.variable_id == variable.id,
        )
    )
    if start is not None:
        stmt = stmt.where(GridObservation.observation_date >= start)
    if end is not None:
        stmt = stmt.where(GridObservation.observation_date <= end)

    vmin, vmax, count = session.execute(stmt).one()
    return vmin, vmax, count or 0


def delete_frame(
    session: Session,
    region: Region,
    variable: Variable,
    observation_date: dt.date,
) -> int:
    """Remove one frame. Returns rows deleted."""
    cell_ids = select(GridCell.id).where(GridCell.region_id == region.id)
    result = session.execute(
        GridObservation.__table__.delete().where(
            and_(
                GridObservation.variable_id == variable.id,
                GridObservation.observation_date == observation_date,
                GridObservation.grid_cell_id.in_(cell_ids),
            )
        )
    )
    return result.rowcount or 0
