param(
    [int]$Port = 8765,
    [string]$EnvFile = "web/backend/.env"
)

$ErrorActionPreference = "Stop"
$BackendScript = Join-Path $PSScriptRoot "start_backend.ps1"
Write-Host "Starting the authenticated role portal and API on one origin." -ForegroundColor Cyan
& $BackendScript -Port $Port -EnvFile $EnvFile
exit $LASTEXITCODE
