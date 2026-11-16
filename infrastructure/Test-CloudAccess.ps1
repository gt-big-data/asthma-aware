[CmdletBinding()]
param([string] $Project)
$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($Project)) {
    . (Join-Path $PSScriptRoot 'forecast-job/Common.ps1')
    $Project = (Read-ForecastConfig (Join-Path $PSScriptRoot 'forecast-job/config.local.psd1')).ProjectId
}
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = '1'
$permissions = @(
    'serviceusage.services.enable', 'serviceusage.services.list',
    'iam.serviceAccounts.create', 'iam.serviceAccounts.list',
    'artifactregistry.repositories.create', 'artifactregistry.repositories.list',
    'run.jobs.create', 'run.jobs.list', 'run.jobs.setIamPolicy', 'run.jobs.runWithOverrides',
    'run.services.create', 'run.services.list', 'run.services.setIamPolicy',
    'cloudscheduler.jobs.create', 'cloudscheduler.jobs.list',
    'storage.buckets.create', 'storage.buckets.list', 'storage.buckets.getIamPolicy',
    'storage.buckets.setIamPolicy', 'logging.logEntries.list', 'logging.logMetrics.create',
    'monitoring.alertPolicies.create', 'monitoring.notificationChannels.list'
)
$token = (& gcloud auth print-access-token | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'Cannot obtain cloud credential.' }
try {
    $body = @{permissions=$permissions} | ConvertTo-Json
    $result = Invoke-RestMethod -Method Post -Uri "https://cloudresourcemanager.googleapis.com/v1/projects/${Project}:testIamPermissions" -Headers @{Authorization="Bearer $token"} -ContentType 'application/json' -Body $body
    foreach ($permission in $permissions) {
        $status = if ($result.permissions -contains $permission) { 'ALLOWED' } else { 'MISSING' }
        Write-Output "$status $permission"
    }
} finally { $token = $null }
