param([string]$Python,[string]$MissingPython)
$ErrorActionPreference='Stop'
$runtimeRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$runtimeSources=Get-ChildItem -LiteralPath (Join-Path $runtimeRoot 'installer/src') -Filter '*.cs' | Where-Object {$_.Name -notin @('Program.cs','PathSettingsProgram.cs','Launcher.cs')} | ForEach-Object {$_.FullName}
$runtimeResources=& (Join-Path $runtimeRoot 'installer/runtime-resources.ps1') -ProjectDirectory $runtimeRoot
$runtimeExe=Join-Path $runtimeRoot 'installer/verification/RuntimeDependenciesTests.exe'
& $runtimeCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$runtimeExe" @runtimeSources @runtimeResources (Join-Path $PSScriptRoot 'RuntimeDependenciesTests.cs')
if($LASTEXITCODE -ne 0){throw 'Runtime test compilation failed'}
& $runtimeExe $Python $MissingPython
if($LASTEXITCODE -ne 0){throw 'Runtime tests failed'}
