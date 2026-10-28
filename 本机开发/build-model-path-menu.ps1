$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$compiler = 'C:/msys64/clang64/bin/clang++.exe'
$archiver = 'C:/msys64/clang64/bin/llvm-ar.exe'
$stage = Join-Path $PSScriptRoot 'model-path-menu-build'
New-Item -ItemType Directory -Path $stage -Force | Out-Null
$includes = @('kar','melder','sys','dwsys','stat','fon','dwtools','LPC','external/portaudio','external/flac','external/mp3','external/espeak') | ForEach-Object { '-I'+(Join-Path $projectRoot $_) }
$object = Join-Path $stage 'FunctionEditor.o'
& $compiler -c -std=gnu++17 -municode -D_FILE_OFFSET_BITS=64 -march=x86-64-v3 -O3 -Wshadow @includes (Join-Path $projectRoot 'foned/FunctionEditor.cpp') -o $object
if ($LASTEXITCODE -ne 0) { throw 'FunctionEditor compilation failed' }
$editorArchive = Join-Path $stage 'libfoned.a'
Copy-Item -LiteralPath (Join-Path $projectRoot 'foned/libfoned.a') -Destination $editorArchive
& $archiver r $editorArchive $object
if ($LASTEXITCODE -ne 0) { throw 'Editor archive update failed' }
$libraries = @('fon/libfon.a','artsynth/libartsynth.a','FFNet/libFFNet.a','gram/libgram.a','EEG/libEEG.a','LPC/libLPC.a','dwtools/libdwtools.a','sensors/libsensors.a') | ForEach-Object { Join-Path $projectRoot $_ }
$libraries += $editorArchive
$libraries += @('fon/libfon.a','stat/libstat.a','dwsys/libdwsys.a','sys/libsys.a','melder/libmelder.a','kar/libkar.a','external/espeak/libespeak.a','external/portaudio/libportaudio.a','external/flac/libflac.a','external/lame/liblame.a','external/mp3/libmp3.a','external/glpk/libglpk.a','external/clapack/libclapack.a','external/gsl/libgsl.a','external/num/libnum.a','external/vorbis/libvorbis.a','external/opusfile/libopusfile.a','external/whispercpp/libwhisper.a','external/blake3/libblake3.a','external/zlib/libzlib.a') | ForEach-Object { Join-Path $projectRoot $_ }
$native = Join-Path $stage 'Praat.exe'
& $compiler -o $native (Join-Path $projectRoot 'main/main_Praat.o') (Join-Path $projectRoot 'main/praat_win.o') @libraries -lmfuuid -lwinmm -lwsock32 -lcomctl32 -lole32 -lgdi32 -lgdiplus -lcomdlg32 -lwinspool -lshell32 -luxtheme -ldwmapi -ladvapi32 -luuid -static -lc++ -lc++abi -mwindows
if ($LASTEXITCODE -ne 0) { throw 'Praat link failed' }
# A warning-and-continue here previously delivered Low executables. The final
# file's MIC is part of desktop correctness; see windows-integrity-design.md.
& (Join-Path $projectRoot 'tools/Publish-NativeExecutable.ps1') -Source $native -Destination $native -MediumDirectory (Split-Path -Parent $projectRoot)
Get-FileHash -LiteralPath $native -Algorithm SHA256 | Format-List Path,Hash
