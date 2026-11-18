# Deploy the dashboard forecast to Google Cloud

Run these commands in **Git Bash** on the development machine, or in **Google Cloud Shell**, from the repository root,
using a checkout that contains this guide, the GCS adapter, and the backend
Dockerfile. Use the team's Google Cloud project with billing enabled. Your
Google account needs permission to enable APIs, create the resources below,
and deploy Cloud Run with the two service accounts. In Git Bash, first run
`gcloud auth login`. Use Cloud Shell once this branch is available in its
checkout.

## 1. Set the project and create the bucket

Replace the project ID and the allowed web origin. For a local Expo browser
connecting to the cloud API, use the localhost origin shown here. A deployed
frontend needs its own exact HTTPS origin with no trailing slash.

```bash
PROJECT_ID='YOUR_ASTHMAAWARE_PROJECT_ID'
REGION='us-east1'
BUCKET="${PROJECT_ID}-forecasts-dev"
WEB_ORIGIN='http://localhost:8081'
SUPABASE_URL='https://robbrnvlnysxeqvgaupt.supabase.co'

gcloud config set project "$PROJECT_ID"
gcloud services enable storage.googleapis.com run.googleapis.com \
  artifactregistry.googleapis.com cloudbuild.googleapis.com \
  secretmanager.googleapis.com iam.googleapis.com

gcloud storage buckets create "gs://$BUCKET" \
  --location="$REGION" --uniform-bucket-level-access --public-access-prevention
```

