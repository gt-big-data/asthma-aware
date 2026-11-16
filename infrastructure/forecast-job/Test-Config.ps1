# Offline checks only: never imports Deploy.ps1 or invokes gcloud/Docker.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot 'Common.ps1')
$example = Join-Path $PSScriptRoot 'config.example.psd1'
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ("forecast-config-" + [guid]::NewGuid().ToString() + '.psd1')
$checks = 0
try {
    foreach ($file in Get-ChildItem $PSScriptRoot -Filter '*.ps1') {
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$null, [ref]$parseErrors)
        if ($parseErrors.Count -gt 0) { throw ($parseErrors | Out-String) }
    }
    try { $null = Read-ForecastConfig $example; throw 'Expected placeholder rejection.' }
    catch { if ($_.Exception.Message -notlike 'Set a real ProjectId*') { throw } }
    $checks++
    $valid = (Get-Content $example -Raw).Replace('REPLACE_WITH_PROJECT_ID', 'sample-project-123').Replace('REPLACE_WITH_REGION', 'us-central1')
    Set-Content -LiteralPath $temporary -Value $valid -Encoding UTF8
    $c = Read-ForecastConfig $temporary
    if ($c.RunUri -ne 'https://run.googleapis.com/v2/projects/sample-project-123/locations/us-central1/jobs/asthma-forecast-sample:run') { throw 'Incorrect Jobs API URI.' }
    if ($c.Image -ne 'us-central1-docker.pkg.dev/sample-project-123/asthma-forecast-job/forecast:sample-v1') { throw 'Incorrect image URI.' }
    if ($c.RuntimeEmail -eq $c.SchedulerEmail) { throw 'Accounts must differ.' }
    $checks++
    $cases = @(
        @{Text = $valid.Replace("Region = 'us-central1'", "Region = ''"); Expected = 'Set a real Region*'},
        @{Text = $valid.Replace("Region = 'us-central1'", "Region = 'invalid'"); Expected = 'Invalid Region*'},
        @{Text = $valid.Replace("SchedulerAccount = 'asthma-forecast-scheduler'", "SchedulerAccount = 'asthma-forecast-runtime'"); Expected = 'Runtime and Scheduler accounts must differ*'},
        @{Text = $valid.Replace("Schedule = '0 6 * * *'", "Schedule = 'invalid'"); Expected = 'Use a numeric five-field cron*'}
    )
    foreach ($case in $cases) {
        Set-Content -LiteralPath $temporary -Value $case.Text -Encoding UTF8
        try { $null = Read-ForecastConfig $temporary; throw 'Expected invalid config rejection.' }
        catch { if ($_.Exception.Message -notlike $case.Expected) { throw } }
        $checks++
    }
    Write-Output "Passed $checks offline configuration checks and PowerShell parsing. No cloud commands executed."
} finally {
    if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
}
