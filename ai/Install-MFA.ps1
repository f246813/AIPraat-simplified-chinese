[CmdletBinding()]
param(
    [string] $MiniforgeRoot = (Join-Path $(if($env:PRAAT_AI_INSTALL_ROOT){$env:PRAAT_AI_INSTALL_ROOT}else{Split-Path -Parent $PSScriptRoot}) 'tools/miniforge3'),
    [string] $EnvironmentName = 'aligner'
)

$ErrorActionPreference = 'Stop'
$projectRoot = if($env:PRAAT_AI_INSTALL_ROOT){$env:PRAAT_AI_INSTALL_ROOT}else{Split-Path -Parent $PSScriptRoot}
if(-not $env:MFA_ROOT_DIR){$env:MFA_ROOT_DIR=Join-Path $projectRoot 'models/mfa'}
if(-not $env:CONDA_PKGS_DIRS){$env:CONDA_PKGS_DIRS=Join-Path $projectRoot 'cache/conda/pkgs'}
if(-not $env:CONDA_ENVS_PATH){$env:CONDA_ENVS_PATH=Join-Path $projectRoot 'tools/miniforge3/envs'}
if(-not $env:PIP_CACHE_DIR){$env:PIP_CACHE_DIR=Join-Path $projectRoot 'cache/pip'}
$conda = Join-Path $MiniforgeRoot 'condabin\conda.bat'
if (-not (Test-Path -LiteralPath $conda -PathType Leaf)) {
    throw "Miniforge conda not found: $conda"
}

& $conda create -n $EnvironmentName -c conda-forge montreal-forced-aligner -y
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to create the MFA conda environment.'
}

& $conda run -n $EnvironmentName python -m pip install `
    spacy-pkuseg dragonmapper hanziconv
if ($LASTEXITCODE -ne 0) {
    throw 'Failed to install Chinese tokenization support for MFA.'
}

& $conda run -n $EnvironmentName mfa version
