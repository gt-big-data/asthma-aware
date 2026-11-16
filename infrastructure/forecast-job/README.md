# Forecast job: infrastructure milestone 1

Current deployment work and live access findings are recorded in
[the development verification report](../development/VERIFICATION.md).
The historical local checks below are supplemented by that report.

This is a **sample-only** Cloud Run Job invoked by Cloud Scheduler. It does not
serve HTTP, run NASA acquisition or a model, access a database, personalize risk,
or publish forecasts to the app. No Cloud Storage or Cloud Tasks are configured.
Artifact Registry is used only to hold the container image.

The Python package uses the standard library only. It does not import the active
API, legacy model, collector, or database packages. Its orchestration is in
`backend/forecast_job/__main__.py`; replaceable sample generation is in `sample.py`.
Python 3.11+ is suitable locally; the container uses Python 3.12.

## Local run (no cloud credentials)

From the repository root, using an existing Python installation:

```powershell
Push-Location backend
python -B -S -m forecast_job --mode sample --output-dir data/forecast-job
if ($LASTEXITCODE -ne 0) { throw 'Forecast job failed' }
$artifact = Get-ChildItem data/forecast-job/forecast-*.json |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Get-Content $artifact.FullName
# With pytest already installed:
python -B -m pytest -p no:cacheprovider tests/test_forecast_job.py
Pop-Location
```

The `.npy`/model files and external credentials are unnecessary. `-S` even disables
site-packages. `--mode sample` is mandatory. `--output-dir` overrides the
`FORECAST_OUTPUT_DIR` environment variable; otherwise `/tmp/forecast` is used.
Use the explicit local directory on Windows.

Each invocation writes `forecast-<UUID>.json`. The artifact has schema version
`1.0`, a unique run ID, UTC `generated_at`, geographic bounds, `source: sample`,
three hourly `valid_at` timestamps, and two cells per timestamp. `valid_from` is
inclusive; `valid_until` is exclusive. Validity starts at the next UTC hour.
`sample_index` is an artificial dimensionless value, not a pollution measurement
or health score. With the same run ID and generation time the payload is
deterministic; real invocations deliberately vary those metadata fields.

Logs on stdout identify start, processing, output path/size, and completion with
the run ID. Exceptions produce a failed completion log and exit status 1;
invalid/missing CLI arguments exit 2. A successful process exits 0. Files have
unique names and are created exclusively. A failed write may leave an incomplete
local file; no artifact is published, and the failed execution must not be treated
as successful.

**Cloud Run filesystem output is temporary, consumes instance memory, and is lost
when the task instance is removed. These artifacts are not durable and are not
available to the app.** Logs record output metadata, not the full payload. This
milestone verifies execution wiring only, not forecast delivery or accuracy.

## Local container check

Install/start Docker Desktop with Linux containers. From the repository root:

```powershell
docker build --platform linux/amd64 -t asthma-forecast-job:local backend/forecast_job
docker run --rm --network none asthma-forecast-job:local
# Optional: retain output on the host for inspection.
New-Item -ItemType Directory -Force backend/data/forecast-job-container | Out-Null
$output = (Resolve-Path backend/data/forecast-job-container).Path
docker run --rm --network none --mount "type=bind,source=$output,target=/output" asthma-forecast-job:local --mode sample --output-dir /output
Get-Content backend/data/forecast-job-container/forecast-*.json
```

The container runs as UID 10001; a Linux host bind directory must be writable by
that UID. The build context is **only** `backend/forecast_job`, with an allowlist
`.dockerignore`; credentials, datasets, `.venv`, and models are not sent to Docker.
No Python dependencies are installed and no web server is started. Building needs
access to the Python base-image registry; executing the sample needs no network.

## Google Cloud prerequisites

Nothing below has been executed by this implementation. You need:

1. A Google Cloud project you are authorized to change, with billing enabled.
   Cloud Run, Scheduler, Artifact Registry, and retained logs can incur charges.
2. A region supported by both Cloud Run Jobs and Scheduler, chosen explicitly.
3. Google Cloud CLI (`gcloud`), PowerShell 5.1+ and Docker with Linux containers.
   Authenticate interactively using `gcloud auth login`. No service-account keys
   are needed; do not create or download any. Application-default credentials are
   not needed by this sample.
4. Setup permissions to enable APIs (`roles/serviceusage.serviceUsageAdmin`),
   create service accounts (`roles/iam.serviceAccountAdmin`), manage the dedicated
   image repository (`roles/artifactregistry.admin`), deploy/manage job IAM
   (`roles/run.admin`), and manage schedules (`roles/cloudscheduler.admin`).
   Ask your administrator for these permissions or equivalent limited/custom
   permissions; the scripts do not grant your user broad project roles.
5. `roles/iam.serviceAccountUser` on **both new service accounts** for the deploying
   user (allows `iam.serviceAccounts.actAs`). An administrator may need to grant
   these after Setup and before Job/Scheduler. Creating an account alone is not
   evidence that you can attach it. The image publisher needs repository upload
   permission (included in repository admin; writer suffices after setup).
   Verification needs job execution/list/get permissions, Scheduler run permission,
   and log access (`roles/logging.viewer`).

