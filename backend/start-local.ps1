param([int]$Port = 8000)

$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path -Parent $PSScriptRoot
$commonGitDirectory = & git -C $repositoryRoot rev-parse --path-format=absolute --git-common-dir
if ($LASTEXITCODE -ne 0) { throw 'Could not locate the shared repository.' }
$sharedRepository = Split-Path -Parent $commonGitDirectory
$pythonCandidates = @(
    (Join-Path $repositoryRoot '.venv/Scripts/python.exe'),
    (Join-Path $sharedRepository '.venv/Scripts/python.exe')
)
$localPython = $pythonCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
if (-not $localPython) { throw 'Create the project .venv and install backend/requirements.txt first.' }

$env:PYTHONPATH = "$(Join-Path $repositoryRoot 'ml-work');$PSScriptRoot"
Write-Host "Starting the backend from $PSScriptRoot on port $Port. Press Ctrl+C to stop."
Push-Location $PSScriptRoot
try {
    & $localPython -m uvicorn app.main:app --reload --host 127.0.0.1 --port $Port
} finally {
    Pop-Location
}
