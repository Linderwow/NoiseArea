<#
install.ps1 - put this package into a NinjaTrader 8 user folder on a new machine (2026-10-22).

    powershell -ExecutionPolicy Bypass -File tools\install.ps1                 # dry run: shows what would be copied
    powershell -ExecutionPolicy Bypass -File tools\install.ps1 -Apply          # copies

  -Nt8 <folder>   the NinjaTrader 8 user folder (default: Documents\NinjaTrader 8, OneDrive Documents first)

What it does with -Apply:
  1. refuses to run while NinjaTrader is open (it rewrites the workspace and NinjaScript files on exit / F5);
  2. backs up every file it would overwrite to <Nt8>\_to_delete\install_backup_<time>\ (nothing is deleted);
  3. copies nt8\* into <Nt8> (CLAUDE.md, bin\Custom NinjaScript, templates, workspaces, agent_exports\review);
  4. rewrites this machine's path into the scheduled-check prompts (agent_exports\review\routines\*.md) and CLAUDE.md, which
     were written for C:\Users\linde\OneDrive\Documents\NinjaTrader 8.
Then follow README.md "New machine" from step 4 (F5, workspace, strategy settings, recorder, Python, routines).
#>
param([string]$Nt8 = '', [switch]$Apply)
$ErrorActionPreference = 'Stop'
$repo = Split-Path -Parent $PSScriptRoot
$src = Join-Path $repo 'nt8'
$oldRoot = 'C:\Users\linde\OneDrive\Documents\NinjaTrader 8'

if (-not $Nt8) {
    foreach ($b in @((Join-Path $env:USERPROFILE 'OneDrive\Documents'), (Join-Path $env:USERPROFILE 'Documents'))) {
        if (Test-Path (Join-Path $b 'NinjaTrader 8')) { $Nt8 = Join-Path $b 'NinjaTrader 8'; break }
    }
}
if (-not $Nt8 -or -not (Test-Path $Nt8)) { throw "NinjaTrader 8 user folder not found - install NinjaTrader 8, start it once, close it, or pass -Nt8 <folder>" }
if ($Apply -and (Get-Process -Name NinjaTrader -ErrorAction SilentlyContinue)) { throw "NinjaTrader is running - close it first" }

$files = Get-ChildItem -Path $src -Recurse -File
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backup = Join-Path $Nt8 "_to_delete\install_backup_$stamp"
$new = 0; $over = 0
foreach ($f in $files) {
    $rel = $f.FullName.Substring($src.Length + 1)
    $dst = Join-Path $Nt8 $rel
    if (Test-Path $dst) {
        $over++
        if ($Apply) { $b = Join-Path $backup $rel; New-Item -ItemType Directory -Force (Split-Path $b) | Out-Null; Copy-Item -LiteralPath $dst $b -Force }
    } else { $new++ }
    if ($Apply) { New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null; Copy-Item -LiteralPath $f.FullName $dst -Force }
}
Write-Output ("{0} files -> {1}: {2} new, {3} overwritten{4}" -f $files.Count, $Nt8, $new, $over, $(if ($Apply -and $over) { " (backups in $backup)" } elseif (-not $Apply) { ' - DRY RUN, nothing copied; add -Apply' } else { '' }))

if ($Apply -and $Nt8 -ne $oldRoot) {
    $targets = @(Get-ChildItem (Join-Path $Nt8 'agent_exports\review\routines') -Filter *.md) + @(Get-Item (Join-Path $Nt8 'CLAUDE.md'))
    foreach ($t in $targets) {
        $txt = [IO.File]::ReadAllText($t.FullName)
        if ($txt.Contains($oldRoot)) { [IO.File]::WriteAllText($t.FullName, $txt.Replace($oldRoot, $Nt8), (New-Object Text.UTF8Encoding($false))); Write-Output "paths rewritten: $($t.Name)" }
    }
}
if ($Apply) { Write-Output 'Next: README.md "New machine" step 4 (start NinjaTrader, F5 in the NinjaScript Editor, open the workspace, add the strategy).' }
