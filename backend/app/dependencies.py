"""Trusted per-request SQL context. Transaction-local settings cannot leak through pools."""

from datetime import datetime, timezone
from typing import Iterator
from uuid import UUID

from fastapi import Depends, HTTPException
from sqlalchemy import select, func, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.auth import get_current_user_id
from db.config import DatabaseNotConfigured
from db.session import get_session_factory


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_user_session(user_id: UUID = Depends(get_current_user_id)) -> Iterator[Session]:
    try:
        with get_session_factory()() as session:
            with session.begin():
                # Applies even if a development connection uses the table owner's login.
                session.execute(text("SET LOCAL ROLE asthmaaware_api"))
                session.execute(select(func.set_config("app.user_id", str(user_id), True)))
                yield session
    except DatabaseNotConfigured:
        raise HTTPException(503, "Application database is not configured") from None
    except SQLAlchemyError:
        raise HTTPException(503, "Application database is unavailable") from None
