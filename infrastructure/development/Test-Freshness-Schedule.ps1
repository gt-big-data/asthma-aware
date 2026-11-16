# Verify the real hourly probe dispatch, then leave the monitor enabled.
# Inbox receipt is recorded separately and may be checked later by the owner.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = '1'
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$schedule = 'asthma-freshness-hourly-dev'
$job = 'asthma-freshness-dev'
$verified = Get-Content (Join-Path $PSScriptRoot 'verified.local.json') -Raw | ConvertFrom-Json
$release = Get-Content (Join-Path $PSScriptRoot 'release.local.json') -Raw | ConvertFrom-Json
if ($verified.project -ne $project -or $verified.region -ne $region -or $verified.image -ne $release.image -or [datetime]::Parse($verified.verifiedAtUTC).ToUniversalTime() -lt (Get-Date).ToUniversalTime().AddDays(-1)) { throw 'Successful current-release cloud verification is required.' }
$resource = Get-GcloudJson @('scheduler','jobs','describe',$schedule,"--project=$project","--location=$region")
if ($resource.description -ne 'asthma-cloud-dev' -or $resource.httpTarget.oauthToken.serviceAccountEmail -ne $c.SchedulerEmail -or $resource.httpTarget.uri -ne "https://run.googleapis.com/v2/projects/$project/locations/$region/jobs/${job}:run" -or $resource.schedule -ne '15 * * * *') { throw 'Unexpected freshness schedule configuration.' }
$before = @(Get-GcloudJson @('run','jobs','executions','list',"--job=$job","--project=$project","--region=$region",'--limit=20') | ForEach-Object { $_.metadata.name })
$success = $false
try {
    if ($resource.state -eq 'PAUSED') { Invoke-Gcloud @('scheduler','jobs','resume',$schedule,"--project=$project","--location=$region",'--quiet') }
    Invoke-Gcloud @('scheduler','jobs','run',$schedule,"--project=$project","--location=$region",'--quiet')
    $deadline = (Get-Date).AddSeconds(180)
    do {
        $new = @(Get-GcloudJson @('run','jobs','executions','list',"--job=$job","--project=$project","--region=$region",'--limit=20') | Where-Object { $_.metadata.name -notin $before })
        foreach ($execution in $new) {
            if ($execution.status.PSObject.Properties['failedCount'] -and $execution.status.failedCount -gt 0) { throw 'Scheduled freshness check failed.' }
            if ($execution.status.PSObject.Properties['succeededCount'] -and $execution.status.succeededCount -eq 1) {
                $success = $true
                @{project=$project;region=$region;execution=$execution.metadata.name;schedule=$resource.schedule;verifiedAtUTC=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'freshness-schedule-verified.local.json') -Encoding UTF8
                break
            }
        }
        if ($success) { break }
        Write-Output 'Waiting for scheduled freshness execution...'
        Start-Sleep -Seconds 10
    } while ((Get-Date) -lt $deadline)
    if (-not $success) { throw 'No successful scheduled freshness execution before deadline.' }
    Write-Output 'PASS Scheduler-to-freshness execution. Hourly freshness monitoring is enabled; email receipt remains a separate owner check.'
} finally {
    if (-not $success) { Invoke-Gcloud @('scheduler','jobs','pause',$schedule,"--project=$project","--location=$region",'--quiet') }
}
