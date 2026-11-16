# Production backend definitions

`main.tf.json` uses Terraform's native JSON configuration syntax. It defines a
separate namespace from the preserved synthetic development environment.
**No resource was deployed.** `terraform validate` and Docker builds have not run
in this sandbox: Terraform is absent and the Docker daemon pipe is denied.

Read [the deployment sequence](../../GOOGLE_CLOUD_DEPLOYMENT.md) before using it.
Do not apply against existing resources without reviewing/importing their exact
ownership and intended configuration. No broad project IAM/billing/API-enable
resources are included. Actual deployment needs separate explicit authorization.

Safety defaults:

- Scheduler has `paused=true` and `create_scheduler=false` by default. The default
  creates no schedule, avoiding the provider's potential create-then-pause race.
- API private IAM; worker always private. Tasks and Places disabled.
- Optional global HTTPS LB/CDN disabled, since it has a standing hourly cost.
- Job: one task/parallelism, one vCPU/512 MiB, 300s timeout, zero retries, no GPU.
- Services scale to zero and at most one instance; no instance CPU allocation when
  idle. This limits resource use but is not a spending cap.
- Three private versioned buckets, public access prevention, prevent-destroy and
  no force-destroy. No lifecycle rule deletes objects still referenced by manifests.
- Secret Manager accesses existing secret names; it creates no secret values.
- No ML executable, model weights, fake output or placeholder model is built.

Run `python infrastructure/production/test_configuration.py` from the root or
`powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/production/Validate.ps1`
for offline policy checks. These are static tests, not provider/cloud acceptance.
`Validate.ps1 -Terraform` only validates providers already installed locally and
does not initialize or download them.
