param([string]$Python='',[string]$FFmpeg='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $taskRoot -Python $Python
$FFmpeg=Resolve-TestFFmpeg -ProjectDirectory $taskRoot -FFmpeg $FFmpeg
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskSources=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'installer/src') -Filter '*.cs' | Where-Object {$_.Name -notin @('Program.cs','PathSettingsProgram.cs','Launcher.cs')} | ForEach-Object {$_.FullName}
$taskResources=& (Join-Path $taskRoot 'installer/runtime-resources.ps1') -ProjectDirectory $taskRoot
$taskExe=Join-Path $taskRoot 'installer/verification/InstallationPathsTests.exe'
& $taskCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$taskExe" @taskSources @taskResources (Join-Path $PSScriptRoot 'InstallationPathsTests.cs')
if($LASTEXITCODE -ne 0){throw 'Installation path test compilation failed'}
$taskFixture=Join-Path $taskRoot ('scratch/installation-paths-中文-'+[guid]::NewGuid().ToString('N'))
& $taskExe $taskFixture $FFmpeg $Python
if($LASTEXITCODE -ne 0){throw 'Installation path tests failed'}
