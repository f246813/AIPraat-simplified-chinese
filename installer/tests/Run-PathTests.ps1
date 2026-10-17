param([string]$Python = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $projectRoot -Python $Python
$output = Join-Path $projectRoot 'installer/verification'
New-Item -ItemType Directory -Path $output -Force | Out-Null
$compiler = Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$sources = @('InstallModel.cs','RuntimeDependencies.cs','FfmpegDependencies.cs','PathValidation.cs','AlignmentPaths.cs','Configuration.cs','DependencyDiscovery.cs','MfaAlignmentProbe.cs','DependencyPanel.cs','RuntimeDependenciesForm.cs','OutlineButton.cs','FilePathField.cs','PythonSetup.cs','PythonSetupGuide.cs','PathSettings.cs','PathSettingsForm.cs') | ForEach-Object { Join-Path $projectRoot ('installer/src/' + $_) } | Where-Object { Test-Path -LiteralPath $_ }
$test = Join-Path $output 'PathSettingsTests.exe'
$runtimeResources=(& (Join-Path $projectRoot 'installer/runtime-resources.ps1') -ProjectDirectory $projectRoot)
& $compiler /nologo /optimize+ /platform:x64 /target:exe /reference:System.Windows.Forms.dll /reference:System.Drawing.dll /reference:System.Web.Extensions.dll /reference:System.IO.Compression.dll /reference:System.IO.Compression.FileSystem.dll "/out:$test" @sources (Join-Path $PSScriptRoot 'PathSettingsTests.cs') @runtimeResources
if ($LASTEXITCODE -ne 0) { throw 'Path test compilation failed.' }
& $test $Python
if ($LASTEXITCODE -ne 0) { throw 'Path configuration tests failed.' }
