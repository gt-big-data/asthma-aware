[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$root = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
Push-Location $root
try {
    & ./backend/.venv/Scripts/python.exe -B -m pytest -p no:cacheprovider backend/tests/test_forecast_job.py backend/tests/test_cloud_publication.py backend/tests/test_health.py backend/tests/test_dashboard_api.py backend/tests/test_dashboard_service.py -q
    if ($LASTEXITCODE -ne 0) { throw 'Local Python checks failed.' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/forecast-job/Test-Config.ps1
    if ($LASTEXITCODE -ne 0) { throw 'Offline config checks failed.' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Test-Deploy.ps1
    if ($LASTEXITCODE -ne 0) { throw 'Offline deployment behavior checks failed.' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/development/Test-Monitoring.ps1
    if ($LASTEXITCODE -ne 0) { throw 'Offline monitoring configuration checks failed.' }
    foreach ($file in Get-ChildItem infrastructure -Filter '*.ps1' -Recurse) {
        $parseErrors = $null
        [void][System.Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$null, [ref]$parseErrors)
        if ($parseErrors.Count -gt 0) { throw ($parseErrors | Out-String) }
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/forecast-job/Deploy.ps1 -Stage Validate
    if ($LASTEXITCODE -ne 0) { throw 'Default deployment configuration failed.' }
    & powershell -NoProfile -ExecutionPolicy Bypass -File infrastructure/forecast-job/Deploy.ps1 -Stage Validate -ConfigPath infrastructure/forecast-job/config.local.psd1
    if ($LASTEXITCODE -ne 0) { throw 'Explicit deployment configuration failed.' }
    & docker build --platform linux/amd64 -t asthma-forecast-job:local backend/forecast_job
    if ($LASTEXITCODE -ne 0) { throw 'Sample image build failed.' }
    & docker run --rm --network none asthma-forecast-job:local
    if ($LASTEXITCODE -ne 0) { throw 'Sample container failed.' }
    & docker run --rm --network none asthma-forecast-job:local --mode sample --output-dir /root/forecast
    if ($LASTEXITCODE -ne 1) { throw 'Expected sample write failure status 1.' }
    & docker build --platform linux/amd64 -t asthma-cloud-dev:local -f backend/Dockerfile.cloud backend
    if ($LASTEXITCODE -ne 0) { throw 'Cloud image build failed.' }
    $smoke = (Resolve-Path infrastructure/development/container_smoke.py).Path
    & docker run --rm --network none --mount "type=bind,source=$smoke,target=/checks.py,readonly" --env PYTHONPATH=/srv asthma-cloud-dev:local python /checks.py
    if ($LASTEXITCODE -ne 0) { throw 'Cloud container HTTP smoke check failed.' }
    Write-Output 'PASS: local tests, configuration paths, script parsing, sample success/failure, and cloud container HTTP checks.'
} finally { Pop-Location }
