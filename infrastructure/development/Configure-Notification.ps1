[CmdletBinding()]
param([Parameter(Mandatory=$true)][string] $Email)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$env:PATH = "$env:LOCALAPPDATA\Google\Cloud SDK\google-cloud-sdk\bin;$env:PATH"
. (Join-Path $PSScriptRoot '../forecast-job/Common.ps1')
$c = Read-ForecastConfig (Join-Path $PSScriptRoot '../forecast-job/config.local.psd1')
if ($Email -notmatch '^[^\s@]+@[^\s@]+\.[^\s@]+$') { throw 'Provide the owner-approved email recipient.' }
$token = (Invoke-Gcloud @('auth','print-access-token') | Out-String).Trim()
try {
    $base = "https://monitoring.googleapis.com/v3/projects/$($c.ProjectId)/notificationChannels"
    $next = $base
    $found = @()
    do {
        $page = Invoke-RestMethod -Method Get -Uri $next -Headers @{Authorization="Bearer $token"}
        if ($page.PSObject.Properties['notificationChannels']) {
            $found += @($page.notificationChannels | Where-Object { $_.type -eq 'email' -and $_.labels.email_address -eq $Email -and $_.enabled })
        }
        $next = $null
        if ($page.PSObject.Properties['nextPageToken'] -and $page.nextPageToken) { $next = $base + '?pageToken=' + [uri]::EscapeDataString($page.nextPageToken) }
    } while ($next)
    if ($found.Count -gt 0) { $channel = $found[0] } else {
        $body = @{type='email';displayName='AsthmaAware development administrator';labels=@{email_address=$Email};enabled=$true;userLabels=@{'managed-by'='asthma-cloud-dev'}} | ConvertTo-Json -Depth 8
        $channel = Invoke-RestMethod -Method Post -Uri $base -Headers @{Authorization="Bearer $token"} -ContentType 'application/json' -Body $body
    }
    @{notificationChannel=$channel.name} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $PSScriptRoot 'observability.local.json') -Encoding UTF8
    Write-Output "Configured notification channel: $($channel.name)"
    if ($channel.PSObject.Properties['verificationStatus']) { Write-Output "Verification status: $($channel.verificationStatus)" }
} finally { $token = $null }
