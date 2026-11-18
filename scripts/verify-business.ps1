# Business-flow verification (real sessions): submit/approve/reject, forgery, concurrency, rollback.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-business.ps1
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $RootDir 'scripts\verify_business.py'

Push-Location $RootDir
try {
    python $Script
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
