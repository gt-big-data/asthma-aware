[CmdletBinding()]
param([switch] $ValidateOnly, [string] $ConfigPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
$project = $c.ProjectId
if ([string]::IsNullOrWhiteSpace($ConfigPath)) { $ConfigPath = Join-Path $PSScriptRoot 'observability.local.json' }
if (-not (Test-Path -LiteralPath $configPath)) { throw 'Copy observability.example.json to observability.local.json and choose an existing verified notification channel.' }
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
if ($config.notificationChannel -notmatch "^projects/$project/notificationChannels/[0-9]+$") { throw 'Set a real notification channel in this project. Placeholder values cannot enable monitoring.' }

# Log-based metrics keep per-execution cardinality out of metric labels.
$metrics = @(
    @{name='asthma_dev_publication_success'; description='asthma-cloud-dev'; filter='resource.type="cloud_run_job" AND resource.labels.job_name="asthma-forecast-publisher-dev" AND textPayload:"status=success" AND textPayload:"promoted=True"'},
    @{name='asthma_dev_failure'; description='asthma-cloud-dev'; filter='resource.type="cloud_run_job" AND (resource.labels.job_name="asthma-forecast-publisher-dev" OR resource.labels.job_name="asthma-freshness-dev") AND textPayload:"status=failed"'},
    @{name='asthma_dev_freshness_success'; description='asthma-cloud-dev'; filter='resource.type="cloud_run_job" AND resource.labels.job_name="asthma-freshness-dev" AND textPayload:"freshness_probe status=success"'}
)
$policies = @(
    @{displayName='AsthmaAware dev workload failure'; enabled=$true; combiner='OR'; alertStrategy=@{autoClose='1800s'}; userLabels=@{'managed-by'='asthma-cloud-dev'}; notificationChannels=@($config.notificationChannel); conditions=@(
        @{displayName='Publisher or freshness check failed'; conditionThreshold=@{filter='resource.type="cloud_run_job" AND metric.type="logging.googleapis.com/user/asthma_dev_failure"'; comparison='COMPARISON_GT'; thresholdValue=0; duration='0s'; aggregations=@(@{alignmentPeriod='300s'; perSeriesAligner='ALIGN_DELTA'; crossSeriesReducer='REDUCE_SUM'})}}
    )},
    @{displayName='AsthmaAware dev freshness monitor missing'; enabled=$true; combiner='OR'; userLabels=@{'managed-by'='asthma-cloud-dev'}; notificationChannels=@($config.notificationChannel); conditions=@(
        @{displayName='No successful freshness check for two hours'; conditionAbsent=@{filter='resource.type="cloud_run_job" AND metric.type="logging.googleapis.com/user/asthma_dev_freshness_success"'; duration='7200s'; aggregations=@(@{alignmentPeriod='300s'; perSeriesAligner='ALIGN_DELTA'; crossSeriesReducer='REDUCE_SUM'})}}
    )},
    @{displayName='AsthmaAware dev API errors'; enabled=$true; combiner='OR'; userLabels=@{'managed-by'='asthma-cloud-dev'}; notificationChannels=@($config.notificationChannel); conditions=@(
        @{displayName='Repeated API server errors'; conditionThreshold=@{filter='resource.type="cloud_run_revision" AND resource.labels.service_name="asthma-api-dev" AND metric.type="run.googleapis.com/request_count" AND metric.labels.response_code_class="5xx"'; comparison='COMPARISON_GT'; thresholdValue=2; duration='0s'; aggregations=@(@{alignmentPeriod='300s'; perSeriesAligner='ALIGN_DELTA'; crossSeriesReducer='REDUCE_SUM'})}}
    )},
    @{displayName='AsthmaAware dev API latency'; enabled=$true; combiner='OR'; userLabels=@{'managed-by'='asthma-cloud-dev'}; notificationChannels=@($config.notificationChannel); conditions=@(
        @{displayName='P95 API latency over 10 seconds'; conditionThreshold=@{filter='resource.type="cloud_run_revision" AND resource.labels.service_name="asthma-api-dev" AND metric.type="run.googleapis.com/request_latencies"'; comparison='COMPARISON_GT'; thresholdValue=10000; duration='300s'; aggregations=@(@{alignmentPeriod='300s'; perSeriesAligner='ALIGN_PERCENTILE_95'; crossSeriesReducer='REDUCE_MAX'})}}
    )}
)
if ($ValidateOnly) {
    $metrics | ConvertTo-Json -Depth 20 | ConvertFrom-Json | Out-Null
    $policies | ConvertTo-Json -Depth 20 | ConvertFrom-Json | Out-Null
    Write-Output 'Monitoring configuration validates locally. Cloud acceptance and notification delivery remain unverified.'
    return
}
$token = (Invoke-Gcloud @('auth','print-access-token') | Out-String).Trim()
function Call-API([string] $Method, [string] $Uri, $Body = $null) {
    Write-Host "Monitoring API: $Method $Uri"
    $parameters = @{Method=$Method;Uri=$Uri;Headers=@{Authorization="Bearer $token"}}
    if ($null -ne $Body) { $parameters.ContentType='application/json'; $parameters.Body=($Body | ConvertTo-Json -Depth 25) }
    try { return Invoke-RestMethod @parameters }
    catch {
        if ($null -ne $_.ErrorDetails) { Write-Host $_.ErrorDetails.Message }
        elseif ($_.Exception.Response) {
            $reader = New-Object System.IO.StreamReader($_.Exception.Response.GetResponseStream())
            try { Write-Host $reader.ReadToEnd() } finally { $reader.Dispose() }
        }
        throw
    }
}
function List-API([string] $Uri, [string] $Field) {
    $next = $Uri
    do {
        $page = Call-API 'Get' $next
        if ($page.PSObject.Properties[$Field]) { $page.$Field }
        $next = $null
        if ($page.PSObject.Properties['nextPageToken'] -and $page.nextPageToken) {
            $next = $Uri + '?pageToken=' + [uri]::EscapeDataString($page.nextPageToken)
        }
    } while ($next)
}
try {
    $channel = Call-API 'Get' "https://monitoring.googleapis.com/v3/$($config.notificationChannel)"
    if (-not $channel.enabled -or ($channel.PSObject.Properties['verificationStatus'] -and $channel.verificationStatus -eq 'UNVERIFIED')) { throw 'Notification channel must be enabled and verified where required.' }
    $metricBase = "https://logging.googleapis.com/v2/projects/$project/metrics"
    $existingMetrics = @(List-API $metricBase 'metrics')
    foreach ($metric in $metrics) {
        $found = @($existingMetrics | Where-Object { $_.name -eq $metric.name })
        if ($found.Count -gt 0) {
            if ($found[0].description -ne 'asthma-cloud-dev') { throw 'Metric name collision.' }
            $null = Call-API 'Put' "$metricBase/$($metric.name)" $metric
        } else { $null = Call-API 'Post' $metricBase $metric }
    }
    $policyBase = "https://monitoring.googleapis.com/v3/projects/$project/alertPolicies"
    $existingPolicies = @(List-API $policyBase 'alertPolicies')
    foreach ($policy in $policies) {
        $found = @($existingPolicies | Where-Object { $_.displayName -eq $policy.displayName })
        if ($found.Count -gt 0) {
            if ($found.Count -ne 1 -or $found[0].userLabels.'managed-by' -ne 'asthma-cloud-dev') { throw 'Alert policy name collision.' }
            $policy.name = $found[0].name
            $mask = 'displayName,enabled,combiner,userLabels,notificationChannels,conditions'
            if ($policy.ContainsKey('alertStrategy')) { $mask += ',alertStrategy' }
            $null = Call-API 'Patch' "https://monitoring.googleapis.com/v3/$($policy.name)?updateMask=$mask" $policy
        } else { $null = Call-API 'Post' $policyBase $policy }
    }
    Write-Output 'Monitoring policies configured. Force a safe failure and verify receipt before marking monitoring complete.'
} finally { $token = $null }

# The freshness probe is created and executed by Verify before it is scheduled.
$freshJob = 'asthma-freshness-dev'
$freshSchedule = 'asthma-freshness-hourly-dev'
$probe = Get-GcloudJson @('run','jobs','describe',$freshJob,"--project=$project","--region=$($c.Region)")
if ($probe.metadata.labels.'managed-by' -ne 'asthma-cloud-dev') { throw 'Freshness probe ownership mismatch.' }
Invoke-Gcloud @('run','jobs','add-iam-policy-binding',$freshJob,"--project=$project","--region=$($c.Region)","--member=serviceAccount:$($c.SchedulerEmail)",'--role=roles/run.invoker','--quiet')
$existing = @(Get-GcloudJson @('scheduler','jobs','list',"--project=$project","--location=$($c.Region)") | Where-Object { $_.name -eq "projects/$project/locations/$($c.Region)/jobs/$freshSchedule" })
if ($existing.Count -eq 0) {
    $future = (Get-Date).ToUniversalTime().AddDays(2)
    Invoke-Gcloud @('scheduler','jobs','create','http',$freshSchedule,"--project=$project","--location=$($c.Region)",'--description=asthma-cloud-dev',"--schedule=0 0 $($future.Day) $($future.Month) *",'--time-zone=Etc/UTC',"--uri=https://run.googleapis.com/v2/projects/$project/locations/$($c.Region)/jobs/${freshJob}:run",'--http-method=POST',"--oauth-service-account-email=$($c.SchedulerEmail)",'--oauth-token-scope=https://www.googleapis.com/auth/cloud-platform','--headers=Content-Type=application/json','--message-body={}','--max-retry-attempts=0','--max-retry-duration=0s','--attempt-deadline=60s','--quiet')
} elseif ($existing[0].description -ne 'asthma-cloud-dev') { throw 'Freshness schedule name collision.' }
$status = (Invoke-Gcloud @('scheduler','jobs','describe',$freshSchedule,"--project=$project","--location=$($c.Region)",'--format=value(state)') | Out-String).Trim()
if ($status -ne 'PAUSED') { Invoke-Gcloud @('scheduler','jobs','pause',$freshSchedule,"--project=$project","--location=$($c.Region)",'--quiet') }
Invoke-Gcloud @('scheduler','jobs','update','http',$freshSchedule,"--project=$project","--location=$($c.Region)",'--schedule=15 * * * *','--time-zone=Etc/UTC','--quiet')
Write-Output 'Hourly freshness schedule configured and PAUSED. Enable after forced-dispatch and alert-receipt checks.'
