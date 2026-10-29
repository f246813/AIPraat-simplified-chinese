param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'scratch\native-output-isolation' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path
$stage = Join-Path $outputRoot ('run-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null

$cases = @()
foreach ($name in 'python-editor-quit', 'whisper-model-dims') {
    $testOutput = Join-Path $stage $name
    $existingOutput = Join-Path $testOutput 'existing-results'
    New-Item -ItemType Directory -Path $existingOutput -Force | Out-Null
    $sentinel = Join-Path $existingOutput 'user-data.txt'
    [IO.File]::WriteAllText($sentinel, 'Existing output must survive both runs.')
    $sentinelHash = (Get-FileHash -LiteralPath $sentinel).Hash
    $tool = Join-Path $PSScriptRoot "verify-native-$name.ps1"
    $previousRunPaths = @()
    for ($iteration = 1; $iteration -le 2; $iteration++) {
        $runLog = Join-Path $stage "$name-$iteration.log"
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $tool -OutputDirectory $testOutput *> $runLog
        if ($LASTEXITCODE -ne 0) { throw "$name run $iteration failed; see $runLog" }
        if (-not (Test-Path -LiteralPath $sentinel) -or (Get-FileHash -LiteralPath $sentinel).Hash -ne $sentinelHash) {
            throw "$name run $iteration removed or modified existing output"
        }
        foreach ($previous in $previousRunPaths) {
            if (-not (Test-Path -LiteralPath (Join-Path $previous 'result.json'))) {
                throw "$name run $iteration removed the result of an earlier run"
            }
        }
        $runPaths = @(Get-ChildItem -LiteralPath $testOutput -Directory -Filter 'run-*' |
            Select-Object -ExpandProperty FullName)
        if ($runPaths.Count -ne $iteration) { throw "$name did not create one private directory per run" }
        foreach ($runPath in $runPaths) {
            if (-not (Test-Path -LiteralPath (Join-Path $runPath 'result.json'))) {
                throw "$name left a run without its verification result: $runPath"
            }
        }
        $previousRunPaths = $runPaths
    }
    $cases += [pscustomobject]@{ test = $name; runs = $previousRunPaths; existingOutputPreserved = $true }
}
$result = [pscustomobject]@{ stage = $stage; cases = $cases }
$result | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (Join-Path $stage 'result.json') -Encoding utf8
$result | ConvertTo-Json -Depth 5 | Write-Output
Write-Output 'PASS: both verification tools preserve existing output and earlier runs.'
