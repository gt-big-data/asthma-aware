# AsthmaAware cloud implementation and acceptance plan

Status: implementation plan, not evidence of a completed deployment.

Implementation progress and actual results are tracked in
[the development runbook](development/README.md) and
[the verification report](development/VERIFICATION.md). Local checks pass;
cloud execution and storage/API checks pass in the corrected `asthma-aware`
project. See the verification report for rollout and recovery evidence.

## Objective and boundaries

Deploy and verify an isolated development environment in project
`asthma-aware`, region `us-east1`. First prove scheduled
container execution; then prove durable sample publication and private API
retrieval. Use synthetic data wherever real providers are unavailable.
Successful infrastructure checks do not validate a medical model or real data.

Billing and budget setup, Cloud CLI installation, and Docker availability have
been reported by the owner. Verify access and runtime availability during preflight.
No cloud deployment has been verified in this conversation. The local deployment
configuration passed validation with an explicit config path; its default-path
invocation previously failed and must be fixed before documented commands are used.

The existing sample generator, Dockerfile, tests, and deployment scripts are the
starting point. Preserve existing local changes. Do not deploy archived code under
`backend/legacy`, migrate databases, or change the frontend during initial phases.

## Target architecture

```text
Local verified build -> Artifact Registry (versioned images / recorded digests)
                                      |                    |
                                      v                    v
Cloud Scheduler --OAuth--> Cloud Run publishing Job    private Cloud Run API
                                      |                    |
                                      v                    | read only
                          private Cloud Storage <----------+
                          runs/<run-id>/forecast.json
                          runs/<run-id>/manifest.json
                          current.json

Job and API -> Cloud Logging / Monitoring
Later: API -> Secret Manager -> development Supabase over TLS
```

Phase 1 uses the existing sample-only job with temporary filesystem output.
Phase 2 adds a separate publisher identity and durable storage. Phase 3 adds a
private API test path. Scheduler targets only the phase explicitly under test;
the sample-only and publishing jobs must have distinct names.

## Placeholder policy and configuration

| Item | Development value or behavior | Replacement rule |
| --- | --- | --- |
| Project / region | Confirmed values above | Always pass explicitly to cloud commands |
| Execution-test job | `asthma-forecast-sample` | Existing configuration |
| Execution-test schedule | `asthma-forecast-daily` | Proposed daily 06:00 America/New_York; activate only after manual verification |
| Image repository | `asthma-forecast-job` | Verify ownership before reuse |
| Publisher job | Proposed `asthma-forecast-publisher-dev` | Verify no collision before creating |
| API service | Proposed `asthma-api-dev` | Private IAM access initially |
| Forecast bucket | `asthma-aware-forecast-dev` | Dedicated private development bucket |
| Forecast data | Existing deterministic synthetic generator | Explicit `source: sample`; never label as measured pollution or personal health risk |
| Live model / acquisition | Sample generator implements the publisher input contract | Real provider replaces generator only after contract tests |
| Database | Disabled, optional repository stub for local tests | Real TLS connection is a separate gate |
| Supabase URL / secret | `REPLACE_WITH_SUPABASE_URL`, `REPLACE_WITH_SECRET_RESOURCE` in examples only | Reject sentinels if database mode is enabled |
| App authentication | Private Cloud Run IAM and authorized developer identity tokens | Never fabricate user tokens; browser authentication is a later integration |
| Alert recipient / budget amount | `REPLACE_WITH_ALERT_RECIPIENT`, `REPLACE_WITH_MONTHLY_BUDGET` in examples | Verify the owner's actual budget and notifications without overwriting them |
| Test coordinates | Existing sample points within 33.7–33.8 latitude, -84.4–-84.3 longitude | Return unsupported coverage outside the fixture; no invented coverage |

Examples contain no credentials. Store deployment-specific values in ignored local
configuration. Use attached service identities for Google APIs, not downloaded
service-account keys. Mocks must be explicit configuration modes; a live provider
failure must never silently return sample data.

