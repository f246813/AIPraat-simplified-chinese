param([Parameter(Mandatory=$true)][string]$ProjectDirectory)
foreach($relative in @('ai/runtime_dependencies.py','ai/requirements.txt','ai/requirements-wav2vec2.txt','installer/src/ConfigurePython.ps1')) {
    $path=Join-Path $ProjectDirectory $relative
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw "Runtime resource missing: $path"}
    '/resource:'+ $path + ',AIPraat.' + [IO.Path]::GetFileName($path)
}
foreach($relative in @('ai/Install-FFmpeg.ps1','ai/ffmpeg-probe.ogg')) {
    $path=Join-Path $ProjectDirectory $relative
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw "FFmpeg resource missing: $path"}
    '/resource:'+ $path + ',AIPraat.' + [IO.Path]::GetFileName($path)
}
