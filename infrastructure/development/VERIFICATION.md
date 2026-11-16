# AsthmaAware cloud verification — November 12, 2026 (America/New_York)

## Current target and result

Project: **`asthma-aware`**, project number `243825508967`, region **`us-east1`**.
The previous project ID was incorrect. The access issue documented in
[the earlier report](VERIFICATION-EARLIER-PROJECT.md) does not apply to this project.
Billing is enabled; all tested setup permissions are available.

The development API and synthetic forecast publisher are deployed. Local checks,
live GCS correctness tests, private API and runtime IAM checks, and live recovery
drills have passed. Scheduling and final operational checks are recorded below.
This validates synthetic infrastructure, not a real forecasting model or health risk.

## Resources created

| Resource | Name |
| --- | --- |
| Artifact Registry repository | `us-east1/asthma-forecast-job` |
| Isolated execution-test job | `asthma-forecast-sample` |
| Durable publisher job | `asthma-forecast-publisher-dev` |
| Private API | `asthma-api-dev` |
| Versioned private bucket | `asthma-aware-forecast-dev` |
| Publisher schedule | `asthma-publisher-daily-dev` (final status below) |
| Freshness probe and schedule | `asthma-freshness-dev`, `asthma-freshness-hourly-dev` |
| Acceptance/permission jobs | `asthma-gcs-acceptance-dev`, `asthma-api-permissions-dev`, `asthma-noaccess-probe-dev` |
| Recovery job | `asthma-forecast-recovery-dev` |
| Workload identities | `asthma-forecast-runtime`, `asthma-forecast-scheduler`, `asthma-publisher-dev`, `asthma-api-dev` |
| Notification channel | `projects/asthma-aware/notificationChannels/8593980526254201537` |

Existing `asthmaaware-forecasts` storage and `asthmaaware-api` /
`asthmaaware-publisher` identities were left untouched. Runtime identities were not
given setup administrator roles. Standard Google service agents were provisioned
as required by API enablement.

API: `https://asthma-api-dev-243825508967.us-east1.run.app` (requires IAM authentication).
The original healthy revision `asthma-api-dev-00001-jx7` is retained for rollback.
The tested current revision is `asthma-api-dev-verify-20261113015117`.

Current image index digest: `sha256:f377ea42cbf53b32e6738b225f6b7af7ba0df248ee12c32ec107ae6f89b297ff`.
Previous image index digest: `sha256:fb3b74f9cb3946800903da4c605b8e81ab0c997babcab98289964f6a866c7122`.
Cloud Run resolves these OCI indexes to their Linux/amd64 manifest digests in
execution records. Both releases have been executed successfully.

## Verified checks

| Check | Result / evidence |
| --- | --- |
| Focused Python tests including existing API/dashboard regressions | 65 passed |
| Offline config checks | 6 passed; default and explicit paths work |
| Deployment behavior checks against gcloud double | 5 passed |
| Monitoring template validation and placeholder rejection | Passed |
| Local pinned Linux/amd64 images | Built; non-root HTTP checks passed |
| Local sample success / controlled write failure | Exit 0 / expected exit 1 |
| Sample job execution | Passed; `asthma-forecast-sample-ndrg8` |
| GCS interruption/race/duplicate/corruption/immutability/rollback drills | Passed; `asthma-gcs-acceptance-dev-djfwj` |
| Durable publisher execution | Passed; `asthma-forecast-publisher-dev-dnvdq` |
| Controlled failed publication | Intended failure confirmed in execution-specific logs; current complete selection preserved |
| Private API | Authorized health and selected-run retrieval passed; unauthenticated call denied |
| API edge cases | Unsupported coverage 404, invalid input 422, disabled dashboard 503, stale fixture rejection tested |
| API read-only identity | Passed; `asthma-api-permissions-dev-89cl8` |
| Ungranted Scheduler storage access | Denied as intended; `asthma-noaccess-probe-dev-j5pnr` |
| Current-release freshness probe | Passed; `asthma-freshness-dev-vnclj` |
| Pointer rollback / restoration through API | Passed |
| Candidate API revision / traffic promotion / rollback | Passed; restored old revision, then returned to tested current revision |
| Previous job image execution / current image restoration | Passed |
| Failure metric and Monitoring incident | Real metric points and an OPEN incident observed |
| Daily publisher scheduling | Forced `asthma-forecast-publisher-dev-wxnt6` and natural `asthma-forecast-publisher-dev-tqvbk` passed; daily 06:00 America/New_York restored and enabled |
| Deliberately overdue data | `asthma-freshness-dev-v26nt` failed for the intended age check; fresh selection restored and `asthma-freshness-dev-hcsm7` passed |
| Hourly monitor scheduling | Forced Scheduler execution `asthma-freshness-dev-4zgv6` passed; `15 * * * *` UTC is enabled |
| Final read-only audit | Passed at `2026-11-13T02:12:39Z`: job/API limits, private API IAM, no workload project roles, versioned private bucket, correct enabled cron schedules, and a valid synthetic API response |

