param([string]$Python='',[string]$FFmpeg='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $taskRoot -Python $Python
$FFmpeg=Resolve-TestFFmpeg -ProjectDirectory $taskRoot -FFmpeg $FFmpeg
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskExe=Join-Path $taskRoot 'InstallerWindowProductionTests.exe'
& $taskCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/out:$taskExe" (Join-Path $PSScriptRoot 'InstallerWindowProductionTests.cs')
if($LASTEXITCODE -ne 0){throw 'Installer production test compilation failed'}
try{& $taskExe $Python $FFmpeg;if($LASTEXITCODE -ne 0){throw 'Installer production workflow failed'}}finally{Remove-Item -LiteralPath $taskExe}
