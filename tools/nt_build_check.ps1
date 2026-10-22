<#
nt_build_check.ps1 - compile the whole NinjaTrader.Custom project the way F5 does,
without NinjaTrader running, so a broken .cs never reaches the live platform.

One bad file fails NinjaTrader's entire custom build, and NT8 is the live
execution half of this project. This builds every file in NinjaTrader.Custom.csproj
plus any .cs not yet listed there (NT adds new files on its next compile) with
NinjaTrader's own bundled Roslyn, and prints every error.

Roslyn 5.0 in NT 8.1.8.x references System.Collections.Immutable 9.0.0.0, which
binds to NT's 10.0.0.3 only through NinjaTrader.exe.config's redirects - so an
AssemblyResolve handler loads NT's copies by name before any Roslyn type is touched.

Usage (Windows PowerShell 5.1):
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\nt_build_check.ps1
  ... -Canary     adds a duplicate class that must fail (CS0101): proves the check can fail
Exit code 0 when the build has no errors.
#>
param(
    [string]$Custom = (Join-Path $env:USERPROFILE 'OneDrive\Documents\NinjaTrader 8\bin\Custom'),
    [string]$NtBin = 'C:\Program Files\NinjaTrader 8\bin',
    [switch]$Canary
)
$ErrorActionPreference = 'Stop'
$fw = 'C:\Windows\Microsoft.NET\Framework64\v4.0.30319'

Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
public static class NtBuildResolve {
    static readonly Dictionary<string, Assembly> cache = new Dictionary<string, Assembly>(StringComparer.OrdinalIgnoreCase);
    public static void Install(string dir) {
        AppDomain.CurrentDomain.AssemblyResolve += delegate(object s, ResolveEventArgs e) {
            string name = new AssemblyName(e.Name).Name;
            Assembly a;
            if (cache.TryGetValue(name, out a)) return a;
            string path = Path.Combine(dir, name + ".dll");
            a = File.Exists(path) ? Assembly.LoadFrom(path) : null;
            cache[name] = a;
            return a;
        };
    }
}
'@
[NtBuildResolve]::Install($NtBin)

$asm = @{}
foreach ($d in 'System.Collections.Immutable', 'System.Reflection.Metadata', 'Microsoft.CodeAnalysis', 'Microsoft.CodeAnalysis.CSharp') {
    $asm[$d] = [System.Reflection.Assembly]::LoadFrom((Join-Path $NtBin "$d.dll"))
}
function T($a, $n) { return $asm[$a].GetType($n, $true) }
$tParseOpts = T 'Microsoft.CodeAnalysis.CSharp' 'Microsoft.CodeAnalysis.CSharp.CSharpParseOptions'
$tLangVer   = T 'Microsoft.CodeAnalysis.CSharp' 'Microsoft.CodeAnalysis.CSharp.LanguageVersion'
$tTree      = T 'Microsoft.CodeAnalysis.CSharp' 'Microsoft.CodeAnalysis.CSharp.CSharpSyntaxTree'
$tComp      = T 'Microsoft.CodeAnalysis.CSharp' 'Microsoft.CodeAnalysis.CSharp.CSharpCompilation'
$tCompOpts  = T 'Microsoft.CodeAnalysis.CSharp' 'Microsoft.CodeAnalysis.CSharp.CSharpCompilationOptions'
$tSyntax    = T 'Microsoft.CodeAnalysis' 'Microsoft.CodeAnalysis.SyntaxTree'
$tMetaRef   = T 'Microsoft.CodeAnalysis' 'Microsoft.CodeAnalysis.MetadataReference'
$tOutKind   = T 'Microsoft.CodeAnalysis' 'Microsoft.CodeAnalysis.OutputKind'
$tSeverity  = T 'Microsoft.CodeAnalysis' 'Microsoft.CodeAnalysis.DiagnosticSeverity'

# Sources: the csproj's Compile items (URL-encoded: @ is %40) plus .cs files NT has not listed yet.
[xml]$proj = Get-Content (Join-Path $Custom 'NinjaTrader.Custom.csproj')
$listed = @($proj.Project.ItemGroup.Compile | Where-Object { $_ -and $_.Include } |
            ForEach-Object { [System.Uri]::UnescapeDataString($_.Include) })
# NT rewrites the csproj from the folder on every F5 (csproj and DLL share a timestamp), so an entry whose file was
# moved out since the last F5 is stale, not a build input. Skip it, as F5 will.
$gone   = @($listed | Where-Object { -not (Test-Path -LiteralPath (Join-Path $Custom $_)) })
$listed = @($listed | Where-Object { $gone -notcontains $_ })
$extra = @(Get-ChildItem $Custom -Recurse -Filter *.cs | ForEach-Object { $_.FullName.Substring($Custom.Length + 1) } |
           Where-Object { $_ -notmatch '^(obj|bin)\\' -and $listed -notcontains $_ })
Write-Output ("csproj files: " + $listed.Count + " | not yet in csproj: " + $(if ($extra) { $extra -join ', ' } else { 'none' }) +
              " | listed but no longer on disk (skipped): " + $(if ($gone) { $gone -join ', ' } else { 'none' }))