Sanitized raw execution evidence and logs are retained in ignored `evidence/`;
release, acceptance, and recovery checkpoints are in ignored `*.local.json` files.
Those files contain no bearer tokens or database credentials.

## Monitoring and notification boundary

Owner-selected alert destination: **`asthmaaware26@gmail.com`**.
The configured workload-failure policy opened incident
`projects/asthma-aware/alerts/0.oeygrdr05tfh` at `2026-11-13T01:58:19Z`.
**Inbox receipt is intentionally deferred at the user's request.** A configured
channel and open incident do not prove that the recipient read or received email.

An hourly probe checks complete object retrieval, integrity, current validity, and
whether publication is more than 26 hours old. Its failures trigger the workload
failure policy. A separate two-hour absence policy detects a stopped probe after
successful metric history exists. API error and latency policies are also installed.
The direct 25-hour absence design was corrected because Google limits metric-absence
windows to 23.5 hours. The 48-hour synthetic horizon supports daily refreshes.

Final selected run at audit: `a8a64654-d071-405d-8861-9806bb324d62`, valid through
`2026-11-15T02:00:00Z`. Both schedules are enabled: publisher daily 06:00
America/New_York and freshness checks at minute 15 of every UTC hour.
The real overdue-data failure generated an additional incident,
`projects/asthma-aware/alerts/0.oeyh0m7pn29r`, OPEN as of the final status read.
The earlier controlled-publisher incident was CLOSED. The final workload/API
checks are healthy; these alerts were deliberately triggered by acceptance drills.
The failure policy has a 30-minute no-data auto-close window to avoid leaving
one-off test failures open indefinitely. Failure and heartbeat metrics both have
real data points. Inbox confirmation remains deferred.

Natural daily and hourly executions
at their restored schedules require later observation. No two-hour monitor-absence
wait or API load/latency-threshold drill has been performed.

## Fixes made during live verification

- Removed unsupported `--condition=None` from Cloud Run **Jobs** IAM commands;
  bucket and service IAM commands retain their supported condition flags.
- Retried bucket bindings after newly created service accounts propagated.
- Replaced a log-reader check with full Cloud Logging entries. On Windows, quoted
  label-map keys in native CLI filter arguments did not match reliably; execution
  selection now happens in PowerShell after retrieving job-scoped entries.
- Added safe resume from the log stage: successful recent GCS/publisher execution
  evidence must match the actual release's Linux manifest and project namespace.
  This avoids rerunning completed storage tests when only log verification failed.

## Deferred product work and owner follow-up

See [the administrator handoff](HANDOFF.md). Verify the actual alert email, billing
budget's project filter/recipients, and later scheduled executions. Actual charges
can appear after usage; no zero-cost guarantee is claimed. Hourly monitoring adds
executions beyond the original daily-only estimate.

No real ML, NASA/AirNow acquisition, Supabase credentials/query, end-user login,
frontend migration, or production health-risk calculation is deployed. The API's
diagnostic path is explicitly opt-in and returns synthetic provenance. No existing
database migrations or frontend routes were changed.
