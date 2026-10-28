param(
    [string]$OutputDirectory = '',
    [switch]$UseBuiltSystemLibrary
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'scratch\native-python-editor-quit' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$outputRoot = (Resolve-Path -LiteralPath $OutputDirectory).Path
$stage = Join-Path $outputRoot ('run-' + [guid]::NewGuid().ToString('N'))
if ($stage -match '[^\x20-\x7e]') { throw "The stage path must be plain ASCII for the native test: $stage" }
New-Item -ItemType Directory -Path $stage | Out-Null

$compiler = 'C:/msys64/clang64/bin/clang.exe'
$includes = @('kar','melder','sys','dwsys','stat','fon','dwtools','LPC','external/portaudio','external/flac','external/mp3','external/espeak') | ForEach-Object { '-I'+(Join-Path $projectRoot $_) }
$flags = @('-c','-std=gnu++17','-municode','-D_FILE_OFFSET_BITS=64','-march=x86-64-v3','-O2')
$archive = Join-Path $projectRoot 'sys/libsys.a'
if (-not $UseBuiltSystemLibrary) {
    if (-not (Test-Path -LiteralPath $archive)) {
        throw "Build the worktree first (make PRAAT_OS=windows PRAAT_COMPILER=clang PRAAT_ARCH=x64v3 -j4) or pass a rebuilt sys/libsys.a."
    }
}

$libraries = @('fon/libfon.a','artsynth/libartsynth.a','FFNet/libFFNet.a','gram/libgram.a','EEG/libEEG.a','LPC/libLPC.a','dwtools/libdwtools.a','sensors/libsensors.a','foned/libfoned.a','fon/libfon.a','stat/libstat.a','dwsys/libdwsys.a') | ForEach-Object { Join-Path $projectRoot $_ }
$libraries += $archive
$libraries += @('melder/libmelder.a','kar/libkar.a','external/espeak/libespeak.a','external/portaudio/libportaudio.a','external/flac/libflac.a','external/lame/liblame.a','external/mp3/libmp3.a','external/glpk/libglpk.a','external/clapack/libclapack.a','external/gsl/libgsl.a','external/num/libnum.a','external/vorbis/libvorbis.a','external/opusfile/libopusfile.a','external/whispercpp/libwhisper.a','external/blake3/libblake3.a','external/zlib/libzlib.a') | ForEach-Object { Join-Path $projectRoot $_ }
foreach ($library in $libraries) {
    if (-not (Test-Path -LiteralPath $library)) { throw "Missing library built from the current sources: $library" }
}
$systemLibraries = @('-lmfuuid','-lwinmm','-lwsock32','-lcomctl32','-lole32','-lgdi32','-lgdiplus','-lcomdlg32','-lwinspool','-lshell32','-luxtheme','-ldwmapi','-ladvapi32','-luuid','-static','-lc++','-lc++abi')

$testObject = Join-Path $stage 'native_python_script_editor.o'
& $compiler --driver-mode=g++ @flags @includes (Join-Path $projectRoot 'ai/tests/native_python_script_editor.cpp') -o $testObject
if ($LASTEXITCODE -ne 0) { throw 'Native Python editor test compilation failed' }
$test = Join-Path $stage 'native_python_script_editor.exe'
& $compiler --driver-mode=g++ -o $test $testObject (Join-Path $projectRoot 'main/praat_win.o') @libraries @systemLibraries
if ($LASTEXITCODE -ne 0) { throw 'Native Python editor test link failed' }

function Invoke-TestProcess {
    param([string]$Mode, [int]$TimeoutMilliseconds)
    $env:PRAAT_PYEDITOR_TEST_STAGE = $stage
    $env:PRAAT_PYEDITOR_TEST_MODE = $Mode
    # Start-Process -PassThru gives no ExitCode once its streams are redirected to files, so the
    # process is started directly; the streams are read asynchronously to avoid a full pipe.
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $test
    $startInfo.Arguments = '--no-pref-files --no-plugins --utf8'   # Windows PowerShell has no ArgumentList
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($startInfo)
    $standardOutput = $process.StandardOutput.ReadToEndAsync()
    $standardError = $process.StandardError.ReadToEndAsync()
    if (-not $process.WaitForExit($TimeoutMilliseconds)) {
        $process.Kill()
        throw "$Mode : the test did not finish within $TimeoutMilliseconds ms; a modal question was left unanswered"
    }
    $process.WaitForExit()   # flushes the redirected streams
    $text = $standardOutput.Result + "`n" + $standardError.Result
    $name = if ($Mode) { $Mode } else { 'main' }
    [IO.File]::WriteAllText((Join-Path $stage "$name-output.log"), $text)
    # Only the trailing object may reach the caller: a stray value here would make $main a
    # collection and turn every later test into a comparison against $null.
    Write-Output ([pscustomobject]@{ name = $name; exitCode = $process.ExitCode; output = $text })
}

$main = @(Invoke-TestProcess -Mode '' -TimeoutMilliseconds 180000)[-1]
$quit = @(Invoke-TestProcess -Mode 'quit' -TimeoutMilliseconds 60000)[-1]
if ($main -isnot [pscustomobject] -or $null -eq $main.exitCode) { throw 'the main test run returned no exit code' }
if ($quit -isnot [pscustomobject] -or $null -eq $quit.exitCode) { throw 'the save-and-quit run returned no exit code' }

# One line per entry, so an assertion can ask for a line rather than a substring of the whole run.
$mainLines = @($main.output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
$quitLines = @($quit.output -split "`r?`n" | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
$nativeQuestions = @($mainLines | Where-Object { $_ -like 'helper: the native question on the screen is titled:*' })
# Case sensitive: the PASS line for the save case contains the words "failed save".
$mainFailures = @($mainLines | Where-Object { $_ -cmatch 'FAILED' })
$quitFailures = @($quitLines | Where-Object { $_ -cmatch 'FAILED' })

$savedByQuit = Join-Path $stage 'saved-by-quit.py'
$savedContent = ''
if (Test-Path -LiteralPath $savedByQuit) { $savedContent = [string] (Get-Content -LiteralPath $savedByQuit -Raw) }
$quitTemplate = Join-Path $stage 'quit-template.py'
$expectedQuitTemplate = Join-Path $stage 'quit-template-expected.py'
$quitTemplateMatches = (Test-Path -LiteralPath $quitTemplate) -and
    (Test-Path -LiteralPath $expectedQuitTemplate) -and
    ((Get-FileHash -LiteralPath $quitTemplate).Hash -eq (Get-FileHash -LiteralPath $expectedQuitTemplate).Hash)

$result = [pscustomobject]@{
    stage = $stage
    mainExitCode = $main.exitCode
    mainLineCount = $mainLines.Count
    quitExitCode = $quit.exitCode
    quitClickedSaveProject = [bool] (@($quitLines | Where-Object { $_ -like 'child: clicking Save Project*' }).Count -gt 0)
    savedByQuitFileContent = $savedContent
    generatedTemplateSavedByQuit = $quitTemplateMatches
    projectQuestionProbe = [bool] (@($mainLines | Where-Object { $_ -like '*while the question for a file name was open*' }).Count -gt 0)
    nativeQuestions = $nativeQuestions
    mainFailedLines = $mainFailures
    quitFailedLines = $quitFailures
}
$result | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath (Join-Path $stage 'result.json') -Encoding utf8
$result | ConvertTo-Json -Depth 4 | Write-Output

$failures = @()
if ($main.exitCode -ne 0) { $failures += "the in-process checks did not pass (exit code $($main.exitCode))" }
foreach ($required in @(
    'PASS the registry and the dirty query follow the open editors',
    'PASS the template and the examples keep existing text and empty editors are filled in place',
    'PASS saving writes the text, and a cancelled or failed save stays unsaved',
    'PASS Cancel in the quit dialog leaves the program running with its work unsaved',
    'PASS the project file is asked first and cancelling it saves nothing')) {
    if (@($mainLines | Where-Object { $_ -eq $required }).Count -eq 0) { $failures += "missing line: $required" }
}
# The user must see a message when a file cannot be written, and the project file must be asked before the scripts.
foreach ($requiredQuestion in @('Message', 'Save', 'Save workspace as .praat file')) {
    if (@($nativeQuestions | Where-Object { $_ -eq "helper: the native question on the screen is titled: $requiredQuestion" }).Count -eq 0) {
        $failures += "the native question titled '$requiredQuestion' never came on the screen"
    }
}
if (@($mainLines | Where-Object { $_ -like '*the script file held: ORIGINAL' }).Count -eq 0) {
    $failures += 'the script file was not still ORIGINAL while the question for the project file was open'
}
if ($quit.exitCode -ne 0) { $failures += "the save-and-quit process did not exit by itself (exit code $($quit.exitCode))" }
if (-not $result.quitClickedSaveProject) { $failures += 'the save-and-quit process never reached the button' }
if ($savedContent -notmatch 'saved by the quit dialog') { $failures += "the script that the quit dialog should have saved is not on disk: '$savedContent'" }
if (-not $quitTemplateMatches) { $failures += 'the new template window was not saved with its full content by the quit dialog' }
if ($quitFailures.Count -gt 0) { $failures += "the save-and-quit process reported a failure:`n$($quitFailures -join "`n")" }
if ($mainFailures.Count -gt 0) { $failures += "the in-process checks reported a failure:`n$($mainFailures -join "`n")" }
if ($failures.Count -gt 0) { throw ($failures -join "`n") }
Write-Output 'PASS the Python script editor is registered and queried, keeps existing text, saves to disk, and the quit dialog saves the scripts before it exits.'
