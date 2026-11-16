[CmdletBinding()]
param([string] $Project, [string] $Region)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = '1'
. (Join-Path $PSScriptRoot 'forecast-job/Common.ps1')
$config = Read-ForecastConfig (Join-Path $PSScriptRoot 'forecast-job/config.local.psd1')
if ([string]::IsNullOrWhiteSpace($Project)) { $Project = $config.ProjectId }
if ([string]::IsNullOrWhiteSpace($Region)) { $Region = $config.Region }
Invoke-Gcloud @('version', '--format=json')
Invoke-Gcloud @('projects', 'describe', $Project, '--format=json')
Invoke-Gcloud @('billing', 'projects', 'describe', $Project, '--format=json')
$enabled = @(Invoke-Gcloud @('services', 'list', '--enabled', "--project=$Project", '--format=value(config.name)'))
$enabled
Invoke-Gcloud @('iam', 'service-accounts', 'list', "--project=$Project", '--format=json')
if ($enabled -contains 'artifactregistry.googleapis.com') {
    Invoke-Gcloud @('artifacts', 'repositories', 'list', "--project=$Project", "--location=$Region", '--format=json')
} else { Write-Output 'Artifact Registry API disabled; inventory deferred until enablement.' }
if ($enabled -contains 'run.googleapis.com') {
    Invoke-Gcloud @('run', 'jobs', 'list', "--project=$Project", "--region=$Region", '--format=json')
    Invoke-Gcloud @('run', 'services', 'list', "--project=$Project", "--region=$Region", '--format=json')
} else { Write-Output 'Cloud Run API disabled; inventory deferred until enablement.' }
if ($enabled -contains 'cloudscheduler.googleapis.com') {
    Invoke-Gcloud @('scheduler', 'jobs', 'list', "--project=$Project", "--location=$Region", '--format=json')
} else { Write-Output 'Scheduler API disabled; inventory deferred until enablement.' }
Invoke-Gcloud @('storage', 'buckets', 'list', "--project=$Project", '--format=json')
