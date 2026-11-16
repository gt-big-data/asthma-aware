# Offline template checks only. The dummy channel is never sent to Google Cloud.
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$temporary = Join-Path ([System.IO.Path]::GetTempPath()) ('asthma-monitor-' + [guid]::NewGuid() + '.json')
try {
    . (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
    $c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
    @{notificationChannel="projects/$($c.ProjectId)/notificationChannels/123"} | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    & (Join-Path $PSScriptRoot 'Monitor.ps1') -ValidateOnly -ConfigPath $temporary
    @{notificationChannel='REPLACE_WITH_NOTIFICATION_CHANNEL'} | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding UTF8
    try { & (Join-Path $PSScriptRoot 'Monitor.ps1') -ValidateOnly -ConfigPath $temporary; throw 'Expected placeholder rejection.' }
    catch { if ($_.Exception.Message -notlike 'Set a real notification channel*') { throw } }
    Write-Output 'PASS monitoring JSON serialization and placeholder rejection. No cloud commands executed.'
} finally { if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary } }
