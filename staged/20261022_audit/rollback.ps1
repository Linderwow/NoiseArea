<#
rollback.ps1 - put back the versions deploy.ps1 replaced: the newest rollback_<time> folder whose files are NOT the deployed
build (a repeated deploy once backed up the new files themselves - those folders are skipped).
  powershell -ExecutionPolicy Bypass -File rollback.ps1      then F5 in NinjaTrader and re-enable the strategy
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'   # the Documents folder NinjaTrader itself uses
$files = @{ 'NoiseAreaStrategy.cs' = 'bin\Custom\Strategies\NoiseAreaStrategy.cs'; 'TapeRecorder.cs' = 'bin\Custom\Indicators\TapeRecorder.cs' }
$new = @((Get-FileHash (Join-Path $here 'NoiseAreaStrategy.cs') -Algorithm SHA256).Hash, (Get-FileHash (Join-Path $here 'TapeRecorder.cs') -Algorithm SHA256).Hash)
$rb = Get-ChildItem $here -Directory -Filter 'rollback_*' | Sort-Object Name -Descending | Where-Object {
    $d = $_.FullName; @($files.Keys | Where-Object { (Test-Path (Join-Path $d $_)) -and ($new -notcontains (Get-FileHash (Join-Path $d $_) -Algorithm SHA256).Hash) }).Count -gt 0
} | Select-Object -First 1
if (-not $rb) { Write-Host "no rollback folder with an earlier version - nothing to restore" -ForegroundColor Red; exit 1 }
foreach ($f in $files.Keys) {
    $src = Join-Path $rb.FullName $f
    if ((Test-Path $src) -and ($new -notcontains (Get-FileHash $src -Algorithm SHA256).Hash)) {
        Copy-Item $src (Join-Path $nt $files[$f]) -Force; Write-Host "restored $f from $($rb.Name)"
    }
}
Write-Host "Now F5 in NinjaTrader and re-enable NoiseAreaStrategy." -ForegroundColor Green
