# Deliberately select a still-valid but overdue fixture, verify alarm failure,
# then restore the real selection and verify freshness recovery. No app writes.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = '1'
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$bucket = "$project-forecast-dev"
$evidence = Join-Path $PSScriptRoot 'evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
$path = Join-Path $evidence 'overdue-fixture.json'
$manifestPath = Join-Path $evidence 'overdue-manifest.json'
$before = ((Invoke-Gcloud @('storage','cat',"gs://$bucket/current.json","--project=$project")) -join "`n" | ConvertFrom-Json)
& (Join-Path $PSScriptRoot 'Recover.ps1') -Action Pause
$runId = (& (Join-Path $PSScriptRoot '../../backend/.venv/Scripts/python.exe') -B (Join-Path $PSScriptRoot 'make_overdue_fixture.py') $path | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $runId -notmatch '^overdue-probe-[a-f0-9-]+$') { throw 'Overdue fixture creation failed.' }
$objectPath = "runs/$runId/forecast.json"
Invoke-Gcloud @('storage','cp',$path,"gs://$bucket/$objectPath",'--if-generation-match=0',"--project=$project")
$object = Get-GcloudJson @('storage','objects','describe',"gs://$bucket/$objectPath","--project=$project")
$bytes = [System.IO.File]::ReadAllBytes($path)
$hasher = [System.Security.Cryptography.SHA256]::Create()
try { $checksum = [BitConverter]::ToString($hasher.ComputeHash($bytes)).Replace('-','').ToLowerInvariant() } finally { $hasher.Dispose() }
$manifest = @{schema_version='1.0';run_id=$runId;path=$objectPath;generation=[long]$object.generation;bytes=$bytes.Length;sha256=$checksum} | ConvertTo-Json
[System.IO.File]::WriteAllText($manifestPath, $manifest, (New-Object System.Text.UTF8Encoding($false)))
Invoke-Gcloud @('storage','cp',$manifestPath,"gs://$bucket/runs/$runId/manifest.json",'--if-generation-match=0',"--project=$project")
$selected = $false
$failedExecution = $null
try {
    $selected = $true
    & (Join-Path $PSScriptRoot 'Recover.ps1') -Action ForecastPointer -RunId $runId
    & gcloud run jobs execute asthma-freshness-dev "--project=$project" "--region=$region" --wait --format=json
    if ($LASTEXITCODE -eq 0) { throw 'Overdue publication did not fail the freshness probe.' }
    $failed = @(Get-GcloudJson @('run','jobs','executions','list','--job=asthma-freshness-dev',"--project=$project","--region=$region",'--limit=1'))
    $failedExecution = $failed[0].metadata.name
    $filter = 'resource.type=cloud_run_job AND resource.labels.job_name=asthma-freshness-dev'
    $causeFound = $false
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        $logs = @(Get-GcloudJson @('logging','read',$filter,"--project=$project",'--limit=50') | Where-Object { $_.PSObject.Properties['labels'] -and $_.labels.PSObject.Properties['run.googleapis.com/execution_name'] -and $_.labels.'run.googleapis.com/execution_name' -eq $failedExecution })
        $logText = $logs | ConvertTo-Json -Depth 25
        $logText | Set-Content (Join-Path $evidence 'freshness-failure-logs.json')
        if ($logText -match 'Publication is overdue') { $causeFound = $true; break }
        Start-Sleep -Seconds 5
    }
    if (-not $causeFound) { throw 'Freshness failed, but the expected overdue cause was not observed.' }
    Write-Output "PASS overdue publication detected: $failedExecution"
} finally {
    if ($selected) { & (Join-Path $PSScriptRoot 'Recover.ps1') -Action ForecastPointer -RunId $before.run_id }
}
Invoke-Gcloud @('run','jobs','execute','asthma-freshness-dev',"--project=$project","--region=$region",'--wait')
@{project=$project;region=$region;overdueRun=$runId;failedExecution=$failedExecution;restoredRun=$before.run_id;verifiedAtUTC=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'freshness-alert-drill.local.json') -Encoding UTF8
Write-Output 'PASS actual overdue-data failure and successful freshness recovery. Check the configured administrator inbox for the alert.'
