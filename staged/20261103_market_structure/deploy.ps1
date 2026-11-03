<#
deploy.ps1 - put the MarketStructure indicator (2026-11-03) into NinjaTrader (run it yourself; Claude may not deploy to the live platform).
  MarketStructure.cs  NEW chart indicator: swing-point market structure, BOS / CHoCH breaks, direction read, panel; non-repainting
                      (swings confirm N bars later, breaks on bar close, Calculate forced to OnBarClose). Touches nothing else:
                      NoiseAreaStrategy / NoiseAreaMomentum / TapeRecorder are unchanged.
Compiled with the whole Custom project by NinjaTrader's own compiler offline (0 errors + canary, 2026-11-03 10:29 ET).
Verdict on its direction call: agent_exports\review\research\MSB_RESULTS.txt - read it before trading the arrows.

  powershell -ExecutionPolicy Bypass -File deploy.ps1        refuses while the strategy holds a position (F5 reloads every script)
Then in NinjaTrader (Sim101 flat): New > NinjaScript Editor > F5 (wait for "compiled"); the strategy keeps running through a compile
of an unchanged strategy, but check the heartbeat afterwards. Add the indicator to any chart: right-click > Indicators > MarketStructure.
Undo: rollback.ps1 (moves the file to _to_delete), then F5 again.
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'   # the Documents folder NinjaTrader itself uses
$src = Join-Path $here 'MarketStructure.cs'
$dst = Join-Path $nt 'bin\Custom\Indicators\MarketStructure.cs'
$expect = 'A0147AF8CF685357DF166F5F13756B3D44A7BF785ADD026CD6FEF263AE99D81F'

foreach ($hb in Get-ChildItem (Join-Path $nt 'agent_exports') -Filter 'NoiseArea_status_MNQ_*.json' | Where-Object Name -notmatch 'Backtest') {
    $s = Get-Content $hb.FullName -Raw | ConvertFrom-Json
    if ($s.state -eq 'running' -and ([int]$s.account_position -ne 0 -or [string]$s.position -ne 'Flat')) {
        Write-Host "STOP: $($s.account) holds a position ($($s.position) x$($s.quantity), account $($s.account_position)). Deploy only while flat." -ForegroundColor Red; exit 1
    }
}
$h = (Get-FileHash $src -Algorithm SHA256).Hash
if ($h -ne $expect) { Write-Host "STOP: MarketStructure.cs does not match the verified build ($h)" -ForegroundColor Red; exit 1 }
if ((Test-Path $dst) -and ((Get-FileHash $dst -Algorithm SHA256).Hash -eq $expect)) { Write-Host "Already deployed - nothing to do." -ForegroundColor Green; exit 0 }
if (Test-Path $dst) {
    $rb = Join-Path $here ('rollback_' + (Get-Date -Format 'yyyyMMdd_HHmmss')); New-Item -ItemType Directory -Force $rb | Out-Null
    Copy-Item $dst (Join-Path $rb 'MarketStructure.cs'); Write-Host "previous MarketStructure.cs kept in $rb"
}
Copy-Item $src $dst -Force
if ((Get-FileHash $dst -Algorithm SHA256).Hash -ne $expect) { Write-Host "STOP: the copy did not verify - run rollback.ps1" -ForegroundColor Red; exit 1 }
Write-Host "deployed MarketStructure.cs to $dst"
Write-Host ""
Write-Host "Now in NinjaTrader: NinjaScript Editor > F5 (wait for 'compiled'), then add MarketStructure to a chart." -ForegroundColor Green
