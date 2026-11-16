# AsthmaAware verification report

Date: 2026-11-16. Verification began at 07:47 UTC. Implementation and successful
final test/build checks were completed by 08:19 UTC (about 33 minutes). A later
read-only final check returned after the session clock advanced to 14:34 UTC;
total elapsed wall time therefore exceeded the requested three-hour window.
No further implementation was started after observing that limit; only completion
status and this timing correction were recorded. All changes are local and remain
uncommitted. The final process inventory showed only processes predating this
verification, and all seven checked local documentation links resolved.

**Result: ready for further local development, not verified for production.**
The live forecast path is implemented and passes representative offline tests.
Real NASA acquisition, live Supabase publication/RLS and signed-in device/browser
end-to-end flows remain unverified because required access is unavailable.

## 1. Work completed

- Reviewed backend/frontend/authentication/symptom logging, database migrations,
  NASA ingestion, existing local cloud code and ML module boundaries.
- Preserved pre-existing uncommitted infrastructure work. Local `main` and the
  previously fetched `origin/main` both point to `84ec66c`. A read-only remote-head
  check failed because outbound GitHub access is blocked. No merge, commit, push
  or PR was performed during this verification phase.
- Completed the development forecast adapter, manual bounded publisher, compact
  forecast database migration/repository, shared frontend forecast state and live
  Dashboard/Map consumers.
- Added meaningful contract, conversion, cache, authentication, publication,
  expiry, grid-conservation and client tests; corrected a test-fixture compatibility
  issue without changing the assertions being tested.
- Added setup/architecture/limits documentation and an offline benchmark.

The earlier implementation phase fetched/fast-forwarded main and reconciled three
overlapping local cloud files before the later no-merge restriction. Those edits
and the original backup stash are preserved. Do not reapply
`pre-real-data-integration`: its tracked changes have already been restored.

## 2. Functionality verified locally

| Area | Executed evidence | Limit |
| --- | --- | --- |
| NASA conversion | Actual xarray/NetCDF write/read tests, source units, mole fraction → ppb, midpoint → hour start | Upstream responses represented by fixtures |
| Grid subdivision | 28×24 Atlanta subcells tile the native footprint with unchanged values | Approximate kilometre geometry, no learned detail |
| Population placeholder | Equal weights, clipping, missing/nonfinite/type rejection | Engineering scale, not calibrated health risk |
| API | FastAPI requests exercising real file artifacts, auth requirement, 200/401/404/422/503, provenance, health | Test identity override for data requests; JWT signatures tested separately |
| Auth | Locally generated ES256 JWTs and fixture JWKS; invalid claims/signature/origin rejection | No live Supabase login/JWKS call |
| Snapshot cache | Reuse, TTL expiry, file replacement invalidation, failure closure | DB read boundary mocked |
| Publication | Atomic local output; invalid input keeps prior complete file; identical file no-op | File adapter assumes one local writer |
| Database adapter | SQL/upsert/idempotency contract and Alembic SQL generation | No PostgreSQL connection executed |
| Frontend | Forecast/client/date/coordinate tests, TypeScript and lint, production web export | No signed-in interactive browser/native smoke test |
| Infrastructure | Offline configuration, gcloud doubles and monitoring template checks | No cloud acceptance test in this phase |

## 3. Bugs discovered and fixed

1. Dashboard risk values and statewide map data were hardcoded/generated. Replaced
   those consumers with one shared authenticated forecast path. Failed/absent data
   produces explicit messages rather than fabricated risk, pollutants or coverage.
2. The in-progress single-collection publisher omitted `lead_time`, which the
   existing NetCDF writer encodes. It now creates that coordinate and is covered
   by a real NetCDF round-trip acquisition test.
3. Changing `OPEN_RETRIES` alone would not change Python's bound default argument.
   The bounded publisher explicitly applies one request attempt and 30s timeout.
   It probes only the pollution collection, not unrelated met/satellite sources.
4. Tightened the artifact contract: native centers, matching daily initialization
   and run ID, contiguous identical time axes, timezone/hour semantics, finite
   nonnegative pollutant values, source/units and memory bounds. Missing required
   data rejects publication instead of becoming a zero score.
5. Corrected map color scaling: values are mapped to the fixed 0–100 development
   score scale, not normalized separately to every viewport's minimum/maximum.
6. Added web iframe support to the map, empty-data clearing and a sandboxed message
   bridge. This is compiled/typechecked; real tile/render interaction is unverified.
