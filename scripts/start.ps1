# Start the HRIS NocoBase demo stack (NocoBase + PostgreSQL).
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\start.ps1
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$EnvFile = Join-Path $RootDir '.env'
$ComposeFile = Join-Path $RootDir 'deploy\compose.yaml'

if (-not (Test-Path -LiteralPath $EnvFile)) {
    Write-Error "Missing .env. Copy .env.example to .env and fill in local secrets."
    exit 1
}

Push-Location $RootDir
try {
    docker compose --env-file $EnvFile -f $ComposeFile up -d
    if ($LASTEXITCODE -ne 0) {
        Write-Error "docker compose up failed with exit code $LASTEXITCODE"
        exit $LASTEXITCODE
    }
    Write-Host "Stack started. Checking status..."
    docker compose --env-file $EnvFile -f $ComposeFile ps
} finally {
    Pop-Location
}