For example, an authorized administrator can grant the two account-scoped actAs
bindings after Setup (using the config-loading commands below):

```powershell
$deployer = 'user:YOUR_EMAIL_ADDRESS'
gcloud iam service-accounts add-iam-policy-binding $c.RuntimeEmail --member=$deployer --role=roles/iam.serviceAccountUser --project=$($c.ProjectId)
gcloud iam service-accounts add-iam-policy-binding $c.SchedulerEmail --member=$deployer --role=roles/iam.serviceAccountUser --project=$($c.ProjectId)
```

Replace the deployer placeholder; use a `group:` member instead if your team grants
access through a group. These are deployer permissions, not runtime permissions.

The runtime account gets **no project roles**. It can run sample code without any
Google API calls. The Scheduler client account gets `roles/run.invoker` **on this
Cloud Run Job only**, not project-wide. The built-in Cloud Scheduler service agent
must retain Google's `roles/cloudscheduler.serviceAgent` grant so it can mint the
client's OAuth token. Do not assign that service-agent role to the client account.
Normal same-project Cloud Run service-agent permissions handle image pulls; the
runtime account does not need Artifact Registry permissions.

## Prepare and deploy (user-run steps)

There were no existing deployment scripts to extend. These scripts always pass
explicit project/region values and never change the active gcloud project.
Run them from the repository root:

```powershell
# Run this copy only if the local config does not already exist.
if (-not (Test-Path infrastructure/forecast-job/config.local.psd1)) {
    Copy-Item infrastructure/forecast-job/config.example.psd1 infrastructure/forecast-job/config.local.psd1
}
# Edit config.local.psd1: set ProjectId and Region; review all resource names.
# Proposed default: 0 6 * * * = daily 06:00 America/New_York, NOT a product requirement.
```

The local config is ignored by Git and contains no secrets. Choose dedicated names
for this milestone. Scripts refuse existing accounts/repositories/schedules without
the setup description marker or jobs without its management label. Rerunning uses
the same resources and updates the job/schedule. These markers prevent accidental
name collisions, but are not security/ownership boundaries: do not reuse names
from another team's setup. Setup is not transactional; after an error, resolve it
and rerun the relevant stage. Unknown locations, cron field ranges and timezone
names receive final validation from Google Cloud; local validation catches missing
values, placeholders and malformed identifiers before any cloud commands.

```powershell
# Validation only, no cloud commands:
& ./infrastructure/forecast-job/Deploy.ps1 -Stage Validate
# Enables only Run, Scheduler, Artifact Registry and IAM APIs;
# creates two service accounts and a dedicated Docker repository:
& ./infrastructure/forecast-job/Deploy.ps1 -Stage Setup
# Have an administrator grant your user actAs on both accounts if needed.
# Builds locally and pushes to Artifact Registry. This configures the gcloud
# Docker credential helper for the selected regional registry in Docker config:
& ./infrastructure/forecast-job/Deploy.ps1 -Stage Image
# Creates/updates the job and its job-scoped Scheduler invoker binding:
& ./infrastructure/forecast-job/Deploy.ps1 -Stage Job
```

On Windows where script execution is disabled, after reviewing the scripts use
`powershell -NoProfile -ExecutionPolicy Bypass -File ./infrastructure/forecast-job/Deploy.ps1 -Stage Validate`
(substitute the intended stage). This applies only to that process; no persistent
execution-policy change is required. Organization policy may still prevent it.

The job has **one task, parallelism 1, timeout 300 seconds, max retries 0, one CPU,
512 MiB RAM**, and explicit sample arguments. Deploying does not execute it.
Use a new `ImageTag` for later releases if you want separate image versions.
No Cloud Build, buckets, service-account key files, frontend configuration, or
existing application secrets are used.

## Execute and inspect before adding the schedule

In a PowerShell session allowed to load these scripts:

```powershell
. ./infrastructure/forecast-job/Common.ps1
$c = Read-ForecastConfig ./infrastructure/forecast-job/config.local.psd1
gcloud run jobs execute $c.JobName --project=$($c.ProjectId) --region=$($c.Region) --wait
gcloud run jobs executions list --job=$($c.JobName) --project=$($c.ProjectId) --region=$($c.Region)
# Copy the execution name from the list (not an artifact's UUID):
$execution = 'PASTE_EXECUTION_NAME'
gcloud run jobs executions describe $execution --project=$($c.ProjectId) --region=$($c.Region)
gcloud run jobs logs read $c.JobName --project=$($c.ProjectId) --region=$($c.Region) --limit=100
```

Expect a successful task/execution and the four lifecycle logs sharing an artifact
run ID. Logs can arrive after execution completion. A missing file cannot be
downloaded after the container terminates; inspecting durable cloud artifacts is
outside this milestone. Cloud Run execution names and artifact UUIDs are distinct;
Cloud Logging execution labels let you filter the lifecycle messages per execution.

## Configure and verify Scheduler

```powershell
& ./infrastructure/forecast-job/Deploy.ps1 -Stage Scheduler
# The deployment stage leaves schedules PAUSED. Resume only after manual checks:
gcloud scheduler jobs pause $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
gcloud scheduler jobs describe $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
```

