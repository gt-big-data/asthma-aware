# Integration development deployment

Google Cloud project `asthma-aware`, region `us-east1`.

| Workload | Resource |
| --- | --- |
| Web | https://asthmaaware-web-243825508967.us-east1.run.app |
| API | https://asthmaaware-api-243825508967.us-east1.run.app |
| Publisher | Cloud Run Job `asthmaaware-forecast` |
| Availability checker | Cloud Run Job `asthmaaware-forecast-check` |
| Storage | Private bucket `asthmaaware-forecasts` |
| Runtime database | Secret `asthmaaware-database-url`, version 1 |

The API uses `asthmaaware-api`, the job uses `asthmaaware-publisher`, and static
hosting uses `asthmaaware-web` service accounts. Web hosting has no bucket or
database grants. The API has bucket reads and access to its runtime secret.
The publisher has object access to the app bucket. The dedicated
`asthmaaware-forecast-check` account has bucket reads and permission to execute
only the publisher job with a pinned-date override. It has no database access.
Supabase is at migration
`0005_exposure_pipelines`.

History uploads carry their preprocessing identity as GCS object metadata. The
API selects matching tagged archives before downloading them, then verifies the
archive's own metadata. If no matching tagged archives exist, retained legacy
publications use the previous content-scan fallback. This prevents new-model
personal requests from repeatedly downloading incompatible old-model history.

The first release uses the integration source at `b280f2d`, followed by the
NASA timestamp precision fix. Source NetCDF decoding on the Linux image introduced
3–6 microsecond rounding errors. Ingestion now accepts up to 10 microseconds of
rounding at exact hourly midpoints and rejects larger/off-grid changes.
Remote time axes are explicitly decoded at microsecond precision because the
year-one epoch exceeds nanosecond precision's range. The ingestion image pins
Xarray 2026.7.0, pandas 3.0.6 and netCDF4 1.7.5, matching the reference preparation
environment and retaining compatibility with the Zarr v2 cloud catalog.
Time-coordinate overflows during remote open or lazy loading use the same
three-attempt retry budget as transport failures. Persistent failures still
stop the publisher and leave the previous complete pointer unchanged.

## Build the backend images

From the repository root, in PowerShell:

```powershell
./infrastructure/release/Build.ps1 -Release YOUR_NEW_TAG -CacheRelease no2-20261124-v3
```

The script stages only application, ingestion, ML source and requirement files.
Checkpoint binaries, local environment files, and downloaded data are excluded.
It builds API and CPU ML images in the existing `asthma-forecast-job` registry.

## Publisher configuration

- Image `integration-ml:no2-20261124-v3` in the project registry.
- Arguments `--model-dir data/models --models o3 no2 --model-threads 4 --only-newer --analysis-lead-hours 0.5`.
- 4 vCPU, 8 GiB, one task, one-hour timeout, zero retries.
- `FORECAST_GCS_BUCKET=asthmaaware-forecasts`.
- `MODEL_GCS_PREFIX=models/checkpoints/o3-no2-9444d777-92c80408`.
- `RISK_REFERENCE_OBJECT=reference/392bcd9ad4365b3cf4ed6745f3c005f3bb904cc1e68ed2fda7517644ff4e2d16.npz`.

Checkpoint identities are pinned in `models/checkpoints/manifest.json`.
The reference is provisional and matches both selected checkpoints and the
explicit experimental historical-analysis lead assumption of 0.5 hours. Actual
forecast inference uses each hour's true forecast lead. This preserves the local
development integration's policy and does not establish forecast skill. PM2.5
and SO2 retain native NASA values on the fine grid. Column NO2 is excluded from
the surface risk computation. See `backend/docs/no2-surface-downscaling.md`.

```powershell
gcloud run jobs execute asthmaaware-forecast --region=us-east1 --account=asthmaaware26@gmail.com --project=asthma-aware --wait
gcloud storage cat gs://asthmaaware-forecasts/published/current.json --account=asthmaaware26@gmail.com --project=asthma-aware
```

Only a complete publication advances the pointer. The job also attempts an
independent environmental-trends refresh after forecast publication.

## API configuration

