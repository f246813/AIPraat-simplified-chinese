$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
# Inspect metadata/resources without loading project executables for execution.
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
foreach($taskReference in @([System.ComponentModel.Component].Assembly,[System.Windows.Forms.UserControl].Assembly,[System.Drawing.Bitmap].Assembly)) {
    [void][Reflection.Assembly]::ReflectionOnlyLoad($taskReference.FullName)
}
$taskResources=@{
    'runtime_dependencies.py'='ai/runtime_dependencies.py'
    'Install-FFmpeg.ps1'='ai/Install-FFmpeg.ps1'
    'ffmpeg-probe.ogg'='ai/ffmpeg-probe.ogg'
}
foreach($taskName in @('AIPraat-install.exe','AIPraat-paths.exe')) {
    $taskAssembly=[Reflection.Assembly]::ReflectionOnlyLoad([IO.File]::ReadAllBytes((Join-Path $taskRoot $taskName)))
    if($taskAssembly.GetType('AIPraat.Setup.AlignmentPathFields')){throw "$taskName still contains retired AlignmentPathFields"}
    Write-Output "PASS $taskName excludes retired AlignmentPathFields"
    foreach($taskType in @('DependencyPanel','DependencyDiscovery','RuntimeDependenciesForm','FfmpegDependencies')) {
        if(-not $taskAssembly.GetType('AIPraat.Setup.'+$taskType)){throw "$taskName missing $taskType"}
    }
    foreach($taskFile in $taskResources.Keys) {
        $taskStream=$taskAssembly.GetManifestResourceStream('AIPraat.'+$taskFile)
        if(-not $taskStream){throw "$taskName missing $taskFile"}
        $taskSha=[Security.Cryptography.SHA256]::Create()
        try{$taskHash=([BitConverter]::ToString($taskSha.ComputeHash($taskStream))).Replace('-','')}
        finally{$taskStream.Dispose();$taskSha.Dispose()}
        if($taskHash -ne (Get-FileHash -LiteralPath (Join-Path $taskRoot $taskResources[$taskFile])).Hash){throw "$taskName stale resource $taskFile"}
    }
    Write-Output "PASS $taskName contains shared panel, discovery and current embedded resources"
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$taskZip=[IO.Compression.ZipFile]::OpenRead((Join-Path $taskRoot 'installer/build/payload.zip'))
try {
    foreach($taskName in @('AIPraat-paths.exe','Praat.exe','ai/runtime_dependencies.py','ai/Install-FFmpeg.ps1')) {
        $taskEntry=$taskZip.GetEntry($taskName)
        if(-not $taskEntry){throw "Payload missing $taskName"}
        $taskStream=$taskEntry.Open();$taskSha=[Security.Cryptography.SHA256]::Create()
        try{$taskHash=([BitConverter]::ToString($taskSha.ComputeHash($taskStream))).Replace('-','')}
        finally{$taskStream.Dispose();$taskSha.Dispose()}
        if($taskHash -ne (Get-FileHash -LiteralPath (Join-Path $taskRoot $taskName)).Hash){throw "Payload stale: $taskName"}
        Write-Output "PASS payload $taskName matches final delivery"
    }
}finally{$taskZip.Dispose()}
$taskReport=Get-Content -LiteralPath (Join-Path $taskRoot 'installer/build/build-report.json') -Raw | ConvertFrom-Json
if($taskReport.sha256 -ne (Get-FileHash -LiteralPath (Join-Path $taskRoot 'AIPraat-install.exe')).Hash.ToLowerInvariant()){throw 'Installer build report hash mismatch'}
Write-Output 'PASS installer SHA-256 matches build report'
