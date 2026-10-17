param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference='Stop'
$root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$output=Join-Path $root 'scratch/normalization-runtime/csharp'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$resources=& (Join-Path $root 'installer/runtime-resources.ps1') -ProjectDirectory $root
foreach($hostName in @('wizard','paths')) {
    $exclude=if($hostName -eq 'wizard'){@('PathSettingsProgram.cs','Launcher.cs')}else{@('Program.cs','Launcher.cs')}
    $sources=Get-ChildItem -LiteralPath (Join-Path $root 'installer/src') -Filter '*.cs' | Where-Object {$_.Name -notin $exclude} | ForEach-Object {$_.FullName}
    $exe=Join-Path $output ($hostName+'-verification.exe')
    & $compiler /nologo /target:exe /main:LoudnessRuntimeTests /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$exe" @sources @resources (Join-Path $PSScriptRoot 'LoudnessRuntimeTests.cs')
    if($LASTEXITCODE -ne 0){throw "$hostName verification compilation failed"}
    & $exe $root $Python $hostName
    if($LASTEXITCODE -ne 0){throw "$hostName loudness runtime tests failed"}
}