The API serves `app.main:app`, with two CPUs, 8 GiB, concurrency one, at most two
instances and a five-minute request timeout. It reads the bucket above, uses
the existing Supabase project and its restricted runtime secret, and allows
both hosted web URLs plus localhost port 8081 in CORS. Requests to private
application endpoints verify Supabase access tokens.

## Build the web image

From `Front-end`, set `EXPO_PUBLIC_API_URL` to the API URL above and run:

```powershell
npx expo export --platform web
```

Build `Dockerfile.web` with only `dist/`, `nginx.conf` and the Dockerfile in the
upload context. Deploy to `asthmaaware-web` on port 8080, one CPU, 256 MiB,
maximum two instances, using its dedicated service account. Supabase's
publishable configuration is included in the web export.

## Schedules, checks and alerts

Run `Configure-Schedules.ps1` after building the selected release. It creates or
updates the jobs and schedules, and can be rerun. `-JobsOnly` prepares jobs and
permissions without changing Scheduler. The existing scheduler account invokes
the availability and freshness checks; the availability check invokes the publisher.

`asthmaaware-publish-daily` now targets the lightweight availability job, with
cron `15 17-23 * * *` in UTC: 17:15, 18:15, through 23:15 each day. The resource
name is retained from the original once-daily schedule. The checker reads the
current GCS pointer and NASA metadata. If NASA has nothing newer, it exits
without downloading checkpoints, forecast fields or running inference. A newer
run must have the same complete 120-hour timeline and required variables/units
in chemistry and weather, plus the preceding analysis window, before dispatch.
This preflight cannot guarantee every data transfer succeeds; the publisher
still validates the downloaded fields before publishing.

The checker passes `FORECAST_RUN_DATE` as an execution-local override. The
publisher pins that initialization and never falls back to an older run. It
also checks freshness again before processing and takes a generation-protected
lease at `control/forecast-publication-lease.json`. This prevents overlapping
expensive runs from duplicate invocations. Normal exits release the lease;
after a killed container it expires in 75 minutes (job timeout is 60 minutes).
Later scheduled checks can retry after failure. The publisher fetches NASA data,
runs O3 inference, builds risk/map fields, and uploads complete results.

The probe's `REQUIRED_VARIABLES` matches the current O3 checkpoint plus the four
surface pollutants and also covers the surface-NO2 checkpoint's predictors.
Update it when enabling a model with different predictors.

Run `Configure-Monitoring.py` with Python to configure failure metrics and a
missing-freshness alert. It uses the explicitly named operator login, without
writing its access token to disk. API errors, API latency and workload failure
policies also target the existing shared inbox notification channel. Alert
delivery to that inbox still needs to be confirmed.

The hourly freshness job (`:30 UTC`) checks issue age (40 hours), future coverage, and existence
of the risk and map objects. The app marks forecasts stale after 24 hours while
continuing to use the remaining hours. A missing successful probe for 2.5 hours
raises a separate alert. This covers broken or disabled scheduling after the
success metric has first received data.

The 40-hour limit allows the last 23:15 UTC check to acquire a replacement for
the previous day's 09 UTC run (38.25 hours old), with processing time and grace.
It does not change the app's 24-hour stale label. Forecasts retain their true
initialization timestamps, even when they are published later.

Run `python infrastructure/release/Smoke.py --require-fresh` from the repository
root after a new publication. This checks the hosted routes, the anonymous
authentication boundary, four dashboard days, matching dashboard/map scores,
a rendered map PNG and historical trends. It does not write symptom logs.
Add `--auth` to sign in with the dedicated `SMOKE_TEST_EMAIL` and
`SMOKE_TEST_PASSWORD` from the ignored `backend/.env`, check private profile,
history, saved locations and personalized forecast reads, and sign that session
out. This option needs python-dotenv and the frontend's public Supabase key in
its ignored `.env.local`. Credentials and user data are not printed.
Add `--require-no2` to check the learned ppb layer, the 0–20 ppb display scale,
an Atlanta cell and PNG, and agreement with the risk map's point score.

`Verify-Artifacts.py --pipeline PIPELINE --reference-sha HASH` checks both the
current forecast and historical risk metadata through GCS range reads. It
verifies preprocessing and normalization identities without downloading full
rasters or printing user data.

