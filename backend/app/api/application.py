"""Authenticated V1 profiles and daily symptom logs."""

from datetime import date, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.auth import get_current_user_id
from app.dependencies import get_user_session, utc_now
from app.models.application import ProfilePatch, ProfileResponse, SymptomHistory, SymptomResponse, SymptomWrite
from db.repositories import application as repository

router = APIRouter(prefix="/v1", tags=["Application"])
User = Annotated[UUID, Depends(get_current_user_id)]
# Commit failures must be reported before the response is sent.
Database = Annotated[Session, Depends(get_user_session, scope="function")]
Clock = Annotated[datetime, Depends(utc_now)]


def render_log(session: Session, record, now: datetime, *, matched: bool | None = None) -> SymptomResponse:
    if matched is None:
        matched = repository.has_exposure(session, record)
    return SymptomResponse(
        **{field: getattr(record, field) for field in (
            "id", "log_date", "severity", "latitude", "longitude", "location_source",
            "timezone", "revision", "finalizes_at", "created_at", "updated_at",
        )},
        status="final" if now >= record.finalizes_at else "provisional",
        exposure_status="matched" if matched else "pending",
    )


@router.get("/users/me", response_model=ProfileResponse)
def profile(user: User, session: Database):
    return ProfileResponse.model_validate(repository.ensure_profile(session, user))


@router.patch("/users/me", response_model=ProfileResponse)
def update_profile(body: ProfilePatch, user: User, session: Database):
    record = repository.ensure_profile(session, user, lock=True)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(record, field, value)
    session.flush()
    return ProfileResponse.model_validate(record)


@router.get("/symptoms/today", response_model=SymptomResponse)
def today_log(user: User, session: Database, now: Clock):
    profile = repository.ensure_profile(session, user)
    day = repository.local_today(now, profile.timezone)
    return find_log(day, user, session, now)


@router.get("/symptoms", response_model=SymptomHistory)
def symptom_history(
    user: User, session: Database, now: Clock,
    start: date | None = None, end: date | None = None, before: date | None = None,
    limit: int = Query(30, ge=1, le=100),
):
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "start must not be after end")
    rows = repository.history(session, user, start=start, end=end, before=before, limit=limit)
    matches = repository.exposure_keys(session, user, rows[:limit])
    return SymptomHistory(
        items=[render_log(session, record, now, matched=(record.id, record.revision) in matches) for record in rows[:limit]],
        next_before=rows[limit - 1].log_date if len(rows) > limit else None,
    )


@router.get("/symptoms/{log_date}", response_model=SymptomResponse)
def find_log(log_date: date, user: User, session: Database, now: Clock):
    record = repository.get_log(session, user, log_date)
    if record is None:
        raise HTTPException(404, "No symptom entry for this date")
    return render_log(session, record, now)


@router.put("/symptoms/{log_date}", response_model=SymptomResponse)
def save_log(log_date: date, body: SymptomWrite, user: User, session: Database, now: Clock):
    try:
        record = repository.save_log(session, user, log_date, body.model_dump(), now)
    except repository.RevisionConflict as error:
        raise HTTPException(409, str(error)) from error
    except ValueError as error:
        raise HTTPException(422, str(error)) from error
    return render_log(session, record, now)
