<#
rollback.ps1 - put back the versions deploy.ps1 replaced (the newest rollback_<time> folder next to this script).
  powershell -ExecutionPolicy Bypass -File rollback.ps1      then F5 in NinjaTrader and re-enable the strategy
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path $env:USERPROFILE 'OneDrive\Documents\NinjaTrader 8'
$rb = Get-ChildItem $here -Directory -Filter 'rollback_*' | Sort-Object Name | Select-Object -Last 1
if (-not $rb) { Write-Host "no rollback folder - nothing was deployed from here" -ForegroundColor Red; exit 1 }
$files = @{ 'NoiseAreaStrategy.cs' = 'bin\Custom\Strategies\NoiseAreaStrategy.cs'; 'TapeRecorder.cs' = 'bin\Custom\Indicators\TapeRecorder.cs' }
foreach ($f in $files.Keys) { Copy-Item (Join-Path $rb.FullName $f) (Join-Path $nt $files[$f]) -Force; Write-Host "restored $f from $($rb.Name)" }
Write-Host "Now F5 in NinjaTrader and re-enable NoiseAreaStrategy." -ForegroundColor Green
