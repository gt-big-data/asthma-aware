# Google Cloud and backend verification report

Date: 2026-11-16. Scope began 19:53 UTC; all implementation and checks remained within the shared three-hour deadline of 22:53 UTC.

## Result and scope

The cloud/backend implementation is prepared locally and passes the available local checks. It is NOT deployed or fully cloud-validated. Docker builds, Terraform provider validation, actual PostgreSQL/RLS, live NASA binary retrieval, and authenticated cloud acceptance remain release gates.

Four agents worked with separate ownership: NASA/storage, infrastructure, worker/Tasks/Places, and root API/integration. The worker agent also independently reviewed security and container dependencies.

No ML training, inference, placeholder model, downscaling, population score, or fabricated forecast was added to the new operational path. Existing ml-work source and checkpoints were not modified. Earlier prototype algorithms/tests were preserved as non-executable reference text where superseded. The previous verification report is preserved as [VERIFICATION_REPORT_LOCAL_FORECAST_HISTORY.md](VERIFICATION_REPORT_LOCAL_FORECAST_HISTORY.md); its prototype conclusions are historical, not the current deployment contract.

No Git merge, push, commit or PR occurred in this phase. Existing uncommitted frontend/cloud work was preserved. Local main and last-fetched origin/main are 84ec66c; a read-only remote head check failed because outbound GitHub access is blocked.

## Components implemented

| Component | Implemented behavior | Verification level |
| --- | --- | --- |
| NASA batch | Actual GEOS-CF discovery/pinned retrieval, Atlanta bbox, time/variable/unit validation, bounded retries/deadline, no filling or simulation | Real NetCDF fixture integration; live request blocked |
| Raw publication | Versioned NetCDF/source metadata, content-addressed files, scientific duplicate detection, immutable manifests, generation-CAS selection, orphan/concurrency recovery | Actual local files/SQLite and in-memory generation tests |
| ML handoff | Accept externally produced files with explicit model/units/raw provenance; no model callback; validate stage/copy/promote to separate delivery catalog | Supplied test artifacts only |
| GCS adapters | Bounded binary reads/writes, pinned generations, checksums, content/cache metadata and ADC identity | Client-independent contracts/local adapters; no live bucket |
| Cloud FastAPI | Separate app.cloud entrypoint; existing profile/log routes, forecast availability/metadata/CDN signing, task/Places routes, TLS DB guard, configuration readiness, structured sanitized logs/CORS | ASGI/local JWT tests |
| Cloud Tasks | Deterministic task IDs, durable pending-dispatch state, log-revision dedup, explicit submission and retry recovery, OIDC task transport | Mock queue; actual SDK absent locally |
| Personalization worker | Private OIDC audience/issuer/signature/service-account verification, transactional state handling, idempotent blocked_ml_unavailable with null result | Local signed RSA verification and repository doubles; PostgreSQL test skipped |
| Places | Server-only fixed endpoint/fields, validated query/coordinates, timeout/rate controls, attribution, no content caching, disabled default | Mock HTTP provider tests only |
| Infrastructure | Cloud Run API/job/worker, three private buckets, scoped IAM/secrets, Tasks queue, absent-by-default paused Scheduler definition, optional HTTPS LB/CDN | JSON/structural tests only; Terraform unavailable |
| Containers | Separate non-root images, pinned base digest, narrow source copies and allowlisted context | Isolated Python-source import tests; Docker daemon denied |

Production entrypoints are app.cloud:app and app.worker:app. The historical app.main/Dockerfile.cloud/sample deployment is preserved but excluded from the new images and infrastructure. The compatibility /v1/forecast endpoint returns 503 instead of calculating a placeholder. The frontend must consume actual ML product metadata/delivery when that team supplies outputs; no frontend features were changed in this phase.

## Architecture and contracts

Paused/absent Scheduler -> one-task Cloud Run NASA job -> real NASA input -> private raw catalog -> external ML team -> validated processed staging -> immutable environmental delivery catalog -> Cloud CDN -> app.

App -> Supabase JWT -> FastAPI -> private PostgreSQL, explicit Cloud Tasks submission, forecast metadata and optionally Places. Tasks -> dedicated OIDC dispatcher -> private worker -> PostgreSQL task state. Missing ML remains pending/blocked, never a prediction.

