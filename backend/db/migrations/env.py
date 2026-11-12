"""Alembic environment.

Connection settings come from ``db.config`` (i.e. the DATABASE_URL in
backend/.env), not from alembic.ini -- we never want a credential
committed to the repo.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the `backend/` directory importable so `import db` resolves no
# matter which directory alembic was invoked from.
BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from db import config as db_config  # noqa: E402
from db.models import Base  # noqa: E402  (registers every table)

config = context.config
# ConfigParser treats percent-encoded passwords as interpolation expressions.
database_url = config.attributes.get("database_url") or db_config.get_migration_database_url()
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata

# PostGIS installs its own tables and indexes into the same schema.
# Without this filter, autogenerate proposes dropping spatial_ref_sys on
# every single migration.
POSTGIS_TABLES = {"spatial_ref_sys", "geography_columns", "geometry_columns"}


def include_object(obj, name, type_, reflected, compare_to):
    # Never propose dropping unrelated tables from a shared Supabase database.
    if type_ == "table" and reflected and compare_to is None:
        return False
    if type_ == "table" and name in POSTGIS_TABLES:
        return False
    # GeoAlchemy2 creates the spatial index itself as a side effect of
    # creating the geometry column, so alembic must not also manage it.
    if type_ == "index" and name is not None and name.startswith("idx_") and name.endswith("_geom"):
        return False
    return True


def include_name(name, type_, parent_names):
    if type_ == "schema":
        return name in (None, "app")
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of running it (``alembic upgrade --sql``)."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=include_object,
        compare_type=True,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    supplied = config.attributes.get("connection")
    if supplied is not None:
        configure_connection(supplied)
        return
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        configure_connection(connection)


def configure_connection(connection) -> None:
    context.configure(
        connection=connection, target_metadata=target_metadata,
        include_object=include_object, compare_type=True,
        include_schemas=True, include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
