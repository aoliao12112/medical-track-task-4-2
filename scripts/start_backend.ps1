param(
    [int]$Port = 8000,
    [string]$EnvFile = "web/backend/.env"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvConfig = Join-Path $ProjectRoot ".venv\pyvenv.cfg"
$SitePackages = Join-Path $ProjectRoot ".venv\Lib\site-packages"
$SourcePath = Join-Path $ProjectRoot "src"
$ResolvedEnvFile = Join-Path $ProjectRoot $EnvFile

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project virtual environment not found: $PythonPath. Follow docs/team-setup-and-api-guide.md first."
}

# Windows 的 venv 启动器会把解释器绝对路径写进自身配置。中文用户名被旧工具
# 错误编码后，python.exe 虽然存在，却会报 “Unable to create process”。此时直接
# 使用 pyvenv.cfg 中的基础解释器，并显式加载该 venv 的 site-packages。
$LauncherWorks = $false
try {
    & $PythonPath -c "import sys" 2>$null
    $LauncherWorks = $LASTEXITCODE -eq 0
} catch {
    $LauncherWorks = $false
}
if (-not $LauncherWorks) {
    if (-not (Test-Path -LiteralPath $VenvConfig)) {
        throw "Virtual environment launcher is broken and pyvenv.cfg is missing: $VenvConfig"
    }
    $ExecutableLine = Get-Content -LiteralPath $VenvConfig | Where-Object { $_ -match '^executable\s*=' } | Select-Object -First 1
    if (-not $ExecutableLine) {
        throw "Virtual environment launcher is broken and no base executable is recorded in pyvenv.cfg"
    }
    $BasePython = ($ExecutableLine -replace '^executable\s*=\s*', '').Trim()
    if (-not (Test-Path -LiteralPath $BasePython)) {
        throw "Virtual environment base Python does not exist: $BasePython"
    }
    $PythonPath = $BasePython
    Write-Warning "The venv launcher is broken; using its base Python with the existing site-packages."
}

$ProjectPythonPath = "$SourcePath;$SitePackages"
if ($env:PYTHONPATH) {
    $env:PYTHONPATH = "$ProjectPythonPath;$env:PYTHONPATH"
} else {
    $env:PYTHONPATH = $ProjectPythonPath
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
