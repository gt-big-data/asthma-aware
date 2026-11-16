# Dot-source for validation and checked native commands. Importing does not contact GCP.
function Read-ForecastConfig([string] $Path) {
    $c = Import-PowerShellDataFile -LiteralPath $Path
    $required = @('ProjectId', 'Region', 'Repository', 'JobName', 'SchedulerName', 'RuntimeAccount', 'SchedulerAccount', 'ImageTag', 'Schedule', 'TimeZone')
    foreach ($key in $required) {
        if (-not $c.ContainsKey($key) -or [string]::IsNullOrWhiteSpace($c[$key]) -or $c[$key] -match '(?i)replace|placeholder|your[-_ ]|[<>]') {
            throw "Set a real $key in $Path before any cloud commands."
        }
    }
    if ($c.ProjectId -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]$') { throw 'Invalid ProjectId.' }
    if ($c.Region -notmatch '^[a-z]+(?:-[a-z]+)+[0-9]+$') { throw 'Invalid Region.' }
    foreach ($key in @('Repository', 'JobName', 'SchedulerName', 'RuntimeAccount', 'SchedulerAccount')) {
        if ($c[$key] -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]$') { throw "Invalid $key; use 6-30 lowercase letters/digits/hyphens." }
    }
    if ($c.RuntimeAccount -eq $c.SchedulerAccount) { throw 'Runtime and Scheduler accounts must differ.' }
    if ($c.ImageTag -notmatch '^[a-zA-Z0-9_][a-zA-Z0-9_.-]{0,127}$') { throw 'Invalid ImageTag.' }
    if (($c.Schedule.Trim() -split '\s+').Count -ne 5 -or $c.Schedule -notmatch '^[0-9*/?, \-]+$') { throw 'Use a numeric five-field cron Schedule.' }
    if ($c.TimeZone -notmatch '^[A-Za-z_]+(?:/[A-Za-z0-9_+\-]+)+$') { throw 'Use an IANA TimeZone such as America/New_York or Etc/UTC.' }
    $c.RuntimeEmail = "$($c.RuntimeAccount)@$($c.ProjectId).iam.gserviceaccount.com"
    $c.SchedulerEmail = "$($c.SchedulerAccount)@$($c.ProjectId).iam.gserviceaccount.com"
    $c.Image = "$($c.Region)-docker.pkg.dev/$($c.ProjectId)/$($c.Repository)/forecast:$($c.ImageTag)"
    $c.RunUri = "https://run.googleapis.com/v2/projects/$($c.ProjectId)/locations/$($c.Region)/jobs/$($c.JobName):run"
    return $c
}

function Invoke-Gcloud([string[]] $Arguments) {
    & gcloud @Arguments
    if ($LASTEXITCODE -ne 0) { throw "gcloud failed: $($Arguments[0..1] -join ' ')" }
}

function Get-GcloudJson([string[]] $Arguments) {
    $result = Invoke-Gcloud ($Arguments + '--format=json')
    return ($result -join "`n" | ConvertFrom-Json)
}
