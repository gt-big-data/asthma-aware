"""Database connection settings, read from the environment.

The only required setting is DATABASE_URL. Everything else has a default
that works for local development.

We deliberately do not hardcode credentials anywhere in the repo: the
hosted Postgres connection string is shared out-of-band and lives in each
developer's untracked ``backend/.env``.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BACKEND_DIR / ".env"


def _load_dotenv() -> None:
    """Load backend/.env into os.environ if python-dotenv is installed.

    Made optional so that importing db.config never hard-fails in an
    environment that only installed the core requirements.
    """
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH, override=False)


_load_dotenv()


class DatabaseNotConfigured(RuntimeError):
    """Raised when DATABASE_URL is missing.

    Carries setup instructions rather than a bare message, because the
    most common time anyone sees this is their first five minutes on the
    project.
    """

    def __init__(self) -> None:
        super().__init__(
            "DATABASE_URL is not set.\n"
            "\n"
            "Copy backend/.env.example to backend/.env and fill in the\n"
            "connection string for the shared Postgres instance, e.g.\n"
            "\n"
            "  DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST:5432/postgres\n"
            "\n"
            "See backend/db/README.md for where to get that string."
        )


def _normalize_url(url: str) -> str:
    """Force the psycopg (v3) driver.

    Hosted providers hand out URLs starting with ``postgres://`` or
    ``postgresql://``. SQLAlchemy maps both to psycopg2, which we do not
    install, so we rewrite the scheme to be explicit.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def get_database_url(required: bool = True) -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        if required:
            raise DatabaseNotConfigured()
        return None
    return _normalize_url(url)


def is_configured() -> bool:
    """True when a DATABASE_URL is present.

    Lets callers degrade gracefully (fall back to files/live APIs) instead
    of crashing when someone has not set the database up yet.
    """
    return bool(os.environ.get("DATABASE_URL", "").strip())


# Echo every statement to stdout. Useful when debugging a slow upsert.
SQL_ECHO = os.environ.get("SQL_ECHO", "").lower() in {"1", "true", "yes"}

# Hosted Postgres (Supabase/Neon) closes idle connections fairly
# aggressively, and long-running ingestion jobs sit idle while waiting on
# an upstream API. Recycling below the provider timeout avoids handing a
# dead connection to the next statement.
POOL_RECYCLE_SECONDS = int(os.environ.get("DB_POOL_RECYCLE", "280"))
POOL_SIZE = int(os.environ.get("DB_POOL_SIZE", "5"))
MAX_OVERFLOW = int(os.environ.get("DB_MAX_OVERFLOW", "10"))

# Default region for CLIs and repository helpers that take an optional region.
DEFAULT_REGION = os.environ.get("DEFAULT_REGION", "atlanta")
