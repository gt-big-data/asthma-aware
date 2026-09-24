"""Census ACS data: write once per year, read on every request.

Replaces the fetch-on-cache-miss behaviour in
``app/services/socioeconomic_service.py``. That version requests the
entire national ZCTA table (~33k rows across ~45 variables) from the
Census API and discards all but the ~21 Atlanta ZCTAs -- on every cold
start, and only if the Census API happens to be reachable at that moment.

ACS 5-year estimates are published annually. Fetching them once at
ingest time and serving from Postgres is both faster and immune to the
upstream being down.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Iterable, List, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from db.models import Region, SocioeconomicRecord, Zcta

# Columns that carry an actual indicator, as opposed to bookkeeping.
# Kept as a list so upsert_record can accept a plain dict straight from
# the existing transform code without naming each field twice.
INDICATOR_FIELDS = (
    "population",
    "population_density",
    "median_age",
    "children_under_18_rate",
    "seniors_65_plus_rate",
    "median_housing_age",
    "median_household_income",
    "median_gross_rent",
    "median_home_value",
    "poverty_rate",
    "bachelor_degree_or_higher_rate",
    "no_vehicle_households_rate",
    "severe_rent_burden_rate",
    "overcrowded_housing_rate",
    "unemployment_rate",
)


def upsert_zcta(
    session: Session,
    zcta_code: str,
    *,
    region: Optional[Region] = None,
    name: Optional[str] = None,
    area_sq_miles: Optional[float] = None,
) -> Zcta:
    zcta = session.scalar(select(Zcta).where(Zcta.zcta == zcta_code))
    if zcta is None:
        zcta = Zcta(
            zcta=zcta_code,
            region_id=region.id if region else None,
            name=name,
            area_sq_miles=area_sq_miles,
        )
        session.add(zcta)
    else:
        if region is not None:
            zcta.region_id = region.id
        if name is not None:
            zcta.name = name
        if area_sq_miles is not None:
            zcta.area_sq_miles = area_sq_miles
    session.flush()
    return zcta


def upsert_record(
    session: Session,
    *,
    zcta: Zcta,
    dataset_year: int,
    source: str,
    indicators: Dict[str, Any],
    raw: Optional[Dict[str, Any]] = None,
    run_id: Optional[int] = None,
) -> SocioeconomicRecord:
    """Write one ZCTA-year row.

    ``indicators`` may contain extra keys (``zipcode``, ``area_sq_miles``)
    -- the existing transform emits them -- and they are ignored here
    rather than raising, so the caller can pass its dict through
    untouched.
    """
    record = session.scalar(
        select(SocioeconomicRecord).where(
            SocioeconomicRecord.zcta_id == zcta.id,
            SocioeconomicRecord.dataset_year == dataset_year,
        )
    )
    fields = {
        name: indicators.get(name)
        for name in INDICATOR_FIELDS
        if name in indicators
    }
    if record is None:
        record = SocioeconomicRecord(
            zcta_id=zcta.id,
            dataset_year=dataset_year,
            source=source,
            raw=raw,
            run_id=run_id,
            fetched_at=dt.date.today(),
            **fields,
        )
        session.add(record)
    else:
        record.source = source
        record.run_id = run_id
        record.fetched_at = dt.date.today()
        if raw is not None:
            record.raw = raw
        for key, value in fields.items():
            setattr(record, key, value)
    session.flush()
    return record


def get_records(
    session: Session,
    *,
    region: Optional[Region] = None,
    dataset_year: Optional[int] = None,
    zcta_codes: Optional[Sequence[str]] = None,
) -> List[SocioeconomicRecord]:
    """Records for a region/year, ZCTA-sorted.

    With no ``dataset_year`` the most recent year present is used, so
    callers do not have to know which vintage has been ingested.
    """
    if dataset_year is None:
        dataset_year = latest_dataset_year(session, region=region)
        if dataset_year is None:
            return []

    stmt = (
        select(SocioeconomicRecord)
        .join(Zcta, Zcta.id == SocioeconomicRecord.zcta_id)
        .where(SocioeconomicRecord.dataset_year == dataset_year)
        .order_by(Zcta.zcta)
    )
    if region is not None:
        stmt = stmt.where(Zcta.region_id == region.id)
    if zcta_codes:
        stmt = stmt.where(Zcta.zcta.in_(list(zcta_codes)))
    return list(session.scalars(stmt).all())


def latest_dataset_year(
    session: Session, *, region: Optional[Region] = None
) -> Optional[int]:
    stmt = select(SocioeconomicRecord.dataset_year).order_by(
        SocioeconomicRecord.dataset_year.desc()
    )
    if region is not None:
        stmt = stmt.join(Zcta, Zcta.id == SocioeconomicRecord.zcta_id).where(
            Zcta.region_id == region.id
        )
    return session.scalar(stmt.limit(1))


def record_to_dict(record: SocioeconomicRecord) -> Dict[str, Any]:
    """Shape a row like the existing API response.

    Field names match ``SocioeconomicRecord`` in
    ``app/models/response_models.py`` exactly, so a DB-backed service can
    return these without the frontend noticing any change.
    """
    out: Dict[str, Any] = {
        "zipcode": record.zcta.zcta,
        "area_sq_miles": record.zcta.area_sq_miles,
    }
    for name in INDICATOR_FIELDS:
        out[name] = getattr(record, name)
    return out