The current fixture starts at the next UTC hour and spans three hours. Daily
execution is sufficient for testing Scheduler, but cannot establish continuous
forecast freshness. For API tests, use an injected clock locally and manually
publish a fixture covering the cloud test window. Before continuous availability
is claimed, agree on a fixture horizon and a schedule that cover startup delay,
failed runs, and refresh intervals. Reject future-only or expired data for a
requested time rather than changing timestamps at read time.

## Phase 0: preflight and local baseline

1. Record git status; identify existing changes and read the deployment scripts.
2. Verify Cloud CLI version, active signed-in account, access to the explicit
   project, billing linkage where permissions allow, and the existing budget alert
   destination. Verify Docker daemon and Linux container support.
3. Inventory APIs, repositories, identities, jobs, schedules, services, buckets,
   and relevant IAM bindings. Save a sanitized baseline. Existing resource names
   with different ownership markers require a different name, not an overwrite.
4. Fix default config-path handling in `forecast-job/Deploy.ps1`; validate both
   default and explicit paths. Exercise existing config validation tests.
5. Run the forecast tests and relevant backend tests offline. Generate a local
   JSON file; validate schema, unique run IDs, UTC times, bounds, and sample labels.
6. Build the container with the narrow `backend/forecast_job` context; run with
   no network. Confirm non-root execution, success logs, exit 0, and failure exit
   status for invalid inputs. Never pass datasets or credentials into the build.

Gate: reproducible local success, known target account/project, no unresolved
resource collisions, and no unexplained failures. Record commands and results.
An inaccessible resource inventory is an unresolved check, not proof of absence.

## Phase 1: isolated cloud execution milestone

1. Enable only the required Run, Scheduler, Artifact Registry, and IAM APIs.
   Create the dedicated repository and existing runtime/Scheduler identities.
2. Give the sample runtime no project roles. Give deployer only required setup,
   image upload, deployment, and account-specific actAs permissions. Check the
   Cloud Run and Scheduler service-agent permissions without broadening them.
3. Build and push a unique image tag; record its resolved digest. Deploy the job
   by digest with one task, parallelism 1, one CPU, 512 MiB, 300-second timeout,
   and zero task retries. Do not execute as a deployment side effect.
4. Manually execute once; inspect the exact execution and task status plus
   start, processing, output metadata, and completion logs. Correlate execution
   name and generated run ID. Files under `/tmp` are ephemeral, not published.
5. Use a separate disposable failure-test job/image or execution argument
   override to generate a deterministic nonzero exit. Confirm failed execution
   and useful error logs without modifying the successful job configuration.
6. Only after manual success, create Scheduler with OAuth targeting the Run Jobs
   API and job-scoped invoker access. Disable configured Scheduler retries.
   The current script creates an enabled schedule: implement create-and-pause
   handling and avoid a due-time race before first scheduled deployment.
7. Resume and force a Scheduler dispatch; confirm a new execution of the intended
   job actually completes. Scheduler acceptance alone is insufficient. Observe
   one natural scheduled execution before declaring scheduling verified.

Gate: successful local and cloud execution, demonstrated failure reporting,
least-privilege access, and verified Scheduler-to-job execution. Missing natural
execution evidence remains pending. Daily scheduling alone does not test freshness.

## Phase 2: durable and failure-safe fixture publishing

1. Agree a versioned fixture contract: run ID, schema version, source, UTC creation
   and validity times, geographic coverage, units, and timestamped cells. The
   manifest contains object paths, generations, sizes, and checksums.
2. Add a storage adapter with local/in-memory tests and GCS integration tests.
   Keep sample generation separate from publication and provider acquisition.
3. Create a private regional bucket with public access prevention and uniform
   bucket-level access. Add the API and publisher identities separately.
4. API identity receives read-only access on the forecast bucket. Publisher gets
   only required object permissions: create immutable run objects, read current
   metadata, and conditionally replace the pointer. Review permissions for pointer
   replacement explicitly; object-creator access alone is insufficient. Limit
   pointer mutation using supported IAM conditions or separate control storage if
   needed. Scheduler receives no storage access.
