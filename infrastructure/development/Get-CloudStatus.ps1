# Read-only cloud evidence collection. Credentials remain in memory.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$evidence = Join-Path $PSScriptRoot 'evidence'
New-Item -ItemType Directory -Force -Path $evidence | Out-Null
foreach ($job in @('asthma-forecast-publisher-dev','asthma-gcs-acceptance-dev','asthma-freshness-dev')) {
    $filter = 'resource.type="cloud_run_job" AND resource.labels.job_name="' + $job + '"'
    $logs = Get-GcloudJson @('logging','read',$filter,"--project=$($c.ProjectId)",'--limit=40','--format=json')
    $logs | ConvertTo-Json -Depth 25 | Set-Content (Join-Path $evidence "$job-logs.json") -Encoding UTF8
    $textEntries = @($logs | Where-Object { $_.PSObject.Properties['textPayload'] } | Select-Object -First 3)
    foreach ($entry in $textEntries) { Write-Output "$job $($entry.textPayload)" }
}
$token = (Invoke-Gcloud @('auth','print-access-token') | Out-String).Trim()
try {
    $headers = @{Authorization="Bearer $token";'x-goog-user-project'=$c.ProjectId}
    $base = "https://monitoring.googleapis.com/v3/projects/$($c.ProjectId)"
    $alerts = Invoke-RestMethod -Uri "$base/alerts?pageSize=100" -Headers $headers
    $alerts | ConvertTo-Json -Depth 30 | Set-Content (Join-Path $evidence 'alerts.json') -Encoding UTF8
    if ($alerts.PSObject.Properties['alerts']) {
        $alerts.alerts | Select-Object name,state,openTime,policy | ConvertTo-Json -Depth 10 | Write-Output
    } else { Write-Output 'No alerts have been reported yet.' }
    $filter = [uri]::EscapeDataString('metric.type = starts_with("monitoring.googleapis.com/notification_channel/")')
    $metrics = Invoke-RestMethod -Uri "$base/metricDescriptors?filter=$filter" -Headers $headers
    $metrics | ConvertTo-Json -Depth 15 | Set-Content (Join-Path $evidence 'notification-metric-descriptors.json') -Encoding UTF8
    if ($metrics.PSObject.Properties['metricDescriptors']) {
        $metrics.metricDescriptors | Select-Object type,labels,monitoredResourceTypes,unit | ConvertTo-Json -Depth 8 | Write-Output
    }
    $end = [uri]::EscapeDataString((Get-Date).ToUniversalTime().ToString('o'))
    $start = [uri]::EscapeDataString((Get-Date).ToUniversalTime().AddHours(-2).ToString('o'))
    foreach ($metric in @('asthma_dev_failure', 'asthma_dev_freshness_success')) {
        $filter = [uri]::EscapeDataString('metric.type="logging.googleapis.com/user/' + $metric + '"')
        $series = Invoke-RestMethod -Uri "$base/timeSeries?filter=$filter&interval.startTime=$start&interval.endTime=$end" -Headers $headers
        $series | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $evidence "$metric-series.json") -Encoding UTF8
        if ($series.PSObject.Properties['timeSeries']) {
            Write-Output "Metric $metric has $(@($series.timeSeries).Count) time series."
        } else { Write-Output "Metric $metric has no points yet." }
    }
} finally { $token = $null }
