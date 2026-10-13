param(
    [string]$InstallDirectory = (Join-Path $env:LOCALAPPDATA 'AIPraat\ffmpeg'),
    [string]$ArchivePath = '',
    [string]$ExpectedSha256 = ''
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = [Text.UTF8Encoding]::new($false)
$OutputEncoding = [Console]::OutputEncoding
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$installRoot = [IO.Path]::GetFullPath($InstallDirectory)
$stage = Join-Path ([IO.Path]::GetTempPath()) ('aipraat-ffmpeg-download-' + [Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $stage | Out-Null
try {
    if (-not $ArchivePath) {
        $url = 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip'
        Write-Output '正在下载 FFmpeg Windows x64（FFmpeg 官网列出的 Gyan 构建）…'
        $ArchivePath = Join-Path $stage 'ffmpeg.zip'
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -UseBasicParsing -Uri ($url + '.sha256') -OutFile (Join-Path $stage 'sha256.txt')
        $ExpectedSha256 = [regex]::Match([IO.File]::ReadAllText((Join-Path $stage 'sha256.txt')), '(?i)\b[0-9a-f]{64}\b').Value
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $ArchivePath
    }
    if ($ExpectedSha256 -notmatch '^[0-9a-fA-F]{64}$') { throw '需要发布方提供的 SHA-256 校验值。' }
    if ((Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash -ine $ExpectedSha256) { throw 'FFmpeg 下载包 SHA-256 校验失败。' }
    Write-Output '校验通过，正在解压…'
    $unpacked = Join-Path $stage 'unpacked'
    Expand-Archive -LiteralPath $ArchivePath -DestinationPath $unpacked
    $candidates = @(Get-ChildItem -LiteralPath $unpacked -Recurse -File -Filter 'ffmpeg.exe')
    if ($candidates.Count -ne 1) { throw '安装包中没有唯一的 ffmpeg.exe。' }
    $executable = $candidates[0].FullName
    $version = & $executable -nostdin -version
    if ($LASTEXITCODE -ne 0 -or $version[0] -notlike 'ffmpeg version *') { throw 'FFmpeg 可执行性检测失败。' }
    $compressedProbe = Join-Path $stage 'probe.ogg'
    $probe = Join-Path $stage 'probe.wav'
    & $executable -nostdin -v error -f lavfi -i 'anullsrc=r=44100:cl=mono' -t 0.08 -c:a libvorbis -y $compressedProbe
    if ($LASTEXITCODE -ne 0) { throw 'FFmpeg 检测样本生成失败。' }
    & $executable -nostdin -v error -i $compressedProbe -map 0:a:0 -vn -c:a pcm_s24le -y $probe
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $probe)) { throw 'FFmpeg 实际 OGG 解码检测失败。' }
    New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
    $target = Join-Path $installRoot 'ffmpeg.exe'
    Copy-Item -LiteralPath $executable -Destination ($target + '.new') -Force
    Move-Item -LiteralPath ($target + '.new') -Destination $target -Force
    foreach ($name in @('LICENSE','LICENSE.txt','README.txt')) {
        $license = @(Get-ChildItem -LiteralPath $unpacked -Recurse -File -Filter $name)
        foreach ($file in $license) { Copy-Item -LiteralPath $file.FullName -Destination (Join-Path $installRoot $name) -Force }
    }
    Write-Output ('已安装：' + $target)
    Write-Output $version[0]
} finally {
    $resolvedStage = [IO.Path]::GetFullPath($stage)
    $tempRoot = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedStage.StartsWith($tempRoot,[StringComparison]::OrdinalIgnoreCase) -and [IO.Path]::GetFileName($resolvedStage).StartsWith('aipraat-ffmpeg-download-')) {
        Remove-Item -LiteralPath $resolvedStage -Recurse -Force
    }
}
