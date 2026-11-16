# AsthmaAware backend API and ML integration contract

Deployment entrypoint: `uvicorn app.cloud:app --host 0.0.0.0 --port 8080`.
Worker entrypoint: `uvicorn app.worker:app --host 0.0.0.0 --port 8080`.
These are separate services. Historical `app.main` prototype routes and sample
diagnostics are not mounted by the cloud application. No ML execution, score
generation, downscaling or fabricated forecast fallback occurs in this API.

## Authentication and runtime configuration

All `/v1` application routes require `Authorization: Bearer <Supabase user JWT>`.
Verification checks an ES256/RS256 signature against the configured project's
public JWKS, issuer, authenticated audience/role, UUID subject and expiry. User
identity comes from that verified subject, not request bodies/query parameters.
Missing/invalid JWTs return 401; missing auth configuration/key service returns 503.

Cloud Run's invocation gate is independent. The prepared API is private by default;
a mobile Supabase token alone cannot satisfy Google IAM. Browser/mobile ingress
requires a separately reviewed invocation/proxy design or explicit approval of the
public invocation flag, while retaining all Supabase application authorization.
The worker always remains private and verifies its own Google OIDC identity.

Server configuration uses existing secrets for restricted `DATABASE_URL` and,
on the worker only, `WORKER_DATABASE_URL`. PostgreSQL adapters require TLS. Main
application transactions set the restricted API role and transaction-local user
context; forced RLS protects private records. Model/team integration cannot obtain
personal records by requesting public forecast files. No operator/migration login
belongs in the API container or mobile app.

CORS accepts only explicit configured origins; GET/POST/PATCH/PUT, bearer and
content headers are allowed. An `X-Request-ID` correlates structured logs without
recording query strings, bodies, bearer tokens, SQL values or signed URLs.

## Route summary

| Method | Route | Behavior |
| --- | --- | --- |
| GET | `/health` | Independent process liveness, no external calls |
| GET | `/ready` | Required auth/TLS-DB configuration check; explicitly does not probe connectivity |
| GET | `/v1/forecasts/availability` | Separate raw/processed status and ML pending/published state |
| GET | `/v1/forecasts/metadata?product=raw\|processed` | Current validated immutable product manifest |
| GET | `/v1/forecasts/delivery?name=...` | Short-lived CDN URL for an exact current processed object |
| GET | `/v1/forecast` | Compatibility 503 until the frontend consumes the ML product contract; never computes a placeholder |
| GET/PATCH | `/v1/users/me` | Existing own profile/timezone contract |
| PUT/GET | `/v1/symptoms/{log_date}` | Existing owned daily symptom logs/revisions |
| GET | `/v1/symptoms/today` | Today resolved from profile timezone |
| GET | `/v1/symptoms` | Bounded date history/pagination |
| POST | `/v1/personalization/tasks` | Validate owned log/revision and enqueue deterministic durable task |
| GET | `/v1/personalization/tasks/{task_id}` | User-scoped durable state |
| POST | `/v1/places/search` | Opt-in bounded Google Places Text Search, disabled by default |

OpenAPI is generated at `/openapi.json` and interactive docs at `/docs` on the API;
the worker has neither interactive docs nor OpenAPI exposed.

## Forecast availability

`GET /v1/forecasts/availability` returns, for example, raw `available`, processed
`pending` and `ml_status: pending` after genuine raw ingestion but before ML output
is supplied. This status is not a fabricated forecast. Current-product checks use
UTC intervals with inclusive start/exclusive end. Corrupt manifests/storage failure
return 503, not a healthy empty result. Metadata/delivery/availability responses
are `Cache-Control: private, no-store`.

Valid coordinates/geography are defined by the manifest, not arbitrary generated
statewide coverage. NASA hourly mean timestamps remain in raw NetCDF; the manifest
exposes the covered averaging-window interval. Source initialization is never
rewritten to make an old product appear fresh.

## Raw NASA input contract

`nasa-geos-cf-raw-v1`, source `NASA GEOS-CF v2`, product `raw`:

- Daily NASA `run_id`, UTC initialization and validity interval.
- Versioned native NetCDF and source metadata, retaining all configured source
  variables, original units, 0.25-degree coordinates and source URLs.
- Geographic bounds, variable-unit map and request/artifact version.
- Each object reference has `name`, positive `generation`, SHA256,
  `size_bytes`, `content_type`. Manifests are at most 1 MiB; individual artifact
  reads/writes at most 64 MiB. Ingestion bounds native cells/hours before downloading.
- Immutable path: `raw/<NASA-run>/<request-version>/...`; conditional pointer
  `raw/current.json`. A failed/partial upload never promotes that pointer.

Real ingestion entrypoint:

```powershell
python -m scripts.publish_nasa_forecast --output-dir data/nasa --hours 48 --bbox 33.45,34.05,-84.80,-83.95 --deadline 240
```

With future explicit cloud execution approval, `--gcs-bucket` selects an actual raw
bucket and attached ADC identity. Without it, actual acquired files are stored in
an isolated local catalog. No mock provider switch exists in this command. Tests
substitute upstream readers only inside test fixtures. Raw publication remains raw;
it does not invoke an ML callback or fill missing values.

## ML team's output handoff

ML processing is external to this implementation. Supply real output files and an
`asthma-ml-output-v1` manifest with:

- `product: processed`, same NASA source/run/initialization, explicit artifact
  version, covered interval/bounds and output variable units.
