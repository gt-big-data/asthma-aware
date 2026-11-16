# Google Cloud deployment preparation

Status: implementation/configuration prepared locally; **not deployed or live
verified**. This document is a future operator runbook, not authorization to create
resources. No Google Cloud credits were spent on workloads during preparation.
See `GOOGLE_CLOUD_IMPLEMENTATION_PLAN.md` and `VERIFICATION_REPORT.md` for scope.

## Separate active backend from historical diagnostics

Use `backend/Dockerfile.api`, entrypoint `app.cloud:app`, for this architecture.
`app.main:app`, `Dockerfile.cloud` and `infrastructure/development` are preserved
historical work and include synthetic/score diagnostics. They are not the new
production deployment path. The new forecast image acquires validated NASA data
only; processed availability remains pending until the ML team supplies real
output through the manifest contract. No inference or fabricated tiles are created.

## Before any deployment approval

1. Inspect the actual explicit project, account, billing balance, quotas and
   existing resources. This sandbox could not perform a fresh live inventory.
   Existing project resources must not be overwritten merely because names match.
2. Confirm region and choose a unique 5–20-character `name_prefix`. Defaults use
   `us-east1`, `asthma-prod`; they are examples, not evidence of ownership.
3. Have an authorized administrator verify prerequisite Run, Artifact Registry,
   Storage, Scheduler, Tasks, Secret Manager and optional Compute APIs/service
   agents. This configuration does not enable APIs or alter billing/quotas.
4. Prepare a disposable PostgreSQL/PostGIS environment and pass actual migrations,
   RLS, task idempotency and restricted-identity tests before a shared database.
   Review all migration SQL first. Do not use production data as test fixtures.
5. Provision existing Secret Manager versions for restricted API `DATABASE_URL`
   and worker `WORKER_DATABASE_URL`. Use TLS connection strings and separate login
   roles. Browser publishable keys do not belong in these server secrets.
6. Restrict any future Places API key to Places API (New), appropriate application
   restrictions and budget/quota controls. Keep Places disabled until separately
   approved; variable configuration does not authorize billable requests.
7. Use an encrypted remote Terraform state with restricted access and an approved
   locking configuration. No remote-state backend is guessed here. Local state,
   plan and tfvars files are ignored. Never commit them.
8. Generate and retain platform-specific dependency lock files from the reviewed
   requirements. Current image dependency ranges are bounded where practical but
   not a release lock; actual container resolution/build remains unverified.

## Local container commands (not executed here)

Run from `backend/` with a permitted local Docker daemon. These commands build
locally; they do not push or create Cloud Run resources:

```powershell
docker build --platform linux/amd64 -f Dockerfile.api -t asthma-api:local .
docker build --platform linux/amd64 -f Dockerfile.forecast -t asthma-forecast:local .
docker build --platform linux/amd64 -f Dockerfile.worker -t asthma-worker:local .
```

Images use a pinned Python 3.12 base digest and non-root UID 10001. An allowlisted
build context excludes credentials, data, model weights, historical placeholder
drafts and tests. API/worker listen on Cloud Run `PORT` (8080 default), with exec
signal forwarding. The NASA image has no web listener and exits after the batch.
Do not mount `.env` into an image or pass secret values through build arguments.

After explicit release approval, push only these reviewed images to an existing
authorized registry and record immutable `@sha256:` digests. GitHub pushes are
not required. Registry/image upload is a separate unexecuted step that may cost.

## Reviewed Terraform plan and apply sequence

Definitions: `infrastructure/production/main.tf.json`. JSON syntax is intentional
and is accepted by Terraform; the Google provider still must validate the schema.
Terraform was unavailable here. Eight offline invariants passed; that does not
substitute for `terraform validate`, a real plan or deployment acceptance.

1. Copy `example.tfvars.json` to ignored `deployment.tfvars.json`; substitute real
   project/number, image digests, existing secret IDs and public Supabase origin.
   Leave all feature flags false. Keep credentials/key material out of tfvars.
