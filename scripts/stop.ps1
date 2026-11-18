# Stop the HRIS NocoBase demo stack. Volumes are preserved (no `down -v`).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\stop.ps1
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir '.env'
$ComposeFile = Join-Path $RootDir 'deploy\compose.yaml'

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Write-Error "Missing .env."
    exit 1
}

Push-Location $RootDir
try {
    docker compose --env-file $EnvFile -f $ComposeFile stop
    if ($LASTEXITCODE -ne 0) {
        Write-Error "docker compose stop failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
    Write-Host "Stack stopped. Data volumes preserved."
} finally {
    Pop-Location
}
