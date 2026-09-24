"""Database layer for AsthmaAware.

This package owns everything that touches Postgres:

    db/config.py        connection settings read from the environment
    db/session.py       engine + session factory, and the `session_scope()` helper
    db/base.py          declarative base shared by every table
    db/models/          the table definitions
    db/repositories/    the functions the rest of the backend actually calls

Application code and ingestion collectors should import from
``db.repositories`` rather than building queries by hand -- that keeps the
SQL in one place and lets the schema change without touching every caller.

See db/README.md for setup instructions.
"""
