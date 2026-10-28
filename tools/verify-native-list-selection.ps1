param([string]$OutputDirectory = '', [switch]$UseBuiltSystemLibrary)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) { $OutputDirectory = Join-Path $projectRoot 'scratch/native-list-selection' }
New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$stage = (Resolve-Path -LiteralPath $OutputDirectory).Path
$compiler = 'C:/msys64/clang64/bin/clang.exe'
$archiver = 'C:/msys64/clang64/bin/llvm-ar.exe'
$includes = @('kar','melder','sys','dwsys','stat','fon','dwtools','LPC','external/portaudio','external/flac','external/mp3','external/espeak') | ForEach-Object { '-I'+(Join-Path $projectRoot $_) }
$archive = Join-Path $projectRoot 'sys/libsys.a'
if (-not $UseBuiltSystemLibrary) {
    $objects = @()
    foreach ($source in @('sys/GuiList.cpp', 'sys/praat.cpp')) {
        $object = Join-Path $stage (([IO.Path]::GetFileNameWithoutExtension($source)) + '.o')
        & $compiler --driver-mode=g++ -c -std=gnu++17 -municode -D_FILE_OFFSET_BITS=64 -march=x86-64-v3 -O3 -Wshadow @includes (Join-Path $projectRoot $source) -o $object
        if ($LASTEXITCODE -ne 0) { throw "Compilation failed: $source" }
        $objects += $object
    }
    $archive = Join-Path $stage 'libsys.a'
    Copy-Item -LiteralPath (Join-Path $projectRoot 'sys/libsys.a') -Destination $archive
    & $archiver r $archive @objects
    if ($LASTEXITCODE -ne 0) { throw 'System archive update failed' }
}
$libraries = @('fon/libfon.a','artsynth/libartsynth.a','FFNet/libFFNet.a','gram/libgram.a','EEG/libEEG.a','LPC/libLPC.a','dwtools/libdwtools.a','sensors/libsensors.a','foned/libfoned.a','fon/libfon.a','stat/libstat.a','dwsys/libdwsys.a') | ForEach-Object { Join-Path $projectRoot $_ }
$libraries += $archive
$libraries += @('melder/libmelder.a','kar/libkar.a','external/espeak/libespeak.a','external/portaudio/libportaudio.a','external/flac/libflac.a','external/lame/liblame.a','external/mp3/libmp3.a','external/glpk/libglpk.a','external/clapack/libclapack.a','external/gsl/libgsl.a','external/num/libnum.a','external/vorbis/libvorbis.a','external/opusfile/libopusfile.a','external/whispercpp/libwhisper.a','external/blake3/libblake3.a','external/zlib/libzlib.a') | ForEach-Object { Join-Path $projectRoot $_ }
$systemLibraries = @('-lmfuuid','-lwinmm','-lwsock32','-lcomctl32','-lole32','-lgdi32','-lgdiplus','-lcomdlg32','-lwinspool','-lshell32','-luxtheme','-ldwmapi','-ladvapi32','-luuid','-static','-lc++','-lc++abi')
$native = Join-Path $stage 'Praat.exe'
& $compiler --driver-mode=g++ -o $native (Join-Path $projectRoot 'main/main_Praat.o') (Join-Path $projectRoot 'main/praat_win.o') @libraries @systemLibraries -mwindows
if ($LASTEXITCODE -ne 0) { throw 'Praat link failed' }
& (Join-Path $PSScriptRoot 'Publish-NativeExecutable.ps1') -Source $native -Destination $native -MediumDirectory (Split-Path -Parent $projectRoot)
$testObject = Join-Path $stage 'native_list_selection.o'
& $compiler --driver-mode=g++ -c -std=gnu++17 -municode -D_FILE_OFFSET_BITS=64 -march=x86-64-v3 -O2 @includes (Join-Path $projectRoot 'ai/tests/native_list_selection.cpp') -o $testObject
if ($LASTEXITCODE -ne 0) { throw 'Native list test compilation failed' }
$test = Join-Path $stage 'native_list_selection.exe'
& $compiler --driver-mode=g++ -o $test $testObject (Join-Path $projectRoot 'main/praat_win.o') @libraries @systemLibraries
if ($LASTEXITCODE -ne 0) { throw 'Native list test link failed' }
& (Join-Path $PSScriptRoot 'Publish-NativeExecutable.ps1') -Source $test -Destination $test -MediumDirectory (Split-Path -Parent $projectRoot)
& $test --no-pref-files --no-plugins --utf8
if ($LASTEXITCODE -ne 0) { throw 'Native list selection checks failed' }
Get-FileHash -LiteralPath $native -Algorithm SHA256 | Format-List Path,Hash