Raw contract: nasa-geos-cf-raw-v1. Output contract: asthma-ml-output-v1. Both retain NASA run/initialization/validity, bounds, units, version and exact object generation/hash/size. Raw footprint bounds include native half-cell edges; sidecar center coordinates remain unchanged. ML output must link to real raw manifest and object bytes, stay within its time/geographic coverage, and explicitly declare environmental-public/no user data.

Raw/processed/delivery stores are separate. Per-bucket generations are rebuilt on copy. Only after all destination files verify does the delivery current pointer advance. No incomplete objects are selected, and older runs cannot regress current. Conflicting versions of one NASA initialization are conservatively rejected rather than silently choosing a model rollout; explicit same-run activation policy is future operator work.

See [BACKEND_API_DOCUMENTATION.md](BACKEND_API_DOCUMENTATION.md) and [NASA_PIPELINE.md](NASA_PIPELINE.md).

## Files changed in this phase

- Added app.cloud, app.worker, API routers forecasts/personalization/places, delivery signing, Tasks and Places services.
- Extended app.config/auth, bounded db.session connection/statement/lock waits, updated Supabase preflight revision/table awareness and backend/.env.example.
- Added db.repositories.personalization and migration 0004_personalization_tasks. No migration was applied.
- Added/reworked forecast_pipeline contracts/catalog/storage/pipeline/ml_handoff and contract-name constants; archived prior draft implementations under reference/*.historical.txt.
- Replaced the prototype publisher CLI with genuine raw ingestion and added publish_processed_forecast for externally supplied outputs. No trained model is loaded.
- Added Dockerfile.api, Dockerfile.forecast, Dockerfile.worker, reviewed dependency ranges, narrow build allowlist and infrastructure/production definitions/tests.
- Added backend tests test_cloud_api, test_cloud_end_to_end, test_cloud_security, test_cloud_performance, test_personalization_tasks, test_places_api and test_real_forecast_pipeline; revised backend publisher tests for its new raw-only contract, preserving original tests as reference text.
- Added GOOGLE_CLOUD_IMPLEMENTATION_PLAN.md, GOOGLE_CLOUD_DEPLOYMENT.md, BACKEND_API_DOCUMENTATION.md, NASA_PIPELINE.md and backend/docs/personalization-and-places.md; updated root/backend scope notices.
- Frontend and ml-work code were not edited during this phase. Earlier uncommitted changes remain present.

## Tests executed and results

**Final available backend suite: 280 passed, 52 skipped, zero failures/errors in 91.28 seconds.**

Final command, from backend with workspace Python 3.12:
python -m pytest -q --ignore=tests/test_grid_resample.py --basetemp=./.pytest_tmp_cloud_verified --tb=short --junitxml=data/verification/cloud_backend.xml

Machine-readable evidence: backend/data/verification/cloud_backend.xml (local ignored artifact).

Additional executed checks:
- Eight infrastructure invariant tests passed, including job limits, default disabled features, private buckets/worker, scoped pointer IAM, CDN settings and source-copy dependencies.
- Validate.ps1 offline check passed. JSON parsed; 19 resource groups. This is not Terraform provider validation or a plan.
- Twelve security checks passed, including dependency closure, production import isolation, private/traversal/query capability rejection and two isolated source-layout imports.
- End-to-end offline test passed: NASA fixture NetCDF -> actual raw validation/publication -> supplied test-only ML file -> staged/delivery promotion -> local ES256 JWT verification -> metadata and signed CDN response -> exact delivered bytes. No real model/cloud/database was substituted into a success claim.
- Real Google-style OIDC signature tests used locally signed RSA fixtures and local certificate responses, not a real Cloud Tasks delivery.
- Both publisher CLI help paths exited successfully without network/cloud operations.
- Seventy Python modules parsed; git diff checks passed with ordinary LF/CRLF notices.
- Local documentation links were checked; no missing targets were found.

The 52 skipped cases require a disposable PostgreSQL/PostGIS environment with TEST_DATABASE_URL and explicit reset opt-in. They include real profile/log transactions/RLS and task/forecast persistence. No database was reset or connected. Raster tests were explicitly excluded because rasterio is absent and dependency installation/network access is blocked. They are NOT counted as passing. ML tests were not changed or run under this backend-only scope.

Earlier integration exposed five failures in obsolete prototype-publisher tests after its interface was replaced. Tests were updated to assert the genuine raw-ingestion contract, not skipped or weakened to retain obsolete scoring behavior; originals are archived. A wrongly selected infrastructure test directory was corrected to the actual test_configuration.py runner. Final listed checks succeeded.

The remaining warning is Starlette's httpx TestClient deprecation. No failed test was reported as passing. Commands stayed below five minutes, retries were bounded, and no training/load loops ran.

## Bugs found and fixed

1. Production/scoring separation: new app entrypoint and narrow image contents exclude prototype sample/model endpoints; no inference or placeholder import at startup.
2. Fresh API image was missing httpx even though local test dependencies masked it; explicit cloud requirements now include it.
3. Containers initially omitted forecast_job.storage used by the new artifact adapter; narrow storage package copies and import regression tests fix that.
4. Staging/CDN mismatch: signed paths must exist in delivery, not only processed staging. Cross-bucket generation-aware copy/promotion and FORECAST_DELIVERY_BUCKET now align origin and catalog.
5. Raw sidecars could disagree with actual NetCDF units/names/endpoints; validation now compares them and rejects missing/nonfinite/negative surface fields without filling.
6. Native-center bounds would reject valid fine-grid edge coverage; raw manifest uses genuine containing-cell footprint bounds instead.
7. Interrupted raw upload with a new acquisition timestamp could permanently conflict with fixed object names. Byte-addressed files let a complete retry recover while leaving unselected orphans; identical-science concurrency reuses the verified winner.
8. Ambiguous queue outcomes are retryable using stable task names and durable pending_dispatch, not duplicated log processing or false completion.
9. Worker signing-key transport failure now returns 503, distinct from invalid-token 401. Configured audience normalization matches the actual queue target.
10. URL signers/authentication reject credential-bearing, ambiguous or traversing paths; signing expires no later than five minutes/product expiry, and key values are excluded from Settings repr.
11. Availability checks query only the relevant namespace per bucket, avoiding unnecessary probes for raw data in delivery and processed data in raw.
12. Production application database requests fail closed without TLS; connection, SQL and lock waits are bounded.

## NASA ingestion and performance

The real job uses the existing NASA GEOS-CF v2 AQC/MET/XGC collection implementation and all configured variables (currently 13), not the old three-variable risk adapter. Bounds and lead times are constrained before fetching; source URLs/run timestamps remain pinned. Disk and durable GCS caches avoid redownloading identical native fields; discovery still checks the current source.

Official NASA catalog and DDS metadata were readable through browsing. A bounded direct Python request failed with URLError caused by socket PermissionError; a browser-tool constrained ASCII request was inaccessible. Therefore live binary acquisition by the implemented command remains UNVERIFIED. Fixture NetCDF serialization/validation genuinely executed, not a screenshot or assumed provider success.

The job has an internal 240s maximum configurable deadline beneath Cloud Run's 300s, two bounded upstream attempts and 20s per request. The parent terminates its process tree/group on deadline. Actual Cloud Run/container execution and resource sufficiency at 1 CPU/512 MiB are not verified; limits were not raised.

Automated local SQLite catalog benchmark, 100 fixture lookups: median 1.822 ms, p95 2.921 ms, extra traced peak 20,038 bytes in the final run. Earlier isolated measurements were approximately 1.95 ms/3.6 ms/29 KB. These exclude native-library baseline memory and cloud network latency; they are not an SLA or a claim of 512 MiB container acceptance. No fake forecast or model was generated outside automated test fixtures.

## Supabase and application status

JWT signature/claims and TLS guards are locally tested. Existing private symptom/profile schemas, user context, revision conflict handling, retry-safe log/outbox writes and time-based finality remain in place.

Migration 0004 defines durable user-owned task status, forced RLS, log/revision constraints, API grants and a restricted worker role. Earlier migration 0003's prototype forecast table is retained for migration-history compatibility but is not the forecast store of the new GCS architecture.

Actual database credentials, a disposable database and accessible Docker daemon are absent. No Supabase query, production data change, migration, role provisioning or policy change occurred. Real PostgreSQL/RLS tests remain skipped. No broad worker access to symptom records is granted in anticipation of ML; future input access must be designed/reviewed explicitly.

Task submission is explicit; the pre-existing outbox is not automatically consumed. Recovery of pending_dispatch currently requires retrying the same request. No sweeper was silently enabled. Missing ML is acknowledged as blocked_ml_unavailable with null result, not retried forever or reported as completed inference.

## Cloud Run, Tasks and CDN readiness

Prepared: separate non-root containers, job/service definitions, 0-1 service scaling, scoped runtime/dispatcher identities, per-secret access, private versioned buckets, bounded queue dispatch/retries and optional HTTPS CDN.

Not verified: Terraform CLI/provider schema/plan, locked release dependencies, Linux image builds/boot, live bucket IAM/readback, actual Tasks SDK/queue/OIDC delivery, DNS/certificates/CDN signed/unsigned behavior and end-to-end hosted app access.

Terraform is not installed; Docker daemon pipe access is denied. The Google Cloud Tasks SDK is declared for container installation but absent from the local environment; transport tests use injected clients. These are release blockers, not merely documentation TODOs.

Default create_scheduler=false creates no schedule; prepared job definition is paused=true. Tasks dispatch, Places, CDN and public API invocation are disabled by default. No job executes as a deployment side effect. Enabling a network-facing API still requires Supabase JWT authorization; the worker retains its private invocation/OIDC gate.

## Security and costs

No credentials or actual user records were exposed or committed. No service-account key files were created. Logs omit authorization, query strings, bodies, signed URLs and SQL values. Worker logs record status/error type without user/task identifiers or predictions. Secret values are injected from existing secret references, not generated in Terraform.

Cloud CDN key material is sensitive and would be retained in Terraform state if an operator later enables it. Encrypted restricted state and controlled key rotation are mandatory. Metadata and private app/Places/task responses are not public-cached. Only externally reviewed environmental outputs may enter delivery storage; declared public flags cannot automatically prove scientific validity or absence of personal content.

Places is disabled and no real provider calls were made. Its selected fields can incur a paid SKU. Local per-instance limits are not a global dollar cap; API restrictions and separate cost approval remain required.

No live Google Cloud project operations, new resources, deployments, API enablement, executions, billing/quota edits or paid workloads occurred during this phase. Public documentation browsing is not a project resource inspection. Existing cloud schedules/resources were not resumed or modified. Current project inventory and the remaining credit balance could not be freshly verified.

Estimated incremental deployed-workload cost for this local preparation: $0; actual account billing is UNVERIFIED. Existing retained resources may still cost money. Available $200 credits are not permission to spend.

For a future separately approved deployment: listed Tier 1 CPU/memory rates imply about $0.0057 for a full five-minute 1-vCPU/0.5-GiB job, or $0.171 for 30 such runs, before other charges/free tiers. The listed global forwarding-rule group costs $0.025/hour (about $18.25/730h month), plus CDN/traffic/IP/storage/other costs. These are conditional estimates, not quotes or account totals.
Sources: [Cloud Run pricing](https://cloud.google.com/run/pricing), [network pricing](https://cloud.google.com/vpc/network-pricing#lb), [CDN signing](https://docs.cloud.google.com/cdn/docs/using-signed-urls).

## Remaining release gates and ML team requirements

1. Install/lock required platform dependencies and validate the Google provider schema/plan in an authorized environment; build/scan/boot all three Linux images.
2. Run real PostgreSQL/PostGIS migration, restricted role, ownership/RLS, task concurrency and symptom-log tests against explicitly disposable data.
3. Perform one bounded genuine NASA acquisition and raw-store readback with permitted network access; measure actual child/parent memory and elapsed runtime under 512 MiB/300s.
4. Have the ML team supply real outputs/units/model provenance satisfying the documented raw/output contracts; verify the staging/delivery handoff with those files. No algorithm is included here.
5. After separate approval only, verify Google IAM, Tasks delivery/OIDC, actual Places behavior/cost restrictions and CDN DNS/certificate/signature/cache boundaries.
6. Review the frontend consumer contract and application ingress. The new API deliberately does not return prototype risk values; actual rendering/model output semantics belong to the app/ML teams.
7. Design an explicit dispatcher/sweeper if automatic outbox processing is desired; consider an operator workflow for same-run model-version activation and safe orphan/retention cleanup.
8. Keep all schedules absent/paused and paid features disabled until explicit approval. Do not treat these deliverables as deployment authorization.

The implementation is ready for authorized local/cloud acceptance work, not claimed production-ready without those checks. Final checks completed at 20:25 UTC, approximately 32 minutes after starting. All task-started test/CLI subprocesses exited; the final Python process inventory contained only two processes predating this task. No development server or acquisition job was left running. Existing unrelated platform processes were preserved.

