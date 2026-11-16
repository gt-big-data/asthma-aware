# Read-only acceptance of the final deployed configuration and API behavior.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$release = Get-Content (Join-Path $PSScriptRoot 'release.local.json') -Raw | ConvertFrom-Json
$summary = @{project=$project;region=$region;image=$release.image;checkedAtUTC=(Get-Date).ToUniversalTime().ToString('o')}

foreach ($job in @($c.JobName, 'asthma-forecast-publisher-dev')) {
    $resource = Get-GcloudJson @('run','jobs','describe',$job,"--project=$project","--region=$region")
    $spec = $resource.spec.template.spec
    $task = $spec.template.spec
    if ($spec.taskCount -ne 1 -or $spec.parallelism -ne 1 -or $task.maxRetries -ne 0 -or [int]$task.timeoutSeconds -ne 300 -or $task.containers[0].resources.limits.memory -ne '512Mi' -or $task.containers[0].resources.limits.cpu -notin @('1','1000m')) { throw "Unexpected resource limits on $job." }
}
$api = Get-GcloudJson @('run','services','describe','asthma-api-dev',"--project=$project","--region=$region")
$policy = Get-GcloudJson @('run','services','get-iam-policy','asthma-api-dev',"--project=$project","--region=$region")
foreach ($binding in $policy.bindings) {
    if ($binding.members -contains 'allUsers' -or $binding.members -contains 'allAuthenticatedUsers') { throw 'API has a public IAM binding.' }
}
if ($api.spec.template.spec.serviceAccountName -ne "asthma-api-dev@$project.iam.gserviceaccount.com") { throw 'Unexpected API runtime identity.' }
$limits = $api.spec.template.spec.containers[0].resources.limits
if ($limits.memory -ne '512Mi' -or $limits.cpu -notin @('1','1000m')) { throw 'Unexpected API CPU/memory limits.' }
$maxScale = $api.spec.template.metadata.annotations.PSObject.Properties['autoscaling.knative.dev/maxScale']
if ($null -eq $maxScale) { $maxScale = $api.metadata.annotations.PSObject.Properties['run.googleapis.com/maxScale'] }
if ($null -eq $maxScale -or [int]$maxScale.Value -ne 1) { throw 'API maximum instances is not 1.' }
$minScale = $api.spec.template.metadata.annotations.PSObject.Properties['autoscaling.knative.dev/minScale']
if ($null -ne $minScale -and [int]$minScale.Value -ne 0) { throw 'API minimum instances is not 0.' }
$bucket = Get-GcloudJson @('storage','buckets','describe',"gs://$project-forecast-dev","--project=$project")
if ($bucket.public_access_prevention -ne 'enforced' -or -not $bucket.uniform_bucket_level_access -or -not $bucket.versioning_enabled) { throw 'Bucket privacy or versioning configuration differs from the plan.' }
$projectPolicy = Get-GcloudJson @('projects','get-iam-policy',$project)
foreach ($account in @($c.RuntimeEmail,$c.SchedulerEmail,"asthma-publisher-dev@$project.iam.gserviceaccount.com","asthma-api-dev@$project.iam.gserviceaccount.com")) {
    foreach ($binding in $projectPolicy.bindings) {
        if ($binding.members -contains "serviceAccount:$account") { throw "Workload identity has a project role: $account $($binding.role)" }
    }
}
foreach ($name in @('asthma-publisher-daily-dev','asthma-freshness-hourly-dev')) {
    $schedule = Get-GcloudJson @('scheduler','jobs','describe',$name,"--project=$project","--location=$region")
    if ($schedule.state -ne 'ENABLED' -or $schedule.description -ne 'asthma-cloud-dev' -or $schedule.httpTarget.oauthToken.serviceAccountEmail -ne $c.SchedulerEmail) { throw "Schedule $name is not enabled with the expected identity." }
    if ($name -eq 'asthma-publisher-daily-dev' -and ($schedule.schedule -ne '0 6 * * *' -or $schedule.timeZone -ne 'America/New_York')) { throw 'Publisher cron was not restored.' }
    if ($name -eq 'asthma-freshness-hourly-dev' -and ($schedule.schedule -ne '15 * * * *' -or $schedule.timeZone -ne 'Etc/UTC')) { throw 'Freshness cron differs from the intended hourly schedule.' }
    $summary[$name] = @{cron=$schedule.schedule;timeZone=$schedule.timeZone;state=$schedule.state}
}
$token = (Invoke-Gcloud @('auth','print-identity-token') | Out-String).Trim()
try {
    $baseUrl = $api.status.url
    $health = Invoke-RestMethod -Uri "$baseUrl/health" -Headers @{Authorization="Bearer $token"} -TimeoutSec 60
    $forecast = Invoke-RestMethod -Uri "$baseUrl/internal/forecast?lat=33.725&lon=-84.375" -Headers @{Authorization="Bearer $token"} -TimeoutSec 60
    if ($health.status -ne 'ok' -or $forecast.source -ne 'sample') { throw 'Final API smoke test failed.' }
    $now = (Get-Date).ToUniversalTime()
    if ($now -lt [datetime]::Parse($forecast.valid_from).ToUniversalTime() -or $now -ge [datetime]::Parse($forecast.valid_until).ToUniversalTime()) { throw 'Final selected forecast is outside validity.' }
    $unauthenticatedDenied = $false
    try { $null = Invoke-WebRequest -UseBasicParsing -Uri "$baseUrl/health" -TimeoutSec 60 }
    catch { if ($null -ne $_.Exception.Response -and [int]$_.Exception.Response.StatusCode -eq 403) { $unauthenticatedDenied = $true } else { throw } }
    if (-not $unauthenticatedDenied) { throw 'Expected unauthenticated API denial.' }
    $summary.apiUrl = $baseUrl
    $summary.currentRun = $forecast.run_id
    $summary.validUntil = $forecast.valid_until
    $summary.currentRevision = $api.status.latestReadyRevisionName
} finally { $token = $null }
$summary | ConvertTo-Json -Depth 12 | Set-Content (Join-Path $PSScriptRoot 'final-audit.local.json') -Encoding UTF8
Write-Output 'PASS final cloud audit: workload limits, private API, scoped identities, private versioned storage, enabled schedules, and fresh synthetic API data.'
