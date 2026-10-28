$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$source = Join-Path $PSScriptRoot 'model-path-menu-build/Praat.exe'
$target = Join-Path $projectRoot 'Praat.exe'
if (-not (Test-Path -LiteralPath $source)) { throw '缺少已编译的新程序，请先运行 build-model-path-menu.ps1。' }
if (Get-Process -Name Praat -ErrorAction SilentlyContinue | Where-Object { $_.Path -eq $target }) {
    throw '请先保存工作并关闭此目录的 Praat，再运行本脚本。'
}
$backup = Join-Path $projectRoot ('.aipraat-backups/model-path-menu-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
Copy-Item -LiteralPath $target -Destination (Join-Path $backup 'Praat.exe')
# Preserve a known Medium object's security descriptor on an in-place update.
# If a prior copy already made it Low, preserving that label preserves the bug;
# republish through the same-volume Medium stage instead. See windows-integrity-design.md.
. (Join-Path $projectRoot 'tools/NativeIntegrity.ps1')
$expected = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash
if ([AIPraat.NativeIntegrity]::FileLevel($target) -eq 0x2000) {
    [IO.File]::WriteAllBytes($target, [IO.File]::ReadAllBytes($source))
} else {
    & (Join-Path $projectRoot 'tools/Publish-NativeExecutable.ps1') -Source $source -Destination $target -MediumDirectory (Split-Path -Parent $projectRoot)
}
if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash -ne $expected) { throw '更新后的程序校验失败。' }
if ([AIPraat.NativeIntegrity]::FileLevel($target) -ne 0x2000) { throw '最终程序必须为中等完整性。' }
Write-Output "菜单更新已应用。原程序备份：$backup"
