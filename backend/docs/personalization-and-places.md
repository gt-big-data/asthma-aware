# Personalization task and Places infrastructure

Implementation only: no cloud queue/service/API was created, enabled or called,
and no PostgreSQL migration was applied in this task. ML is intentionally absent.

## API contract

`POST /v1/personalization/tasks` requires the existing verified Supabase user JWT:

```json
{"log_id":"UUID","log_revision":1,"idempotency_key":"UUID"}
```

The referenced log must belong to the verified subject and the requested revision
must be current. Extra fields, including `user_id`, are rejected. Successful
submission returns 202 with `id`, log ID/revision, status, reason, attempts,
availability and timestamps. `GET /v1/personalization/tasks/{id}` returns only a
record belonging to that user. Both use private/no-store caching. Missing rows
return 404; revision/key conflict 409; absent infrastructure/provider errors 503.

The API commits `pending_dispatch` before the Cloud Tasks RPC, uses a deterministic
task name, and transitions to `queued` after acceptance. A timeout/connection error
leaves the durable pending row. Retrying the same request/name reconciles ambiguous
enqueue outcomes (`AlreadyExists` is success). There is one durable task per log
revision, so different idempotency keys cannot enqueue duplicate work for that
revision. It does not automatically enqueue on every symptom write: the existing
symptom outbox remains intact, and explicit task submission is the current trigger.
No background outbox sweeper has been deployed or enabled. Reconciliation of an
orphan pending row currently requires the client/operator to retry submission.

Tasks are scheduled no earlier than the referenced log's stored finalization time.
Queue bodies contain only `task_id`; the worker loads ownership from the database.
No symptom severity, location, JWT, email or other patient data is embedded in tasks.
The transport performs one RPC attempt, with 10s timeout and a 60s dispatch deadline.
Queue retry/rate limits are deployment configuration, not algorithmic retries.

## Worker contract and ML integration boundary

Entrypoint: `uvicorn app.worker:app --host 0.0.0.0 --port $PORT`.

- `/health` is process liveness only.
- `POST /internal/tasks/personalization` accepts exactly `{ "task_id": "UUID" }`.
- Cloud Run must be private; only the task dispatch service account gets invoker.
- Application verification also checks Google's signature, exact configured
  audience, issuer, verified email and expected dispatch service account email.
  Google certificates are cached for at most five minutes; fetches have 5s timeout.
  Header names claiming to be Cloud Tasks are never accepted as authentication.
- A row lock plus state check makes redelivery idempotent. Missing/not-due/database
  failures return non-2xx for bounded queue retry. Successful processing currently
  writes `blocked_ml_unavailable`, reason `ML integration is not configured`, and
  returns `result: null` with HTTP 200 so absent ML does not cause a retry storm.
- No exposure, weights, risk score or prediction is manufactured. A future ML team
  integration must define input revisions, artifact/model versions, result schema,
  transactional result writes and a new versioned task/reprocess lifecycle. The
  current blocked task is terminal; do not relabel it as successful inference.

## Database and credentials

Migration `0004_personalization_tasks` adds private `app.personalization_tasks`,
composite log ownership/revision foreign keys, unique log revision, timestamps,
status, reason, attempts and optional queue name. RLS is enabled and forced.

- `asthmaaware_api` can SELECT/INSERT/UPDATE only within its transaction-local
  `app.user_id` policy. User ID comes from JWT verification, not a task body.
- `asthmaaware_worker` is a NOLOGIN/NOSUPERUSER/NOBYPASSRLS group with SELECT/UPDATE
  only on task records. It has no grant to read patient symptom/profile tables or
  write personalization revisions. A future ML input access design is separate.
- Public and Supabase browser roles are explicitly denied access to the new table.
- API `DATABASE_URL` and worker-only `WORKER_DATABASE_URL` must be separate,
  restricted login credentials with appropriate group membership. Connections
  require PostgreSQL and TLS (`sslmode=require`, verify-ca or verify-full), 5s
  connection timeout and 10s statement timeout. No connection string is logged.
- Operator credentials are not mounted in API/worker containers. Migration creates
  group roles and grants the operator group membership for isolated tests, not an
  application login or password. Existing unsafe named worker roles are rejected.

Environment defaults fail closed:

```dotenv
TASKS_ENABLED=false
CLOUD_PROJECT=
TASKS_LOCATION=
TASKS_QUEUE=
PERSONALIZATION_WORKER_URL=
TASKS_SERVICE_ACCOUNT_EMAIL=
WORKER_AUDIENCE=
WORKER_SERVICE_ACCOUNT_EMAIL=
WORKER_DATABASE_URL=
PLACES_ENABLED=false
PLACES_API_KEY=
```

Worker audience must be the exact approved HTTPS service origin and match the
Cloud Tasks OIDC audience. Until the actual URI/identity is known, leave dispatch
disabled. Empty identity config never disables verification.

## Google Places

`POST /v1/places/search` also requires the Supabase user JWT. Body:

```json
{"query":"Atlanta","latitude":33.75,"longitude":-84.39}
```

Coordinates are optional but must appear together, be finite and globally valid.
The text query is stripped and limited to 2–120 characters. User-controlled URLs,
API keys, field masks or pagination are not accepted. Backend calls only Places
Text Search (New), returning at most five place IDs, display names, coordinates
and provider attributions. No sensitive health data is submitted.

Calls are disabled unless both `PLACES_ENABLED=true` and a server-side key are set.
The fixed field mask is
`places.id,places.displayName,places.location,places.attributions`; it can trigger
the Text Search Pro SKU, so enabling it requires a separately approved cost review.
Key restrictions should permit only Places API (New) and the intended server usage.
The key belongs in Secret Manager, never an Expo variable or response.

There is no Places-content cache or response storage. Results include attribution
metadata for the consuming UI to display per Google's policies. Requests have a
5s timeout and no automatic retries. Rate-limit responses become 429 with
`Retry-After: 60`; provider errors/malformed values become sanitized 503 responses.
Per-instance safety limits are at most five requests per user and 30 total per
minute. They are not a distributed billing cap: keep the initial API deployment
max instances at one, and add centralized enforcement before scaling Places usage.
No claims of a particular free SKU allowance are made.

## Verification and remaining acceptance

Offline tests use a fake Cloud Tasks client, httpx MockTransport and real local
RSA-signed JWT verification with fixture Google public keys. They exercise request
validation, no-call disabled states, deterministic names, ambiguity recovery,
claimed identity rejection, worker unavailable/no-result output and Places limits.
An opt-in disposable PostgreSQL test covers semantic request deduplication,
idempotent worker redelivery and user-scoped status; it is skipped without a test
database. Real queue OIDC delivery, actual PostgreSQL RLS/role behavior, live Places,
API-key restrictions and deployment resource limits remain unverified.

Primary references used:

- [Cloud Tasks HTTP targets and OIDC](https://docs.cloud.google.com/tasks/docs/creating-http-target-tasks)
- [Places Text Search requests and field masks](https://developers.google.com/maps/documentation/places/web-service/text-search)
