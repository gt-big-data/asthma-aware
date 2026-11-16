# Live recovery drills, scoped to development resources and two recorded releases.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
$env:CLOUDSDK_CORE_DISABLE_PROMPTS = '1'
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$service = 'asthma-api-dev'
$job = 'asthma-forecast-publisher-dev'
$release = Get-Content (Join-Path $PSScriptRoot 'release.local.json') -Raw | ConvertFrom-Json
$previous = Get-Content (Join-Path $PSScriptRoot 'previous-release.local.json') -Raw | ConvertFrom-Json
foreach ($state in @($release, $previous)) {
    if ($state.project -ne $project -or $state.region -ne $region -or $state.image -notmatch '@sha256:[a-f0-9]{64}$') { throw 'Release target mismatch.' }
}
if ($release.image -eq $previous.image) { throw 'Two distinct image digests are required for image rollback evidence.' }
$api = Get-GcloudJson @('run','services','describe',$service,"--project=$project","--region=$region")
if ($api.metadata.labels.'managed-by' -ne 'asthma-cloud-dev') { throw 'API ownership mismatch.' }
$traffic = @($api.status.traffic | Where-Object { $_.PSObject.Properties['percent'] -and $_.percent -eq 100 })
if ($traffic.Count -ne 1) { throw 'Expected one known-good API revision receiving 100% traffic.' }
$oldRevision = $traffic[0].revisionName
$baseUrl = $api.status.url
$token = (Invoke-Gcloud @('auth','print-identity-token') | Out-String).Trim()
$candidateRevision = $null
$success = $false

function Assert-API([string] $Url, [string] $ExpectedRun = '') {
    $health = Invoke-RestMethod -Uri "$Url/health" -Headers @{Authorization="Bearer $token"} -TimeoutSec 60
    if ($health.status -ne 'ok') { throw 'API health failed during recovery.' }
    $forecast = Invoke-RestMethod -Uri "$Url/internal/forecast?lat=33.725&lon=-84.375" -Headers @{Authorization="Bearer $token"} -TimeoutSec 60
    if ($forecast.source -ne 'sample' -or ($ExpectedRun -and $forecast.run_id -ne $ExpectedRun)) { throw 'Unexpected forecast during recovery.' }
}
function Read-Pointer {
    return ((Invoke-Gcloud @('storage','cat',"gs://$project-forecast-dev/current.json","--project=$project")) -join "`n" | ConvertFrom-Json)
}

try {
    & (Join-Path $PSScriptRoot 'Recover.ps1') -Action Pause
    $before = Read-Pointer
    Invoke-Gcloud @('run','jobs','execute',$job,"--project=$project","--region=$region",'--wait')
    $after = Read-Pointer
    if ($after.run_id -eq $before.run_id) { throw 'Need two different published runs for pointer rollback.' }
    & (Join-Path $PSScriptRoot 'Recover.ps1') -Action ForecastPointer -RunId $before.run_id
    Assert-API $baseUrl $before.run_id
    & (Join-Path $PSScriptRoot 'Recover.ps1') -Action ForecastPointer -RunId $after.run_id
    Assert-API $baseUrl $after.run_id
    Write-Output 'PASS pointer rollback and recovery through authenticated API.'

    $suffix = 'verify-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
    Invoke-Gcloud @('run','deploy',$service,"--image=$($release.image)","--project=$project","--region=$region",'--no-allow-unauthenticated','--no-traffic','--tag=infra-candidate',"--revision-suffix=$suffix",'--quiet')
    $candidate = Get-GcloudJson @('run','services','describe',$service,"--project=$project","--region=$region")
    $tagged = @($candidate.status.traffic | Where-Object { $_.PSObject.Properties['tag'] -and $_.tag -eq 'infra-candidate' })
    if ($tagged.Count -ne 1) { throw 'No tagged candidate URL.' }
    $candidateRevision = $tagged[0].revisionName
    Assert-API $tagged[0].url
    Invoke-Gcloud @('run','services','update-traffic',$service,"--to-revisions=${candidateRevision}=100","--project=$project","--region=$region",'--quiet')
    Assert-API $baseUrl
    & (Join-Path $PSScriptRoot 'Recover.ps1') -Action ApiRevision -Revision $oldRevision
    Assert-API $baseUrl
    Write-Output "PASS candidate test, traffic promotion, and API rollback to $oldRevision."

    # Recover.ps1 restores the saved prior digest. Then restore and execute the
    # candidate digest, leaving the environment on the tested current release.
    & (Join-Path $PSScriptRoot 'Recover.ps1') -Action JobImage
    Invoke-Gcloud @('run','jobs','execute',$job,"--project=$project","--region=$region",'--wait')
    Assert-API $baseUrl
    Invoke-Gcloud @('run','jobs','update',$job,"--image=$($release.image)","--project=$project","--region=$region",'--quiet')
    Invoke-Gcloud @('run','jobs','execute',$job,"--project=$project","--region=$region",'--wait')
    Assert-API $baseUrl
    Write-Output 'PASS prior job image execution and current image restoration.'

    Invoke-Gcloud @('run','services','update-traffic',$service,"--to-revisions=${candidateRevision}=100","--project=$project","--region=$region",'--quiet')
    Assert-API $baseUrl
    $success = $true
    @{project=$project;region=$region;previousImage=$previous.image;image=$release.image;oldRevision=$oldRevision;currentRevision=$candidateRevision;pointerRollbackRun=$before.run_id;verifiedAtUTC=(Get-Date).ToUniversalTime().ToString('o')} | ConvertTo-Json | Set-Content (Join-Path $PSScriptRoot 'recovery-verified.local.json') -Encoding UTF8
} finally {
    $token = $null
    if (-not $success) {
        Write-Warning 'Recovery drill stopped. Schedule remains paused; restoring previous API traffic and current job image.'
        Invoke-Gcloud @('run','services','update-traffic',$service,"--to-revisions=${oldRevision}=100","--project=$project","--region=$region",'--quiet')
        Invoke-Gcloud @('run','jobs','update',$job,"--image=$($release.image)","--project=$project","--region=$region",'--quiet')
    }
}
