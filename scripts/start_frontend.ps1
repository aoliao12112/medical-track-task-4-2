param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$VenvConfig = Join-Path $ProjectRoot ".venv\pyvenv.cfg"
$WebRoot = Join-Path $ProjectRoot "web\dist"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project virtual environment not found: $PythonPath. Follow docs/team-setup-and-api-guide.md first."
}

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
    Write-Warning "The venv launcher is broken; using its recorded base Python for the static web server."
}

Write-Host "Web: http://127.0.0.1:$Port" -ForegroundColor Cyan
& $PythonPath -m http.server $Port --bind 127.0.0.1 --directory $WebRoot