- `model: {name, version, input_contract: nasa-geos-cf-raw-v1}` with actual model
  provenance, plus `input_raw_manifest` pointing to the exact raw generation/hash.
- `audience: environmental-public` and `contains_user_data: false`.
- File declarations `name`, `relative_path` (local supplied file), SHA256,
  `size_bytes`, `content_type`; generations are rebuilt for each destination store.

Supported file MIME types are JSON, NetCDF, PNG, WebP and Mapbox vector tiles. The
backend validates file existence/containment/hash, input linkage and declared public
scope. It cannot prove scientific correctness or detect every form of personal
data inside arbitrary files: ML/operator review remains required. Column NO2/O3,
surface concentrations and temperature are distinct quantities and must not be
relabeled. No model architecture, predictors or output values are invented here.

```powershell
python -m scripts.publish_processed_forecast --manifest PATH_TO_REAL_MANIFEST.json --files-root PATH_TO_REAL_FILES --raw-bucket RAW --processed-bucket STAGING --delivery-bucket DELIVERY --deadline 240
```

This command performs validation/storage only. Files stage into the private
processed bucket, then copy with checksum verification into the private
environmental delivery bucket. Every object is immutable/create-only; only after
the complete manifest verifies does `processed/current.json` in delivery advance
using generation preconditions. The API catalog and CDN origin use that same
delivery bucket. There is no ML service deployment in this task.

## Forecast delivery

Configure `FORECAST_RAW_BUCKET`, `FORECAST_PROCESSED_BUCKET`,
`FORECAST_DELIVERY_BUCKET`, HTTPS `FORECAST_DELIVERY_BASE_URL`,
`FORECAST_CDN_KEY_NAME` and the existing secret `FORECAST_CDN_SIGNING_KEY`.
Only exact files of a validated, currently available processed manifest receive
signed URLs. Raw paths, traversal, private files, arbitrary hosts and arbitrary
prefixes are rejected. URLs expire within five minutes or the product's end,
whichever is earlier. Signed URLs are bearer capabilities; do not log/share them.
The application API is not a proxy for arbitrary GCS paths.

Cloud CDN remains disabled in the prepared infrastructure. No fake tiles exist;
before actual ML output and separately approved CDN setup, delivery returns 503.
Immutable environmental objects have public cache headers only inside a private
authenticated origin; private app/task/Places responses are never public-cached.

## Private symptom logging

The existing [profile/symptom contract](backend/docs/application-api.md) remains
unchanged: severity 1–5, one active record per user/local date, edits for today and
the previous five dates, revision conflicts, retry-safe observation/outbox writes,
automatic time-based midnight finality, and DST-aware timezone boundaries. Old
outbox records are retained. This slice does not dispatch that outbox automatically;
task submission is explicit. No exposure/personalization result is fabricated.

## Asynchronous task request/status

POST body:

```json
{"log_id":"11111111-1111-4111-8111-111111111111","log_revision":1,"idempotency_key":"22222222-2222-4222-8222-222222222222"}
```

The log must belong to the JWT user and match its current revision. A private task
row is committed as `pending_dispatch` before calling Cloud Tasks. The deterministic
queue name and one-task-per-log-revision constraint suppress duplicate billable
dispatch. Successful submission returns 202 with task ID/status. A queue/RPC failure
returns sanitized 503; retry the same idempotency key to recover pending dispatch.
No recovery/sweeper infrastructure is silently started.

Worker payload is only `{"task_id":"..."}`. It cannot set a user ID or predictions.
Cloud Tasks attaches Google OIDC using a dedicated dispatcher identity and exact
worker origin audience. Worker verifies signature, issuer, expiry, audience,
verified email and exact expected service account, in addition to private Cloud Run
IAM. Task handling is transactionally locked and retry-safe. Since ML is absent,
it records `blocked_ml_unavailable` and acknowledges with 200 and `result: null`;
it does not repeatedly retry unavailable ML or claim completed inference.

Worker log-input/model permissions and a real ML processing adapter are future
contracts, not broad credentials granted in anticipation. Status supports future
completed/failed states but this worker does not invent a completed result.

## Places

`POST /v1/places/search` body: `{"query":"Georgia Tech","latitude":33.7756,"longitude":-84.3963}`.
Query length is 2–120; optional coordinates must be supplied together and be finite.
The server fixes upstream endpoint, five-result page size and response fields;
clients cannot select URLs, key material or field masks. Five-second timeout, zero
automatic retries, per-instance five/user and thirty total requests/minute caps.
Provider 429 maps to 429 with Retry-After; invalid/unavailable provider maps to 503.
Display returned Google attribution. No provider content is persistently cached.

Disabled unless `PLACES_ENABLED=true` and a server-only restricted key is mounted.
The selected display-name/location fields may trigger a paid Places SKU. Local rate
limits are not a distributed billing ceiling; future activation requires separate
cost approval and key/API restrictions. No live Places requests were made here.

## Verification limits

Unit/local fixture checks do not establish a live NASA download, GCS permissions,
Google Tasks delivery, Places billing/behavior, Cloud Run image boot or actual
PostgreSQL RLS. See [VERIFICATION_REPORT.md](VERIFICATION_REPORT.md) and
[GOOGLE_CLOUD_DEPLOYMENT.md](GOOGLE_CLOUD_DEPLOYMENT.md) for executed checks and
remaining release gates. Nothing in this document authorizes deployment or paid
requests.
