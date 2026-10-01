param(
    [Parameter(Mandatory = $true)][string]$PlanJson,
    [Parameter(Mandatory = $true)][string]$ImageDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [Parameter(Mandatory = $true)][string]$CaseId,
    [string]$BackendUrl = 'http://127.0.0.1:8000'
)

$ErrorActionPreference = 'Stop'
$plan = Get-Content -LiteralPath $PlanJson -Raw -Encoding UTF8 | ConvertFrom-Json
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$script:partialNumber = 0

function Invoke-DocumentChunk {
    param([object[]]$Documents, [string]$Label)

    $script:partialNumber++
    $number = $script:partialNumber
    $target = Join-Path $OutputDirectory ('partial-{0:D3}.json' -f $number)
    if (Test-Path -LiteralPath $target) {
        try {
            $existing = Get-Content -LiteralPath $target -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($existing.case_id -eq $CaseId) {
                Write-Host "CACHE partial=$number label=$Label"
                return
            }
        } catch {
            Write-Warning "Invalid cached partial will be replaced: $target"
        }
    }

    $temporary = "$target.download"
    $arguments = @(
        '--noproxy', '*', '-sS', '-X', 'POST', "$BackendUrl/api/cases/process",
        '-F', "case_id=$CaseId"
    )
    foreach ($document in $Documents) {
        $path = Join-Path $ImageDirectory $document.document_id
        if (-not (Test-Path -LiteralPath $path)) { throw "Missing image: $path" }
        $arguments += '-F'
        $arguments += "files=@$path"
    }
    $arguments += '-o'
    $arguments += $temporary
    $arguments += '-w'
    $arguments += '%{http_code}'

    Write-Host "RUN partial=$number label=$Label pages=$($Documents.Count)"
    $status = & curl.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "curl failed with exit code $LASTEXITCODE" }
    if ($status -eq '200') {
        Move-Item -LiteralPath $temporary -Destination $target -Force
        Write-Host "OK partial=$number label=$Label"
        return
    }

    $errorBody = if (Test-Path -LiteralPath $temporary) {
        Get-Content -LiteralPath $temporary -Raw -Encoding UTF8
    } else { '' }
    Write-Warning "FAIL partial=$number label=$Label HTTP=$status body=$errorBody"
    if ($Documents.Count -le 1) {
        throw "Single-page chunk failed: $Label HTTP=$status"
    }
    $midpoint = [math]::Floor($Documents.Count / 2)
    Invoke-DocumentChunk -Documents @($Documents[0..($midpoint - 1)]) -Label "$Label-A"
    Invoke-DocumentChunk -Documents @($Documents[$midpoint..($Documents.Count - 1)]) -Label "$Label-B"
}

foreach ($chunk in $plan.chunks) {
    $documents = @(
        foreach ($documentId in $chunk.document_ids) {
            $plan.documents | Where-Object { $_.document_id -eq $documentId } | Select-Object -First 1
        }
    )
    Invoke-DocumentChunk -Documents $documents -Label "plan-$($chunk.index)"
}

Write-Host "DONE partials=$script:partialNumber"
