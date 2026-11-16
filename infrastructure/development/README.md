# Development cloud deployment and verification

See [the complete architecture plan](../CLOUD_IMPLEMENTATION_PLAN.md) and
[the current verification report](VERIFICATION.md). Workloads use synthetic data.
Database, user authentication, live acquisition, and model integration remain disabled.

## Current boundary

Local tests and Linux Docker HTTP checks pass. Workloads are deployed in the
corrected `asthma-aware` project: sample and publisher execution, GCS failure/race
checks, private API access, and runtime permission probes have passed. See the
verification report for scheduling, monitoring, and recovery evidence.
The original project ID supplied earlier was incorrect; its IAM denial did not
describe access in this project. Existing forecast resources were left untouched.

The default backend still registers only `/health` and `/dashboard`.
`FORECAST_PROVIDER=gcs-sample` explicitly enables `/internal/forecast` for private
developer diagnostics. It returns the complete synthetic fixture with provenance,
not personalized health risk. `FORECAST_BUCKET` is required in that mode.

## Access handoff

Target: `asthma-aware`, `us-east1`.
CLI principal: `romeerdhillon@gmail.com`.

Run `powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Test-CloudAccess.ps1`
to print allowed/missing permissions without printing credentials or modifying IAM.

The original, incorrect project's bindings did not provide setup access. The
corrected project's setup permissions have now been checked successfully.
An administrator can perform setup instead of granting the deployer administrative
roles. For a temporary project-scoped setup role set, the missing capabilities map to:

| Phase | Setup role / capability |
| --- | --- |
| Enable APIs | `roles/serviceusage.serviceUsageAdmin` |
| Create dedicated accounts | `roles/iam.serviceAccountAdmin` |
| Create image repository | `roles/artifactregistry.admin` |
| Set job/service resource IAM | `roles/run.admin` (existing Run Developer does not set IAM) |
| Create Scheduler jobs | `roles/cloudscheduler.admin` |
| Create/configure bucket and its IAM | `roles/storage.admin` (Object Admin does not manage buckets) |
| Create log-based metrics | `roles/logging.configWriter` |
| Configure alerts and inspect channels | `roles/monitoring.editor` |

After setup, remove unnecessary administrative grants and retain scoped deployment,
image upload, invocation/log viewing, and account-specific actAs access. Runtime
accounts receive only the workload permissions in the scripts, never these setup
roles. Existing project-wide Service Account User is broader than the eventual
account-specific grant needed by this deployment; narrowing it is administrator work.

## Local verification

From the repository root, using Docker Linux containers:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Verify-Local.ps1
```

This runs 65 focused Python checks, six config checks, five deployment behavior
checks using a gcloud double, monitoring template checks, and PowerShell parsing.
It builds both pinned Linux images; verifies successful and failed sample execution;
then checks real HTTP requests inside the non-root cloud container, without network
access or credentials. It does not contact Google Cloud. Local `.venv` requirements
include pytest/httpx for API tests plus the API and cloud runtime dependencies.

The runtime lock is `backend/requirements/cloud.lock`. Regenerate it intentionally
with `backend/.venv/Scripts/python.exe infrastructure/Lock-Cloud-Dependencies.py`,
then rebuild and verify Linux compatibility. Do not regenerate during deployment.
Both image bases are pinned by digest. The build allowlist excludes `.env`, data,
virtual environments, archived code, and credentials.

## Cloud rollout order

The wrapper discovers the per-user Google Cloud CLI installation and always uses
the project and region in ignored `forecast-job/config.local.psd1`. It does not
change your active CLI project. Other installation locations can be added to PATH.
All external command failures stop the relevant stage. Setup is not transactional:
inventory partial resources before rerunning after a failure.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Preflight
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Setup
# Re-run inventory after APIs become available; investigate any name collisions.
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Preflight
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Image
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Job
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage DevSetup
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage DevImage
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage DevJob
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage DevApi
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Verify
```

`Image` builds the isolated sample-only image. `DevImage` builds and pushes a unique
development image tag, resolves its digest, and saves ignored release state. API
and publisher use that immutable digest. On subsequent releases, the preceding
state is saved in `previous-release.local.json` for job-image recovery.

`DevSetup` creates a private regional, versioned bucket. Versioning retains pointer
generations for in-flight reads and recovery. No lifecycle deletion is installed.
The publisher can create/read immutable run objects and replace only `current.json`;
the API can only read. Bucket public access prevention is enabled. Validate actual
IAM with probes; labels/descriptions prevent accidental collisions but are not
security ownership boundaries.

`Verify` manually runs the sample and publisher jobs, validates the private API,
forces a publisher failure before promotion, and checks that the previous selection
survives. It runs jobs under the API and ungranted Scheduler identities to verify
read-only and denied access, and runs a freshness probe. It saves sanitized evidence
under ignored `evidence/` and writes `verified.local.json` only after all checks pass.
The failure must be inspected in the saved logs to confirm the intended cause;
transport/IAM failure alone does not satisfy the failure drill.

