[CmdletBinding()]
param(
    [string] $OutputDirectory = (Join-Path $(if($env:PRAAT_AI_INSTALL_ROOT){$env:PRAAT_AI_INSTALL_ROOT}else{Split-Path -Parent $PSScriptRoot}) 'models/Qwen3.5'),
    [ValidateSet ('Q4_K_M', 'Q4_K_S', 'Q5_K_M', 'Q8_0')]
    [string] $Quantization = 'Q4_K_M'
)

$ErrorActionPreference = 'Stop'
$null = New-Item -ItemType Directory -Path $OutputDirectory -Force

$modelName = "Qwen3.5-0.8B-$Quantization.gguf"
$modelUrl = "https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/main/$modelName"
$mmprojUrl = 'https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF/resolve/main/mmproj-F16.gguf'
$modelPath = Join-Path $OutputDirectory $modelName
$mmprojPath = Join-Path $OutputDirectory 'mmproj-F16.gguf'

$modelTemporary = "$modelPath.download"
$mmprojTemporary = "$mmprojPath.download"

& curl.exe -L --fail --retry 3 --output $modelTemporary $modelUrl
if ($LASTEXITCODE -ne 0) {
    throw "Failed to download $modelName."
}
Move-Item -LiteralPath $modelTemporary -Destination $modelPath -Force

& curl.exe -L --fail --retry 3 --output $mmprojTemporary $mmprojUrl
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to download mmproj-F16.gguf.'
}
Move-Item -LiteralPath $mmprojTemporary -Destination $mmprojPath -Force

Get-Item -LiteralPath $modelPath, $mmprojPath |
    Select-Object FullName, Length, LastWriteTime
