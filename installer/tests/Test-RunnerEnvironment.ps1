$ErrorActionPreference='Stop'
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$testRoot=Join-Path ([IO.Path]::GetTempPath()) ('AIPraat-runner-env-'+[guid]::NewGuid().ToString('N'))
$project=Join-Path $testRoot 'project'
$old=Join-Path $project '.venv/Scripts/python.exe'
$ready=Join-Path $project 'venv-ai-modern/Scripts/python.exe'
$alias=Join-Path $testRoot 'Microsoft/WindowsApps/python.exe'
foreach($file in @($old,$ready,$alias)){New-Item -ItemType Directory -Path (Split-Path -Parent $file) -Force | Out-Null;[IO.File]::WriteAllText($file,'fixture')}
$previousOverride=$env:AIPRAAT_TEST_PYTHON
$script:probes=@()
function Invoke-TestExecutable([string]$Executable,[string]$Arguments) {
    $script:probes+=$Executable
    $valid=$Executable -eq $ready
    $report=@{supported=$valid;completed=$true;ready=$valid;version=$(if($valid){'3.12.14'}else{'3.14.7'});message='unsupported or missing runtime';items=@(@{label='NumPy';ok=$valid})}
    [pscustomobject]@{ExitCode=$(if($valid){0}else{1});Output=($report | ConvertTo-Json -Compress -Depth 4);Error=''}
}
function Get-Command([string]$Name,$ErrorAction){[pscustomobject]@{Source=$alias}}
function Assert([bool]$Condition,[string]$Message){if(-not $Condition){throw $Message}}
try {
    Remove-Item Env:AIPRAAT_TEST_PYTHON -ErrorAction SilentlyContinue
    $selected=Resolve-TestPython $project
    Assert ($selected -eq $ready) 'Default failed to skip an unready venv and choose the ready venv'
    Assert ($script:probes -notcontains $alias) 'WindowsApps alias was executed by default'
    Write-Output 'PASS default selects first ready project interpreter and skips WindowsApps'
    $script:probes=@();$errorText=''
    try{Resolve-TestPython $project $old | Out-Null}catch{$errorText=$_.Exception.Message}
    Assert ($errorText.Contains('-Python') -and $errorText.Contains('未就绪') -and $errorText.Contains('3.14.7')) 'Explicit invalid Python lost environment diagnostics'
    Assert ($script:probes.Count -eq 1 -and $script:probes[0] -eq $old) 'Explicit interpreter silently fell back'
    Write-Output 'PASS explicit unready interpreter fails preflight with version and -Python guidance'
    $env:AIPRAAT_TEST_PYTHON=$ready
    Assert ((Resolve-TestPython $project) -eq $ready) 'Shared environment override ignored'
    Assert ((Resolve-TestPython $project (Split-Path -Parent (Split-Path -Parent $ready))) -eq $ready) 'Explicit venv directory not resolved'
    Write-Output 'PASS shared override and explicit environment directory use the same resolver'
    Remove-Item Env:AIPRAAT_TEST_PYTHON -ErrorAction SilentlyContinue
    $script:probes=@();$errorText=''
    try{Resolve-TestPython (Join-Path $testRoot 'empty-project') | Out-Null}catch{$errorText=$_.Exception.Message}
    Assert ($errorText.Contains('未就绪') -and $script:probes.Count -eq 0) 'No-ready environment did not fail clearly or launched WindowsApps'
    $errorText='';try{Resolve-TestFFmpeg $project (Join-Path $testRoot 'missing-ffmpeg.exe') | Out-Null}catch{$errorText=$_.Exception.Message}
    Assert ($errorText.Contains('-FFmpeg') -and $errorText.Contains('missing-ffmpeg.exe')) 'Missing FFmpeg lost its explicit diagnostic'
    Write-Output 'PASS absent Python and FFmpeg produce actionable environment messages'
} finally {
    if($null -eq $previousOverride){Remove-Item Env:AIPRAAT_TEST_PYTHON -ErrorAction SilentlyContinue}else{$env:AIPRAAT_TEST_PYTHON=$previousOverride}
}
