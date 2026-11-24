param(
    [string]$Release = "no2-20261124-v2",
    [string]$Project = "asthma-aware",
    [string]$Account = "asthmaaware26@gmail.com",
    [switch]$JobsOnly,
    [string[]]$Models = @("o3", "no2"),
    [string]$ModelPrefix = "models/checkpoints/o3-no2-9444d777-92c80408",
    [string]$ReferenceObject = "reference/392bcd9ad4365b3cf4ed6745f3c005f3bb904cc1e68ed2fda7517644ff4e2d16.npz",
    [double]$AnalysisLeadHours = 0.5
)
$ErrorActionPreference = "Stop"
$region = "us-east1"
$bucket = "asthmaaware-forecasts"
$scheduler = "asthma-forecast-scheduler@$Project.iam.gserviceaccount.com"
$checkerName = "asthmaaware-forecast-check"
$checker = "$checkerName@$Project.iam.gserviceaccount.com"
$common = @("--account=$Account", "--project=$Project", "--quiet")
$repo = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path

gcloud iam service-accounts describe $checker @common --format="value(email)" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    gcloud iam service-accounts create $checkerName --display-name="AsthmaAware forecast availability checker" @common
    if ($LASTEXITCODE -ne 0) { throw "Checker identity creation failed" }
}
gcloud storage buckets add-iam-policy-binding "gs://$bucket" "--member=serviceAccount:$checker" --role=roles/storage.objectViewer @common | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Checker bucket access failed" }
gcloud run jobs add-iam-policy-binding asthmaaware-forecast "--region=$region" "--member=serviceAccount:$checker" --role=roles/run.jobsExecutorWithOverrides @common | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Checker publisher invocation access failed" }

# Versioned checkpoint/reference settings travel with the model selection.
# The checker supplies a date using execution-local overrides.
$publisherFlags = Join-Path $repo "backend/data/release-publisher.json"
@{
    "--image" = "us-east1-docker.pkg.dev/$Project/asthma-forecast-job/integration-ml:$Release"
    "--args" = @("--model-dir", "data/models", "--models") + $Models + @("--model-threads", "4", "--only-newer", "--analysis-lead-hours", $AnalysisLeadHours.ToString([Globalization.CultureInfo]::InvariantCulture))
    "--update-env-vars" = "MODEL_GCS_PREFIX=$ModelPrefix,RISK_REFERENCE_OBJECT=$ReferenceObject"
    "--region" = $region
} | ConvertTo-Json -Depth 4 | Set-Content $publisherFlags
gcloud run jobs update asthmaaware-forecast "--flags-file=$publisherFlags" @common
if ($LASTEXITCODE -ne 0) { throw "Guarded publisher update failed" }

foreach ($job in @(
    @{ Name = "asthmaaware-forecast-check"; Module = "scripts.check_forecast_update"; Identity = $checker;
       Arguments = @("--project", $Project); Timeout = "180s" },
    @{ Name = "asthmaaware-freshness"; Module = "scripts.check_forecast_freshness";
       Identity = "asthmaaware-api@$Project.iam.gserviceaccount.com"; Arguments = @(); Timeout = "120s" }
)) {
    $flags = Join-Path $repo "backend/data/release-$($job.Name).json"
    @{
        "--image" = "us-east1-docker.pkg.dev/$Project/asthma-forecast-job/integration-api:$Release"
        "--command" = "python"
        "--args" = @("-m", $job.Module) + $job.Arguments
        "--service-account" = $job.Identity
        "--set-env-vars" = "FORECAST_GCS_BUCKET=$bucket"
        "--cpu" = "1"
        "--memory" = "1Gi"
        "--task-timeout" = $job.Timeout
        "--max-retries" = 0
        "--region" = $region
    } | ConvertTo-Json -Depth 4 | Set-Content $flags
    gcloud run jobs deploy $job.Name "--flags-file=$flags" @common
    if ($LASTEXITCODE -ne 0) { throw "Deployment failed for $($job.Name)" }
    gcloud run jobs add-iam-policy-binding $job.Name "--region=$region" "--member=serviceAccount:$scheduler" --role=roles/run.invoker @common | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Scheduler permission failed for $($job.Name)" }
}
if ($JobsOnly) { return }

foreach ($schedule in @(
    @{ Name = "asthmaaware-publish-daily"; Cron = "15 17-23 * * *"; Job = "asthmaaware-forecast-check" },
    @{ Name = "asthmaaware-freshness-hourly"; Cron = "30 * * * *"; Job = "asthmaaware-freshness" }
)) {
    gcloud scheduler jobs describe $schedule.Name "--location=$region" --format="value(name)" @common 2>$null | Out-Null
    $action = if ($LASTEXITCODE -eq 0) { "update" } else { "create" }
    gcloud scheduler jobs $action http $schedule.Name "--location=$region" "--schedule=$($schedule.Cron)" --time-zone=Etc/UTC --http-method=POST "--uri=https://run.googleapis.com/v2/projects/$Project/locations/$region/jobs/$($schedule.Job):run" "--oauth-service-account-email=$scheduler" --message-body="{}" @common
    if ($LASTEXITCODE -ne 0) { throw "Schedule configuration failed for $($schedule.Name)" }
}
