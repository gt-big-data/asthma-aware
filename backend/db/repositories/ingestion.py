"""Run bookkeeping for collectors.

Every pipeline should wrap its work in ``run_scope`` so that what was
attempted, what failed and why ends up on record::

    with session_scope() as session:
        with run_scope(session, "satellite", region=region, source=source,
                       start=date, end=date, params=vars(args)) as run:
            ...
            run.rows_written += n
            record_issue(session, run, "no overpass", variable=no2,
                         observation_date=date)

The run is marked ``success``, ``partial`` (work done but issues logged)
or ``failed`` (an exception escaped) automatically.
"""

from __future__ import annotations

import datetime as dt
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import (
    DataSource,
    IngestionIssue,
    IngestionRun,
    RasterFile,
    Region,
    Variable,
)


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def start_run(
    session: Session,
    pipeline: str,
    *,
    region: Optional[Region] = None,
    source: Optional[DataSource] = None,
    start: Optional[dt.date] = None,
    end: Optional[dt.date] = None,
    params: Optional[Dict[str, Any]] = None,
) -> IngestionRun:
    run = IngestionRun(
        pipeline=pipeline,
        region_id=region.id if region else None,
        source_id=source.id if source else None,
        requested_start=start,
        requested_end=end,
        status="running",
        started_at=_utcnow(),
        rows_written=0,
        params=_jsonable(params),
    )
    session.add(run)
    session.flush()
    return run


def _jsonable(params: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Coerce argparse values into something JSONB accepts.

    Dates and Paths are the common offenders; without this, storing
    ``vars(args)`` raises at commit time rather than at the call site.
    """
    if params is None:
        return None
    out: Dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, (dt.date, dt.datetime)):
            out[key] = value.isoformat()
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        else:
            out[key] = str(value)
    return out


def finish_run(
    session: Session,
    run: IngestionRun,
    *,
    status: Optional[str] = None,
    error: Optional[str] = None,
) -> IngestionRun:
    """Close a run out.

    When ``status`` is not given it is inferred: any logged issue makes
    the run ``partial`` rather than ``success``, so a run that quietly
    skipped half its variables never looks clean in the run history.
    """
    if status is None:
        has_issues = session.scalar(
            select(IngestionIssue.id).where(IngestionIssue.run_id == run.id).limit(1)
        )
        status = "partial" if has_issues else "success"
    run.status = status
    run.error = error
    run.finished_at = _utcnow()
    session.flush()
    return run


def record_issue(
    session: Session,
    run: IngestionRun,
    reason: str,
    *,
    variable: Optional[Variable] = None,
    observation_date: Optional[dt.date] = None,
    kind: str = "missing",
) -> IngestionIssue:
    issue = IngestionIssue(
        run_id=run.id,
        variable_id=variable.id if variable else None,
        observation_date=observation_date,
        kind=kind,
        reason=reason,
    )
    session.add(issue)
    session.flush()
    return issue


@contextmanager
def run_scope(session: Session, pipeline: str, **kwargs) -> Iterator[IngestionRun]:
    """Start a run, and close it out however the block exits.

    On an exception the run is marked ``failed`` with the message stored,
    then the exception is re-raised -- the caller still sees the failure,
    but there is now a record of it that outlives the terminal session.
    """
    run = start_run(session, pipeline, **kwargs)
    try:
        yield run
    except Exception as exc:
        finish_run(session, run, status="failed", error=f"{type(exc).__name__}: {exc}")
        # Commit the failure record before unwinding; session_scope would
        # otherwise roll it back along with the partial data.
        session.commit()
        raise
    else:
        finish_run(session, run)


def record_raster_file(
    session: Session,
    *,
    region: Region,
    variable: Variable,
    observation_date: dt.date,
    path: str,
    run: Optional[IngestionRun] = None,
    source: Optional[DataSource] = None,
    source_date: Optional[dt.date] = None,
    unit: Optional[str] = None,
    scale_meters: Optional[float] = None,
    crs: Optional[str] = None,
    qa_filtering: Optional[str] = None,
    nodata_value: Optional[float] = None,
    size_bytes: Optional[int] = None,
    checksum_sha256: Optional[str] = None,
) -> RasterFile:
    """Upsert provenance for one archived GeoTIFF.

    Keyed on (region, variable, date) so re-downloading a day replaces the
    record instead of accumulating duplicates.
    """
    existing = session.scalar(
        select(RasterFile).where(
            RasterFile.region_id == region.id,
            RasterFile.variable_id == variable.id,
            RasterFile.observation_date == observation_date,
        )
    )
    fields = dict(
        run_id=run.id if run else None,
        source_id=source.id if source else None,
        source_date=source_date,
        path=path,
        unit=unit,
        scale_meters=scale_meters,
        crs=crs,
        qa_filtering=qa_filtering,
        nodata_value=nodata_value,
        size_bytes=size_bytes,
        checksum_sha256=checksum_sha256,
    )
    if existing is None:
        existing = RasterFile(
            region_id=region.id,
            variable_id=variable.id,
            observation_date=observation_date,
            **fields,
        )
        session.add(existing)
    else:
        for key, value in fields.items():
            setattr(existing, key, value)
    session.flush()
    return existing


def recent_runs(
    session: Session, *, pipeline: Optional[str] = None, limit: int = 20
) -> List[IngestionRun]:
    stmt = select(IngestionRun).order_by(IngestionRun.started_at.desc()).limit(limit)
    if pipeline:
        stmt = stmt.where(IngestionRun.pipeline == pipeline)
    return list(session.scalars(stmt).all())
