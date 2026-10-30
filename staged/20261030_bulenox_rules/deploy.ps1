<#
deploy.ps1 - put the 2026-10-30 Bulenox-rules build into NinjaTrader (run it yourself; Claude may not deploy to the live platform).
  NoiseAreaMomentum.cs  activity fallback: "Activity after N dry sessions" / "Activity multiplier" (0 / 0 = off = the old rules
                        exactly; adopted 3 / 0 on Bulenox accounts - research\INACTIVITY_RESULTS.txt)
  NoiseAreaStrategy.cs  passes those settings (registered per account in accounts.json "activity_after"; start refused on a
                        mismatch), latches the band when the ACCOUNT itself has N sessions without a fill, same-direction guard
                        (Bulenox Agreement s.14: no entry while another account on the connection holds the other side; simulation
                        accounts exempt, so Sim101 trades exactly as before)
Both were compiled with the whole Custom project by NinjaTrader's own compiler offline (0 errors).

  powershell -ExecutionPolicy Bypass -File deploy.ps1        refuses while the strategy holds a position
Then in NinjaTrader: New > NinjaScript Editor > F5 (wait for "compiled"), Strategies tab > tick Enabled on NoiseAreaStrategy
(its new settings default to 0 / 0 / guard on - nothing to change on Sim101), Ctrl+S. Undo: rollback.ps1, then F5 again.
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'   # the Documents folder NinjaTrader itself uses
$files = @{ 'NoiseAreaStrategy.cs' = 'bin\Custom\Strategies\NoiseAreaStrategy.cs'; 'NoiseAreaMomentum.cs' = 'bin\Custom\Indicators\NoiseAreaMomentum.cs' }
$expect = @{ 'NoiseAreaStrategy.cs' = '97545613CEDB67C27B3D981ED74199117D1AB71ABFF44A76FBC9586C9AF16E63';
             'NoiseAreaMomentum.cs' = 'E55326F304761A6090FC8230BCFA00DE16D82FE52DF8492DCE8E1ACB228DA1D3' }

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