2. With authorization for provider downloads and read-only cloud inspection,
   initialize the approved state backend and Google provider, then run:

   ```powershell
   terraform -chdir=infrastructure/production validate
   terraform -chdir=infrastructure/production plan -var-file=deployment.tfvars.json -out=reviewed.tfplan
   ```

3. Review exact additions, IAM and estimated costs. `apply` is **not** part of this
   task. Only after separate explicit approval may an operator apply that reviewed
   plan. First apply keeps API private, worker private, Tasks/Places/CDN disabled,
   and creates no Scheduler. It does not execute the forecast job.
4. Record the worker origin from `worker_uri`. Set `worker_audience` to this exact
   HTTPS origin in a second reviewed plan. This avoids a resource self-reference
   and prevents queueing before the OIDC audience is known. Worker fails closed
   while audience/DB configuration is absent; health does not imply readiness.
5. Verify private IAM denial, authenticated API JWT checks, actual DB TLS/RLS and
   worker OIDC service-account verification. No user identity is accepted merely
   because it is present in a task body.
6. Only after explicit approval, enable `enable_tasks=true` in a separately
   reviewed plan. Queue rate is one dispatch/second, concurrency one, three maximum
   attempts over ten minutes. Submit one isolated test task and correlate durable
   status; missing ML output must be `blocked_ml_unavailable`, not a fake score.
7. For browser/native app access, separately decide `api_allow_unauthenticated`.
   The default API IAM gate requires Google credentials in addition to Supabase.
   Setting this flag grants network invocation only; all private application routes
   must still verify Supabase JWTs. Review app security/CORS before changing it.

There is no auto-apply/deploy helper. `Validate.ps1` runs offline static checks;
its optional Terraform flag only validates already-installed providers.

## Real NASA batch, storage and ML handoff

The image command is `python -m scripts.publish_nasa_forecast`. Cloud Run job
arguments are `--output-dir /tmp/nasa --gcs-bucket RAW_BUCKET --hours 48 --bbox
33.45,34.05,-84.80,-83.95 --deadline 240`. These are real Atlanta-area ingestion
requests, not simulated forecasts. They are not automatically executed on deploy.

Initial job limits are fixed: one vCPU, 512 MiB, task count/parallelism one, 300s
timeout and zero task retries. An inner 240s deadline allows cleanup before the
Run limit. Native geographic/time filtering avoids global downloads. These settings
need actual permitted container memory/runtime measurement; no automatic increase
has been made. An unavailable NASA source fails clearly, without generated values.

Raw objects live under `raw/<run-id>/<request-hash>/` with validated NetCDF,
metadata and immutable manifests. Only `raw/current.json` receives conditional
pointer replacement. The raw runtime has create/read on its dedicated bucket and
object-admin only for that pointer. It cannot write processed/delivery buckets.

The separate `processor` integration service account is an ML-team handoff identity,
not a deployed model runtime. It can read raw objects, create/read immutable outputs
in processed/delivery buckets and conditionally mutate `processed/current.json`.
Real ML output and units/provenance must match the contract before publication.
No user symptoms or personalization artifacts belong in delivery storage.

The forecast image also contains `scripts.publish_processed_forecast` as an
explicit integration command. After a real external ML run, invoke it with the
actual manifest/files root and raw/processed/delivery bucket names. It validates
provenance, stages immutable outputs and promotes the delivery pointer only after
all objects are verified. It does not execute a model. The API's
`FORECAST_DELIVERY_BUCKET` points to the same delivery catalog as the CDN origin,
avoiding metadata URLs for objects that exist only in staging.

The API can read raw/processed/delivery storage; it cannot mutate it. Buckets are
private, versioned and deletion protected. No lifecycle deletion is configured,
because current/rollback manifests must never point at deleted objects. Retention
and orphan cleanup need a separately designed, tested policy.

## Scheduler safety

`create_scheduler=false` by default means no Scheduler resource exists from this
module. This avoids a create-then-pause race in provider implementations. The
prepared resource has `paused=true`, OAuth to the Run Jobs API, 60s dispatch
deadline and zero retries. A dispatch acknowledges job creation, not completion.