$parse = $tParseOpts.GetProperty('Default').GetValue($null).WithLanguageVersion([Enum]::Parse($tLangVer, 'Latest'))
$parseText = $tTree.GetMethods() | Where-Object { $_.Name -eq 'ParseText' -and $_.GetParameters().Count -eq 5 -and $_.GetParameters()[0].ParameterType -eq [string] } | Select-Object -First 1
function Parse([string]$text, [string]$path) {
    return $parseText.Invoke($null, [object[]]@($text, $parse.PSObject.BaseObject, $path, [System.Text.Encoding]::UTF8, [System.Threading.CancellationToken]::None))
}
$trees = [Activator]::CreateInstance([System.Collections.Generic.List``1].MakeGenericType($tSyntax))
foreach ($f in ($listed + $extra)) {
    $path = [string](Join-Path $Custom $f)
    $trees.Add((Parse ([System.IO.File]::ReadAllText($path)) $path))
}
if ($Canary) {
    # Self-contained duplicate (CS0101) so the canary fails whatever strategies exist. It used to duplicate MNQPropBook,
    # which silently stopped failing once MNQPropBook.cs left bin\Custom (found 2026-10-17).
    $trees.Add((Parse 'namespace NinjaTrader.NinjaScript.Strategies { public class BuildCheckCanary { } public class BuildCheckCanary { } }' 'canary.cs'))
}

# References: the csproj's HintPaths plus the framework assemblies NT compiles against.
$refPaths = @(
    "$fw\mscorlib.dll", "$fw\System.dll", "$fw\System.Core.dll", "$fw\System.ComponentModel.DataAnnotations.dll",
    "$fw\System.Net.Http.dll", "$fw\System.Web.Extensions.dll", "$fw\System.Windows.Forms.dll", "$fw\System.Xaml.dll",
    "$fw\System.Xml.dll", "$fw\System.Xml.Linq.dll", "$fw\Microsoft.CSharp.dll", "$fw\System.Drawing.dll",
    "$fw\WPF\WindowsBase.dll", "$fw\WPF\PresentationCore.dll", "$fw\WPF\PresentationFramework.dll", "$fw\WPF\UIAutomationProvider.dll",
    "$fw\netstandard.dll", "$fw\System.Runtime.dll"
)
foreach ($r in $proj.Project.ItemGroup.Reference) { if ($r.HintPath) { $refPaths += $r.HintPath } }
$createFromFile = $tMetaRef.GetMethods() | Where-Object { $_.Name -eq 'CreateFromFile' } | Select-Object -First 1
$refs = [Activator]::CreateInstance([System.Collections.Generic.List``1].MakeGenericType($tMetaRef))
foreach ($p in ($refPaths | Select-Object -Unique)) {
    if (Test-Path $p) {
        $cfArgs = @([string]$p) + @($createFromFile.GetParameters() | Select-Object -Skip 1 | ForEach-Object { [Type]::Missing })
        $refs.Add($createFromFile.Invoke($null, [object[]]$cfArgs))
    }
}

# Compile to memory. Overloads are picked by "first parameter fixed, the rest optional".
$optional = { param($m, $firstType) $ps = $m.GetParameters(); $ps.Count -gt 0 -and $ps[0].ParameterType -eq $firstType -and
              @($ps | Select-Object -Skip 1 | Where-Object { -not $_.IsOptional }).Count -eq 0 }
$ctor = $tCompOpts.GetConstructors() | Where-Object { & $optional $_ $tOutKind } | Select-Object -First 1
$cargs = @([Enum]::Parse($tOutKind, 'DynamicallyLinkedLibrary')) + @($ctor.GetParameters() | Select-Object -Skip 1 | ForEach-Object { [Type]::Missing })
$opts = $ctor.Invoke([object[]]$cargs).WithAllowUnsafe($true)
$create = $tComp.GetMethods() | Where-Object { $_.Name -eq 'Create' -and $_.GetParameters().Count -eq 4 } | Select-Object -First 1
$comp = $create.Invoke($null, @('NinjaTrader.Custom', $trees, $refs, $opts))
$ms = New-Object System.IO.MemoryStream
$emit = $comp.GetType().GetMethods() | Where-Object { $_.Name -eq 'Emit' -and (& $optional $_ ([System.IO.Stream])) } | Select-Object -First 1
$eargs = @($ms.PSObject.BaseObject) + @($emit.GetParameters() | Select-Object -Skip 1 | ForEach-Object { [Type]::Missing })
$res = $emit.Invoke($comp, [object[]]$eargs)
$errSev = [Enum]::Parse($tSeverity, 'Error')
$errors = @($res.Diagnostics | Where-Object { $_.Severity -eq $errSev })
Write-Output ("build " + $(if ($res.Success) { 'OK' } else { 'FAILED' }) + " | errors: " + $errors.Count + " | assembly bytes: " + $ms.Length)
$errors | Select-Object -First 25 | ForEach-Object { Write-Output ("  " + $_.ToString()) }
if ($res.Success) { exit 0 } else { exit 1 }