Supabase's Site URL is configured to the hosted web URL. Runtime data remains in
Supabase; NASA sources, checkpoint/reference assets and derived rasters remain
in private Cloud Storage. No operator database credentials are deployed.

## Rollback

List retained Cloud Run revisions and direct traffic to the chosen previously
healthy revision. Restore the publisher's earlier image digest separately.
For a forecast rollback, select a retained complete publication by updating
`published/current.json` with its observed GCS generation as a precondition.
Keep any objects referenced by retained pointers.

For a model change that reprocesses an already-published NASA run, pause the
availability schedule and run `scripts.activate_forecast_models` in a temporary
ML job with the desired versioned checkpoint/reference settings. It takes the
shared lease and reuses the current publication's immutable raw NASA source.
It deliberately bypasses only the newer-initialization check; normal timestamp,
remaining-coverage, checksum, reference and atomic-publication checks remain.
Verify the serving API supports both old/new artifacts before activation, then
verify the new publication and resume the schedule. Configure-Schedules accepts
explicit Models, ModelPrefix, ReferenceObject and AnalysisLeadHours for repeatable
rollouts and rollback; its defaults select the O3/surface-NO2 release above.

The earlier `asthma-api-dev` and sample jobs belong to the previous development
setup and use a different bucket and pointer format.

## Verified release, 2026-11-20 UTC

- Full publisher execution `asthmaaware-forecast-b9qmq` succeeded in 10m29s.
- Source run `20261119_09z`; all 120 forecast hours and 222 preceding analysis
  hours were validated for the required inputs. Transient time-coordinate
  failures recovered through bounded retries.
- Current publication `c4a59b34-1f81-41ee-92cd-542a1b520008` includes source,
  downscaled O3, risk and map objects. Environmental trends extend through
  2026-11-19 09:00 UTC.
- Fresh hosted smoke check passed: four dashboard days, 96 map frames, matching
  dashboard/map point scores, a PNG frame and environmental trends. Dedicated
  test-account profile, history, saved locations and personal forecast reads
  also passed; anonymous symptom history returned 401.
- 44 targeted ingestion/time tests and eight GCS publication tests passed.
- Publisher and freshness schedules are enabled. API errors, API latency,
  workload failures and missing successful freshness probes have active alerts.
- Scheduler-triggered freshness execution `asthmaaware-freshness-2ncd8`
  succeeded against the new publication. The temporary diagnostic job was
  removed after verification.

This is a development deployment. O3 forecast skill and the provisional fixed
reference still require ML validation. PM2.5 and surface NO2 downscaling are
pending; SO2 stays native. Signup confirmation email delivery and alert inbox
receipt still need manual verification.

## Availability scheduling update, 2026-11-20 UTC

Release images `b280f2d-availability1` contain the metadata-only checker, pinned
newer-only publisher guard, expiring publication lease and 40-hour freshness check.
The serving API/web images are unchanged; their response contracts are unaffected.

- 39 relevant regression tests passed, including readiness, pinned downloads,
  duplicate-run protection, 40-hour age boundaries and existing forecast details.
- Live checker `asthmaaware-forecast-check-x2cld` successfully skipped the already
  published `20261119_09z` run. The guarded publisher execution
  `asthmaaware-forecast-z7h7s` also skipped before model/field downloads.
- Freshness execution `asthmaaware-freshness-gtvmn` succeeded with the new check.
- The current pointer remained `89cf2e96-67c0-478e-906b-332592e1925e`.
- Hosted smoke checks passed for routes, anonymous auth protection, matching
  dashboard/map point scores, map PNG and environmental trends.
- Scheduler `asthmaaware-publish-daily` is enabled with `15 17-23 * * *` and targets
  `asthmaaware-forecast-check`. Freshness remains hourly at `:30`.
- Manually triggered Scheduler delivery started checker execution
  `asthmaaware-forecast-check-j8wpt`, which succeeded in 16.56s and correctly
  reported `status=unchanged` without launching another full publication.

Future-ready dispatch and incomplete-input handling are covered by regression
tests; the first newly available NASA initialization will exercise the full
scheduled dispatch path. An unchanged-run success means only that the check
completed, not that a new forecast was published. Use the publication issue time
and freshness monitoring to assess data age.

