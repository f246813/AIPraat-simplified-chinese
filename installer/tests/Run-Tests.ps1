param([string]$Python = '')
$ErrorActionPreference='Stop'
$projectRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $projectRoot -Python $Python
$verification=Join-Path $projectRoot 'installer\verification'
$output=Join-Path $projectRoot 'test-records\installer'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$compiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$source=Join-Path $projectRoot 'installer\src'
$common=@('InstallModel.cs','RuntimeDependencies.cs','FfmpegDependencies.cs','PathValidation.cs','AlignmentPaths.cs','Configuration.cs','InstallEngine.cs','DependencyDiscovery.cs','MfaAlignmentProbe.cs','DependencyPanel.cs','RuntimeDependenciesForm.cs','OutlineButton.cs','FilePathField.cs','PythonSetup.cs','PythonSetupGuide.cs','WizardForm.cs') | ForEach-Object {Join-Path $source $_}
$options=@('/nologo','/optimize+','/platform:x64','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll',
    '/reference:System.Web.Extensions.dll','/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll')
$options+='/resource:'+(Join-Path $source 'ConfigurePython.ps1')+',AIPraat.ConfigurePython.ps1'
$contracts=Join-Path $verification 'ContractTests.exe'
$runtimeResources=(& (Join-Path $projectRoot 'installer/runtime-resources.ps1') -ProjectDirectory $projectRoot) | Where-Object {$_ -notmatch 'AIPraat.ConfigurePython.ps1$'}
& $compiler @options '/target:exe' "/out:$contracts" @common (Join-Path $PSScriptRoot 'ContractTests.cs') @runtimeResources
if($LASTEXITCODE -ne 0){throw 'Contract test compilation failed.'}
$missingEnvironment=Join-Path $verification 'python_without_deps'
& $Python -m venv --without-pip $missingEnvironment
if($LASTEXITCODE -ne 0){throw 'Missing-dependency fixture creation failed.'}
& $contracts $Python (Join-Path $missingEnvironment 'Scripts\python.exe') *> (Join-Path $output 'installer-contracts.log')
$contractExit=$LASTEXITCODE
$uiHost=Join-Path $verification 'UiHost.exe'
$zip=Join-Path $projectRoot 'installer\build\payload.zip'
& $compiler @options '/target:winexe' "/out:$uiHost" "/resource:$zip,AIPraat.Payload.zip" ("/win32manifest:"+(Join-Path $projectRoot 'installer\app.manifest')) @common (Join-Path $PSScriptRoot 'UiHost.cs') @runtimeResources
if($LASTEXITCODE -ne 0){throw 'UI verification host compilation failed.'}
Push-Location $projectRoot
$previousPythonPath=$env:PYTHONPATH
try {
    $env:PYTHONPATH=Join-Path $projectRoot 'ai'
    & $Python -m unittest discover -s ai\tests *> (Join-Path $output 'ai-unit-tests.log')
    $aiExit=$LASTEXITCODE
}
finally { $env:PYTHONPATH=$previousPythonPath; Pop-Location }
Get-Content (Join-Path $output 'installer-contracts.log') -Tail 6
Get-Content (Join-Path $output 'ai-unit-tests.log') -Tail 8
if($contractExit -ne 0 -or $aiExit -ne 0){throw "Final batch failed: installer=$contractExit AI=$aiExit"}
Write-Output "FINAL BATCH PASSED. UI host: $uiHost"
