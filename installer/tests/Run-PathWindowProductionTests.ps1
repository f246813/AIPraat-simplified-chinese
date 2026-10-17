param([string]$Python='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $taskRoot -Python $Python
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskExe=Join-Path $taskRoot 'PathWindowProductionTests.exe'
& $taskCompiler /nologo /target:exe /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll "/out:$taskExe" (Join-Path $PSScriptRoot 'PathWindowProductionTests.cs')
if($LASTEXITCODE -ne 0){throw 'Path window production test compilation failed'}
try {
    & $taskExe $Python
    if($LASTEXITCODE -ne 0){throw 'Path window production tests failed'}
} finally {Remove-Item -LiteralPath $taskExe}
