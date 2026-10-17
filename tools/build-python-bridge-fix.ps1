param([string]$OutputDirectory = '')
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'scratch/python-bridge-fix' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$stage = (Resolve-Path -LiteralPath $OutputDirectory).Path
$compiler = 'C:/msys64/clang64/bin/clang.exe'
$archiver = 'C:/msys64/clang64/bin/llvm-ar.exe'
$includes = @('kar','melder','sys','dwsys','stat','fon','dwtools','LPC','external/portaudio','external/flac','external/mp3','external/espeak') | ForEach-Object { '-I'+(Join-Path $projectRoot $_) }
$object = Join-Path $stage 'praat_python.o'
& $compiler --driver-mode=g++ -c -std=gnu++17 -municode -D_FILE_OFFSET_BITS=64 -march=x86-64-v3 -O3 -Wshadow @includes (Join-Path $projectRoot 'sys/praat_python.cpp') -o $object
if ($LASTEXITCODE -ne 0) { throw 'Python bridge compilation failed' }
$archive = Join-Path $stage 'libsys.a'
Copy-Item -LiteralPath (Join-Path $projectRoot 'sys/libsys.a') -Destination $archive
& $archiver r $archive $object
if ($LASTEXITCODE -ne 0) { throw 'System archive update failed' }
$libraries = @('fon/libfon.a','artsynth/libartsynth.a','FFNet/libFFNet.a','gram/libgram.a','EEG/libEEG.a','LPC/libLPC.a','dwtools/libdwtools.a','sensors/libsensors.a','foned/libfoned.a','fon/libfon.a','stat/libstat.a','dwsys/libdwsys.a') | ForEach-Object { Join-Path $projectRoot $_ }
$libraries += $archive
$libraries += @('melder/libmelder.a','kar/libkar.a','external/espeak/libespeak.a','external/portaudio/libportaudio.a','external/flac/libflac.a','external/lame/liblame.a','external/mp3/libmp3.a','external/glpk/libglpk.a','external/clapack/libclapack.a','external/gsl/libgsl.a','external/num/libnum.a','external/vorbis/libvorbis.a','external/opusfile/libopusfile.a','external/whispercpp/libwhisper.a','external/blake3/libblake3.a','external/zlib/libzlib.a') | ForEach-Object { Join-Path $projectRoot $_ }
$native = Join-Path $stage 'Praat.exe'
& $compiler --driver-mode=g++ -o $native (Join-Path $projectRoot 'main/main_Praat.o') (Join-Path $projectRoot 'main/praat_win.o') @libraries -lmfuuid -lwinmm -lwsock32 -lcomctl32 -lole32 -lgdi32 -lgdiplus -lcomdlg32 -lwinspool -lshell32 -luxtheme -ldwmapi -ladvapi32 -luuid -static -lc++ -lc++abi -mwindows
if ($LASTEXITCODE -ne 0) { throw 'Praat link failed' }
# A binary recreated in this Low-labelled workspace launches as Low and cannot
# write Medium status.json. Publish the FINAL object at Medium; hashes alone do
# not cover this metadata. See docs/ai-frontend/windows-integrity-design.md.
& (Join-Path $PSScriptRoot 'Publish-NativeExecutable.ps1') -Source $native -Destination $native -MediumDirectory (Split-Path -Parent $projectRoot)
$test = Join-Path $stage 'PythonWaitTests.exe'
& $compiler --driver-mode=g++ -std=gnu++17 -O2 (Join-Path $projectRoot 'test/sys/python_wait.cpp') -o $test -luser32 -static
if ($LASTEXITCODE -ne 0) { throw 'Python wait test compilation failed' }
& $test
if ($LASTEXITCODE -ne 0) { throw 'Python wait tests failed' }
Get-FileHash -LiteralPath $native -Algorithm SHA256 | Format-List Path,Hash
