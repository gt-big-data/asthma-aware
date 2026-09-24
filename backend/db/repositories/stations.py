"""Point observations from ground monitoring sites.

This is the write path for the air quality, NWS weather and pollen
pipelines. Those collectors do not exist yet -- they are empty
placeholders on ``origin/backend_data_ingestion`` -- so this module is
written to be the thing they call, and the worked example in
db/README.md shows exactly how.

A collector's whole job reduces to:

1. fetch from the upstream API
2. ``upsert_station`` for each site
3. ``upsert_observations`` with a list of readings

Everything else -- conflict handling, run bookkeeping, geometry -- is
handled here.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Sequence

from geoalchemy2 import Geography
from sqlalchemy import cast, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from db.models import DataSource, Region, Station, StationObservation, Variable

_CHUNK_ROWS = 5000


def upsert_station(
    session: Session,
    *,
    source: DataSource,
    external_id: str,
    lat: float,
    lon: float,
    name: Optional[str] = None,
    region: Optional[Region] = None,
    elevation_m: Optional[float] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Station:
    """Get-or-update a monitoring site.

    The PostGIS point is derived from lat/lon here so callers never have
    to think about WKT or SRIDs -- and so the two can never disagree.
    """
    station = session.scalar(
        select(Station).where(
            Station.source_id == source.id, Station.external_id == external_id
        )
    )
    geom = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)

    if station is None:
        station = Station(
            source_id=source.id,
            external_id=external_id,
            name=name,
            lat=lat,
            lon=lon,
            geom=geom,
            region_id=region.id if region else None,
            elevation_m=elevation_m,
            extra=extra,
        )
        session.add(station)
    else:
        station.lat = lat
        station.lon = lon
        station.geom = geom
        if name is not None:
            station.name = name
        if region is not None:
            station.region_id = region.id
        if elevation_m is not None:
            station.elevation_m = elevation_m
        if extra is not None:
            station.extra = extra
    session.flush()
    return station


def upsert_observations(
    session: Session,
    readings: Sequence[Dict[str, Any]],
    *,
    run_id: Optional[int] = None,
) -> int:
    """Bulk-write station readings, ignoring duplicates.

    Each reading is a dict with keys::

        station_id     int    (from upsert_station)
        variable_id    int    (from reference.get_variable)
        observed_at    tz-aware datetime, UTC
        value          float or None
        unit           str, optional
        quality_flag   str, optional

    Re-fetching an overlapping time window is normal for hourly feeds --
    most publish a rolling window -- so conflicts update in place rather
    than erroring.

    Returns the number of rows sent.
    """
    if not readings:
        return 0

    payload = []
    for reading in readings:
        observed_at = reading["observed_at"]
        if observed_at.tzinfo is None:
            raise ValueError(
                f"observed_at must be timezone-aware (got naive {observed_at!r}). "
                f"Ground feeds report local time; convert to UTC before writing "
                f"or the data will not line up with satellite frames."
            )
        payload.append(
            {
                "station_id": reading["station_id"],
                "variable_id": reading["variable_id"],
                "observed_at": observed_at.astimezone(dt.timezone.utc),
                "value": reading.get("value"),
                "unit": reading.get("unit"),
                "quality_flag": reading.get("quality_flag"),
                "run_id": run_id,
            }
        )

    written = 0
    for start in range(0, len(payload), _CHUNK_ROWS):
        chunk = payload[start : start + _CHUNK_ROWS]
        stmt = pg_insert(StationObservation).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_station_observations_station_variable_time",
            set_={
                "value": stmt.excluded.value,
                "unit": stmt.excluded.unit,
                "quality_flag": stmt.excluded.quality_flag,
                "run_id": stmt.excluded.run_id,
            },
        )
        session.execute(stmt)
        written += len(chunk)
    return written


def get_stations(
    session: Session,
    *,
    source: Optional[DataSource] = None,
    region: Optional[Region] = None,
) -> List[Station]:
    stmt = select(Station)
    if source is not None:
        stmt = stmt.where(Station.source_id == source.id)
    if region is not None:
        stmt = stmt.where(Station.region_id == region.id)
    return list(session.scalars(stmt.order_by(Station.external_id)).all())


def stations_within(
    session: Session, *, lat: float, lon: float, radius_km: float
) -> List[Station]:
    """Sites within ``radius_km`` of a point.

    Uses ``ST_DWithin`` on the geography type so the distance is real
    metres on the sphere, not degrees -- at Atlanta's latitude a degree of
    longitude is ~30% shorter than a degree of latitude, so a naive
    bounding box in degrees is measurably wrong.
    """
    point = func.ST_SetSRID(func.ST_MakePoint(lon, lat), 4326)
    stmt = select(Station).where(
        func.ST_DWithin(
            cast(Station.geom, Geography),
            cast(point, Geography),
            radius_km * 1000.0,
        )
    )
    return list(session.scalars(stmt).all())


def observation_window(
    session: Session, station: Station, variable: Variable
) -> Optional[tuple]:
    """``(earliest, latest)`` timestamps held for a station/variable pair.

    Lets an incremental collector ask "what do I already have" and fetch
    only the gap instead of the whole history every run.
    """
    row = session.execute(
        select(
            func.min(StationObservation.observed_at),
            func.max(StationObservation.observed_at),
        ).where(
            StationObservation.station_id == station.id,
            StationObservation.variable_id == variable.id,
        )
    ).one()
    if row[0] is None:
        return None
    return (row[0], row[1])