7. Replaced permanently cached GPS coordinates with a time-limited shared cache.
   Manually selected/Atlanta map markers are labeled "Forecast location", rather
   than claiming to be the user's physical location.
8. A point/account change cannot show the previous point's score under new
   coordinates. Requests are cancelled on replacement/unmount and account changes
   clear forecast state. Both screens share the same result instead of refetching
   on tab switches. No forecast API polling was introduced.
9. API request timeout now includes session-token lookup; a hung auth lookup no
   longer leaves the forecast request waiting indefinitely before its timer starts.
10. Authentication now rejects credential-bearing/non-origin Supabase URLs before
    key lookup, matching the preflight's existing configuration restrictions.
11. The installed pytest 9.2 monkeypatch attribute restoration does not preserve
    Starlette's dynamic State storage. The dashboard fixture patches the underlying
    settings mapping, preserving the original value correctly. Assertions remain.
12. Initial static web export failed: installed Expo server renderer calls
    `getRouteInfoForMatchedRoute`, absent from pinned `expo-router@58.0.24`.
    Configured the authenticated web app as a client-rendered SPA; export passes.
    **The dependency mismatch itself is not repaired.** Per-route static rendering
    requires dependency alignment later; SPA hosting needs an index.html fallback.

## 4. Files modified/added

New forecast implementation:

- `backend/app/services/live_forecast.py`
- `backend/app/api/live_forecast.py`
- `backend/db/repositories/live_forecast.py`
- `backend/db/migrations/versions/0003_live_forecast.py`
- `backend/scripts/publish_nasa_forecast.py`
- `backend/scripts/benchmark_live_forecast.py`
- `Front-end/lib/forecast.ts`
- `Front-end/providers/ForecastProvider.tsx`
- `Front-end/components/ForecastScreen.tsx`

Integration/configuration changes:

- `backend/app/main.py`, `backend/app/config.py`, `backend/app/auth.py`
- `backend/scripts/supabase_preflight.py`, `backend/.env.example`
- `Front-end/app/_layout.tsx`, `Front-end/app/(tabs)/dashboard.tsx`,
  `Front-end/app/(tabs)/map.tsx`
- `Front-end/components/HeatmapView.tsx`, `Front-end/constants/userLocation.ts`,
  `Front-end/lib/api-client.ts`, `Front-end/app.json`, `Front-end/package.json`
- Root/backend/frontend ignore files and `backend/pytest.ini` for local artifacts.

Tests:

- Added `backend/tests/test_live_forecast.py`, `test_publish_nasa_forecast.py`,
  `test_live_forecast_database.py` and `Front-end/tests/forecast.test.ts`.
- Extended `backend/tests/test_auth.py`; fixed the fixture in
  `backend/tests/test_dashboard_api.py`.

Documentation: root/backend/frontend READMEs, `backend/docs/live-forecast.md`,
`infrastructure/REAL_DATA_PLAN.md`, `VERIFICATION_PLAN.md`, this report.

Pre-existing uncommitted `backend/forecast_job`, cloud Docker/lock files,
`forecast_diagnostic.py`, cloud tests and `infrastructure/` remain in the workspace.
They were inspected/tested where safe, not replaced or deployed.

## 5. Commands and test results

Backend commands below ran from `backend/` using the workspace
`.venv-integration/Scripts/python.exe` (Python 3.12). The old `backend/.venv`
references an inaccessible Python installation and was not modified.

| Check | Observed result |
| --- | --- |
| Initial unrestricted backend pytest discovery | Failed collection: missing `rasterio` |
| Backend: `python -m pytest -q --ignore=tests/test_grid_resample.py --basetemp=./.pytest_tmp_final --tb=short` | **218 passed, 51 skipped**, 43.46s |
| Focused forecast/publisher/database/auth/preflight regression run | **68 passed, 2 skipped**, 5.43s; included in suite above |
| Frontend: `npm run typecheck` | Passed, no TypeScript errors |
| Frontend: `npm run lint` | Passed, no lint errors |
| Frontend: `npm test` | **13 passed**, zero failures/skips |
| Web: `EXPO_OFFLINE=1 CI=1 expo export --platform web --output-dir dist-verification` | SPA export passed; 9 JS bundles and 37 assets; an observed warm bundle run took 11.37s |
| Initial static web export | Failed for Expo/router API mismatch described above |
| `ml-work`: `python -m pytest tests -q --collect-only --tb=short` | **4 collection errors**: missing `rasterio`/`torch`; no ML tests executed |
| Offline `forecast-job/Test-Config.ps1` | **6 checks passed**, PowerShell parsing passed |
| Offline `development/Test-Deploy.ps1` | **5 checks passed**, gcloud was an in-process double |
| Offline `development/Test-Monitoring.ps1` | JSON/template/placeholder checks passed; no cloud commands |
| `git diff --check` / cached check | Passed after removing an extra README EOF blank line; CRLF normalization notices only |

