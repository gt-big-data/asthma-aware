"""Engine and session management.

Typical use in a script or collector::

    from db.session import session_scope

    with session_scope() as session:
        repositories.grid.upsert_frame(session, ...)

``session_scope`` commits on success and rolls back on any exception, so
a collector that dies halfway through a date range never leaves a
half-written frame behind.

In FastAPI routes, use ``get_session`` as a dependency instead.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from db import config

_engine: Optional[Engine] = None
_SessionFactory: Optional[sessionmaker] = None


def get_engine() -> Engine:
    """Return the process-wide engine, creating it on first use.

    Lazy so that importing db.session does not require DATABASE_URL --
    only actually talking to the database does.
    """
    global _engine
    if _engine is None:
        _engine = create_engine(
            config.get_database_url(),
            echo=config.SQL_ECHO,
            pool_size=config.POOL_SIZE,
            max_overflow=config.MAX_OVERFLOW,
            pool_recycle=config.POOL_RECYCLE_SECONDS,
            # Verify a pooled connection is alive before handing it out.
            # Hosted Postgres drops idle connections and the first
            # statement after that would otherwise fail.
            pool_pre_ping=True,
        )
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(), expire_on_commit=False, future=True
        )
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope around a series of operations."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency.

    Does not commit -- read paths do not need it, and write routes should
    commit explicitly so the failure is visible at the call site.
    """
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()


def reset_engine() -> None:
    """Drop the cached engine/factory.

    Only needed by tests that point at a different DATABASE_URL after
    this module has already been imported.
    """
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
