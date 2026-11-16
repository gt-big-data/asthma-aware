# Google Cloud/backend implementation plan

Started 2026-11-16 19:53 UTC. Hard deadline 22:53 UTC for all agents together.
Local implementation only: no deployments, enabled schedules, paid resources,
production writes, Git merges/pushes or ML implementation.

## Baseline and architecture

Local main and the last fetched origin/main are 84ec66c. There is extensive
uncommitted prior-session work; preserve it. Protected CLI paths, Docker pipe,
outbound shell network and real database credentials were unavailable in the
previous verification. Review remote references read-only; skip denied operations.

Existing code includes real GEOS-CF ingestion, Supabase JWT verification, private
app profiles/symptom logs with RLS and a transactional outbox, and a historical
synthetic cloud diagnostic deployment. Prior prototype code also implements model
placeholders; it is NOT part of this deployment's operational path.

## Target flow and separation

Scheduler (PAUSED) -> one-task Cloud Run NASA job -> bounded real GEOS-CF subset ->
private raw GCS objects + immutable manifest. ML team consumes this contract and
submits separately validated processed objects/manifest. Only complete processed
environmental products can be promoted to delivery storage and Cloud CDN.
Absent ML output remains pending/unavailable; never invent tiles or scores.

React Native -> Supabase JWT -> FastAPI app.cloud -> private app PostgreSQL,
forecast metadata/delivery, opt-in Places. FastAPI -> Cloud Tasks OIDC -> private
app.worker -> durable user-scoped task status in PostgreSQL. Without an ML worker
adapter, record blocked_ml_unavailable with no fabricated result.

## Work ownership

- nasa_storage agent: forecast_pipeline contracts/catalog/storage, real publisher,
  NASA fixture tests and NASA_PIPELINE.md. No algorithms or inference.
- infrastructure agent: infrastructure/production declarative resources, scoped
  IAM, Dockerfiles, deployment docs. No resource creation.
- worker_places agent: Cloud Tasks, private worker, task-status migration/repository,
  Places and their tests. No personalization implementation.
- root: cloud API entrypoint/config, readiness/logging/delivery signing, cross-agent
  integration/security/testing and final reports.

## Shared contracts and storage

GCS layout: raw/<run>/... NetCDF and provenance manifest; raw/current.json pointer.
Processed layout: processed/<version>/<run>/... immutable ML-supplied files and
manifest; processed/current.json pointer. File references include generation,
SHA256, byte length and content type. Generation preconditions prevent races;
incomplete/stale outputs cannot replace current. Separate raw and processed
buckets; delivery permits environmental products only, never app/user data.

Catalog interface: availability(now), metadata(product,now), delivery(name,now).
API endpoints: /health, /ready, /v1/forecasts/availability,
/v1/forecasts/metadata, /v1/forecasts/delivery, /v1/personalization/tasks,
/v1/personalization/tasks/{id}, /v1/places/search and existing V1 user/log routes.
All application routes require Supabase JWTs; worker separately verifies Google
OIDC audience/issuer/service-account identity. Delivery URLs are short-lived and
issued only for exact files of a validated, available processed manifest.

## Dependencies and IAM

Cloud Run, Scheduler, Artifact Registry, Storage, Tasks, Secret Manager, HTTPS LB/
Cloud CDN and Places (only if explicitly enabled later). Separate job/API/task
dispatcher/worker identities; bucket-scoped reads/writes, queue-scoped enqueue,
worker-only invoker and per-secret accessor. No service-account key files.
Supabase runtime and worker logins are restricted, with user RLS; migration
credentials are never mounted into runtime. No production migration in this task.

## Deployment sequence (future approval required)

Review local tests and generated SQL -> verify project/billing/region/domain and
resource ownership -> provision restricted dev database roles/secrets -> build and
scan images -> review infrastructure plan -> explicitly approved deployment ->
single bounded manual NASA job and readback -> ML-team product handoff -> processed
publication/CDN smoke -> authenticated worker smoke -> only then separately approve
scheduling. Scheduler stays PAUSED throughout this task and initial deployment.

## Resource/cost controls

NASA job: 1 CPU, 512 MiB, one task, parallelism one, 300s timeout, zero retries,
no GPU. Services scale to zero with max one initially. Tasks bounded dispatch/
concurrency/retries; Places disabled by default with bounded rate/request limits.
CDN/LB optional disabled by default because forwarding rules/traffic incur costs.
No budget balance or actual charges assumed verified; $200 is not spending consent.

## Tests and acceptance

Use workspace Python environment and fixtures for upstream failures only. Exercise
real NetCDF conversion, raw checksums, duplicate writes, interrupted promotion,
catalog expiry, JWT/route authorization, OIDC, task idempotency/ownership and Places
errors. Run affected tests after integration, then all available backend tests.
Each command <=5 minutes, retries <=3. Generate SQL offline; actual RLS requires an
explicitly disposable PostGIS database. Inspect Dockerfiles; build only if local
daemon is accessible. Terraform validate/format if installed; no paid init/apply.
Keep mocked/local/live evidence separate in VERIFICATION_REPORT.md.

## Risks and assumptions

Actual NASA/cloud/Places/Supabase connectivity may remain unverifiable in this
sandbox. Trained model targets are not interchangeable with surface concentrations.
Historical app.main remains preserved; deployment uses app.cloud and excludes its
prototype model/sample routes. Existing private Cloud Run IAM and browser Supabase
JWT are separate gates; production public application ingress must retain JWT
verification, while worker IAM remains private. No diagram attachment was supplied
beyond the textual architecture; it is the implementation source of truth.
