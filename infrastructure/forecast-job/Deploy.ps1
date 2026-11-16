[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('Validate', 'Setup', 'Image', 'Job', 'Scheduler')]
    [string] $Stage,
    [string] $ConfigPath
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Common.ps1')
if ([string]::IsNullOrWhiteSpace($ConfigPath)) { $ConfigPath = Join-Path $PSScriptRoot 'config.local.psd1' }
$c = Read-ForecastConfig $ConfigPath
if ($Stage -eq 'Validate') {
    Write-Host "Configuration valid. No cloud commands executed. Image: $($c.Image)"
    return
}
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { throw 'Install Google Cloud CLI first.' }
$marker = 'asthma-forecast-m1'

switch ($Stage) {
    'Setup' {
        Invoke-Gcloud @('services', 'enable', 'run.googleapis.com', 'cloudscheduler.googleapis.com', 'artifactregistry.googleapis.com', 'iam.googleapis.com', "--project=$($c.ProjectId)")
        foreach ($account in @($c.RuntimeAccount, $c.SchedulerAccount)) {
            $email = "$account@$($c.ProjectId).iam.gserviceaccount.com"
            $found = @(Get-GcloudJson @('iam', 'service-accounts', 'list', "--project=$($c.ProjectId)") | Where-Object { $_.email -eq $email })
            if ($found.Count -eq 0) {
                Invoke-Gcloud @('iam', 'service-accounts', 'create', $account, "--description=$marker", "--display-name=$account", "--project=$($c.ProjectId)")
            } elseif ($found[0].description -ne $marker) { throw "Account $email already exists outside this setup; choose another name." }
        }
        $repoName = "projects/$($c.ProjectId)/locations/$($c.Region)/repositories/$($c.Repository)"
        $found = @(Get-GcloudJson @('artifacts', 'repositories', 'list', "--location=$($c.Region)", "--project=$($c.ProjectId)") | Where-Object { $_.name -eq $repoName })
        if ($found.Count -eq 0) {
            Invoke-Gcloud @('artifacts', 'repositories', 'create', $c.Repository, '--repository-format=docker', "--description=$marker", "--location=$($c.Region)", "--project=$($c.ProjectId)")
        } elseif ($found[0].description -ne $marker -or $found[0].format -ne 'DOCKER') { throw 'Repository already exists outside this setup; choose another name.' }
        Write-Host 'Setup complete. Runtime account receives no project IAM roles.'
    }
    'Image' {
        if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { throw 'Install and start Docker first.' }
        Invoke-Gcloud @('auth', 'configure-docker', "$($c.Region)-docker.pkg.dev", '--quiet')
        $context = Join-Path $PSScriptRoot '../../backend/forecast_job'
        & docker build --platform linux/amd64 --tag $c.Image $context
        if ($LASTEXITCODE -ne 0) { throw 'Docker build failed.' }
        & docker push $c.Image
        if ($LASTEXITCODE -ne 0) { throw 'Docker push failed.' }
    }
    'Job' {
        $found = @(Get-GcloudJson @('run', 'jobs', 'list', "--region=$($c.Region)", "--project=$($c.ProjectId)") | Where-Object { $_.metadata.name -eq $c.JobName })
        if ($found.Count -gt 0 -and $found[0].metadata.labels.'managed-by' -ne $marker) { throw 'Job exists outside this setup; choose another name.' }
        Invoke-Gcloud @('run', 'jobs', 'deploy', $c.JobName, "--image=$($c.Image)", "--region=$($c.Region)", "--project=$($c.ProjectId)", "--service-account=$($c.RuntimeEmail)", '--tasks=1', '--parallelism=1', '--task-timeout=300s', '--max-retries=0', '--cpu=1', '--memory=512Mi', "--labels=managed-by=$marker", '--command=python', '--args=-m,forecast_job,--mode,sample,--output-dir,/tmp/forecast', '--quiet')
        Invoke-Gcloud @('run', 'jobs', 'add-iam-policy-binding', $c.JobName, "--region=$($c.Region)", "--project=$($c.ProjectId)", "--member=serviceAccount:$($c.SchedulerEmail)", '--role=roles/run.invoker', '--quiet')
    }
    'Scheduler' {
        $resource = "projects/$($c.ProjectId)/locations/$($c.Region)/jobs/$($c.SchedulerName)"
        $found = @(Get-GcloudJson @('scheduler', 'jobs', 'list', "--location=$($c.Region)", "--project=$($c.ProjectId)") | Where-Object { $_.name -eq $resource })
        $verb = 'create'
        if ($found.Count -gt 0) {
            if ($found[0].description -ne $marker) { throw 'Scheduler job exists outside this setup; choose another name.' }
            $verb = 'update'
            Invoke-Gcloud @('scheduler', 'jobs', 'pause', $c.SchedulerName, "--location=$($c.Region)", "--project=$($c.ProjectId)", '--quiet')
        }
        $cron = $c.Schedule
        $zone = $c.TimeZone
        if ($verb -eq 'create') {
            $future = (Get-Date).ToUniversalTime().AddDays(2)
            $cron = "0 0 $($future.Day) $($future.Month) *"
            $zone = 'Etc/UTC'
        }
        Invoke-Gcloud @('scheduler', 'jobs', $verb, 'http', $c.SchedulerName, "--location=$($c.Region)", "--project=$($c.ProjectId)", "--description=$marker", "--schedule=$cron", "--time-zone=$zone", "--uri=$($c.RunUri)", '--http-method=POST', "--oauth-service-account-email=$($c.SchedulerEmail)", '--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform', '--headers=Content-Type=application/json', '--message-body={}', '--attempt-deadline=60s', '--max-retry-attempts=0', '--max-retry-duration=0s', '--quiet')
        if ($verb -eq 'create') {
            Invoke-Gcloud @('scheduler', 'jobs', 'pause', $c.SchedulerName, "--location=$($c.Region)", "--project=$($c.ProjectId)", '--quiet')
            Invoke-Gcloud @('scheduler', 'jobs', 'update', 'http', $c.SchedulerName, "--location=$($c.Region)", "--project=$($c.ProjectId)", "--schedule=$($c.Schedule)", "--time-zone=$($c.TimeZone)", '--quiet')
        }
        Write-Host 'Scheduler configured and PAUSED. Resume after manual execution checks pass.'
    }
}
