param(
    [Parameter(Mandatory=$true)][string]$Release,
    [Parameter(Mandatory=$true)][string]$CacheRelease,
    [string]$Project = "asthma-aware",
    [string]$Account = "asthmaaware26@gmail.com"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$context = Join-Path $repo ("backend/data/release-" + [guid]::NewGuid().ToString())
New-Item -ItemType Directory -Force "$context/backend", "$context/ml-work", "$context/models/checkpoints" | Out-Null
foreach ($folder in "app", "db", "ingestion", "scripts", "requirements") {
    Copy-Item -LiteralPath "$repo/backend/$folder" -Destination "$context/backend/$folder" -Recurse
}
foreach ($file in "requirements.txt", "Dockerfile", "Dockerfile.ml", "Dockerfile.ml.dockerignore", ".dockerignore") {
    Copy-Item -LiteralPath "$repo/backend/$file" -Destination "$context/backend/$file"
}
Copy-Item -LiteralPath "$repo/ml-work/pyproject.toml" -Destination "$context/ml-work/"
Copy-Item -LiteralPath "$repo/ml-work/asthma_aware" -Destination "$context/ml-work/asthma_aware" -Recurse
Copy-Item -LiteralPath "$repo/models/checkpoints/manifest.json" -Destination "$context/models/checkpoints/"
"**/__pycache__/`n**/*.pyc`n**/.env`n**/.env.*" | Set-Content "$context/.gcloudignore"
gcloud builds submit $context --config="$PSScriptRoot/cloudbuild.yaml" "--substitutions=_RELEASE=$Release,_CACHE_RELEASE=$CacheRelease" "--account=$Account" "--project=$Project" --quiet
if ($LASTEXITCODE -ne 0) { throw "Cloud Build failed" }
