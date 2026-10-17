param([string]$Python='',[string]$FFmpeg='',[string]$OutputDirectory='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $taskRoot -Python $Python
$FFmpeg=Resolve-TestFFmpeg -ProjectDirectory $taskRoot -FFmpeg $FFmpeg
if(-not $OutputDirectory){$OutputDirectory=Join-Path $taskRoot 'test-records/installer/dependency-discovery'}
$OutputDirectory=[IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
New-Item -ItemType Directory -Path (Join-Path $taskRoot 'installer/verification') -Force | Out-Null
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskSources=Get-ChildItem -LiteralPath (Join-Path $taskRoot 'installer/src') -Filter '*.cs' | Where-Object {$_.Name -notin @('Program.cs','PathSettingsProgram.cs','Launcher.cs')} | ForEach-Object {$_.FullName}
$taskResources=& (Join-Path $taskRoot 'installer/runtime-resources.ps1') -ProjectDirectory $taskRoot
$taskExe=Join-Path $taskRoot 'installer/verification/DependencyDiscoveryTests.exe'
& $taskCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$taskExe" @taskSources @taskResources (Join-Path $PSScriptRoot 'DependencyDiscoveryTests.cs')
if($LASTEXITCODE -ne 0){throw 'Dependency test compilation failed'}
& $taskExe $Python $FFmpeg $OutputDirectory
if($LASTEXITCODE -ne 0){throw 'Dependency discovery tests failed'}
