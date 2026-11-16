# Cloud implementation verification — November 12, 2026 (America/New_York)

## Result

The local implementation passes its checks. Actual Google Cloud deployment is
blocked by project IAM. This is not a completed cloud acceptance record.

## Implemented

- Pinned Linux sample and API/publisher images, non-root execution, and allowlisted
  Docker build contexts excluding credentials, datasets, and archived code.
- Explicit synthetic publication mode with a 48-hour fixture covering current time.
  The isolated sample-only mode retains its original three-hour future fixture.
- Memory and GCS adapters, immutable run objects, generation-pinned manifests,
  SHA-256 integrity checks, readback, and compare-and-swap pointer updates.
- Duplicate handling, monotonic concurrent promotion, and explicit pointer recovery.
- An opt-in private-deployment diagnostic route with source/run metadata, coverage
  validation, stale-data rejection, and no changes to default dashboard behavior.
- Deployment stages for a private versioned bucket, scoped runtime identities,
  immutable development image references, Cloud Run publisher, and private API.
- Cloud verification scripts for actual execution, controlled publisher failure,
  previous-run preservation, authenticated/unauthenticated API access, workload
  read-only/denied storage access, and freshness.
- Scheduling that starts paused and requires successful release verification;
  a forced/natural dispatch test and restoration of the daily cron.
- Monitoring templates with real-channel validation, failure/absence/API policies,
  and a paused hourly freshness schedule; job/API/pointer recovery commands.
- Fixed the sample deployment script's default configuration path handling.

## Checks actually completed

| Check | Observed result |
| --- | --- |
| Focused Python tests, including existing API/dashboard regressions | 64 passed |
| Offline config validation and sample PowerShell parsing | 6 checks passed |
| Deployment behavior using an in-process gcloud double | 5 checks passed; no cloud calls |
| Monitoring templates and sentinel rejection | Passed locally; no cloud calls |
| Parsing infrastructure PowerShell scripts | Passed |
| Default and explicit local config paths | Both validated successfully |
| Sample Linux/amd64 build and offline execution | Built; successful lifecycle logs; exit 0 |
| Sample controlled unwritable-output failure | Failed as expected; error log; exit 1 |
| API/publisher Linux/amd64 image build | Built using the runtime lock |
| Actual HTTP server inside non-root, network-disabled Linux container | Health 200, unavailable data 503, sample read 200, outside coverage 404, invalid coordinates 422, expired data 503, unchanged dashboard 503 |
| Cloud CLI | Version 593.0.0 found |
| Docker server | Version 28.4.0 reachable |
| Project and billing | Project ACTIVE; billingEnabled=true |
| Setup API enable attempt | Denied: serviceusage.services.enable; stage stopped |

Local `.venv` was missing timezone data and python-dotenv; installed those runtime
dependencies before final verification. Tests emit one existing FastAPI test-client
deprecation warning. Expected failure drills deliberately produce error logs.

## Live IAM findings

Principal: `romeerdhillon@gmail.com`.
Project: `project-0d3c1cde-9400-4a8e-95b` (`asthmaaware-dev`).
Region: `us-east1`.

Repeated permission checks after the reported update still found missing setup
permissions. A direct project policy read showed these account bindings:

```text
roles/artifactregistry.writer
roles/iam.roleViewer
roles/iam.serviceAccountUser
roles/logging.viewer
roles/run.developer
roles/run.invoker
roles/serviceusage.serviceUsageConsumer
roles/storage.objectAdmin
```

The actual setup attempt was denied when enabling Artifact Registry, Scheduler,
IAM, and Cloud Run APIs. Permission checks also reported missing account/repository/
bucket creation, Scheduler management, service/job IAM updates, log-metric creation,
and Monitoring policy/channel access. The exact setup-role mapping and administrator
alternative are documented in [the runbook](README.md#access-handoff).

No workload resources were created, images pushed, schedules enabled, or IAM roles
changed by this implementation. The Docker credential helper was not configured by
the failed cloud stage. Budget alert configuration was reported by the owner but
has not been independently verified.

## Pending cloud evidence

Actual registry push, sample and publisher executions, downloadable GCS artifacts,
storage concurrency/failure drills, private API deployment, runtime IAM probes,
forced/natural scheduling, alert receipt and missing-publication drills, and live
image/pointer/API rollback remain pending. Cloud scripts have local parsing and
selected behavior checks; they may need adjustment once exercised against the
actual project and its organization policies.

No real-model, frontend, end-user-authentication, or Supabase integration is claimed.
Monitoring requires an approved notification-channel resource; missing credentials
and channels remain explicit placeholders or disabled features.

## Resume

Have the project administrator correct the setup bindings or perform setup on the
deployer's behalf. Run `infrastructure/Test-CloudAccess.ps1` and the preflight again,
then continue through the runbook's staged rollout. Do not skip successful manual
verification to enable a schedule.
