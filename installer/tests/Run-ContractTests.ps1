param([string]$Python,[string]$MissingPython)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskSources=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'installer/src') -Filter '*.cs' | Where-Object {$_.Name -notin @('Program.cs','PathSettingsProgram.cs','Launcher.cs')} | ForEach-Object {$_.FullName}
$taskResources=& (Join-Path $taskRoot 'installer/runtime-resources.ps1') -ProjectDirectory $taskRoot
$taskExe=Join-Path $taskRoot 'installer/verification/ContractTests.exe'
& $taskCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$taskExe" @taskSources @taskResources (Join-Path $PSScriptRoot 'ContractTests.cs')
if($LASTEXITCODE -ne 0){throw 'Contract test compilation failed'}
& $taskExe $Python $MissingPython
if($LASTEXITCODE -ne 0){throw 'Installer contract tests failed'}
