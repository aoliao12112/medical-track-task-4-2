param(
    [int]$Port = 8766,
    [string]$EnvFile = "web/backend/.env"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ResolvedEnvFile = Join-Path $ProjectRoot $EnvFile

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project virtual environment not found: $PythonPath"
}

$Arguments = @(
    "-m", "uvicorn", "web.mobile_gateway:app",
    "--host", "0.0.0.0",
    "--port", "$Port"
)
if (Test-Path -LiteralPath $ResolvedEnvFile) {
    $Arguments += @("--env-file", $ResolvedEnvFile)
}

Set-Location -LiteralPath $ProjectRoot
Write-Host "Mobile preview gateway: http://0.0.0.0:$Port" -ForegroundColor Cyan
Write-Warning "This port is for expiring patient previews only. Keep the main portal bound to 127.0.0.1."
& $PythonPath @Arguments
