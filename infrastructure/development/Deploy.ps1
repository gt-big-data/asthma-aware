[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)]
    [ValidateSet('DevSetup','DevImage','DevJob','DevApi','Verify','Schedule')]
    [string] $Stage
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
$region = $c.Region
$marker = 'asthma-cloud-dev'
$bucket = "$project-forecast-dev"
$publisher = "asthma-publisher-dev@$project.iam.gserviceaccount.com"
$apiAccount = "asthma-api-dev@$project.iam.gserviceaccount.com"
$job = 'asthma-forecast-publisher-dev'
$service = 'asthma-api-dev'
$schedule = 'asthma-publisher-daily-dev'
$statePath = Join-Path $PSScriptRoot 'release.local.json'

function Get-ReleaseImage {
    if (-not (Test-Path -LiteralPath $statePath)) { throw 'Run DevImage first.' }
    $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json
    if ($state.project -ne $project -or $state.region -ne $region -or $state.image -notmatch '@sha256:[a-f0-9]{64}$') { throw 'Release state does not match target.' }
    return $state.image
}

switch ($Stage) {
    'DevSetup' {
        Invoke-Gcloud @('services','enable','storage.googleapis.com','monitoring.googleapis.com','logging.googleapis.com',"--project=$project")
        $accounts = @(Get-GcloudJson @('iam','service-accounts','list',"--project=$project"))
        foreach ($account in @('asthma-publisher-dev','asthma-api-dev')) {
            $existing = @($accounts | Where-Object { $_.email -eq "$account@$project.iam.gserviceaccount.com" })
            if ($existing.Count -eq 0) {
                Invoke-Gcloud @('iam','service-accounts','create',$account,"--description=$marker","--display-name=$account","--project=$project")
            } elseif ($existing[0].description -ne $marker) { throw "Account $account belongs to another setup." }
        }
        $buckets = @(Get-GcloudJson @('storage','buckets','list',"--project=$project"))
        $existing = @($buckets | Where-Object { $_.name -eq $bucket })
        if ($existing.Count -eq 0) {
            Invoke-Gcloud @('storage','buckets','create',"gs://$bucket","--project=$project","--location=$region",'--uniform-bucket-level-access','--public-access-prevention')
            Invoke-Gcloud @('storage','buckets','update',"gs://$bucket",'--versioning',"--update-labels=managed-by=$marker","--project=$project")
        } elseif ($existing[0].labels.'managed-by' -ne $marker) { throw 'Bucket belongs to another setup.' }
        Invoke-Gcloud @('storage','buckets','add-iam-policy-binding',"gs://$bucket","--member=serviceAccount:$apiAccount",'--role=roles/storage.objectViewer','--condition=None',"--project=$project")
        foreach ($role in @('roles/storage.objectCreator','roles/storage.objectViewer')) {
            Invoke-Gcloud @('storage','buckets','add-iam-policy-binding',"gs://$bucket","--member=serviceAccount:$publisher","--role=$role",'--condition=None',"--project=$project")
        }
        $condition = "expression=resource.name == 'projects/_/buckets/$bucket/objects/current.json',title=current-pointer-only"
        Invoke-Gcloud @('storage','buckets','add-iam-policy-binding',"gs://$bucket","--member=serviceAccount:$publisher",'--role=roles/storage.objectAdmin',"--condition=$condition","--project=$project")
    }
    'DevImage' {
        $tag = 'dev-' + (Get-Date).ToUniversalTime().ToString('yyyyMMddHHmmss')
        $image = "$region-docker.pkg.dev/$project/$($c.Repository)/cloud:$tag"
        Invoke-Gcloud @('auth','configure-docker',"$region-docker.pkg.dev",'--quiet')
        & docker build --platform linux/amd64 --build-arg "INFRA_BUILD_LABEL=$tag" --tag $image -f (Join-Path $PSScriptRoot '../../backend/Dockerfile.cloud') (Join-Path $PSScriptRoot '../../backend')
        if ($LASTEXITCODE -ne 0) { throw 'Cloud development image build failed.' }
        & docker push $image
        if ($LASTEXITCODE -ne 0) { throw 'Cloud development image push failed.' }
        $digest = (Invoke-Gcloud @('artifacts','docker','images','describe',$image,"--project=$project",'--format=value(image_summary.digest)') | Out-String).Trim()
        if ($digest -notmatch '^sha256:[a-f0-9]{64}$') { throw 'No resolved image digest.' }
        if (Test-Path -LiteralPath $statePath) { Copy-Item -LiteralPath $statePath -Destination (Join-Path $PSScriptRoot 'previous-release.local.json') }
        @{project=$project;region=$region;tag=$image;image=($image.Split(':')[0] + '@' + $digest)} | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
        Write-Output "Saved pinned release to $statePath"
    }
    'DevJob' {
        $image = Get-ReleaseImage
        $existing = @(Get-GcloudJson @('run','jobs','list',"--project=$project","--region=$region") | Where-Object { $_.metadata.name -eq $job })
        if ($existing.Count -gt 0 -and $existing[0].metadata.labels.'managed-by' -ne $marker) { throw 'Publisher job belongs to another setup.' }
        Invoke-Gcloud @('run','jobs','deploy',$job,"--image=$image","--project=$project","--region=$region","--service-account=$publisher",'--tasks=1','--parallelism=1','--task-timeout=300s','--max-retries=0','--cpu=1','--memory=512Mi',"--labels=managed-by=$marker",'--command=python','--args=-m,forecast_job.publisher,--mode,sample',"--set-env-vars=FORECAST_BUCKET=$bucket",'--quiet')
        Invoke-Gcloud @('run','jobs','add-iam-policy-binding',$job,"--project=$project","--region=$region","--member=serviceAccount:$($c.SchedulerEmail)",'--role=roles/run.invoker','--quiet')
    }
    'DevApi' {
        $image = Get-ReleaseImage
        $existing = @(Get-GcloudJson @('run','services','list',"--project=$project","--region=$region") | Where-Object { $_.metadata.name -eq $service })
        if ($existing.Count -gt 0 -and $existing[0].metadata.labels.'managed-by' -ne $marker) { throw 'API service belongs to another setup.' }
        Invoke-Gcloud @('run','deploy',$service,"--image=$image","--project=$project","--region=$region","--service-account=$apiAccount",'--no-allow-unauthenticated','--min-instances=0','--max-instances=1','--concurrency=8','--cpu=1','--memory=512Mi','--timeout=60s',"--labels=managed-by=$marker",'--set-env-vars',"FORECAST_PROVIDER=gcs-sample,FORECAST_BUCKET=$bucket,DASHBOARD_DEMO_ENABLED=false",'--quiet')
        $account = (Invoke-Gcloud @('auth','list','--filter=status:ACTIVE','--format=value(account)') | Out-String).Trim()
        Invoke-Gcloud @('run','services','add-iam-policy-binding',$service,"--project=$project","--region=$region","--member=user:$account",'--role=roles/run.invoker','--condition=None','--quiet')
    }
    'Verify' {
        & (Join-Path $PSScriptRoot 'Verify.ps1')
    }
    'Schedule' {
        $verified = Get-Content (Join-Path $PSScriptRoot 'verified.local.json') -Raw | ConvertFrom-Json
        if ($verified.project -ne $project -or $verified.region -ne $region -or $verified.image -ne (Get-ReleaseImage) -or [datetime]::Parse($verified.verifiedAtUTC).ToUniversalTime() -lt (Get-Date).ToUniversalTime().AddDays(-1)) { throw 'Run cloud Verify for this release within the last 24 hours before scheduling.' }
        # A newly created schedule is immediately enabled. Use a future date
        # for initial creation, pause, then install the intended daily schedule.
        $existing = @(Get-GcloudJson @('scheduler','jobs','list',"--project=$project","--location=$region") | Where-Object { $_.name -eq "projects/$project/locations/$region/jobs/$schedule" })
        $uri = "https://run.googleapis.com/v2/projects/$project/locations/$region/jobs/${job}:run"
        if ($existing.Count -eq 0) {
            $future = (Get-Date).ToUniversalTime().AddDays(2)
            $initialCron = "0 0 $($future.Day) $($future.Month) *"
            Invoke-Gcloud @('scheduler','jobs','create','http',$schedule,"--project=$project","--location=$region","--description=$marker","--schedule=$initialCron",'--time-zone=Etc/UTC',"--uri=$uri",'--http-method=POST',"--oauth-service-account-email=$($c.SchedulerEmail)",'--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform','--headers=Content-Type=application/json','--message-body={}','--max-retry-attempts=0','--max-retry-duration=0s','--attempt-deadline=60s','--quiet')
        } elseif ($existing[0].description -ne $marker) { throw 'Schedule belongs to another setup.' }
        $status = (Invoke-Gcloud @('scheduler','jobs','describe',$schedule,"--project=$project","--location=$region",'--format=value(state)') | Out-String).Trim()
        if ($status -ne 'PAUSED') { Invoke-Gcloud @('scheduler','jobs','pause',$schedule,"--project=$project","--location=$region",'--quiet') }
        Invoke-Gcloud @('scheduler','jobs','update','http',$schedule,"--project=$project","--location=$region",'--schedule=0 6 * * *','--time-zone=America/New_York',"--uri=$uri",'--quiet')
        Write-Output 'Publisher schedule configured and PAUSED. Resume only after verification.'
    }
}
