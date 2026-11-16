[CmdletBinding()]
param([switch] $Terraform)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$repo = (Resolve-Path (Join-Path $PSScriptRoot '../..')).Path
$python = Join-Path $repo '.venv-integration/Scripts/python.exe'
if (-not (Test-Path -LiteralPath $python)) { $python = 'python' }
& $python (Join-Path $PSScriptRoot 'test_configuration.py')
if ($LASTEXITCODE -ne 0) { throw 'Static infrastructure invariants failed.' }
if ($Terraform) {
    if (-not (Get-Command terraform -ErrorAction SilentlyContinue)) { throw 'Terraform is unavailable; provider validation has not run.' }
    # This flag does not initialize/download providers. Run an approved init first.
    & terraform "-chdir=$PSScriptRoot" validate -no-color
    if ($LASTEXITCODE -ne 0) { throw 'Terraform provider validation failed.' }
}
Write-Output 'Offline validation complete. No cloud APIs, apply, job execution or schedule enablement invoked.'
