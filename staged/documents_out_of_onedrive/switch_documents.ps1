<#
switch_documents.ps1 - point Windows' Documents folder back to C:\Users\linde\Documents, out of OneDrive (2026-10-23).
Windows' own "Restore Default" failed ("a folder in the same location that can't be redirected") because OneDrive left cloud-only
stubs that cannot be moved while OneDrive is off. NinjaTrader 8 was therefore COPIED to C:\Users\linde\Documents\NinjaTrader 8
first (69,402 files, all verified; the original stays in OneDrive\Documents as the fallback) and this script only changes the
three settings "Restore Default" would have changed. Run it yourself with NinjaTrader closed, then sign out and back in.
  powershell -ExecutionPolicy Bypass -File switch_documents.ps1          undo: undo_switch_documents.ps1
#>
$ErrorActionPreference = 'Stop'
$here  = Split-Path -Parent $MyInvocation.MyCommand.Path
$local = Join-Path $env:USERPROFILE 'Documents'
if (Get-Process NinjaTrader -ErrorAction SilentlyContinue) { Write-Host 'STOP: close NinjaTrader first.' -ForegroundColor Red; exit 1 }
if (-not (Test-Path (Join-Path $local 'NinjaTrader 8\bin\Custom\Strategies\NoiseAreaStrategy.cs'))) {
    Write-Host "STOP: the NinjaTrader copy is not in $local\NinjaTrader 8." -ForegroundColor Red; exit 1 }
$usf = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders'
$sf  = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders'
$names = @('Personal', '{F42EE2D3-909F-4907-8871-4C22FC0BF756}')
$before = Join-Path $here 'documents_location_before.txt'
if (-not (Test-Path $before)) {
    $u = Get-ItemProperty $usf; $s = Get-ItemProperty $sf
    @("usf|Personal|" + $u.Personal, "usf|{F42EE2D3-909F-4907-8871-4C22FC0BF756}|" + $u.'{F42EE2D3-909F-4907-8871-4C22FC0BF756}', "sf|Personal|" + $s.Personal) |
        Set-Content -Path $before -Encoding UTF8
}
foreach ($n in $names) { New-ItemProperty -Path $usf -Name $n -Value '%USERPROFILE%\Documents' -PropertyType ExpandString -Force | Out-Null }
New-ItemProperty -Path $sf -Name 'Personal' -Value $local -PropertyType String -Force | Out-Null
Write-Host ("Documents now points to: " + [Environment]::ExpandEnvironmentVariables((Get-ItemProperty $usf).Personal)) -ForegroundColor Green
Write-Host "Previous values saved in $before"
Write-Host "Next: sign out of Windows and back in (Start > your name > Sign out), keep OneDrive off, then tell Claude 'signed in'." -ForegroundColor Green
