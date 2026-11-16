[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('Preflight','Setup','Image','Job','Scheduler','DevSetup','DevImage','DevJob','DevApi','Verify','Schedule','Monitor','TestSchedule')]
    [string] $Stage
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = '1'
if ($Stage -eq 'Preflight') { & (Join-Path $PSScriptRoot 'Preflight.ps1'); return }
if ($Stage -eq 'Monitor') { & (Join-Path $PSScriptRoot 'development/Monitor.ps1'); return }
if ($Stage -eq 'TestSchedule') { & (Join-Path $PSScriptRoot 'development/Test-Schedule.ps1'); return }
if ($Stage -in @('Setup','Image','Job','Scheduler')) {
    & (Join-Path $PSScriptRoot 'forecast-job/Deploy.ps1') -Stage $Stage
} else {
    & (Join-Path $PSScriptRoot 'development/Deploy.ps1') -Stage $Stage
}
