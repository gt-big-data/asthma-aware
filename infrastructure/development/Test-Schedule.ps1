# Tests one forced and one natural dispatch, then enables the verified daily schedule.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$schedule = 'asthma-publisher-daily-dev'
$job = 'asthma-forecast-publisher-dev'
$verified = Get-Content (Join-Path $PSScriptRoot 'verified.local.json') -Raw | ConvertFrom-Json
$release = Get-Content (Join-Path $PSScriptRoot 'release.local.json') -Raw | ConvertFrom-Json
if ($verified.project -ne $project -or $verified.region -ne $region -or $verified.image -ne $release.image -or [datetime]::Parse($verified.verifiedAtUTC).ToUniversalTime() -lt (Get-Date).ToUniversalTime().AddDays(-1)) { throw 'Fresh cloud Verify evidence is required for this release.' }
$resource = Get-GcloudJson @('scheduler','jobs','describe',$schedule,"--project=$project","--location=$region")
if ($resource.description -ne 'asthma-cloud-dev' -or $resource.httpTarget.uri -ne "https://run.googleapis.com/v2/projects/$project/locations/$region/jobs/${job}:run" -or $resource.httpTarget.oauthToken.serviceAccountEmail -ne $c.SchedulerEmail) { throw 'Unexpected schedule target or identity.' }
$cron = $resource.schedule
$zone = $resource.timeZone
$success = $false
function Executions {
    return @(Get-GcloudJson @('run','jobs','executions','list',"--job=$job","--project=$project","--region=$region",'--limit=20'))
}
function Wait-NewSuccess([string[]] $Before, [string] $Kind) {
    $deadline = (Get-Date).AddSeconds(180)
    do {
        $new = @(Executions | Where-Object { $_.metadata.name -notin $Before })
        foreach ($execution in $new) {
            $status = $execution.status
            if ($status.PSObject.Properties['failedCount'] -and $status.failedCount -gt 0) { throw "$Kind execution failed: $($execution.metadata.name)" }
            if ($status.PSObject.Properties['succeededCount'] -and $status.succeededCount -eq 1) {
                Write-Output "PASS $Kind execution=$($execution.metadata.name)"
                return $execution.metadata.name
            }
        }
        Write-Output "Waiting for $Kind execution completion..."
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    throw "$Kind execution did not complete before verification deadline."
}
try {
    $before = @(Executions | ForEach-Object { $_.metadata.name })
    Invoke-Gcloud @('scheduler','jobs','resume',$schedule,"--project=$project","--location=$region",'--quiet')
    Invoke-Gcloud @('scheduler','jobs','run',$schedule,"--project=$project","--location=$region",'--quiet')
    $forced = @(Wait-NewSuccess $before 'forced')[-1]
    Invoke-Gcloud @('scheduler','jobs','pause',$schedule,"--project=$project","--location=$region",'--quiet')
    Invoke-Gcloud @('scheduler','jobs','update','http',$schedule,"--project=$project","--location=$region",'--schedule=* * * * *','--time-zone=Etc/UTC','--quiet')
    $before = @(Executions | ForEach-Object { $_.metadata.name })
    Invoke-Gcloud @('scheduler','jobs','resume',$schedule,"--project=$project","--location=$region",'--quiet')
    $natural = @(Wait-NewSuccess $before 'natural')[-1]
    $success = $true
} finally {
    Invoke-Gcloud @('scheduler','jobs','pause',$schedule,"--project=$project","--location=$region",'--quiet')
    Invoke-Gcloud @('scheduler','jobs','update','http',$schedule,"--project=$project","--location=$region","--schedule=$cron","--time-zone=$zone",'--quiet')
}
if ($success) {
    Invoke-Gcloud @('scheduler','jobs','resume',$schedule,"--project=$project","--location=$region",'--quiet')
    @{project=$project;region=$region;forcedExecution=$forced;naturalExecution=$natural;schedule=$cron;timeZone=$zone} | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'schedule-verified.local.json') -Encoding UTF8
    Write-Output 'PASS forced and natural scheduling. Original daily schedule restored and enabled.'
}