The 51 skipped backend cases require an explicitly disposable PostgreSQL/PostGIS
database (`TEST_DATABASE_URL` plus `TEST_DATABASE_ALLOW_RESET=true`). This includes
real symptom-log transactions/RLS and the two new forecast PostgreSQL tests.
**Skipped is not passed.** Raster tests are excluded explicitly because their
dependency cannot be installed with the current network restrictions. ML model
tests are separately recorded as collection failures, not hidden/skipped successes.

Warnings observed: Starlette's httpx TestClient deprecation; Node's typeless-package
ES-module warning in TS tests; Expo's NO_COLOR/FORCE_COLOR warning. None was treated
as evidence of functional success or suppressed by changing assertions.

No individual executed test/build command exceeded five minutes. No training,
stress loop or repeated retry cycle was run.

## 6. Backend/frontend integration status

The implemented flow is: manually published NASA artifact → authenticated
`GET /v1/forecast` → shared frontend state → concentrations, hourly development
score and native-cell display map. Existing profile and symptom routes remain
registered, with the same private-schema design and user JWT/RLS context.

Loading, missing configuration, unsupported coverage, failed requests, malformed
responses and expired data have explicit code paths. Pure/client/API behaviors
are tested. Native builds, sign-in, map interaction, actual location permissions,
symptom entry editing and navigation have not been exercised interactively.
The built frontend has no Supabase project/key configured here.

The existing private Cloud Run IAM gate would independently reject a Supabase-only
browser token. The local API is the integration target in this slice; production
ingress/authentication needs a separately reviewed design, not an IAM change here.

## 7. NASA pipeline status

Configured source is NASA GEOS-CF v2's OPeNDAP AQC collection. The public catalog
was viewable through a web read earlier in the session, but that is not a successful
forecast acquisition. A direct Python `.dds` request failed with Windows socket
permission error 10013. No live forecast was downloaded or represented as live
test evidence. The existing ingestion tests and new fixtures validate parsing,
subsetting assumptions, units, timestamps, invalid/missing responses and conversion.

The manual publisher downloads only three pollution fields for one native point,
up to 48 requested hours. It has a 180s default whole-process deadline, configurable
10–240s; the parent cancels the child process tree/group on timeout. Cancellation
orchestration was tested with a process double, not a hanging real NASA service.
Recent complete local artifacts are reused before any network request.

## 8. Supabase and symptom logging status

No `backend/.env`, `SUPABASE_URL`, `DATABASE_URL`, `FORECAST_PUBLISH_DATABASE_URL`
or disposable test database was configured. Docker's daemon pipe is denied, so no
isolated PostgreSQL/PostGIS container was started. No shared migration, database
query, user-data write, policy change or auth-account mutation was performed.

`0003_live_forecast` is generated/reviewed locally only. It introduces the separate
`forecast.current` singleton, read-only API grants, a restricted forecast writer,
forced RLS, and explicit revocation from public/Supabase browser roles. It does not
change the existing `app` policies. Transactional promotion rejects older runs;
identical retries do not rewrite the row. Conflicting same-run snapshots are
rejected, so first publish the full intended point/coverage/horizon for a run.

Existing logging code was reviewed for verified user IDs, one active log per
user/date, optimistic revision conflicts, retry idempotence, transactional outbox
insertion, midnight/DST handling and history query batching. Its real transaction
and RLS tests remain skipped; local JWT, date/coordinate and API-client tests pass.
Exposure matching/outbox processing and personalized model fitting are not wired.

## 9. Performance and reliability

Executed `python -m scripts.benchmark_live_forecast` against a representative
400-native-cell × 48-hour fixture, not a production service:

| Measurement | Observed |
| --- | --- |
| JSON artifact size | 1,840,610 bytes |
| Snapshot build with allocation tracing enabled | 440.7 ms |
| Additional traced peak during snapshot build | 3,835,873 bytes; excludes already-decoded JSON |
| Mean cached lookup over 1,000 calls | 0.033 ms |
| Pixels allocated for selected Atlanta native cell/hour | 672 |
| Full-state/full-horizon duplicated pixel records avoided | 12,902,400 |

