$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$runtimeRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$runtimeFiles=@{
    'runtime_dependencies.py'='ai/runtime_dependencies.py'
    'requirements.txt'='ai/requirements.txt'
    'requirements-wav2vec2.txt'='ai/requirements-wav2vec2.txt'
    'ConfigurePython.ps1'='installer/src/ConfigurePython.ps1'
}
$runtimeReport=@()
foreach($name in @('AIPraat-install.exe','AIPraat-paths.exe')) {
    $path=Join-Path $runtimeRoot $name
    $assembly=[Reflection.Assembly]::Load([IO.File]::ReadAllBytes($path))
    foreach($file in $runtimeFiles.Keys) {
        $stream=$assembly.GetManifestResourceStream('AIPraat.'+$file)
        if(-not $stream){throw "$name missing $file"}
        $sha=[Security.Cryptography.SHA256]::Create()
        try {$hash=([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','')}
        finally {$sha.Dispose();$stream.Dispose()}
        $expected=(Get-FileHash -LiteralPath (Join-Path $runtimeRoot $runtimeFiles[$file]) -Algorithm SHA256).Hash
        if($hash -ne $expected){throw "$name stale resource $file"}
        Write-Output "PASS $name embedded $file matches project source"
    }
    if(-not $assembly.GetType('AIPraat.Setup.RuntimeDependenciesForm') -or -not $assembly.GetType('AIPraat.Setup.RuntimeDependencies')){throw "$name missing shared runtime implementation"}
    $panelConstructor=$assembly.GetType('AIPraat.Setup.DependencyPanel').GetConstructor([type[]]@([bool],[string]))
    $panel=$panelConstructor.Invoke([object[]]@([bool]($name -eq 'AIPraat-install.exe'),[string]$runtimeRoot))
    try {
        foreach($field in @('LlamaServerPath','MfaExecutablePath','ModelPath','MmprojPath','Wav2Vec2ModelPath')) {
            if($panel.Controls.Find('Process'+$field,$true).Length -ne 0){throw "$name still contains duplicate processing action: $field"}
        }
        foreach($field in @('PythonPath','FfmpegPath')) {
            if($panel.Controls.Find('Process'+$field,$true).Length -ne 1){throw "$name missing installation action: $field"}
        }
        $modelCount=if($name -eq 'AIPraat-install.exe'){1}else{0}
        foreach($field in @('ModelPath','ModelPathStatus','BrowseModelPath','Wav2Vec2ModelPath','Wav2Vec2ModelPathStatus','BrowseWav2Vec2ModelPath')) {
            if($panel.Controls.Find($field,$true).Length -ne $modelCount){throw "$name unexpected language model field: $field"}
        }
        if($panel.Controls.Find('RuntimePath',$true).Length -ne 0 -or $panel.Controls.Find('ConfigureRuntime',$true).Length -ne 0 -or $panel.Controls.Find('RuntimeDetails',$true).Length -ne 1){throw "$name duplicate runtime path or entry"}
        Write-Output "PASS $name optional processing buttons removed; Python/FFmpeg installation actions retained"
    } finally {$panel.Dispose()}
    $runtimeReport+=[pscustomobject]@{file=$name;size=(Get-Item -LiteralPath $path).Length;sha256=(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant()}
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$archive=[IO.Compression.ZipFile]::OpenRead((Join-Path $runtimeRoot 'installer/build/payload.zip'))
try {
    foreach($name in @('ai/runtime_dependencies.py','ai/requirements.txt','ai/requirements-wav2vec2.txt','ai/ConfigurePython.ps1','AIPraat-paths.exe')) {
        $entry=$archive.GetEntry($name)
        if(-not $entry){throw "Payload missing $name"}
        $stream=$entry.Open();$sha=[Security.Cryptography.SHA256]::Create()
        try {$hash=([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-','')}
        finally {$sha.Dispose();$stream.Dispose()}
        $expectedPath=if($name -eq 'ai/ConfigurePython.ps1'){'installer/src/ConfigurePython.ps1'}else{$name}
        if($hash -ne (Get-FileHash -LiteralPath (Join-Path $runtimeRoot $expectedPath)).Hash){throw "Payload stale: $name"}
        Write-Output "PASS payload $name matches final delivery"
    }
}finally {$archive.Dispose()}
$runtimeReport | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtimeRoot 'test-records/installer/runtime-delivery.json') -Encoding UTF8
