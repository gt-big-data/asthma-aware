"""User-scoped application queries. The caller owns the transaction and RLS context."""

from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from db.models.application import ExposureSnapshot, OutboxEvent, Profile, SymptomLog, SymptomLogRevision


class RevisionConflict(ValueError):
    pass


def ensure_profile(session: Session, user_id: UUID, *, lock: bool = False) -> Profile:
    session.execute(insert(Profile).values(user_id=user_id, timezone="America/New_York").on_conflict_do_nothing())
    query = select(Profile).where(Profile.user_id == user_id)
    if lock:
        query = query.with_for_update()
    return session.scalars(query).one()


def local_today(now: datetime, timezone_name: str) -> date:
    return now.astimezone(ZoneInfo(timezone_name)).date()


def midnight_after(day: date, timezone_name: str) -> datetime:
    return datetime.combine(day + timedelta(days=1), time.min, tzinfo=ZoneInfo(timezone_name)).astimezone(timezone.utc)


def get_log(session: Session, user_id: UUID, day: date) -> SymptomLog | None:
    return session.scalar(select(SymptomLog).where(SymptomLog.user_id == user_id, SymptomLog.log_date == day))


def save_log(session: Session, user_id: UUID, day: date, values: dict, now: datetime) -> SymptomLog:
    # Serializes writes per user, including concurrent profile-timezone changes.
    profile = ensure_profile(session, user_id, lock=True)
    today = local_today(now, profile.timezone)
    if day > today or day < today - timedelta(days=5):
        raise ValueError("Logs can be saved for today and the previous five local dates")
    expected_revision = values.pop("expected_revision")
    record = get_log(session, user_id, day)
    if record is not None:
        if all(getattr(record, field) == value for field, value in values.items()):
            return record  # Retry-safe; no duplicate observation, revision, or event.
        if expected_revision != record.revision:
            raise RevisionConflict("This entry has changed; reload it and submit the current revision")
        for field, value in values.items():
            setattr(record, field, value)
        record.revision += 1
        record.updated_at = now
    else:
        if expected_revision != 0:
            raise RevisionConflict("No entry exists for the supplied revision")
        record = SymptomLog(
            user_id=user_id, log_date=day, timezone=profile.timezone,
            finalizes_at=midnight_after(day, profile.timezone), revision=1,
            created_at=now, updated_at=now, **values,
        )
        session.add(record)
    session.flush()
    session.add(SymptomLogRevision(
        log_id=record.id, revision=record.revision, user_id=user_id, severity=record.severity,
        context={
            "log_date": day.isoformat(), "timezone": record.timezone,
            "latitude": record.latitude, "longitude": record.longitude,
            "location_source": record.location_source,
            "finalizes_at": record.finalizes_at.isoformat(),
        },
    ))
    session.flush()
    session.add(OutboxEvent(
        user_id=user_id, log_id=record.id, log_revision=record.revision,
        available_at=max(now, record.finalizes_at),
    ))
    session.flush()
    return record


def has_exposure(session: Session, record: SymptomLog) -> bool:
    return bool(session.scalar(select(exists().where(
        ExposureSnapshot.user_id == record.user_id,
        ExposureSnapshot.log_id == record.id,
        ExposureSnapshot.log_revision == record.revision,
    ))))


def history(session: Session, user_id: UUID, *, start: date | None, end: date | None, before: date | None, limit: int):
    query = select(SymptomLog).where(SymptomLog.user_id == user_id)
    if start is not None:
        query = query.where(SymptomLog.log_date >= start)
    if end is not None:
        query = query.where(SymptomLog.log_date <= end)
    if before is not None:
        query = query.where(SymptomLog.log_date < before)
    return list(session.scalars(query.order_by(SymptomLog.log_date.desc()).limit(limit + 1)))


def exposure_keys(session: Session, user_id: UUID, records: list[SymptomLog]) -> set:
    if not records:
        return set()
    return set(session.execute(select(ExposureSnapshot.log_id, ExposureSnapshot.log_revision).where(
        ExposureSnapshot.user_id == user_id,
        tuple_(ExposureSnapshot.log_id, ExposureSnapshot.log_revision).in_([(row.id, row.revision) for row in records]),
    )).all())