Creating a Scheduler is a separately approved operation. Choose a safe future
dispatch time during creation, verify it is paused and inspect active executions.
Do not resume it until one explicitly approved manual job and publication/freshness
check succeed. Do not resume the pre-existing development schedules as a shortcut.

## Optional HTTPS load balancer and CDN

`enable_cdn=false` by default. Enabling it creates a global IPv4 address, managed
certificate, HTTPS forwarding rule/proxy/URL map and a Cloud CDN backend bucket.
It requires a controlled DNS hostname, existing API signing-key secret and a matching
base64url-encoded 128-bit backend key. The key input is sensitive but **is retained
in Terraform state**; restricted encrypted state is mandatory. Never put it in Git,
logs, command histories or the mobile app. Supply it through an approved secret
injection process. The module creates no signing-key secret values itself.

The delivery origin stays private. Cloud CDN's cache-fill service account receives
bucket-scoped viewer access only after a backend signing key creates that identity.
Unsigned origin access must fail. Signed responses and unsigned responses have
separate cache behavior; a CDN flag alone does not protect a public origin.
[Google's signed URL guidance](https://docs.cloud.google.com/cdn/docs/using-signed-urls)
describes this boundary.

Use only validated immutable environmental products in CDN storage. Signed URL
cache max-age is 300s; availability/current pointers come through uncached API
metadata, not a user-sensitive public cache. Verify the API's configured HTTPS base
URL, key name and exact key value match the backend. Private user/task/symptom
responses must never be routed through the CDN. DNS/certificate activation,
signed/unsigned access and cache-expiration acceptance remain unverified.

## Cost controls and estimates (not actual billing)

No resources were deployed for these tests, so incremental deployed-workload cost
from this preparation is estimated $0. Actual project balance/charges were not
verified. Retained resources elsewhere in the existing project can still cost.

- At the listed US Tier 1 job rates, a five-minute 1-vCPU/0.5-GiB run is about
  `$0.000018*300 + $0.000002*150 = $0.0057` before free-tier effects, storage,
  network/logging/registry costs. Thirty maximum-duration runs would be about
  $0.171 compute alone. Real region/usage must be checked before approval.
  [Cloud Run pricing](https://cloud.google.com/run/pricing)
- A global forwarding-rule group alone is listed at $0.025/hour, approximately
  $18.25 per 730-hour month, even with little traffic. CDN bandwidth, lookups,
  storage/network processing and potentially other IP costs are additional.
  That is why CDN is disabled rather than assumed covered by credits.
  [Network pricing](https://cloud.google.com/vpc/network-pricing#lb),
  [CDN pricing](https://cloud.google.com/cdn/pricing)
- Tasks has a monthly account-level free operation allowance, but retries and
  deliveries count and other projects share the account allowance. No free-tier
  eligibility or $0 production cost is asserted.
  [Cloud Tasks pricing](https://cloud.google.com/tasks/pricing)

Max instances/timeouts/rate limits reduce runaway work, but are not dollar caps.
Available $200 credits do not authorize deployment or usage. Standing CDN resources,
API/Places traffic and retained storage must be included in any approved budget.

## Recovery and required acceptance gates

Keep Scheduler absent/paused; prevent new tasks; inspect/cancel only known task/job
executions when separately authorized. Restore the recorded previous API/worker
image digest through a reviewed plan; restore a validated retained forecast pointer
using generation preconditions. Do not delete buckets/project or change unrelated
IAM as an incident shortcut. `prevent_destroy`, versioning and disabled force
destroy guard against accidental data loss but do not replace recovery drills.

Before calling this deployment verified: provider validation/plan, clean Linux
image builds and smoke tests, bounded actual NASA download, GCS write/read/CAS,
Supabase TLS/RLS/migration tests, task OIDC/idempotency, real-output publication,
private-origin CDN signed/unsigned/cache tests, load/memory measurements and billing
review must pass. Missing ML integration must remain explicitly unavailable.