5. Validate the whole payload locally before uploading. Create
   `runs/<run-id>/forecast.json` with create-only generation preconditions;
   publish the manifest only when all referenced objects are complete.
6. Read `current.json` and its generation before attempting promotion. Promote
   only a complete, validated candidate using that generation precondition
   (create-only if absent). On conflict, reread and compare run freshness; never
   blindly retry an older candidate over a newer run. Use deterministic freshness
   ordering and disallow ordinary regression; explicit operator rollback is separate.
7. Reader resolves the pointer once per request and pins the manifest/run objects
   to their recorded generations. A pointer change cannot mix runs mid-request.
8. Test interrupted upload, missing/corrupt objects, invalid schema, concurrent
   publishers, duplicate delivery, ambiguous upload responses, and older run
   finishing last. Keep the previous complete selection on all failed promotions.
9. Initially retain every run. Before enabling lifecycle deletion, define retention
   and demonstrate that the current run, previous rollback run, and their objects
   cannot be removed while referenced. Log failed orphan cleanup for later review.

Gate: cloud objects can be downloaded and independently validated; a fresh process
can read them after the publisher exits; all failure/concurrency cases preserve
consistent selection. Sample payloads remain explicitly marked synthetic.

## Phase 3: private API deployment and storage integration

1. Containerize `backend/app/main.py` with deployment dependencies pinned for
   reproducible builds. Start on `0.0.0.0` using Cloud Run's `PORT`; preserve `/health`
   as a process-liveness check that does not require storage or database access.
2. Add an explicit GCS sample-provider mode. The current `/dashboard` response
   includes fabricated personalized risk, so do not directly map `sample_index`
   into health scores. First use a documented internal, IAM-protected forecast
   diagnostic endpoint to verify retrieval and provenance without changing the
   dashboard contract. Agree the product adapter separately.
3. Validate coverage, requested time, freshness, manifest integrity, and schema.
   Define and test responses: 404 for unsupported coordinates; 503 for unavailable,
   future-only, expired, corrupt, or incompatible forecast data. Return explicit
   sample provenance, run ID, and validity metadata for successful test responses.
4. Use bounded cache TTLs with freshness checks on each request. Bound provider
   timeouts. Storage failure must not be reported as healthy forecast availability.
5. Deploy a private development service with its own read-only identity, minimum
   instances 0 and maximum instances 1 initially. Start with one CPU / 512 MiB and
   conservative concurrency; revise only from measured memory and latency results.
6. Test authorized identity-token requests and unauthenticated denial. Test API
   storage reads and denied writes using the runtime identity. An ungranted test
   identity must be denied private bucket access.
7. Deploy a second revision receiving no normal traffic; test it through an
   authenticated tagged URL, move traffic after success, then demonstrate rollback
   to the recorded known-good revision and verify the forecast response again.

Gate: private API retrieves the exact published fixture, reports accurate source
and freshness, handles failure predictably, and passes a real revision rollback.
The frontend still uses legacy `/map/*`; browser integration is not complete here.

## Phase 4: operational verification and recovery

- Structured logs include workload version, execution/run ID, publication stage,
  elapsed time, and success/failure. Exclude tokens, connection strings, and personal
  records. Bound log volume; do not log entire forecast payloads.
- Configure alerts for failed executions, missing expected completion/publication,
  expired current data, API errors/latency, and billing thresholds. Select an owner
  and recipient; force a safe failure to prove delivery. Scheduler success is not
  a substitute for publication/freshness monitoring.
- Scheduler and task retry settings reduce repeated work but do not promise
  exactly-once delivery. Publication must tolerate duplicates and overlapping runs.
  A one-task job does not prevent separate executions from overlapping.
- Rollback job: pause Scheduler; inspect active executions; cancel only known test
  executions if needed; redeploy recorded previous digest and settings; execute
  manually and verify before resuming. Jobs do not use service traffic rollback.
- Rollback forecast: stop promotion, validate a retained run, and conditionally
  update the pointer to that run. Expired rollback data must still be rejected by
  the API. This action is explicit and audited rather than ordinary publication.
