param(
    [string]$Executable = '',
    [string]$OutputDirectory = '',
    [string]$ControlExecutable = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Executable) { $Executable = Join-Path $projectRoot 'Praat.exe' }
if (-not (Test-Path -LiteralPath $Executable)) { throw "Executable not found: $Executable" }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'scratch\native-whisper-model-dims' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path
$stage = Join-Path $outputRoot ('run-' + [guid]::NewGuid().ToString('N'))
$modelFolder = Join-Path $stage 'prefs\models\whispercpp'
New-Item -ItemType Directory -Path $modelFolder -Force | Out-Null

# The flat "ggml" model format that this whisper.cpp reads: magic, ten hyper parameters, ftype,
# the mel filter bank, the vocabulary list, and then one record per tensor.  Each record starts
# with the number of dimensions, which is then used to read that many extent numbers into the
# fixed array ne [4].  The fixtures below are minimal files that differ only in that number.
function New-ModelFixture {
    param([string]$FileName, [int32]$NumberOfDimensions, [int32]$NumberOfExtentNumbers)
    $path = Join-Path $modelFolder $FileName
    $ms = New-Object IO.MemoryStream
    $w = New-Object IO.BinaryWriter($ms)
    $w.Write([int32]0x67676d6c)                                # magic "ggml"
    foreach ($v in 2, 1, 1, 1, 4, 1, 1, 1, 0, 1) { $w.Write([int32]$v) }   # hyper parameters
    $w.Write([int32]1)                                          # ftype: f32
    $w.Write([int32]1); $w.Write([int32]1); $w.Write([single]0.0)   # mel filter bank: 1 by 1
    $w.Write([int32]0)                                          # vocabulary: no words in the file
    $w.Write($NumberOfDimensions)                               # tensor: number of dimensions
    $w.Write([int32]8)                                          # tensor: length of the name
    $w.Write([int32]1)                                          # tensor: element type f32
    for ($i = 0; $i -lt $NumberOfExtentNumbers; $i++) { $w.Write([int32]1) }
    $w.Write([Text.Encoding]::ASCII.GetBytes('abcdefgh'))       # tensor: name
    $w.Flush()
    [IO.File]::WriteAllBytes($path, $ms.ToArray())
    $ms.Dispose()
}

