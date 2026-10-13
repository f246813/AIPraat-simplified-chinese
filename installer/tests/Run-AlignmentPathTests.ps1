param([string]$Python='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $taskRoot -Python $Python
$taskOutput=Join-Path $taskRoot 'installer/verification'
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$taskSources=@('InstallModel.cs','RuntimeDependencies.cs','FfmpegDependencies.cs','PathValidation.cs','Configuration.cs','InstallEngine.cs','AlignmentPaths.cs','DependencyDiscovery.cs','DependencyPanel.cs','RuntimeDependenciesForm.cs','OutlineButton.cs','FilePathField.cs','PythonSetup.cs','PythonSetupGuide.cs','PathSettings.cs','PathSettingsForm.cs','WizardForm.cs') | ForEach-Object {Join-Path $taskRoot ('installer/src/'+$_)} | Where-Object {Test-Path -LiteralPath $_}
$taskResource='/resource:'+(Join-Path $taskRoot 'installer/src/ConfigurePython.ps1')+',AIPraat.ConfigurePython.ps1'
$taskExe=Join-Path $taskOutput 'AlignmentPathTests.exe'
$runtimeResources=(& (Join-Path $taskRoot 'installer/runtime-resources.ps1') -ProjectDirectory $taskRoot) | Where-Object {$_ -notmatch 'AIPraat.ConfigurePython.ps1$'}
& $taskCompiler /nologo /platform:x64 /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll /target:exe "/out:$taskExe" $taskResource @taskSources (Join-Path $PSScriptRoot 'AlignmentPathTests.cs') @runtimeResources
if($LASTEXITCODE -ne 0){throw 'Alignment path compilation failed'}
& $taskExe $Python
if($LASTEXITCODE -ne 0){throw 'Alignment path tests failed'}
