# Executes deployment stages against an in-process gcloud double. Never contacts GCP.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$global:CloudTestCalls = [System.Collections.Generic.List[object]]::new()
$global:CloudTestDeny = $false
$global:CloudTestCollision = $false
function global:gcloud {
    $arguments = @($args)
    $global:CloudTestCalls.Add($arguments)
    $global:LASTEXITCODE = 0
    $command = $arguments[0..2] -join ' '
    if ($global:CloudTestDeny -and $command -eq 'services enable run.googleapis.com') {
        $global:LASTEXITCODE = 1
        return
    }
    if ($command -eq 'iam service-accounts list' -or $command -eq 'artifacts repositories list' -or $command -eq 'scheduler jobs list' -or $command -eq 'storage buckets list') { '[]'; return }
    if ($command -eq 'run jobs list') {
        if ($global:CloudTestCollision) { '[{"metadata":{"name":"asthma-forecast-sample","labels":{"managed-by":"another-team"}}}]' } else { '[]' }
        return
    }
    if ($command -eq 'run jobs add-iam-policy-binding' -and $arguments -contains '--condition=None') { throw 'Run Jobs IAM does not support --condition.' }
}
function Assert-True([bool] $Condition, [string] $Message) { if (-not $Condition) { throw $Message } }
$checks = 0
try {
    $sample = Join-Path $PSScriptRoot '../forecast-job/Deploy.ps1'
    & $sample -Stage Setup
    Assert-True ($global:CloudTestCalls.Count -gt 4) 'Setup did not configure dedicated resources.'
    Assert-True (-not (@($global:CloudTestCalls | Where-Object { $_[0] -eq 'projects' -and $_ -contains 'add-iam-policy-binding' }).Count)) 'Setup must not grant project roles.'
    $checks++
    $global:CloudTestCalls.Clear()
    & $sample -Stage Job
    $deployment = @($global:CloudTestCalls | Where-Object { $_[0] -eq 'run' -and $_[2] -eq 'deploy' })[0]
    foreach ($flag in @('--tasks=1','--parallelism=1','--task-timeout=300s','--max-retries=0','--cpu=1','--memory=512Mi')) {
        Assert-True ($deployment -contains $flag) "Missing job safety setting: $flag"
    }
    Assert-True (-not ($deployment -contains '--execute-now')) 'Deployment must not execute a job.'
    $checks++
    $global:CloudTestCalls.Clear()
    & $sample -Stage Scheduler
    $create = @($global:CloudTestCalls | Where-Object { $_[0] -eq 'scheduler' -and $_[2] -eq 'create' })[0]
    Assert-True ($create -contains '--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform') 'Jobs API requires OAuth.'
    Assert-True ($create -contains '--max-retry-attempts=0') 'Scheduler retries must be disabled.'
    Assert-True (@($global:CloudTestCalls | Where-Object { $_[2] -eq 'pause' }).Count -eq 1) 'New schedule must be paused.'
    $checks++
    $global:CloudTestCalls.Clear()
    $global:CloudTestDeny = $true
    try { & $sample -Stage Setup; throw 'Expected setup denial.' } catch { if ($_.Exception.Message -notlike 'gcloud failed: services enable*') { throw } }
    Assert-True ($global:CloudTestCalls.Count -eq 1) 'Denied setup must stop before resource creation.'
    $checks++
    $global:CloudTestDeny = $false
    $global:CloudTestCollision = $true
    $global:CloudTestCalls.Clear()
    try { & $sample -Stage Job; throw 'Expected collision rejection.' } catch { if ($_.Exception.Message -notlike 'Job exists outside this setup*') { throw } }
    Assert-True ($global:CloudTestCalls.Count -eq 1) 'Collision must stop before deployment.'
    $checks++
    Write-Output "PASS $checks offline deployment behavior checks. No cloud commands executed."
} finally {
    Remove-Item Function:/gcloud
    Remove-Variable CloudTestCalls,CloudTestDeny,CloudTestCollision -Scope Global
}