- Rollback API: restore traffic to the recorded healthy revision. Do not remove
  secrets or storage objects still needed by that revision.
- Do not disable shared APIs, delete existing resources, or automatically remove
  buckets during rollback. Cleanup targets only resources recorded as created by
  this implementation and only after preserving required evidence and artifacts.
- Review actual billing after test runs and again after charges have settled.
  The prior daily sample estimate was about $0–$1/month, not an estimate for the
  entire future architecture. API usage, logging, storage, transfer, retained
  images, and real acquisition/ML require a separate estimate. Budget alerts are
  notifications, not a spending cap.

Gate: alerts reach the owner, failure drills recover cleanly, resource inventory
and IAM changes are documented, and another developer can follow the runbook.

## Phase 5: optional dependencies and product integration

Supabase connectivity is optional for the infrastructure milestone. With no
credentials, keep DB mode disabled and test missing-configuration handling locally.
Do not claim that a mocked query validates network access. When a development
database is available, store its connection string in Secret Manager, grant access
only to the diagnostic workload, require TLS certificate verification, configure
bounded connection timeouts/pooling, and run `SELECT 1`. Do not run migrations or
write application tables as part of this connectivity check. No Cloud SQL resource
is needed.

Real NASA/AirNow acquisition and ML must produce the same agreed publication
contract and pass compatibility/freshness tests before replacing the fixture.
Measure execution time, memory, storage, and upstream failure behavior before
changing job limits or schedule. Keep medical/product validation separate.

Frontend integration requires replacing obsolete `/map/*` calls, agreeing the
dashboard contract, implementing actual end-user authentication/authorization,
setting allowed origins, and end-to-end testing. Cloud Run developer IAM is not
an end-user login mechanism. Do not expose this development API publicly merely
to bypass browser authentication difficulties.

## Deliverables and completion record

Deliverables: reviewed scripts and example config; reproducible images/digests;
publication/reader adapters and contract tests; private API container; phase-specific
runbook; sanitized resource/IAM inventory; alert configuration; recovery commands.
Keep scripts as the initial infrastructure source of truth and record any manual
changes. If Terraform is adopted later, import existing resources before applying
it rather than creating a second competing owner.

For each acceptance check, record timestamp, command/test, expected result,
observed result, execution/revision/run ID where relevant, and pass/fail/pending.
Never record secret values. Re-run only checks affected by subsequent changes.

| Milestone | Required evidence | Latest status; details in verification report |
| --- | --- | --- |
| Local baseline | Tests, valid container artifact, configuration checks | Passed; 65 Python tests plus container/config checks |
| Cloud execution | Exact successful execution and controlled failure logs | Passed |
| Scheduling | Forced and natural successful executions | Publisher forced/natural runs passed; hourly freshness dispatch and final enabled-state audit passed |
| Durable publishing | Downloaded manifest/data, concurrent and interrupted publish drills | Passed on GCS |
| Private API | Authorized fixture read, unauthorized denial, denied runtime write | Passed |
| Recovery | Successful image, pointer, and API rollback exercises | Passed on deployed resources |
| Monitoring | Failure and missing-publication notification received | Real incident and overdue-data failure verified; inbox receipt deferred by owner; long absence window not yet observed |
| Supabase | Actual TLS `SELECT 1`, or explicitly deferred | Deferred |
| Live data / frontend | Real provider and authenticated browser tests | Deferred |

The development architecture is accepted when phases 0–4 pass. Phase 1 alone
establishes execution wiring; phases 2–4 establish durable fixture delivery and
operability. Deferred dependencies remain visible and do not block synthetic
infrastructure testing.

## References

- Existing scope: `backend/docs/cloud-setup-task.md` and `infrastructure/forecast-job/README.md`.
- [Cloud Run execution](https://docs.cloud.google.com/run/docs/execute/jobs).
- [Cloud Run service identities](https://docs.cloud.google.com/run/docs/securing/service-identity).
- [GCS generation preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions).
- [Cloud Run service revision rollout and rollback](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration).
- [Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets).
