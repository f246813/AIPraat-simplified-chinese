param(
    [string]$Executable = '',
    [string]$ControlExecutable = '',
    [string]$OutputDirectory = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Executable) { $Executable = Join-Path $projectRoot 'Praat.exe' }
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'scratch\native-prefs-coexistence' }
if (-not (Test-Path -LiteralPath $Executable)) { throw "Executable not found: $Executable" }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$stage = (Resolve-Path -LiteralPath $OutputDirectory).Path

function New-IsolatedProfile([string]$label) {
    # One isolated preferences folder per run: nothing here touches the real user profile.
    $profile_ = Join-Path $stage "profile-$label"
    if (Test-Path -LiteralPath $profile_) { Remove-Item -LiteralPath $profile_ -Recurse -Force }
    # Windows: version-5 files live in <home>\<AppName>, version-7 files in <home>\AppData\Roaming\<AppName>.
    New-Item -ItemType Directory -Path (Join-Path $profile_ 'Praat') -Force | Out-Null
    New-Item -ItemType Directory -Path (Join-Path $profile_ 'AppData\Roaming\Praat') -Force | Out-Null
    $prefs5 = Join-Path $profile_ 'Praat\Preferences5.ini'
    $buttons5 = Join-Path $profile_ 'Praat\Buttons5.ini'
    Set-Content -LiteralPath $prefs5 -Value "OLD PREFS5 CONTENT marker=$label" -NoNewline -Encoding ascii
    Set-Content -LiteralPath $buttons5 -Value "OLD BUTTONS5 CONTENT marker=$label" -NoNewline -Encoding ascii
    return @{ path = $profile_; prefs5 = $prefs5; buttons5 = $buttons5;
              prefs7 = Join-Path $profile_ 'AppData\Roaming\Praat\Preferences.txt';
              buttons7 = Join-Path $profile_ 'AppData\Roaming\Praat\Buttons.txt';
              beforePrefs5 = (Get-FileHash -LiteralPath $prefs5).Hash;
              beforeButtons5 = (Get-FileHash -LiteralPath $buttons5).Hash }
}

function Get-ProfileResult($profile_, [int]$exitCode) {
    # The obsolete file is only removed when the modern one could be written; prove that precondition.
    return [pscustomobject]@{
        exitCode            = $exitCode
        newSettingsWritten  = Test-Path -LiteralPath $profile_.prefs7
        newButtonsWritten   = Test-Path -LiteralPath $profile_.buttons7
        preferences5Kept    = (Test-Path -LiteralPath $profile_.prefs5) -and ((Get-FileHash -LiteralPath $profile_.prefs5).Hash -eq $profile_.beforePrefs5)
        buttons5Kept        = (Test-Path -LiteralPath $profile_.buttons5) -and ((Get-FileHash -LiteralPath $profile_.buttons5).Hash -eq $profile_.beforeButtons5)
    }
}

function Invoke-BatchExit([string]$exe, [string]$label) {
    $profile_ = New-IsolatedProfile $label
    $emptyScript = Join-Path $stage "empty-$label.praat"
    Set-Content -LiteralPath $emptyScript -Value '' -NoNewline -Encoding ascii
    $previousUserProfile = $env:USERPROFILE
    $env:USERPROFILE = $profile_.path
    try {
        & $exe --run $emptyScript 2>&1 | Out-Null
        $exitCode = $LASTEXITCODE
    } finally { $env:USERPROFILE = $previousUserProfile }
    return Get-ProfileResult $profile_ $exitCode
}

function Invoke-WindowExit([string]$exe, [string]$label) {
    # The script buttons are only saved outside batch mode, so close the real main window
    # the way a user does: taskkill without /F asks the window to close and waits.
    $profile_ = New-IsolatedProfile $label
    $previousUserProfile = $env:USERPROFILE
    $env:USERPROFILE = $profile_.path
    try {
        $process = Start-Process -FilePath $exe -ArgumentList '--no-plugins' -PassThru
        for ([int]$waited = 0; $waited -lt 300 -and $process.MainWindowHandle -eq 0; $waited ++) { Start-Sleep -Milliseconds 200 }
        if ($process.MainWindowHandle -eq 0) { $process.Kill(); throw "$label : Praat never showed its main window" }
        taskkill /PID $process.Id | Out-Null
        if (-not $process.WaitForExit(30000)) {
            $process.Kill()
            throw "$label : the window close left Praat running; a question is probably still on the screen"
        }
        $exitCode = $process.ExitCode
    } finally { $env:USERPROFILE = $previousUserProfile }
    return Get-ProfileResult $profile_ $exitCode
}

$result = [ordered]@{
    executable = $Executable
    patched = (Invoke-BatchExit $Executable 'patched')
    # A window opens for a few seconds and closes again; this is the run that reaches the buttons file.
    window = (Invoke-WindowExit $Executable 'window')
}
if ($ControlExecutable) {
    if (-not (Test-Path -LiteralPath $ControlExecutable)) { throw "Control executable not found: $ControlExecutable" }
    $result.control = Invoke-BatchExit $ControlExecutable 'control'
}
$json = $result | ConvertTo-Json -Depth 4
Set-Content -LiteralPath (Join-Path $stage 'result.json') -Value $json -Encoding utf8
Write-Output $json

foreach ($case in @('patched', 'window')) {
    $run = $result.$case
    if ($run.exitCode -ne 0) { throw "$case : Praat did not exit cleanly (exit code $($run.exitCode))." }
    if (-not $run.newSettingsWritten) { throw "$case : the modern settings file was not written, so the obsolete-file condition was never reached." }
    if (-not $run.preferences5Kept) { throw "$case : Preferences5.ini was changed or deleted on exit." }
    if (-not $run.buttons5Kept) { throw "$case : Buttons5.ini was changed or deleted on exit." }
}
if (-not $result.window.newButtonsWritten) { throw 'The window run did not write the modern buttons file, so it did not reach the script-button branch it is here to exercise.' }
if ($result.control) {
    if ($result.control.newSettingsWritten -and $result.control.preferences5Kept) {
        Write-Warning 'The control executable kept Preferences5.ini as well, so this run does not show the defect being guarded against.'
    }
}
Write-Output 'PASS: Praat 7 exit writes its own settings and buttons and leaves the Praat 6 files untouched, in batch mode and after closing the window.'
