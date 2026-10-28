param([switch] $BuildOnly)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$stage = (New-Item -ItemType Directory -Force -Path (Join-Path $projectRoot 'scratch/appearance-panel')).FullName
$compiler = 'C:/msys64/clang64/bin/clang.exe'
$includes = @('kar','melder','sys','dwsys','stat','fon','foned','dwtools','LPC','external/portaudio','external/flac','external/mp3','external/espeak') |
    ForEach-Object { '-I' + (Join-Path $projectRoot $_) }
$libraries = @('fon/libfon.a','artsynth/libartsynth.a','FFNet/libFFNet.a','gram/libgram.a','EEG/libEEG.a','LPC/libLPC.a',
    'dwtools/libdwtools.a','sensors/libsensors.a','foned/libfoned.a','fon/libfon.a','stat/libstat.a','dwsys/libdwsys.a','sys/libsys.a',
    'melder/libmelder.a','kar/libkar.a','external/espeak/libespeak.a','external/portaudio/libportaudio.a','external/flac/libflac.a',
    'external/lame/liblame.a','external/mp3/libmp3.a','external/glpk/libglpk.a','external/clapack/libclapack.a','external/gsl/libgsl.a',
    'external/num/libnum.a','external/vorbis/libvorbis.a','external/opusfile/libopusfile.a','external/whispercpp/libwhisper.a',
    'external/blake3/libblake3.a','external/zlib/libzlib.a') | ForEach-Object { Join-Path $projectRoot $_ }
$systemLibraries = @('-lmfuuid','-lwinmm','-lwsock32','-lcomctl32','-lole32','-lgdi32','-lgdiplus','-lcomdlg32','-lwinspool',
    '-lshell32','-luxtheme','-ldwmapi','-ladvapi32','-luuid','-static','-lc++','-lc++abi')
$object = Join-Path $stage 'native_appearance_panel.o'
& $compiler --driver-mode=g++ -c -std=gnu++17 -municode -D_FILE_OFFSET_BITS=64 -march=x86-64-v3 -O2 -Wshadow @includes `
    (Join-Path $projectRoot 'ai/tests/native_appearance_panel.cpp') -o $object
if ($LASTEXITCODE -ne 0) { throw 'Native panel layout test compilation failed' }
$testExecutable = Join-Path $stage 'native_appearance_panel.exe'
& $compiler --driver-mode=g++ -o $testExecutable $object (Join-Path $projectRoot 'main/praat_win.o') @libraries @systemLibraries
if ($LASTEXITCODE -ne 0) { throw 'Native panel layout test link failed' }
& (Join-Path $PSScriptRoot 'Publish-NativeExecutable.ps1') -Source $testExecutable -Destination $testExecutable `
    -MediumDirectory (Split-Path -Parent $projectRoot) | Out-Null
if (-not $BuildOnly) {
    Push-Location -LiteralPath $projectRoot
    try {
        New-Item -ItemType Directory -Force -Path 'test-records/appearance-panel-native' | Out-Null
        & $testExecutable
        if ($LASTEXITCODE -ne 0) { throw 'Native panel layout checks failed; see test-records/appearance-panel-native/layout.json' }
    } finally { Pop-Location }
}
