param([Parameter(Mandatory=$true)][string]$FFmpeg,[Parameter(Mandatory=$true)][string]$Python,[switch]$Install)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$sources=Get-ChildItem -LiteralPath (Join-Path $root 'installer/src') -Filter '*.cs' | Where-Object {$_.Name -notin @('Program.cs','PathSettingsProgram.cs','Launcher.cs')} | ForEach-Object {$_.FullName}
$resources=& (Join-Path $root 'installer/runtime-resources.ps1') -ProjectDirectory $root
$exe=Join-Path $root 'installer/verification/FfmpegTests.exe'
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
& $compiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$exe" @sources @resources (Join-Path $PSScriptRoot 'FfmpegTests.cs')
if($LASTEXITCODE -ne 0){throw 'FFmpeg tests compilation failed'}
& $exe $FFmpeg (Join-Path $root 'test-records/ai-tutor-demo-20261013/ffmpeg-path-settings.png') $Python $(if($Install){'install'}else{'probe'})
if($LASTEXITCODE -ne 0){throw 'FFmpeg tests failed'}
