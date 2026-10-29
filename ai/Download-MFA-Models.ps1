[CmdletBinding()]
param(
    [string] $MiniforgeRoot = (Join-Path $(if($env:PRAAT_AI_INSTALL_ROOT){$env:PRAAT_AI_INSTALL_ROOT}else{Split-Path -Parent $PSScriptRoot}) 'tools/miniforge3'),
    [string] $EnvironmentName = 'aligner',
    [string[]] $Models = @('english_us_arpa', 'mandarin_mfa')
)

$ErrorActionPreference = 'Stop'
if(-not $env:MFA_ROOT_DIR){$env:MFA_ROOT_DIR=Join-Path $(if($env:PRAAT_AI_INSTALL_ROOT){$env:PRAAT_AI_INSTALL_ROOT}else{Split-Path -Parent $PSScriptRoot}) 'models/mfa'}
$conda = Join-Path $MiniforgeRoot 'condabin\conda.bat'
if (-not (Test-Path -LiteralPath $conda -PathType Leaf)) {
    throw "Miniforge conda not found: $conda"
}

$credential = "protocol=https`nhost=github.com`n`n" | git credential fill
$token = (($credential | Select-String '^password=').Line -replace '^password=', '')
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'No GitHub credential was available for MFA model downloads.'
}

foreach ($modelName in $Models) {
    & $conda run -n $EnvironmentName mfa model download acoustic $modelName --github_token $token
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download acoustic model $modelName."
    }
    & $conda run -n $EnvironmentName mfa model download dictionary $modelName --github_token $token
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to download dictionary $modelName."
    }
}

& $conda run -n $EnvironmentName mfa model list acoustic
& $conda run -n $EnvironmentName mfa model list dictionary