## Surface-NO2 release, 2026-11-24 UTC

GitHub main `269c546` (PR #43) was merged with the cloud-release controls on
`backend/no2-cloud-release`. The serving API image is
`integration-api:no2-20261124-v2` (source `dc18a72`); the publisher image is
`integration-ml:no2-20261124-v3` (source `77c0328`). API revision
`asthmaaware-api-00010-6hl` serves the new
artifact format while retaining O3-only compatibility. Frontend source matches
the already deployed web build, so the static site does not need rebuilding.

The pinned model files are O3 `9444d777...` and surface NO2 `92c80408...` under
`models/checkpoints/o3-no2-9444d777-92c80408/`. The reference object is
`reference/392bcd9ad4365b3cf4ed6745f3c005f3bb904cc1e68ed2fda7517644ff4e2d16.npz`.
Its preprocessing is
`surface-o3-no2-unet-001deg-v1:d0153397f12828d3fb5e035e76c6c8cf61d7a0a2778da0a3a37458ecebd269d8`.
The same explicit experimental historical lead of 0.5 hours is used for
reference and history; actual forecast hours retain their real leads.

Controlled activation `asthmaaware-no2-activation-sjgff` succeeded in 10m53s.
It reused immutable source publication `84a879cd-e238-4398-b057-1d05c23dac83`,
NASA initialization `20261123_09z`, and produced combined-model publication
`22d4ea0a-d65c-4323-a759-295cc4124c6e`. Range-based checks verified the matching
forecast/history pipeline and reference IDs. Analysis trends advanced through
2026-11-24 09:00 UTC. No initialization timestamp was relabeled as publication time.

Verification performed during this rollout:

- 73 targeted offline tests passed for inference, model selection, artifacts,
  maps, GCS publication, time handling and scheduler guards.
- After finding repeated legacy-history downloads in hosted personal requests,
  28 focused GCS/scheduler tests passed with the metadata-selection fix. These
  overlap the earlier test selection and are not an additional full suite.
- Both real checkpoints successfully inferred two hours from the retained NASA
  source, with correct ppb units and 420x408 grids.
- Hosted checks passed routes, anonymous history rejection, four dashboard days,
  matching risk/map cells, learned NO2 cell/PNG and 0-20 ppb scale, trends, and
  dedicated-account private/personal reads.
- After the history selection fix, the first dedicated-account personal
  dashboard read took 6.561s; warm dashboard/map metadata reads took 0.503s and
  0.631s. These are observed smoke timings, not a latency guarantee.
- 38 focused tests passed after fixing the production publisher to honor the
  checker's execution-local date without rediscovering a moving latest alias.
  The newer-publication check still runs under the lease, and actual downloads
  still validate the selected run's complete timestamps and required variables.
- No disposable-database reset/integration suite was run; no schema migration
  was required. Existing users and symptom records remain in Supabase.

The original API revision `asthmaaware-api-00008-np2` and O3 publication
`84a879cd-e238-4398-b057-1d05c23dac83` were retained as rollback targets. Restore
an eligible O3-only publication before routing traffic to the older API, which
cannot read combined-model artifacts. Do not restore an expired forecast.

Post-rollout operational checks:

- Availability schedule was resumed with `15 17-23 * * *` UTC. Freshness
  execution `asthmaaware-freshness-xt9q6` succeeded. The temporary activation job
  was removed after its successful completion.
- The restored checker dispatched `20261124_09z`. Execution `qs97j` exposed
  inconsistent latest-alias metadata during redundant discovery; publisher v3
  now honors the verified date and rechecks only publication newness under lock.
- Explicit retry `asthmaaware-forecast-cv5qs` validated November 24 batches but
  failed on an upstream NASA OPeNDAP 502 while downloading the chemistry data.
  The complete combined-model publication above remained current. This is a
  data-ingestion availability limitation, not successful publication of Nov 24.
- At final rollout verification the site served the combined O3/surface-NO2
  model on the retained November 23 NASA initialization, correctly marked stale.
  Later scheduled checks can publish a newer initialization without redeploying
  the API or models. No missing fields were filled and no dates were relabeled.
