<#
deploy.ps1 - OrderPnLLabels fix (2026-10-23): with the Account setting blank it now labels the account that has a position AND
working stop/target orders (it used to take the first account with any position - Sim101 holding NoiseArea's trade, which has
no stop/target - and drew nothing for your Chart Trader bracket). Offline build 0 errors, canary fails.
Run it AFTER the close, while NoiseArea is flat: F5 restarts every running strategy.
  powershell -ExecutionPolicy Bypass -File deploy.ps1          then NinjaScript Editor > F5, re-enable NoiseAreaStrategy, Ctrl+S
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$nt = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'NinjaTrader 8'
$live = Join-Path $nt 'bin\Custom\Indicators\OrderPnLLabels.cs'
$expect = '36D88ACA32100748B216A8B76771EA2BF92E74A6E5BD648192D705C57FBB1EAA'
foreach ($hb in Get-ChildItem (Join-Path $nt 'agent_exports') -Filter 'NoiseArea_status_MNQ_*.json' | Where-Object Name -notmatch 'Backtest') {
    $s = Get-Content $hb.FullName -Raw | ConvertFrom-Json
    if ($s.state -eq 'running' -and ([int]$s.account_position -ne 0 -or [string]$s.position -ne 'Flat')) {
        Write-Host "STOP: $($s.account) holds a position ($($s.position) x$($s.quantity)). F5 would restart the strategy mid-trade - deploy after the close." -ForegroundColor Red; exit 1
    }
}
if ((Get-FileHash (Join-Path $here 'OrderPnLLabels.cs') -Algorithm SHA256).Hash -ne $expect) { Write-Host 'STOP: staged file does not match the verified build' -ForegroundColor Red; exit 1 }
if ((Get-FileHash $live -Algorithm SHA256).Hash -eq $expect) { Write-Host 'Already deployed.' -ForegroundColor Green; exit 0 }
$rb = Join-Path $here ('rollback_' + (Get-Date -Format 'yyyyMMdd_HHmmss')); New-Item -ItemType Directory -Force $rb | Out-Null
Copy-Item $live (Join-Path $rb 'OrderPnLLabels.cs')
Copy-Item (Join-Path $here 'OrderPnLLabels.cs') $live -Force
if ((Get-FileHash $live -Algorithm SHA256).Hash -ne $expect) { Write-Host "STOP: copy did not verify - restore from $rb" -ForegroundColor Red; exit 1 }
Write-Host "deployed OrderPnLLabels.cs (previous version in $rb)" -ForegroundColor Green
Write-Host 'Now: NinjaScript Editor > F5, Strategies tab > re-enable NoiseAreaStrategy, Ctrl+S.' -ForegroundColor Green
