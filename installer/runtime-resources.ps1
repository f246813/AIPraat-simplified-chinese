param([Parameter(Mandatory=$true)][string]$ProjectDirectory)
foreach($relative in @('ai/runtime_dependencies.py','ai/requirements.txt','ai/requirements-wav2vec2.txt','installer/src/ConfigurePython.ps1','ai/mfa_alignment_probe.py')) {
    $path=Join-Path $ProjectDirectory $relative
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw "Runtime resource missing: $path"}
    '/resource:'+ $path + ',AIPraat.' + [IO.Path]::GetFileName($path)
}
# The wizard and path-settings host both consume this exact resource list.
$license=Join-Path $ProjectDirectory 'ai/third_party/pyloudnorm/LICENSE'
if(-not (Test-Path -LiteralPath $license -PathType Leaf)){throw "Runtime license missing: $license"}
'/resource:'+ $license + ',AIPraat.pyloudnorm-LICENSE.txt'
foreach($relative in @('ai/Install-FFmpeg.ps1','ai/ffmpeg-probe.ogg')) {
    $path=Join-Path $ProjectDirectory $relative
    if(-not (Test-Path -LiteralPath $path -PathType Leaf)){throw "FFmpeg resource missing: $path"}
    '/resource:'+ $path + ',AIPraat.' + [IO.Path]::GetFileName($path)
}
