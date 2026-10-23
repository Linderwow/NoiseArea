<#
undo_switch_documents.ps1 - put Documents back where switch_documents.ps1 found it (OneDrive\Documents), from
documents_location_before.txt. NinjaTrader then uses its original folder again (still there). Sign out and back in afterwards.
#>
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$before = Join-Path $here 'documents_location_before.txt'
if (-not (Test-Path $before)) { Write-Host 'nothing to undo (no saved values)' -ForegroundColor Red; exit 1 }
$keys = @{ 'usf' = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'; 'sf' = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders' }
foreach ($l in Get-Content $before) {
    $k, $n, $v = $l.Split('|', 3)
    if (-not $v) { continue }
    New-ItemProperty -Path $keys[$k] -Name $n -Value $v -PropertyType ($(if ($k -eq 'usf') { 'ExpandString' } else { 'String' })) -Force | Out-Null
    Write-Host "restored $k $n = $v"
}
Write-Host 'Now sign out and back in.' -ForegroundColor Green
