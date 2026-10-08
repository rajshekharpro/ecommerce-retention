param([switch]$Rebuild)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if ($Rebuild -or -not (Test-Path -LiteralPath 'dashboard/data.js')) {
    python -m retention.pipeline demo
    if ($LASTEXITCODE -ne 0) { throw 'Analytics build failed' }
}
Start-Process (Join-Path $PSScriptRoot 'dashboard/index.html')