The active sample-only generator keeps its original three-hour, next-hour fixture.
The durable publisher explicitly chooses an `available` fixture: current UTC hour
through 48 hours, refreshed daily. This is synthetic test coverage. Expired/future
publication profiles are rejected before promotion. Readers do not relabel expired
data as fresh. Uploads use immutable names, checksums, generations, readback, and
conditional pointer promotion. Concurrent older runs cannot replace newer runs.

## Monitoring and scheduling

Choose an existing owner-approved notification channel in this project. Copy
`observability.example.json` to ignored `observability.local.json` and replace the
sentinel with its `projects/.../notificationChannels/...` resource name. Credentials
and email placeholders are never deployed. Without a real channel, monitoring
remains deferred; synthetic configuration checks do not prove notification delivery.

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Monitor
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage Schedule
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/Invoke-Cloud.ps1 -Stage TestSchedule
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Test-Freshness-Schedule.ps1
```

Monitoring creates workload-failure, missing-monitor-heartbeat, API-error, and API-latency
policies. A read-only freshness probe detects expired/corrupt/missing data and
publication older than 26 hours. Its hourly schedule is initially paused.
`Test-Freshness-Schedule.ps1` verifies a forced dispatch and leaves that monitor
enabled on success. The owner has explicitly deferred inbox confirmation; keep
email receipt pending rather than blocking operational monitoring. The
monitor-heartbeat policy needs successful metric history before its two-hour
absence window is meaningful. Retain pending status until an absence drill proves it.
No heartbeat means no guarantee that every failure mode is observable yet.

`Schedule` requires successful verification of this release within the previous
24 hours and creates the publisher schedule paused. `TestSchedule` confirms a forced
dispatch, temporarily uses a one-minute cron to observe a natural successful dispatch,
then restores and enables daily 06:00 America/New_York execution. On failure it
restores the intended cron and leaves scheduling paused. This test can take several
minutes and creates real billable executions. The next execution of the restored
daily cron remains an additional observation; record it after it happens.

The sample-only `Scheduler` stage also creates a paused schedule. Leave that schedule
paused once the publisher is being used; running both daily is unnecessary.
Hourly freshness monitoring adds about 720 one-minute-minimum job executions per
30-day month and must be included in costs, separate from the daily job estimate.
Budget alerts do not stop spending. Review actual usage, storage versions, retained
images, logs, and API requests. No storage cleanup or shared API disable is automatic.

## Recovery and remaining acceptance drills

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Recover.ps1 -Action Pause
# A prior image must have been saved by a subsequent DevImage stage:
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Recover.ps1 -Action JobImage
# Supply actual retained identifiers; do not run literal placeholders:
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Recover.ps1 -Action ApiRevision -Revision REPLACE_WITH_KNOWN_GOOD_REVISION
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Recover.ps1 -Action ForecastPointer -RunId REPLACE_WITH_RETAINED_RUN_ID
```

The pointer rollback refuses active publisher executions and uses a conditional
update. The target run is checked for completeness/integrity; an expired target
will still be rejected by the API. Recovery never deletes cloud resources.

Live automated recovery and alarm drills (operate only on the isolated development resources):

```powershell
# Requires two distinct DevImage releases, saved in current/previous local state.
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Test-Recovery.ps1
# Temporarily selects a valid but 27-hour-old fixture; always restores fresh data.
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Test-Freshness-Alert.ps1
# Read-only final limits, IAM, private API, bucket, schedule, and data checks.
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Final-Audit.ps1
```

The publisher schedule must be paused for recovery/alarm drills. After successful
recovery, explicitly resume the verified daily schedule; recovery intentionally
does not resume it automatically. Execution-specific log evidence is selected in
PowerShell to avoid Windows native-argument quoting problems with label-map keys.
The verification script supports `-ResumeFromLogs` only when recent successful
GCS/publisher execution evidence matches the release's actual Linux/amd64 manifest
and project. The rest of the API and runtime permission checks still run.

Before accepting phases 0–4, also demonstrate two distinct image releases and job
image restoration; a second private API revision tested without normal traffic,
traffic promotion and rollback; retained-run pointer rollback with authenticated
API verification; interrupted/concurrent publication on GCS; forced freshness
failure and notification receipt; and missing-publication detection. Offline
tests cover publication races, pointer-read consistency, corruption, duplicates,
expiry, and rollback, but do not substitute for these cloud drills.

Supabase is optional and disabled. Use a real Secret Manager resource and TLS
credentials later for a separate `SELECT 1` diagnostic. No fake credentials,
database writes, migrations, frontend changes, or real ML deployment are included.

Official behavior references:
[conditional storage writes](https://docs.cloud.google.com/storage/docs/request-preconditions),
[scheduled job execution](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule),
[service revision recovery](https://docs.cloud.google.com/run/docs/rollouts-rollbacks-traffic-migration).
