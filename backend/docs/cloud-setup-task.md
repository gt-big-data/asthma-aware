# AsthmaAware: Cloud Setup Task

**Goal:** Set up a small GCP development environment that runs the backend and
publishes a sample forecast. Target one week; use fixture data while the real
forecast pipeline is being developed.

## What to build

1. **Development resources:** Confirm the GCP project, region, and budget with
   the team. Create an Artifact Registry repository for container images and a
   private GCS bucket for forecast files. Configure budget alerts and a small
   Cloud Run instance limit. Budget alerts do not stop spending.
2. **API deployment:** Containerize `backend/app/main.py` and deploy it as a
   Cloud Run service. Listen on `0.0.0.0` using the supplied `PORT`.
   Use `GET /health` to check process liveness. Keep access restricted
   to authorized developers while application authentication is unfinished.
3. **Forecast publishing:** Create a manually triggered Cloud Run Job that
   uploads a team-provided forecast fixture under a unique run ID. Validate all
   required files, publish a manifest, then update the current-run reference.
   Failed publication must leave the previous complete run selected. Use GCS
   generation preconditions when updating that reference.
4. **Permissions and configuration:** Use separate service accounts for the API
   and publisher. Give the API forecast read access and the publisher only the
   permissions needed to publish. Use attached service identities for GCP access
   and Secret Manager for credentials; do not commit secrets or service-account keys.
5. **Handoff:** Save infrastructure configuration and deployment commands in the
   repository. Document how to deploy, run the publishing job, inspect logs, and
   roll back the API. Include resource names and basic cost expectations.

## How it fits together

```text
Container images -> Artifact Registry -> Cloud Run API
                                     -> Cloud Run publishing job

Publishing job -> GCS: forecast files + manifest + current-run reference
Cloud Run API -> GCS: read published forecast data
Cloud Run workloads -> Cloud Logging

Later: Cloud Run API -> Supabase PostgreSQL: private application records
```

## Completion checks

- Another teammate can deploy the API using your instructions.
- The deployed API passes its health check and can read the published fixture.
- An interrupted publication leaves the previous complete forecast usable.
- The API identity cannot write forecast objects; unauthorized callers cannot
  access the private resources.
- Demonstrate logs for a failed job and rollback to a previous API revision.

## Scope boundaries

Coordinate with the backend owner for the fixture format and
configuration names. Use the current backend, not the archived prototype.

Database connectivity is a **stretch goal**: connect a diagnostic Cloud Run Job
to a development Supabase database over TLS using a secret, and run `SELECT 1`.
Supabase hosts the application database; do not create a separate Cloud SQL database.

Application tables, database migrations, user authentication, real ML processing,
CDN setup, scheduling, and Cloud Tasks are separate tasks.
