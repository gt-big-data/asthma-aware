# Verification plan — 2026-11-16

Scope: local full-system review and completion of the in-progress NASA/Supabase/
frontend integration, with a maximum of three hours from this verification request.
No merges, pushes, PRs, production writes, cloud deployments or paid workloads.
Preserve all previous uncommitted infrastructure work.

1. Inventory current changes and compare local main/origin-main history. Inspect
   authentication, symptom logs, forecast acquisition/publication and UI consumers.
2. Establish baseline backend tests, frontend tests/typecheck/lint and build.
   Use workspace temporary paths. Record missing dependencies and unavailable
   isolated database/network access; do not disguise skips as passes.
3. Fix critical forecast contract issues: timestamp midpoint semantics, units,
   finite/missing values, geographic boundaries, freshness, bounded replication,
   identical-run reuse, transactional publication and restricted read/write roles.
4. Connect the frontend to authenticated forecast responses, replacing fabricated
   forecast values in the live flow. Preserve existing logging/authentication.
   Verify loading, empty/error states, coordinate selection and request lifetime.
5. Add meaningful fixture/API/client tests for these boundaries. Exercise Alembic
   SQL generation locally; real RLS tests require an explicitly disposable database.
6. Run representative performance checks, all available tests and frontend export.
   Review security, secrets handling and diffs. Stop temporary processes.
7. Write VERIFICATION_REPORT.md with executed commands/results, external blockers,
   readiness and remaining work. No claims of live verification without evidence.

Known environment: Supabase environment variables and backend/.env are absent.
Docker named-pipe access and direct outbound Python network access are denied.
Approval policy is never; elevated operations must be skipped. The previous phase
already paused the daily publisher and hourly freshness schedules and observed zero
running tasks in its ten most recent executions. Do not resume or trigger them.
