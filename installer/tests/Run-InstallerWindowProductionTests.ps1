param([string]$Python='D:\Praat-work\venv-ai-modern\Scripts\python.exe',[string]$FFmpeg='C:\Users\f2468\AppData\Local\AIPraat\ffmpeg\ffmpeg.exe')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskExe=Join-Path $taskRoot 'InstallerWindowProductionTests.exe'
& $taskCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll "/out:$taskExe" (Join-Path $PSScriptRoot 'InstallerWindowProductionTests.cs')
if($LASTEXITCODE -ne 0){throw 'Installer production test compilation failed'}
try{& $taskExe $Python $FFmpeg;if($LASTEXITCODE -ne 0){throw 'Installer production workflow failed'}}finally{Remove-Item -LiteralPath $taskExe}
