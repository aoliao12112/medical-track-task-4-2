param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PythonPath = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$WebRoot = Join-Path $ProjectRoot "web\dist"

if (-not (Test-Path -LiteralPath $PythonPath)) {
    throw "Project virtual environment not found: $PythonPath. Follow docs/team-setup-and-api-guide.md first."
}

Write-Host "Web: http://127.0.0.1:$Port" -ForegroundColor Cyan
& $PythonPath -m http.server $Port --bind 127.0.0.1 --directory $WebRoot
