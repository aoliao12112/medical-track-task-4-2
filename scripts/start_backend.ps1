param(
    [int]$Port = 8000,
    [string]$EnvFile = "web/backend/.env"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$ResolvedEnvFile = Join-Path $ProjectRoot $EnvFile

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project virtual environment not found: $PythonPath. Follow docs/team-setup-and-api-guide.md first."
}

$UvicornArguments = @(
    "-m", "uvicorn", "web.backend.app:app",
    "--host", "127.0.0.1",
    "--port", "$Port"
)

if (Test-Path -LiteralPath $ResolvedEnvFile) {
    $UvicornArguments += @("--env-file", $ResolvedEnvFile)
    Write-Host "Using local environment file: $ResolvedEnvFile" -ForegroundColor Green
} elseif (-not $env:DEEPSEEK_API_KEY) {
    Write-Warning "$ResolvedEnvFile and DEEPSEEK_API_KEY are both missing. OCR can run, but DeepSeek steps will fail or fall back."
}

Set-Location -LiteralPath $ProjectRoot
Write-Host "Backend: http://127.0.0.1:$Port" -ForegroundColor Cyan
Write-Host "API docs: http://127.0.0.1:$Port/docs" -ForegroundColor Cyan
& $PythonPath @UvicornArguments
