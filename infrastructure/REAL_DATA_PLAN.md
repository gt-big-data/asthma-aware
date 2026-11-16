# Real forecast integration plan

Baseline: main `84ec66c`, with existing local cloud changes preserved. No GitHub
pushes. NASA acquisition runs only in a manually invoked bounded batch process;
API requests never start acquisition or compute jobs.

1. Preserve and reconcile the existing cloud configuration with upstream Supabase
   authentication and symptom-log routes. Keep fixture mode explicit.
2. Add a versioned compact forecast artifact from the existing GEOS-CF ingestion
   output. Keep native cells in storage; resolve approximately 1 km display pixels
   on request and copy the containing native cell value unchanged. The native grid
   is 0.25 degrees, not an exact 25 km square. No interpolation or ML claims.
3. Calculate a development score as the equal-weight mean of clipped PM2.5/50,
   NO2(ppb)/100, O3(ppb)/100, multiplied by 100. These denominators are arbitrary
   engineering scales, not AQI thresholds or calibrated medical predictions.
4. Publish complete artifacts atomically to a local file, or transactionally to a
   dedicated shared forecast table in Supabase Postgres. Add an explicit migration;
   preserve private symptom tables and their row policies. Use server credentials
   only. Do not migrate a shared database without first checking its revision.
5. Add authenticated forecast endpoints, explicit provenance and placeholder
   labeling, bounds/time/staleness checks, and no fallback to fabricated values.
   Wire frontend forecast viewing through the existing authenticated API client.
6. Verify unit/API/client tests, type checking and lint. Attempt a small real NASA
   acquisition with a hard process deadline. Record unavailable credentials and
   external checks separately from passing local tests.

Operational limits: no new cloud resources, builds, recurring schedules, model
training, or deployments during local implementation. Any live batch is capped at
five minutes, one native point and at most 48 forecast hours. Existing cloud
schedules must be inspected and paused for an unattended stop when access exists.
The previous phase found Google CLI outside the restricted sandbox, paused both
development schedules, and observed no active tasks in the ten most recent job
executions. This verification phase cannot access that protected installation.
Remaining credits and current billing are unverified; no total-spend guarantee is
possible from an alert-only budget. Do not launch paid compute without resolving
that boundary. Retained resources may still cost money.

Inbox: connected Gmail is romeerdhillon@gmail.com; exact phrase search returned no
messages. This does not verify the asthmaaware26@gmail.com inbox.

Implementation/status: see [the root verification report](../VERIFICATION_REPORT.md).
Live acquisition is blocked
by outbound socket restrictions; Supabase credentials and an accessible disposable
database are absent. No real NASA/DB acceptance is claimed from fixture tests.