function Invoke-ModelLoad {
    param([string]$ModelFileName, [string]$RunExecutable = $Executable, [string]$Label = '')
    $prefix = if ($Label) { "$Label-" } else { '' }
    $scriptPath = Join-Path $stage "load-$prefix$ModelFileName.praat"
    Set-Content -LiteralPath $scriptPath -Value (
        "Create SpeechRecognizer: `"$ModelFileName`", `"English`"`nprint(`"REACHED_LINE_AFTER_FAILURE`")"
    ) -NoNewline -Encoding ascii
    # The whisper loader writes to the standard error stream, so both streams are captured to files;
    # Start-Process keeps them out of PowerShell's error handling, which would otherwise turn every
    # logged line into a terminating error.
    $outPath = Join-Path $stage "stdout-$prefix$ModelFileName.log"
    $errPath = Join-Path $stage "stderr-$prefix$ModelFileName.log"
    $process = Start-Process -FilePath $RunExecutable -NoNewWindow -Wait -PassThru `
        -ArgumentList "`"--pref-dir=$stage\prefs`"", '"--debug=2001"', '--run', "`"$scriptPath`"" `
        -RedirectStandardOutput $outPath -RedirectStandardError $errPath
    $text = ((Get-Content -LiteralPath $outPath -Raw) + "`n" + (Get-Content -LiteralPath $errPath -Raw)) -replace "`0", ''
    return [pscustomobject]@{
        exitCode         = $process.ExitCode
        isolatedFolder   = $text -match [regex]::Escape($modelFolder)
        boundsMessage    = $text -match 'invalid number of dimensions'
        praatReported    = ($text -match 'Cannot create Whisper context') -and ($text -match 'SpeechRecognizer not created')
        loaderReached    = $text -match 'whisper_model_load'
        tensorNameRead   = $text -match "unknown tensor 'abcdefgh'"
        lineAfterFailure = $text -match 'REACHED_LINE_AFTER_FAILURE'
    }
}

# 5 is above the last index of ne [4], -1 is below 0; both must be refused before any extent is read.
# 0 and 4 are the bounds that remain legal, and 2 is a legal intermediate value.
New-ModelFixture -FileName 'dims-5.bin'       -NumberOfDimensions 5  -NumberOfExtentNumbers 0
New-ModelFixture -FileName 'dims-minus-1.bin' -NumberOfDimensions -1 -NumberOfExtentNumbers 0
New-ModelFixture -FileName 'dims-0.bin'       -NumberOfDimensions 0  -NumberOfExtentNumbers 0
New-ModelFixture -FileName 'dims-2.bin'       -NumberOfDimensions 2  -NumberOfExtentNumbers 2
New-ModelFixture -FileName 'dims-4.bin'       -NumberOfDimensions 4  -NumberOfExtentNumbers 4

$cases = [ordered]@{}
foreach ($name in 'dims-5.bin', 'dims-minus-1.bin', 'dims-0.bin', 'dims-2.bin', 'dims-4.bin') {
    $cases[$name] = Invoke-ModelLoad -ModelFileName $name
}
$json = $cases | ConvertTo-Json -Depth 4
Set-Content -LiteralPath (Join-Path $stage 'result.json') -Value $json -Encoding utf8
Write-Output $json

$failures = @()
foreach ($name in $cases.Keys) {
    if (-not $cases[$name].isolatedFolder) { $failures += "${name}: Praat did not read the fixtures folder, so it might have loaded a real model" }
}
foreach ($name in 'dims-5.bin', 'dims-minus-1.bin') {
    $c = $cases[$name]
    if (-not $c.loaderReached) { $failures += "${name}: the loader never started, so nothing was tested" }
    if (-not $c.boundsMessage) { $failures += "${name}: the number of dimensions was not refused" }
    if (-not $c.praatReported) { $failures += "${name}: Praat did not report the failure of Create SpeechRecognizer" }
    if ($c.tensorNameRead) { $failures += "${name}: the record was read on past the refused dimension count" }
}
foreach ($name in 'dims-0.bin', 'dims-2.bin', 'dims-4.bin') {
    $c = $cases[$name]
    if ($c.boundsMessage) { $failures += "${name}: a legal number of dimensions was refused" }
    if (-not $c.tensorNameRead) { $failures += "${name}: the loader did not reach the tensor name, so the legal case is unverified" }
    if (-not $c.praatReported) { $failures += "${name}: Praat did not report the failure of Create SpeechRecognizer" }
}
foreach ($name in $cases.Keys) {
    $exitCode = $cases[$name].exitCode
    # Every fixture makes Create SpeechRecognizer fail, so the script line is not completed and Praat
    # leaves with its error code -1.  An out-of-bounds write in the loader would instead surface as a
    # system exception code, such as 0xC0000005 (-1073741819).
    if ($exitCode -ne -1) { $failures += "${name}: Praat exited with $exitCode, not the error exit of an uncompleted script line" }
    if ($cases[$name].lineAfterFailure) { $failures += "${name}: the script continued after the failed Create SpeechRecognizer" }
}
if ($ControlExecutable) {
    # The same fixtures against a binary without the bounds check, so that the case is shown to be
    # decided by the check rather than by the fixture or by the folder.
    $control = [ordered]@{}
    foreach ($name in 'dims-5.bin', 'dims-minus-1.bin') {
        $control[$name] = Invoke-ModelLoad -ModelFileName $name -RunExecutable $ControlExecutable -Label 'control'
    }
    $controlJson = [pscustomobject]@{ controlExecutable = $ControlExecutable; cases = $control } | ConvertTo-Json -Depth 5
    Set-Content -LiteralPath (Join-Path $stage 'control.json') -Value $controlJson -Encoding utf8
    Write-Output $controlJson
    foreach ($name in $control.Keys) {
        if ($control[$name].boundsMessage) { $failures += "${name}: the control binary refuses the dimension count as well, so the fixtures do not isolate the check" }
        if (-not $control[$name].isolatedFolder) { $failures += "${name}: the control binary did not read the fixtures folder" }
    }
}
if ($failures.Count -gt 0) { throw ($failures -join "`n") }
Write-Output "Results directory: $stage"
Write-Output 'PASS: Create SpeechRecognizer refuses tensor dimension counts outside 0..4 through the normal error path; counts inside the range still reach the tensor name.'
