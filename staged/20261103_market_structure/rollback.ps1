<#
rollback.ps1 - take MarketStructure.cs back out of NinjaTrader: the file moves to Documents\NinjaTrader 8\_to_delete\ (nothing is
deleted), or the previous version from the newest rollback_<time> folder is put back if there was one. Then F5 in NinjaTrader.
  powershell -ExecutionPolicy Bypass -File rollback.ps1
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'
$dst = Join-Path $nt 'bin\Custom\Indicators\MarketStructure.cs'
if (-not (Test-Path $dst)) { Write-Host "MarketStructure.cs is not in bin\Custom - nothing to undo" -ForegroundColor Yellow; exit 0 }
$rb = Get-ChildItem $here -Directory -Filter 'rollback_*' | Sort-Object Name -Descending | Select-Object -First 1
$keep = Join-Path $nt '_to_delete'; New-Item -ItemType Directory -Force $keep | Out-Null
Move-Item $dst (Join-Path $keep ('MarketStructure.cs.' + (Get-Date -Format 'yyyyMMdd_HHmmss'))) -Force
Write-Host "moved the deployed MarketStructure.cs to $keep"
if ($rb -and (Test-Path (Join-Path $rb.FullName 'MarketStructure.cs'))) { Copy-Item (Join-Path $rb.FullName 'MarketStructure.cs') $dst; Write-Host "restored the previous version from $($rb.Name)" }
Write-Host "Now F5 in NinjaTrader." -ForegroundColor Green