Bucket names are globally unique. If the name is taken, choose another name
and update `BUCKET` before continuing.
[Bucket creation reference](https://docs.cloud.google.com/sdk/gcloud/reference/storage/buckets/create)

## 2. Create separate Cloud Run identities

```bash
gcloud iam service-accounts create asthmaaware-api
gcloud iam service-accounts create asthmaaware-publisher

API_SA="asthmaaware-api@${PROJECT_ID}.iam.gserviceaccount.com"
PUBLISHER_SA="asthmaaware-publisher@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$API_SA" --role=roles/storage.objectViewer
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:$PUBLISHER_SA" --role=roles/storage.objectUser
```

The API reads published objects. The publisher needs to create artifacts and
replace the current pointer. Object User also permits deletion on this
development bucket. Cloud Run authenticates using the attached identities and
Application Default Credentials.
[Storage roles](https://docs.cloud.google.com/storage/docs/access-control/iam-roles) ·
[Cloud Run service identity](https://docs.cloud.google.com/run/docs/securing/service-identity)

## 3. Add the runtime Supabase secret and build the image

In Secret Manager, create `asthmaaware-database-url` and paste the value of
`DATABASE_URL` from the local ignored backend environment file. Use the
restricted runtime login and its existing TLS settings. The forecast job has
no database dependency. Grant the API identity access to the secret.

```bash
gcloud secrets add-iam-policy-binding asthmaaware-database-url \
  --member="serviceAccount:$API_SA" --role=roles/secretmanager.secretAccessor

gcloud artifacts repositories create asthmaaware \
  --repository-format=docker --location="$REGION"

IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/asthmaaware/backend:dashboard-dev"
gcloud builds submit backend --tag "$IMAGE"
```

If Cloud Build reports a write permission error, identify the build identity
with `gcloud builds get-default-service-account` and have the project admin
grant it Artifact Registry Writer on the `asthmaaware` repository. The build
context excludes local environment files and downloaded data.
[Cloud Run secrets](https://docs.cloud.google.com/run/docs/configuring/services/secrets) ·
[Cloud Build container images](https://docs.cloud.google.com/build/docs/building/build-containers)

## 4. Deploy and execute the publisher

Upload the fixed historical reference from the development checkout first.
The current reference is provisional; use a new versioned object name for a
later reviewed reference. Run this from the repository root on the machine
containing the file.

```bash
gcloud storage cp backend/data/risk/reference-development.npz \
  "gs://$BUCKET/reference/development-v1.npz" --if-generation-match=0
```

```bash
gcloud run jobs deploy asthmaaware-forecast \
  --image="$IMAGE" --region="$REGION" --service-account="$PUBLISHER_SA" \
  --set-env-vars="FORECAST_GCS_BUCKET=$BUCKET,RISK_REFERENCE_OBJECT=reference/development-v1.npz" \
  --command=python --args=-m,scripts.publish_dashboard_forecast \
  --tasks=1 --parallelism=1 --memory=1Gi --task-timeout=30m --max-retries=0

gcloud run jobs execute asthmaaware-forecast --region="$REGION" --wait
```

The job uploads the regional NetCDF under `raw/geos-cf/<publication-id>/` and
the processed JSON under `published/<publication-id>/`. It advances
`published/current.json` only after both uploads succeed. A generation check
prevents a competing job from silently overwriting the pointer, and an older
NASA run cannot supersede a newer one. With the reference configured, the job
downloads preceding analysis and publishes `risk.npz` (exposures and hourly
population scores) and the historical analysis artifact before advancing the
pointer. The serving grid is 0.01°. Map PNG responses are derived on demand from
the private risk file; no separate public image bucket is needed. See
[risk-map.md](risk-map.md).
[Cloud Run jobs](https://docs.cloud.google.com/sdk/gcloud/reference/run/jobs/deploy) ·
[Cloud Storage write preconditions](https://docs.cloud.google.com/storage/docs/request-preconditions)

## 5. Deploy FastAPI and connect the frontend

For this development service, allow the browser to reach FastAPI. Symptom
routes verify Supabase bearer tokens in FastAPI. The population forecast route
is public. The storage bucket stays private.

```bash
gcloud run deploy asthmaaware-api \
  --image="$IMAGE" --region="$REGION" --service-account="$API_SA" \
  --no-invoker-iam-check --max-instances=2 \
  --set-env-vars="FORECAST_GCS_BUCKET=$BUCKET,SUPABASE_URL=$SUPABASE_URL,CORS_ORIGINS=$WEB_ORIGIN,DB_POOL_SIZE=2,DB_MAX_OVERFLOW=0" \
  --set-secrets="DATABASE_URL=asthmaaware-database-url:1"

API_URL="$(gcloud run services describe asthmaaware-api --region="$REGION" --format='value(status.url)')"
curl -fsS "$API_URL/health"
curl -fsS "$API_URL/v1/dashboard/risk?lat=33.75&lon=-84.39"
curl -fsS "$API_URL/v1/map/forecast"
gcloud storage ls --recursive "gs://$BUCKET"
gcloud storage cat "gs://$BUCKET/published/current.json"
```

Replace secret version `1` if using a different version. Add
`EXPO_PUBLIC_API_URL=<the API_URL printed by the command below>` to
`Front-end/.env.local`, then restart Expo or rebuild the deployed frontend.

```bash
printf '%s\n' "$API_URL"
```

Check the dashboard and sign in to check the symptom log. The forecast should
report a NASA source run and four daily slots. Open Map, scrub the hourly
timeline and check the population layer and valid-time/stale/provisional labels.
If map metadata returns 503 while the legacy dashboard works, verify the
reference setting and execute a risk-enabled publication. To exercise a failed execution
without changing the job definition, pass a missing input for one execution.
Run this before scheduling so another job cannot update the pointer during
the check.

```bash
BEFORE="$(gcloud storage cat "gs://$BUCKET/published/current.json")"
gcloud run jobs execute asthmaaware-forecast --region="$REGION" --wait \
  --args=-m,scripts.publish_dashboard_forecast,--input,/tmp/missing.nc
# The execution above should fail. Then compare the pointer and check the API.
AFTER="$(gcloud storage cat "gs://$BUCKET/published/current.json")"
test "$BEFORE" = "$AFTER" && curl -fsS "$API_URL/v1/dashboard/risk?lat=33.75&lon=-84.39"
```

[Cloud Run public access](https://docs.cloud.google.com/run/docs/authenticating/public)
