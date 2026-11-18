# Permission verification (real sessions, backend API): row/field isolation + guarded actions.
# Usage: powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-permissions.ps1
$ErrorActionPreference = 'Stop'

$RootDir = Split-Path -Parent $PSScriptRoot
$Script = Join-Path $RootDir 'scripts\verify_permissions.py'

Push-Location $RootDir
try {
    python $Script
    exit $LASTEXITCODE
} finally {
    Pop-Location
}
