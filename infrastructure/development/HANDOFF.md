# AsthmaAware administrator handoff

Use Google Cloud project **`asthma-aware`**, region **`us-east1`**. The first project
ID supplied in the conversation was incorrect; its earlier IAM errors are historical.

## What was built

- A Dockerized sample job and a durable synthetic forecast publisher on Cloud Run.
- A private, versioned Cloud Storage bucket: `asthma-aware-forecast-dev`.
- A private API: `asthma-api-dev`, with `/health` and an opt-in
  `/internal/forecast?lat=33.725&lon=-84.375` diagnostic.
- Separate identities for execution, scheduling, publishing, and API reads.
- Daily publishing and hourly freshness scheduling; see the verification report
  for final enabled state and tested executions.
- Monitoring policies for failed work, overdue data/stopped monitoring, API errors,
  and API latency, with notifications addressed to **`asthmaaware26@gmail.com`**.
- Recovery commands for forecast selection, job images, and API revisions.

## What passed

65 local Python tests, offline deployment/configuration checks, and real Linux
container HTTP tests. Cloud tests verified job execution, durable publication,
concurrent and interrupted writes, corruption rejection, private API access,
read-only/denied storage permissions, and retained-run/API/image rollback.
Monitoring has opened a real test incident. Full details and final scheduling
evidence are in [VERIFICATION.md](VERIFICATION.md).

The publisher's forced and natural Scheduler runs passed. Its final daily schedule
is **6:00 AM America/New_York**. Hourly freshness monitoring is also **enabled**
at **15 minutes past each UTC hour**, and its forced Scheduler execution passed.
The final audit verified private access, limits, scoped identities, both schedules,
and a valid synthetic forecast through the API. The overdue-data test deliberately selected a
27-hour-old synthetic fixture, confirmed the monitor failed, restored the fresh
selection, and confirmed recovery. Expected failed test executions remain visible
in Cloud Run history.

At the final check, the original test incident had closed and a second incident
from the overdue-data drill was open. Freshness and API checks had recovered.
Expect test-failure and possible resolved notifications; review their timestamps
and job names before treating them as unexpected outages.

## What the email recipient needs to check later

1. In `asthmaaware26@gmail.com`, check Inbox and Spam for Google Cloud Monitoring
   notifications titled **AsthmaAware dev workload failure**. Some failed executions
   are deliberate acceptance tests, not an unexpected outage. Confirm the project
   in the notification is `asthma-aware` and its link leads to the test incident.
   Receipt is **not yet confirmed**; the user explicitly deferred inbox access.
2. Confirm the channel is enabled and email delivery works. If no message arrived,
   inspect the notification channel in Cloud Monitoring and any required email
   verification or delivery restrictions. Do not assume an OPEN incident proves receipt.
3. Confirm billing budget alerts include the **correct project**, `asthma-aware`,
   and intended recipients. Billing is enabled; budget configuration was reported
   by the owner but its amount/project filter has not been independently verified.
4. Observe the next restored daily publisher run and hourly freshness run. Confirm
   completed executions, new `current.json` selection after publication, and a fresh
   successful diagnostic response. Review actual charges after they settle.
5. After handoff, review temporary setup permissions and narrow deployer access
   where appropriate. Do not remove runtime bucket access or Scheduler's job-scoped
   invoker permission.

The notification email is a destination, **not an automatic IAM access grant**.
Cloud console/API checks require an appropriately authorized Google Cloud account.
No service-account keys or passwords need to be shared.

## Useful links and commands

- [Cloud Run jobs](https://console.cloud.google.com/run/jobs?project=asthma-aware)
- [Cloud Scheduler](https://console.cloud.google.com/cloudscheduler?project=asthma-aware)
- [Monitoring alerts](https://console.cloud.google.com/monitoring/alerting?project=asthma-aware)
- [Forecast bucket](https://console.cloud.google.com/storage/browser/asthma-aware-forecast-dev?project=asthma-aware)

Authenticated local access using an authorized account:

```powershell
gcloud run services proxy asthma-api-dev --project=asthma-aware --region=us-east1 --port=8080
# In another terminal, while the proxy is running:
Invoke-RestMethod http://localhost:8080/health
Invoke-RestMethod 'http://localhost:8080/internal/forecast?lat=33.725&lon=-84.375'
```

Pause the daily publisher from the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Recover.ps1 -Action Pause
```

Other deployment, logging, and recovery commands are in [README.md](README.md).
No automatic deletion or shared-resource cleanup is scheduled.

## What this does not establish

All forecasts are explicitly synthetic. The real asthma model, external data
acquisition, personalized health risk, Supabase, end-user authentication, and
frontend integration remain separate work. Existing forecast resources and the
application database were left untouched. This is a development environment.
