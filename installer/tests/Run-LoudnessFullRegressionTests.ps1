param([Parameter(Mandatory=$true)][string]$Python)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$root=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$output=Join-Path $root 'scratch/normalization-runtime/full312/csharp'
New-Item -ItemType Directory -Force -Path $output | Out-Null
$env:TEMP=Join-Path $output 'temp';$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
# The suites drive real WebView2 smoke windows. When an interrupted run is force-killed,
# its msedgewebview2 tree survives and keeps holding desktop heap; a later suite then fails
# to create window handles ("创建窗口句柄时出错") and dies mid-case. Remove only trees whose
# host is gone, so a live AIPraat window keeps its browser processes.
$removed=0
try {
    $live=@(Get-Process | Select-Object -ExpandProperty Id)
    foreach($browser in @(Get-CimInstance Win32_Process -Filter "Name='msedgewebview2.exe'" -ErrorAction Stop)) {
        if($browser.ParentProcessId -and $live -notcontains [int]$browser.ParentProcessId) {
            Stop-Process -Id $browser.ProcessId -Force -ErrorAction SilentlyContinue;$removed++
        }
    }
} catch { Write-Host "note: orphan WebView2 cleanup skipped: $($_.Exception.Message)" }
if($removed -gt 0){Write-Host "cleaned $removed orphaned WebView2 process(es) before the suites"}
$compiler=Join-Path $env:WINDIR 'Microsoft.NET/Framework64/v4.0.30319/csc.exe'
$resources=& (Join-Path $root 'installer/runtime-resources.ps1') -ProjectDirectory $root
$sourceRoot=Join-Path $root 'installer/src'
$sources=Get-ChildItem -LiteralPath $sourceRoot -Filter '*.cs' | Where-Object {$_.Name -notin @('Program.cs','PathSettingsProgram.cs','Launcher.cs')} | ForEach-Object {$_.FullName}
$options=@('/nologo','/platform:x64','/reference:System.Windows.Forms.dll','/reference:System.Drawing.dll','/reference:System.Web.Extensions.dll','/reference:System.IO.Compression.dll','/reference:System.IO.Compression.FileSystem.dll')
# Actual entry points, not test entry points. Never use the publication builder.
foreach($entry in @('Program.cs','PathSettingsProgram.cs')) {
    $name=if($entry -eq 'Program.cs'){'AIPraat-setup-scratch.exe'}else{'AIPraat-paths-scratch.exe'}
    $exe=Join-Path $output $name
    & $compiler @options /target:winexe "/out:$exe" @sources (Join-Path $sourceRoot $entry) @resources
    if($LASTEXITCODE -ne 0){throw "Compilation failed: $entry"}
    # Inspect exact resource bytes without running either host's installation UI.
    $assembly=[Reflection.Assembly]::Load([IO.File]::ReadAllBytes($exe))
    $pairs=@{
        'runtime_dependencies.py'='ai/runtime_dependencies.py';'requirements.txt'='ai/requirements.txt';
        'requirements-wav2vec2.txt'='ai/requirements-wav2vec2.txt';'ConfigurePython.ps1'='installer/src/ConfigurePython.ps1';
        'pyloudnorm-LICENSE.txt'='ai/third_party/pyloudnorm/LICENSE';'mfa_alignment_probe.py'='ai/mfa_alignment_probe.py'
    }
    foreach($resource in $pairs.Keys) {
        $stream=$assembly.GetManifestResourceStream('AIPraat.'+$resource)
        if(-not $stream){throw "Missing $name resource: $resource"}
        try{$buffer=New-Object IO.MemoryStream;$stream.CopyTo($buffer);$bytes=$buffer.ToArray()}finally{$stream.Dispose()}
        $hash=[Security.Cryptography.SHA256]::Create()
        try{
            $actual=[BitConverter]::ToString($hash.ComputeHash($bytes))
            $expected=[BitConverter]::ToString($hash.ComputeHash([IO.File]::ReadAllBytes((Join-Path $root $pairs[$resource]))))
        }finally{$hash.Dispose()}
        if($actual -ne $expected){throw "Stale $name resource: $resource"}
    }
    Write-Output "PASS actual scratch host resource bytes: $name (6/6)"
}
$missing=Join-Path $output 'missing-env'
if(-not (Test-Path (Join-Path $missing 'Scripts/python.exe'))){
    & $Python -I -m venv --without-pip $missing | Out-Host
    if($LASTEXITCODE -ne 0){throw 'Missing test env creation failed'}
}
foreach($suite in @('RuntimeDependenciesTests','PythonSetupTests','RuntimeLogTests','MfaAlignmentProbeTests')) {
    $exe=Join-Path $output ($suite+'.exe')
    & $compiler @options /target:exe "/out:$exe" @sources @resources (Join-Path $PSScriptRoot ($suite+'.cs'))
    if($LASTEXITCODE -ne 0){throw "$suite compilation failed"}
    $arguments=@($Python,(Join-Path $missing 'Scripts/python.exe'))
    if($suite -eq 'RuntimeLogTests'){$arguments+=@((Join-Path $output 'AIPraat-paths-scratch.exe'),'--scratch-only')}
    # The suites exercise a real WebView2 window; the GUI/CLR stack can print
    # diagnostics to stderr that are not test results. With the script-level
    # Stop preference PowerShell would treat that native stderr as a terminating
    # error and abort the run before the exe reports its own exit code. Keep the
    # suite verdict authoritative: the exe exits 1 when a case fails.
    $strict=$ErrorActionPreference
    $ErrorActionPreference='Continue'
    try { & $exe @arguments 2>&1 | Tee-Object -FilePath (Join-Path $output ($suite+'.log')) }
    finally { $ErrorActionPreference=$strict }
    $code=$LASTEXITCODE
    if($code -ne 0){throw "$suite failed"}
}
