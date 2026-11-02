# V1 profiles and symptom logging

This slice implements authenticated profiles and daily symptom logging. Cloud
deployment, exposure matching, queue dispatch, and model fitting are separate work.
No shared Supabase migration is applied automatically.

## Decisions

- Severity is an integer from 1 (no symptoms today) to 5 (very severe, unable to
  do daily activities). Labels for 2, 3, and 4 still need product wording.
- One active entry per user/local date; create/edit today and the previous five
  dates. Older entries remain readable. Future dates cannot be logged.
- The frontend submits current coordinates or a user-selected representative
  location and identifies which method was used. This is not measured all-day exposure.
- Today's entry is provisional and **automatically becomes final at local
  midnight**, as requested. Users may still edit a final entry within the five-day
  window; editing creates a new revision and invalidates its old exposure match.
- Midnight is computed in the profile timezone at first save and retained with
  the entry. Changing profile timezone does not move existing dates or boundaries.
  DST days can be 23 or 25 hours. New profiles default to America/New_York; the
  frontend should set the user's IANA timezone during onboarding.
- Automatic finalization uses the last submitted severity, even if submitted
  early in the day. That is a product rule, not proof of a complete daily symptom
  measurement; model evaluation must account for this limitation.

## Authentication and database setup

Install `requirements/dev.txt`. Set `SUPABASE_URL` to the HTTPS project origin.
V1 verifies Supabase ES256/RS256 access tokens against its JWKS endpoint, including
signature, issuer, audience, expiry, user role, and UUID subject. Legacy HS256
projects must migrate signing keys before using this implementation. There is no
demo bypass for V1. The existing `/dashboard` remains a separate fixture demo.

Application tables live in the private `app` schema, not the public environmental
schema. **Do not add `app` to Supabase's exposed Data API schemas.** The application
profile UUID is the verified Supabase subject; this migration does not alter or
add a cross-schema foreign key to managed `auth.users`. Auth account deletion
cleanup is not implemented and must be coordinated before offering deletion.

Use an operator connection in `MIGRATION_DATABASE_URL` to review and apply Alembic
migrations. Migration 0002 creates the schema, constraints, RLS policies, and the
NOLOGIN `asthmaaware_api` group. The operator needs schema/role creation privileges.
It grants the migration operator membership for development/testing. Provision a
separate runtime login with membership in that group through a secure operator
session; supply its connection string as `DATABASE_URL`. Passwords are not stored
in migrations. Never mount migration credentials in Cloud Run.

Each V1 transaction sets the restricted group role and a transaction-local
`app.user_id` from the verified JWT. Forced RLS isolates users even when a query
forgets its ownership filter. API code also filters queries by the user UUID.
Missing context grants no rows. The runtime role cannot write exposure snapshots
or personalization revisions. Future workers need a separately designed role.
GCP service identities do not replace PostgreSQL roles or user JWT verification.

## Routes

All V1 routes require `Authorization: Bearer <Supabase access token>`.

| Method | Path | Behavior |
| --- | --- | --- |
| GET | `/v1/users/me` | Get or lazily create the caller's profile |
| PATCH | `/v1/users/me` | Update name and/or IANA timezone |
| PUT | `/v1/symptoms/{log_date}` | Create or edit a dated log |
| GET | `/v1/symptoms/{log_date}` | Read the caller's log; 404 when absent |
| GET | `/v1/symptoms/today` | Resolve today from profile timezone and read its log |
| GET | `/v1/symptoms` | Descending date history with pagination |
| GET | `/health` | Process liveness; no JWT or dependencies required |

Example PUT body for a new dated log:

```json
{
  "severity": 3,
  "latitude": 33.749,
  "longitude": -84.388,
  "location_source": "current",
  "expected_revision": 0
}
```

Use `selected` for a manually selected location. Responses include the record ID,
date, severity, location, timezone, revision, UTC `finalizes_at`, computed
`provisional`/`final` status, `pending`/`matched` exposure status, and timestamps.
For edits, send the last returned revision as `expected_revision`. A conflicting
edit returns 409; refetch before retrying. Identical retries return the current
entry without creating another revision/event. All successful PUTs return 200.
Unknown fields, including request-supplied user IDs or training fields, are rejected.

History accepts optional `start`, `end` (inclusive), `before` (exclusive), and
`limit` (1-100, default 30). Follow `next_before` until null. Authentication errors
return 401; unconfigured/unavailable authentication or database services return
503. Invalid fields/date windows return 422.

## Durable logging and future workers

The save transaction contains the active entry, an immutable audit revision, and
an outbox event. Any failure rolls back all three. Audit revisions are not extra
training observations: only the active revision may be used for a refit.

Today's event is not available until the stored midnight boundary; backfilled
entries are available immediately. Status becomes final from time alone, so a
late dispatcher cannot leave the UI stuck in provisional state. A dispatcher
still needs to run to process due events. Before matching or fitting, the future
worker must verify finality, current revision, and model/feature compatibility.
Out-of-date events must not overwrite newer results. Exposure matching failures
must leave the saved log intact and pending.

Snapshot and personalization tables are scaffolding: no exposures or personal
weights are fabricated. The API never waits for fitting. The daily-to-hourly
prediction contract remains a separate ML validation decision.

## Verification

Tests use local signing keys and a disposable Postgres/PostGIS database. Set
`TEST_DATABASE_URL` and `TEST_DATABASE_ALLOW_RESET=true` only for a disposable
database. Integration fixtures run real Alembic downgrades/upgrades and remove
the application/environmental tables at teardown. Never point them at Supabase.
The cluster-wide NOLOGIN group is deliberately retained on downgrade because
external runtime logins may depend on it.

The November 1, 2026 local verification run passed 170 tests, including all 28
historical PostGIS tests and the new authentication/application tests. It used
a disposable PostgreSQL 17/PostGIS container, exercised real migration round
trips and a restricted login, and found no model/schema drift. Three existing
dependency deprecation warnings remain. Hosted Supabase login/JWKS connectivity,
project privileges, and cloud deployment were not exercised.

Reference: [Supabase JWT verification](https://supabase.com/docs/guides/auth/jwts).