This is an in-process microbenchmark, not HTTP/network latency or a capacity/SLA
claim. Changes reduce repeated parsing/scoring and DB reads with a single-flight,
30s per-process snapshot cache; each request still enforces current validity.
File replacements invalidate immediately. Failed refreshes do not extend the old
snapshot's TTL. The frontend shares data across tabs and generates pixels only
when the selected hour changes. Local identical publication avoids replacement;
PostgreSQL duplicate/older publication avoids an update.

## 10. Security concerns and boundaries

- No credentials or user records were read, printed, committed or placed in the
  browser bundle. Server writer/operator credentials remain separate from the
  public publishable key. Batch failures emit sanitized error types.
- JWT validation retains signature/issuer/audience/expiry/role/UUID checks. Demo
  identity is not accepted by V1 forecast/profile/symptom routes.
- RLS/grants have offline evidence only; actual isolation and restricted login
  membership must be proved on an isolated database before shared rollout.
- Map tiles/CDN scripts are external dependencies. Iframes are sandboxed and no
  tokens/symptom records are sent to them. Geographic views/markers inherently
  reveal the selected area to map providers; review privacy before production.
- The score is explicitly an uncalibrated development index. It is not AQI,
  medical advice, a learned risk model or personalized from patient symptoms.
- Existing auth-account deletion cleanup and exposure/model workers are unfinished.
  Native security/storage behavior and dependency vulnerability auditing were not
  verified; network access for an updated audit is unavailable.

## 11. Cloud resources accessed or created

No live Google Cloud access, resource creation/start, deployment, image upload,
service enablement, billing edit or production Supabase access occurred in this
verification phase. Offline deployment tests used a function named `gcloud`, not
the real executable. No model training or paid compute was run.

Earlier in the same conversation, before the verification phase, the two existing
development schedules (`asthma-publisher-daily-dev`,
`asthma-freshness-hourly-dev`) were successfully paused at the user's request.
The ten most recent listed executions then had zero running tasks. They were not
resumed. Current restricted permissions prevent a fresh project-wide cloud audit;
this report does not claim all historical/shared resources are stopped or deleted.

All foreground test/export commands were allowed to terminate; no development
server or batch acquisition was started and left running. Pre-existing unrelated
Codex/other-project processes were not terminated.

## 12. Costs

**Verified behavior:** no cloud workload was launched or deployed by this
verification. All computation/testing/build output was local.

**Estimate:** incremental cloud workload cost from these actions is $0 because
none were started. Actual billing/remaining credit balance was not queried and is
not verified. Existing retained storage/images/logs or unrelated resources can
still incur charges. This is not a guarantee that the account's total is below
$200, and the credit balance was never treated as permission to spend.

## 13. Unresolved work

1. Real NASA download and a fresh point/region artifact smoke test.
2. Disposable PostgreSQL/PostGIS tests: migrations, TLS identities, transactional
   symptom writes, concurrent publication, and actual RLS denial checks.
3. A separately reviewed development Supabase migration/role setup and real-user
   token smoke test; no production mutation is authorized by this report.
4. Signed-in browser/native interactions and external map-provider failure tests.
5. Full regional acquisition/map delivery; current pilot map shows one selected
   native cell. No coverage is invented outside published cells.
6. Expo/router dependency alignment if static rendering is needed; SPA route
   fallback must be configured for any future hosting.
7. Install optional raster/ML dependencies in an authorized environment and rerun
   excluded/failed-collection suites. No trained model is needed for this pilot.
8. Product exposure matching, outbox consumption, personalized weights, model
   validation, account cleanup and production ingress remain separate work.
9. The alert email to `asthmaaware26@gmail.com` remains unconfirmed: the connected
   Gmail account earlier was a different account and the phrase search returned
   no matching messages. No inbox receipt was inferred from cloud alert settings.

## 14. Recommended next steps and restart handoff

Start by reading this report and `backend/docs/live-forecast.md`. Preserve the
current uncommitted state and existing backup stash. Do not pull/merge/apply the
stash blindly. The project can continue local development immediately using
representative fixtures and `.venv-integration`; do not substitute these fixtures
for real NASA output in a live demonstration.

In an authorized development environment, install the documented optional
dependencies, run an explicitly disposable PostGIS database and execute the
previously skipped tests first. Then acquire a small real NASA forecast under the
existing deadline, verify its provenance, and test the authenticated frontend
against a local API. Only after those pass should separately authorized shared
database/hosting work be considered. Keep schedules paused and do not deploy or
spend cloud credits as part of these remaining local verification steps.
