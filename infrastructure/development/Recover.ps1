[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)] [ValidateSet('Pause','JobImage','ApiRevision','ForecastPointer')] [string] $Action,
    [string] $Revision,
    [string] $RunId
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = '1'
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$schedule = 'asthma-publisher-daily-dev'
$job = 'asthma-forecast-publisher-dev'
$service = 'asthma-api-dev'
$marker = 'asthma-cloud-dev'
function Pause-Publisher {
    $found = @(Get-GcloudJson @('scheduler','jobs','list',"--project=$project","--location=$region") | Where-Object { $_.name -eq "projects/$project/locations/$region/jobs/$schedule" })
    if ($found.Count -eq 0) { return }
    if ($found[0].description -ne $marker) { throw 'Schedule ownership mismatch.' }
    if ($found[0].state -ne 'PAUSED') { Invoke-Gcloud @('scheduler','jobs','pause',$schedule,"--project=$project","--location=$region",'--quiet') }
}
switch ($Action) {
    'Pause' { Pause-Publisher }
    'JobImage' {
        $state = Get-Content (Join-Path $PSScriptRoot 'previous-release.local.json') -Raw | ConvertFrom-Json
        if ($state.project -ne $project -or $state.region -ne $region -or $state.image -notmatch '@sha256:[a-f0-9]{64}$') { throw 'Invalid previous release target.' }
        $resource = Get-GcloudJson @('run','jobs','describe',$job,"--project=$project","--region=$region")
        if ($resource.metadata.labels.'managed-by' -ne $marker) { throw 'Job ownership mismatch.' }
        Pause-Publisher
        Invoke-Gcloud @('run','jobs','update',$job,"--image=$($state.image)","--project=$project","--region=$region",'--quiet')
        Write-Output 'Previous publisher image restored; schedule remains paused. Manually execute and verify before resuming.'
    }
    'ApiRevision' {
        if ($Revision -notmatch '^asthma-api-dev-[a-z0-9-]+$') { throw 'Specify an existing known-good development API revision.' }
        $resource = Get-GcloudJson @('run','services','describe',$service,"--project=$project","--region=$region")
        if ($resource.metadata.labels.'managed-by' -ne $marker) { throw 'API ownership mismatch.' }
        $target = Get-GcloudJson @('run','revisions','describe',$Revision,"--project=$project","--region=$region")
        if ($target.metadata.labels.'serving.knative.dev/service' -ne $service) { throw 'Revision belongs to another service.' }
        Invoke-Gcloud @('run','services','update-traffic',$service,"--to-revisions=${Revision}=100","--project=$project","--region=$region",'--quiet')
    }
    'ForecastPointer' {
        if ($RunId -notmatch '^[A-Za-z0-9-]{1,80}$') { throw 'Specify a retained run ID.' }
        $state = Get-Content (Join-Path $PSScriptRoot 'release.local.json') -Raw | ConvertFrom-Json
        if ($state.project -ne $project -or $state.region -ne $region -or $state.image -notmatch '@sha256:[a-f0-9]{64}$') { throw 'Invalid release target.' }
        $recoveryJob = 'asthma-forecast-recovery-dev'
        $existing = @(Get-GcloudJson @('run','jobs','list',"--project=$project","--region=$region") | Where-Object { $_.metadata.name -eq $recoveryJob })
        if ($existing.Count -gt 0 -and $existing[0].metadata.labels.'managed-by' -ne $marker) { throw 'Recovery job name collision.' }
        Pause-Publisher
        # Pausing Scheduler does not cancel an active publisher. The operator must
        # wait for active publishers to finish; CAS rejects races during rollback.
        $active = @(Get-GcloudJson @('run','jobs','executions','list',"--job=$job","--project=$project","--region=$region") | Where-Object { -not $_.status.PSObject.Properties['completionTime'] })
        if ($active.Count -gt 0) { throw 'Publisher executions are still active; wait for completion before rollback.' }
        Invoke-Gcloud @('run','jobs','deploy',$recoveryJob,"--image=$($state.image)","--project=$project","--region=$region","--service-account=asthma-publisher-dev@$project.iam.gserviceaccount.com",'--tasks=1','--parallelism=1','--cpu=1','--memory=512Mi','--task-timeout=120s','--max-retries=0',"--labels=managed-by=$marker",'--command=python',"--args=-m,forecast_job.recovery,--run-id,$RunId,--allow-regression","--set-env-vars=FORECAST_BUCKET=$project-forecast-dev",'--quiet')
        Invoke-Gcloud @('run','jobs','execute',$recoveryJob,"--project=$project","--region=$region",'--wait')
        Write-Output 'Pointer rollback complete; expired runs still return 503. Schedule remains paused.'
    }
}