Scheduler POSTs `{}` to
`https://run.googleapis.com/v2/projects/PROJECT/locations/REGION/jobs/JOB:run`, using
**OAuth** with the cloud-platform scope. OIDC is not used for this Google API.
The HTTP request deadline is 60 seconds; configured Scheduler retry count and
retry duration are both zero. Its response means the execution request was
accepted, not that forecast processing succeeded. Scheduler does not wait for the
Cloud Run execution to finish and does not retry because the processing task fails.

```powershell
# Record the latest executions BEFORE triggering so you can identify the new one.
gcloud run jobs executions list --job=$($c.JobName) --project=$($c.ProjectId) --region=$($c.Region)
# Resume first if paused, then force a dispatch now:
gcloud scheduler jobs resume $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
gcloud scheduler jobs run $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
gcloud scheduler jobs describe $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
# Wait briefly, then confirm a new Cloud Run execution and inspect it as above:
gcloud run jobs executions list --job=$($c.JobName) --project=$($c.ProjectId) --region=$($c.Region)
gcloud run jobs logs read $c.JobName --project=$($c.ProjectId) --region=$($c.Region) --limit=100
# Optionally pause again after testing:
gcloud scheduler jobs pause $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
```

Check Scheduler dispatch status in Cloud Logging if no execution appears. A 403
usually needs the job-scoped invoker binding, deployer's actAs access, or the
Scheduler service-agent grant checked. The setup stage enables the Scheduler API;
it does not rewrite the Google-managed service agent's IAM bindings.

**Executions can overlap.** A scheduled dispatch, a manual dispatch, a duplicate
delivery, and an uncertain HTTP outcome can each result in separate executions.
One task and zero configured retries do not guarantee exactly-once execution,
global serialization, or deduplication. UUID output names do not implement a lock.
Timezone/DST behavior follows Cloud Scheduler's wall-clock scheduling.

## Pause and remove only this setup's resources

First validate the local config and inspect the resource descriptions/labels. Use
only the dedicated resources created by this setup; do not substitute shared names.
Do not disable project APIs, delete the project, or remove unrelated IAM bindings.
The scripts reject unmarked existing resources; if these names were previously
created by another run/team, retain them unless their owner approves removal.

```powershell
gcloud scheduler jobs pause $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
gcloud scheduler jobs delete $c.SchedulerName --location=$($c.Region) --project=$($c.ProjectId)
# List executions and wait for completion, or cancel only this job's active executions:
gcloud run jobs executions list --job=$($c.JobName) --project=$($c.ProjectId) --region=$($c.Region)
# gcloud run jobs executions cancel EXECUTION_NAME --project=$($c.ProjectId) --region=$($c.Region)
gcloud run jobs delete $c.JobName --project=$($c.ProjectId) --region=$($c.Region)
# Only if these service accounts and repository were created exclusively here:
gcloud iam service-accounts delete $c.SchedulerEmail --project=$($c.ProjectId)
gcloud iam service-accounts delete $c.RuntimeEmail --project=$($c.ProjectId)
# Deletes ALL images in this dedicated repository; retain if anything else uses it.
gcloud artifacts repositories delete $c.Repository --location=$($c.Region) --project=$($c.ProjectId)
```

Deleting the job removes its resource-level invoker policy. No runtime project role
was added to remove. Historical logs follow project retention policy and are not
deleted by these commands. Local images/output and the Docker credential-helper
setting also remain; no global Docker cleanup is performed.

## Verification boundary and references

To rerun offline PowerShell parsing and placeholder/config checks (does not run
`Deploy.ps1` or any cloud command):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File ./infrastructure/forecast-job/Test-Config.ps1
```

Local evidence is reported with the change: focused Python tests, a real sample
invocation/output inspection, PowerShell parsing/config validation, and container
checks where Docker is available. Deployment, IAM propagation, registry push,
cloud execution/logging, and Scheduler dispatch require your account and have not
been exercised. No setup stage or cloud-changing command was run during this task.

Checks completed for this milestone:

- Six Python tests passed, including deterministic structure, unique artifacts,
  output configuration precedence, processing/write errors, and actual subprocess
  exit statuses with site-packages disabled.
- A local sample artifact was generated and its JSON inspected (three times,
  two cells each, required metadata present).
- Six offline configuration checks and PowerShell parsing passed.
- The Linux/amd64 Docker image built and its default command exited 0 with
  networking disabled. An unwritable `/root/forecast` output path exited 1 and
  logged a failed completion.
- At the original milestone check, Google Cloud CLI was unavailable. It is now
  installed; see the development verification report for live project/IAM findings.

Official references used to prepare the commands:

- [Schedule Cloud Run Jobs](https://docs.cloud.google.com/run/docs/execute/jobs-on-schedule)
- [Cloud Scheduler HTTP flags and OAuth](https://docs.cloud.google.com/sdk/gcloud/reference/scheduler/jobs/create/http)
- [Cloud Run Job deployment flags](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy)
- [Cloud Run access control](https://docs.cloud.google.com/run/docs/securing/managing-access)
