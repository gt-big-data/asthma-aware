"""Read-only checks before applying migrations or connecting the frontend.

Run from backend/: python -m scripts.supabase_preflight --mode operator
Use --mode runtime after migration and provisioning the restricted login.
Credentials come from the ignored backend/.env, never command-line arguments.
"""

from __future__ import annotations

import argparse
import json
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.pool import NullPool

from app.auth import ALGORITHMS
from app.config import Settings
from db import config

HEAD = "0002_application"
TABLES = {"profiles", "symptom_logs", "symptom_log_revisions", "exposure_snapshots", "personalization_revisions", "outbox_events"}


def check_signing_keys(project_url: str) -> dict:
    """Fetch only public key metadata; never authenticate a real user."""
    from urllib.request import urlopen

    parsed = urlparse(project_url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Set SUPABASE_URL to the HTTPS project origin")
    with urlopen(project_url.rstrip("/") + "/auth/v1/.well-known/jwks.json", timeout=10) as response:
        payload = json.load(response)
    keys = payload.get("keys", []) if isinstance(payload, dict) else []
    supported = [key for key in keys if isinstance(key, dict) and key.get("kid") and key.get("alg") in ALGORITHMS]
    if not supported:
        raise ValueError("No supported ES256/RS256 signing key is published; check Supabase signing-key setup")
    return {"supported_keys": len(supported)}


def check_database(connection, mode: str) -> dict:
    """Inspect metadata only, inside a read-only transaction."""
    connection.execute(text("SET TRANSACTION READ ONLY"))
    identity = connection.execute(text("""
        SELECT current_user AS role, rolcreaterole AS can_create_roles,
               rolsuper AS superuser, rolbypassrls AS bypass_rls
        FROM pg_roles WHERE rolname = current_user
    """)).mappings().one()
    encrypted = connection.scalar(text("SELECT ssl FROM pg_stat_ssl WHERE pid = pg_backend_pid()"))
    if not encrypted:
        raise ValueError("Database connection is not using TLS; configure SSL in the connection string")
    # The runtime login intentionally has no access to the operator's version table.
    revisions = None
    if mode == "operator":
        version_exists = connection.scalar(text("SELECT to_regclass('public.alembic_version') IS NOT NULL"))
        revisions = list(connection.scalars(text("SELECT version_num FROM public.alembic_version"))) if version_exists else []
    tables = set(connection.scalars(text("SELECT tablename FROM pg_tables WHERE schemaname = 'app'")))
    result = {"database_role": identity["role"], "tls": True, "migration_revisions": revisions, "application_tables": sorted(tables)}
    if mode == "operator":
        if revisions not in ([], ["0001_initial_schema"], [HEAD]):
            raise ValueError("Unexpected migration revision; review the database history before applying migrations")
        if tables and revisions != [HEAD]:
            raise ValueError("Existing app tables do not match the recorded revision; review before migrating")
        if revisions != [HEAD] and not (identity["can_create_roles"] or identity["superuser"]):
            raise ValueError("Migration operator needs role-creation permission for migration 0002")
        result["next_step"] = "Application migration is already applied" if revisions == [HEAD] else "Review generated SQL, then apply Alembic migrations"
    else:
        if identity["superuser"] or identity["bypass_rls"] or identity["can_create_roles"]:
            raise ValueError("Use a restricted runtime login, not operator/administrator credentials")
        if tables != TABLES:
            raise ValueError("Expected application migration/tables are not present")
        member = connection.scalar(text("SELECT pg_has_role(current_user, 'asthmaaware_api', 'MEMBER')"))
        if not member:
            raise ValueError("Runtime login needs membership in asthmaaware_api")
        owns_tables = connection.scalar(text("""
            SELECT count(*) FROM pg_tables WHERE schemaname = 'app' AND tableowner = current_user
        """))
        if owns_tables:
            raise ValueError("Runtime login must not own the application tables")
        connection.execute(text("SET LOCAL ROLE asthmaaware_api"))
        # Do not query actual symptom/profile rows. Inspect policy metadata only.
        protected = connection.scalar(text("""
            SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE n.nspname = 'app' AND c.relkind = 'r' AND c.relrowsecurity AND c.relforcerowsecurity
        """))
        if protected != len(TABLES):
            raise ValueError("All application tables must have enabled and forced RLS")
        result["next_step"] = "Ready for a test-user API smoke test"
    return result


def run(mode: str, *, project_url: str, database_url: str) -> dict:
    if not project_url:
        raise ValueError("SUPABASE_URL is not configured in backend/.env")
    if not database_url:
        variable = "MIGRATION_DATABASE_URL" if mode == "operator" else "DATABASE_URL"
        raise ValueError(f"{variable} is not configured in backend/.env")
    try:
        url = make_url(config._normalize_url(database_url))
    except Exception:
        raise ValueError("Invalid database connection string") from None
    if url.get_backend_name() != "postgresql":
        raise ValueError("A PostgreSQL connection string is required")
    keys = check_signing_keys(project_url)
    engine = create_engine(url, poolclass=NullPool, connect_args={"connect_timeout": 10})
    try:
        with engine.connect() as connection:
            with connection.begin():
                report = check_database(connection, mode)
    finally:
        engine.dispose()
    return {"mode": mode, "signing_keys": keys, **report}


def main(argv=None) -> int:
    import os

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["operator", "runtime"], default="operator")
    args = parser.parse_args(argv)
    variable = "MIGRATION_DATABASE_URL" if args.mode == "operator" else "DATABASE_URL"
    try:
        report = run(args.mode, project_url=Settings.from_env().supabase_url, database_url=os.getenv(variable, "").strip())
    except ValueError as error:
        print("Preflight failed: " + str(error))
        return 1
    except Exception:
        print("Preflight failed: could not verify the signing-key endpoint or database. Check connection settings, permissions, and availability.")
        return 1
    print(json.dumps(report, indent=2))
    print("Read-only checks completed. No migration or user-data query was executed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
