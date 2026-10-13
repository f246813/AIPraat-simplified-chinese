param([int]$ParentPid=$PID)
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$taskCompiler=Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$taskSources=@('InstallModel.cs','RuntimeDependencies.cs','FfmpegDependencies.cs','PathValidation.cs','AlignmentPaths.cs','Configuration.cs','DependencyDiscovery.cs','DependencyPanel.cs','RuntimeDependenciesForm.cs','OutlineButton.cs','FilePathField.cs','PythonSetup.cs','PythonSetupGuide.cs','PathSettings.cs','PathSettingsForm.cs') | ForEach-Object {Join-Path $taskRoot ('installer\src\'+$_)}
$taskExe=Join-Path $taskRoot 'installer\verification\PathSettingsParentTests.exe'
$runtimeResources=(& (Join-Path $taskRoot 'installer/runtime-resources.ps1') -ProjectDirectory $taskRoot)
& $taskCompiler /nologo /platform:x64 /target:exe /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$taskExe" @taskSources (Join-Path $PSScriptRoot 'PathSettingsParentTests.cs') @runtimeResources
if($LASTEXITCODE -ne 0){throw 'Parent test compilation failed'}
& $taskExe $ParentPid
if($LASTEXITCODE -ne 0){throw 'Parent tests failed'}
