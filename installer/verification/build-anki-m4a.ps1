$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$compilerRoot = Join-Path $projectRoot '.build-tools/msys/clang64/bin'
$compiler = Join-Path $compilerRoot 'clang++.exe'
$archiver = Join-Path $compilerRoot 'llvm-ar.exe'
$stage = Join-Path $projectRoot '.build-tools/anki-m4a-20261007'
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$includes = @('kar','melder','sys','dwsys','stat','fon','foned','dwtools','LPC','external/portaudio','external/flac','external/mp3','external/espeak') | ForEach-Object { '-I'+(Join-Path $projectRoot $_) }
foreach ($unit in @('fon/Sound_files.cpp','fon/praat_Sound.cpp','sys/PraatAiControl.cpp','sys/praat_translate.cpp','foned/FunctionEditor.cpp')) {
    $object = Join-Path $stage ([IO.Path]::GetFileNameWithoutExtension($unit)+'.o')
    & $compiler -c -std=gnu++17 -municode -D_FILE_OFFSET_BITS=64 -march=x86-64-v3 -O3 -Wshadow @includes (Join-Path $projectRoot $unit) -o $object
    if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $unit" }
    Write-Output "Compiled $unit"
}
# Reuse the latest model-settings archives, including native menu and IPC fixes.
$archive = Join-Path $stage 'libsys.a'
Copy-Item -LiteralPath (Join-Path $projectRoot '.build-tools/dictionary-models-20261005/libsys.a') -Destination $archive
& $archiver r $archive (Join-Path $stage 'PraatAiControl.o') (Join-Path $stage 'praat_translate.o')
if ($LASTEXITCODE -ne 0) { throw 'System archive update failed' }
$editorArchive = Join-Path $stage 'libfoned.a'
Copy-Item -LiteralPath (Join-Path $projectRoot '.build-tools/dictionary-models-20261005/libfoned.a') -Destination $editorArchive
& $archiver r $editorArchive (Join-Path $stage 'FunctionEditor.o')
if ($LASTEXITCODE -ne 0) { throw 'Editor archive update failed' }
$soundArchive = Join-Path $stage 'libfon.a'
Copy-Item -LiteralPath (Join-Path $projectRoot 'fon/libfon.a') -Destination $soundArchive
& $archiver r $soundArchive (Join-Path $stage 'Sound_files.o') (Join-Path $stage 'praat_Sound.o')
if ($LASTEXITCODE -ne 0) { throw 'Sound archive update failed' }
$libraries = @('fon/libfon.a','artsynth/libartsynth.a','FFNet/libFFNet.a','gram/libgram.a','EEG/libEEG.a','LPC/libLPC.a','dwtools/libdwtools.a','sensors/libsensors.a') | ForEach-Object { Join-Path $projectRoot $_ }
$libraries += $editorArchive
$libraries += @('fon/libfon.a','stat/libstat.a','dwsys/libdwsys.a') | ForEach-Object { Join-Path $projectRoot $_ }
$libraries += $archive
$libraries += @('melder/libmelder.a','kar/libkar.a','external/espeak/libespeak.a','external/portaudio/libportaudio.a','external/flac/libflac.a','external/lame/liblame.a','external/mp3/libmp3.a','external/glpk/libglpk.a','external/clapack/libclapack.a','external/gsl/libgsl.a','external/num/libnum.a','external/vorbis/libvorbis.a','external/opusfile/libopusfile.a','external/whispercpp/libwhisper.a','external/blake3/libblake3.a','external/zlib/libzlib.a') | ForEach-Object { Join-Path $projectRoot $_ }
$libraries = $libraries | ForEach-Object { if ($_ -eq (Join-Path $projectRoot 'fon/libfon.a')) { $soundArchive } else { $_ } }
$native = Join-Path $stage 'Praat-link.exe'
if (Test-Path -LiteralPath $native) {
    Move-Item -LiteralPath $native -Destination (Join-Path $stage ('Praat-previous-' + [DateTime]::UtcNow.Ticks + '.exe'))
}
& $compiler -o $native (Join-Path $projectRoot 'main/main_Praat.o') (Join-Path $projectRoot 'main/praat_win.o') @libraries -lmfuuid -lwinmm -lwsock32 -lcomctl32 -lole32 -lgdi32 -lgdiplus -lcomdlg32 -lwinspool -lshell32 -luxtheme -ldwmapi -ladvapi32 -luuid -static -lc++ -lc++abi -mwindows
if ($LASTEXITCODE -ne 0) { throw 'Praat link failed' }
& (Join-Path $PSScriptRoot 'set-praat-integrity.ps1') -Executable @($native)
Get-FileHash -LiteralPath $native -Algorithm SHA256 | Format-List Path,Hash
