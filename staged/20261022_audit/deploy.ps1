<#
deploy.ps1 - put the 2026-10-22 audit fixes into NinjaTrader (run it yourself; Claude may not deploy to the live platform).
  NoiseAreaStrategy.cs  start checks (Contracts vs accounts.json, a second contract on the account), flatten on a persistent
                        desync, intrabar daily loss stop on the account's real price, atomic day state; trading rules unchanged
  TapeRecorder.cs       each tick written once (was twice), all disk work on its own background thread, per-file failure + retry
Both were compiled together with NinjaTrader's own compiler offline (0 errors; the canary build fails as it must).

  powershell -ExecutionPolicy Bypass -File deploy.ps1        refuses while the strategy holds a position
Then in NinjaTrader: New > NinjaScript Editor > F5 (wait for "compiled"), Strategies tab > tick Enabled on NoiseAreaStrategy,
Ctrl+S to save the workspace (keeps the TapeRecorder chart). Undo: rollback.ps1, then F5 again.
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path $env:USERPROFILE 'OneDrive\Documents\NinjaTrader 8'
$files = @{ 'NoiseAreaStrategy.cs' = 'bin\Custom\Strategies\NoiseAreaStrategy.cs'; 'TapeRecorder.cs' = 'bin\Custom\Indicators\TapeRecorder.cs' }
$expect = @{ 'NoiseAreaStrategy.cs' = 'F1669821EF0277A4EEA31358352680AC42854695482C1F880E40F9EB03E6A1B4';
             'TapeRecorder.cs'      = '6CAA3DB5D79720AF135AD897B454546B98E9534A51F96E65314728A44885FBD7' }

foreach ($hb in Get-ChildItem (Join-Path $nt 'agent_exports') -Filter 'NoiseArea_status_MNQ_*.json' | Where-Object Name -notmatch 'Backtest') {
    $s = Get-Content $hb.FullName -Raw | ConvertFrom-Json
    if ($s.state -eq 'running' -and ([int]$s.account_position -ne 0 -or [string]$s.position -ne 'Flat')) {
        Write-Host "STOP: $($s.account) holds a position ($($s.position) x$($s.quantity), account $($s.account_position)). Deploy only while flat." -ForegroundColor Red; exit 1
    }
}
foreach ($f in $files.Keys) {
    $h = (Get-FileHash (Join-Path $here $f) -Algorithm SHA256).Hash
    if ($h -ne $expect[$f]) { Write-Host "STOP: $f does not match the verified build ($h)" -ForegroundColor Red; exit 1 }
}
$todo = @($files.Keys | Where-Object { (Get-FileHash (Join-Path $nt $files[$_]) -Algorithm SHA256).Hash -ne $expect[$_] })
if ($todo.Count -eq 0) { Write-Host "Already deployed - nothing to do (no new rollback copy made)." -ForegroundColor Green; exit 0 }
$rb = Join-Path $here ('rollback_' + (Get-Date -Format 'yyyyMMdd_HHmmss')); New-Item -ItemType Directory -Force $rb | Out-Null
foreach ($f in $todo) {
    $live = Join-Path $nt $files[$f]
    Copy-Item $live (Join-Path $rb $f)
    Copy-Item (Join-Path $here $f) $live -Force
    if ((Get-FileHash $live -Algorithm SHA256).Hash -ne $expect[$f]) { Write-Host "STOP: copy of $f did not verify - run rollback.ps1" -ForegroundColor Red; exit 1 }
    Write-Host "deployed $f  (previous version kept in $rb)"
}
Write-Host ""
Write-Host "Now in NinjaTrader: NinjaScript Editor > F5, then Strategies tab > Enabled on NoiseAreaStrategy, then Ctrl+S." -ForegroundColor Green
