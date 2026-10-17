param([string]$Python='')
$ErrorActionPreference='Stop'
$runtimeRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $runtimeRoot -Python $Python
$runtimeCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$runtimeSources=Get-ChildItem -LiteralPath (Join-Path $runtimeRoot 'installer/src') -Filter '*.cs' | Where-Object {$_.Name -notin @('Program.cs','PathSettingsProgram.cs','Launcher.cs')} | ForEach-Object {$_.FullName}
$runtimeResources=& (Join-Path $runtimeRoot 'installer/runtime-resources.ps1') -ProjectDirectory $runtimeRoot
$runtimeExe=Join-Path $runtimeRoot 'installer/verification/RuntimeLogTests.exe'
& $runtimeCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$runtimeExe" @runtimeSources @runtimeResources (Join-Path $PSScriptRoot 'RuntimeLogTests.cs')
if($LASTEXITCODE -ne 0){throw 'Runtime log test compilation failed'}
& $runtimeExe $Python
if($LASTEXITCODE -ne 0){throw 'Runtime log tests failed'}
