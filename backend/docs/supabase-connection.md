# Connect the development Supabase project

Work is on `backend/supabase-integration`. New branches in this repository use
the `backend/` prefix.

## Current state

On November 1, 2026, the development project's public signing-key endpoint was
verified, migrations 0001 and 0002 were applied, and Alembic found no model/schema
drift. The restricted `asthmaaware_runtime` login was provisioned with membership
in `asthmaaware_api`. Both operator and runtime connections passed TLS checks,
and runtime inspection confirmed forced RLS on all six application tables.

Live API/database checks passed for profile creation, symptom saving, identical
retries, and cross-user isolation using synthetic identities. The entire test
transaction was rolled back; no synthetic records remain. This did not exercise
a real Supabase user sign-in or frontend authentication.

The project URL, operator URI, and generated runtime URI are stored only in
ignored `backend/.env`. Do not copy that entire file to Cloud Run.

## Operator connection

1. Open the development project in Supabase and select **Connect**.
2. Copy an operator PostgreSQL URI into `MIGRATION_DATABASE_URL` in
   `backend/.env`, replacing the password placeholder locally. Use the session
   pooler URI when the direct IPv6 endpoint is not reachable.
3. Require TLS in the URI. Do not put the URI in chat, Git, or frontend configuration.
4. From `backend/`, run:

   ```powershell
   python -m scripts.supabase_preflight --mode operator
   ```

The check fetches public signing-key metadata and reads database metadata inside
a read-only transaction. It does not migrate, provision roles, or read symptom
records. Review the reported migration history before applying migrations.

## Migration and runtime connection

The [application contract](application-api.md) describes migration 0002 and the
private `app` schema. Review generated SQL, then apply the migration with the
operator connection. Provision a separate runtime login belonging to
`asthmaaware_api`; store its URI as `DATABASE_URL` locally and in Secret Manager
for deployment. Do not give the runtime login administrator, role-creation,
table-ownership, or RLS-bypass privileges.

Run `python -m scripts.supabase_preflight --mode runtime` afterward. This checks
TLS, role membership, private table presence, and forced RLS without querying
user records. The restricted runtime login intentionally cannot read the
operator's migration-version table; version verification is done in operator mode.

For the cloud deployment, put only the runtime `DATABASE_URL` in Secret Manager.
Configure `DB_POOL_SIZE=2` and `DB_MAX_OVERFLOW=0` for the initial small development
deployment, and bound instance/process counts to fit the database connection
budget. Keep `MIGRATION_DATABASE_URL` restricted to operator use. The current
runtime URI uses the session pooler; changing to transaction pooling also requires
reviewing the SQLAlchemy/psycopg configuration.

## Frontend integration

Frontend authentication will need the Supabase project URL and **publishable**
key, plus the development FastAPI URL. These are separate from database/operator
credentials. No database URI or Supabase secret/service-role key belongs in the app.
The frontend now includes sign-in and a Symptom Log tab. See
[frontend setup](../../Front-end/docs/auth-and-logging.md). Its project URL and
publishable key are stored in ignored `Front-end/.env.local`.

A dedicated test user passed real Supabase password sign-in and backend JWT,
profile, save/edit, retry, and conflict checks. The test database transaction was
rolled back and the authentication session signed out afterward. Browser layout
was checked on the signed-out screen; native device GPS needs device testing.
