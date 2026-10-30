<#
deploy.ps1 - put the 2026-10-30 build into NinjaTrader (run it yourself; Claude may not deploy to the live platform).
This build SUPERSEDES staged61030_bulenox_rules (it contains everything from it) and adds the chandelier exit.
  NoiseAreaMomentum.cs  chandelier exit "Chandelier exit (x ATR)" (default 3 x ATR(14), 0 = the validated rules): once in a position, a
                        5-minute close more than 3 ATR beyond the best close since entry exits at the next open; the rules may re-enter at
                        a later check. Gold dotted plot = the exit level. Adopted at Sashkin's decision on 2026-10-30 (the research verdict,
                        research\CHVAL_RESULTS.txt, is "not significant / killed"; the paper comparison runs nightly in fwd_shadow).
                        + activity fallback "Activity after N dry sessions" / "Activity multiplier" (0 / 0 = off; Bulenox accounts 3 / 0)
  NoiseAreaStrategy.cs  passes both; start refused when 'Chandelier exit' differs from accounts.json "chandelier" for the account (Sim101 is
                        registered with 3) or 'Activity after' from "activity_after"; event "chandelier" on every such exit; heartbeat
                        "chandelier" / "ch_level"; same-direction guard (simulation accounts exempt).
Both were compiled with the whole Custom project by NinjaTrader's own compiler offline (0 errors, 2026-10-30 17:30 ET).

  powershell -ExecutionPolicy Bypass -File deploy.ps1        refuses while the strategy holds a position
Then in NinjaTrader: New > NinjaScript Editor > F5 (wait for "compiled"), Strategies tab > tick Enabled on NoiseAreaStrategy
(new settings take their defaults: Chandelier exit 3 / ATR 14 - matches accounts.json; Activity 0 / 0; guard on), Ctrl+S.
To run WITHOUT the chandelier: set 'Chandelier exit (x ATR)' = 0 on the strategy AND "chandelier": 0 in reviewccounts.json.
Undo: rollback.ps1, then F5 again.
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'   # the Documents folder NinjaTrader itself uses
$files = @{ 'NoiseAreaStrategy.cs' = 'bin\Custom\Strategies\NoiseAreaStrategy.cs'; 'NoiseAreaMomentum.cs' = 'bin\Custom\Indicators\NoiseAreaMomentum.cs' }
$expect = @{ 'NoiseAreaStrategy.cs' = '32E0416F5EF541B3EB07C591634CA014565A2B6E20C3566E475762966124CD39';
             'NoiseAreaMomentum.cs' = 'FFBE7B615E60FB280BFE45A9D018738085C4CFC34D36EE36B046B432B67FADF0' }

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
