# Executes only the isolated development resources. Deployment stages must pass first.
[CmdletBinding()]
param([switch] $ResumeFromLogs)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$bucket = "$project-forecast-dev"
$job = 'asthma-forecast-publisher-dev'
$service = 'asthma-api-dev'
$marker = 'asthma-cloud-dev'
$release = Get-Content (Join-Path $PSScriptRoot 'release.local.json') -Raw | ConvertFrom-Json
if ($release.project -ne $project -or $release.region -ne $region) { throw 'Wrong release target.' }
$evidence = Join-Path $PSScriptRoot 'evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null

function Read-Pointer {
    $raw = Invoke-Gcloud @('storage','cat',"gs://$bucket/current.json","--project=$project")
    return ($raw -join "`n" | ConvertFrom-Json)
}

function Check-HTTP([string] $Path, [int] $Expected, [bool] $Authenticated = $true) {
    $headers = @{}
    if ($Authenticated) { $headers.Authorization = "Bearer $script:idToken" }
    try {
        $r = Invoke-WebRequest -UseBasicParsing -Uri "$script:baseUrl$Path" -Headers $headers -TimeoutSec 60
        $status = [int] $r.StatusCode
        $content = $r.Content
    } catch {
        if ($null -eq $_.Exception.Response) { throw }
        $status = [int] $_.Exception.Response.StatusCode
        $content = $null
    }
    if ($status -ne $Expected) { throw "HTTP $Path returned $status, expected $Expected." }
    Write-Output "PASS HTTP $Path status=$status authenticated=$Authenticated"
    if ($content) { return ($content | ConvertFrom-Json) }
}

function Run-Probe([string] $Name, [string] $Identity, [string] $Expected) {
    $found = @(Get-GcloudJson @('run','jobs','list',"--project=$project","--region=$region") | Where-Object { $_.metadata.name -eq $Name })
    if ($found.Count -gt 0 -and $found[0].metadata.labels.'managed-by' -ne $marker) { throw "Probe $Name belongs to another setup." }
    Invoke-Gcloud @('run','jobs','deploy',$Name,"--image=$($release.image)","--service-account=$Identity","--project=$project","--region=$region",'--tasks=1','--parallelism=1','--cpu=1','--memory=512Mi','--task-timeout=120s','--max-retries=0',"--labels=managed-by=$marker",'--command=python',"--args=-m,forecast_job.probe,--expect,$Expected","--set-env-vars=FORECAST_BUCKET=$bucket",'--quiet')
    Invoke-Gcloud @('run','jobs','execute',$Name,"--project=$project","--region=$region",'--wait','--format=json') | Set-Content (Join-Path $evidence "$Name.json")
}

# Both jobs must complete, not just accept an execution request.
if (-not $ResumeFromLogs) {
Invoke-Gcloud @('run','jobs','execute',$c.JobName,"--project=$project","--region=$region",'--wait','--format=json') | Set-Content (Join-Path $evidence 'sample-execution.json')
$drillJob = 'asthma-gcs-acceptance-dev'
$found = @(Get-GcloudJson @('run','jobs','list',"--project=$project","--region=$region") | Where-Object { $_.metadata.name -eq $drillJob })
if ($found.Count -gt 0 -and $found[0].metadata.labels.'managed-by' -ne $marker) { throw 'GCS acceptance job name collision.' }
Invoke-Gcloud @('run','jobs','deploy',$drillJob,"--image=$($release.image)","--service-account=asthma-publisher-dev@$project.iam.gserviceaccount.com","--project=$project","--region=$region",'--tasks=1','--parallelism=1','--cpu=1','--memory=512Mi','--task-timeout=300s','--max-retries=0',"--labels=managed-by=$marker",'--command=python','--args=-m,forecast_job.integration',"--set-env-vars=FORECAST_BUCKET=$bucket",'--quiet')
Invoke-Gcloud @('run','jobs','execute',$drillJob,"--project=$project","--region=$region",'--wait','--format=json') | Set-Content (Join-Path $evidence 'gcs-acceptance-execution.json')
Invoke-Gcloud @('run','jobs','execute',$job,"--project=$project","--region=$region",'--wait','--format=json') | Set-Content (Join-Path $evidence 'publisher-execution.json')
$before = Read-Pointer
& gcloud run jobs execute $job "--project=$project" "--region=$region" --wait '--args=-m,forecast_job.publisher,--mode,sample,--fail-before-promotion' --format=json
if ($LASTEXITCODE -eq 0) { throw 'Controlled failure unexpectedly succeeded.' }
} else {
    # Resume only when immutable, successful execution evidence matches this image,
    # project and recent rollout. Do not rerun successful jobs just to retry logs.
    $manifestRaw = & docker manifest inspect $release.image
    if ($LASTEXITCODE -ne 0) { throw 'Cannot resolve release platform digest.' }
    $manifest = $manifestRaw -join "`n" | ConvertFrom-Json
    $platform = @($manifest.manifests | Where-Object { $_.platform.os -eq 'linux' -and $_.platform.architecture -eq 'amd64' })
    if ($platform.Count -ne 1) { throw 'Expected one Linux/amd64 release manifest.' }
    $namespace = (Invoke-Gcloud @('projects','describe',$project,'--format=value(projectNumber)') | Out-String).Trim()
    foreach ($file in @('gcs-acceptance-execution.json','publisher-execution.json')) {
        $execution = Get-Content (Join-Path $evidence $file) -Raw | ConvertFrom-Json
        if ($execution.metadata.namespace -ne $namespace -or $execution.status.succeededCount -ne 1 -or -not $execution.spec.template.spec.containers[0].image.EndsWith('@' + $platform[0].digest) -or [datetime]::Parse($execution.metadata.creationTimestamp).ToUniversalTime() -lt (Get-Date).ToUniversalTime().AddHours(-1)) { throw 'Successful current-release execution evidence is required to resume.' }
    }
    $before = Read-Pointer
}
$failed = @(Get-GcloudJson @('run','jobs','executions','list',"--job=$job","--project=$project","--region=$region",'--limit=1'))
if ($failed.Count -ne 1 -or $failed[0].status.failedCount -ne 1) { throw 'Expected one failed task; inspect execution failure cause.' }
$failureCauseFound = $false
for ($attempt = 0; $attempt -lt 12; $attempt++) {
    $filter = 'resource.type=cloud_run_job AND resource.labels.job_name=' + $job
    $logs = @(Get-GcloudJson @('logging','read',$filter,"--project=$project",'--limit=100') | Where-Object { $_.PSObject.Properties['labels'] -and $_.labels.PSObject.Properties['run.googleapis.com/execution_name'] -and $_.labels.'run.googleapis.com/execution_name' -eq $failed[0].metadata.name })
    $logText = $logs | ConvertTo-Json -Depth 25
    $logText | Set-Content (Join-Path $evidence 'publisher-failure-logs.json')
    if ($logText -match 'Controlled publication failure before pointer promotion') { $failureCauseFound = $true; break }
    Start-Sleep -Seconds 5
}
if (-not $failureCauseFound) { throw 'Failed execution exists, but intended failure cause is not visible in logs yet.' }
if ((Read-Pointer).run_id -ne $before.run_id) { throw 'Failed publisher changed the selected run.' }

$script:baseUrl = (Invoke-Gcloud @('run','services','describe',$service,"--project=$project","--region=$region",'--format=value(status.url)') | Out-String).Trim()
$script:idToken = (Invoke-Gcloud @('auth','print-identity-token') | Out-String).Trim()
try {
    $null = Check-HTTP '/health' 403 $false
    $null = Check-HTTP '/health' 200
    $response = @(Check-HTTP '/internal/forecast?lat=33.725&lon=-84.375' 200)[-1]
    if ($response.source -ne 'sample' -or $response.run_id -ne $before.run_id) { throw 'API did not return the selected synthetic run.' }
    $null = Check-HTTP '/internal/forecast?lat=1&lon=1' 404
    $null = Check-HTTP '/dashboard?lat=33.749&lon=-84.388' 503
} finally { $script:idToken = $null }
Run-Probe 'asthma-api-permissions-dev' "asthma-api-dev@$project.iam.gserviceaccount.com" 'read-only'
Run-Probe 'asthma-noaccess-probe-dev' $c.SchedulerEmail 'no-access'
Run-Probe 'asthma-freshness-dev' "asthma-api-dev@$project.iam.gserviceaccount.com" 'fresh'
@{project=$project;region=$region;image=$release.image;verifiedAtUTC=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'verified.local.json') -Encoding UTF8
Write-Output "PASS cloud execution, failed-publish preservation, private API, runtime read-only/denied access, freshness. Evidence: $evidence"
