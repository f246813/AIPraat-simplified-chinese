param(
    [Parameter(Mandatory=$true)][string]$Source,
    [Parameter(Mandatory=$true)][string]$Destination,
    [Parameter(Mandatory=$true)][string]$MediumDirectory
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'NativeIntegrity.ps1')
# Use .NET directly so the MSYS2 -> Windows PowerShell entry also works when
# its inherited PSModulePath points at a different PowerShell installation.
function Get-NativeSHA256([string]$Path) {
    $algorithm = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try { return [BitConverter]::ToString($algorithm.ComputeHash($stream)).Replace('-', '') }
    finally { $stream.Dispose(); $algorithm.Dispose() }
}
$sourcePath = (Get-Item -LiteralPath $Source).FullName
$targetPath = [IO.Path]::GetFullPath($Destination)
$targetDirectory = [IO.Path]::GetDirectoryName($targetPath)
$mediumPath = (Get-Item -LiteralPath $MediumDirectory).FullName
if ([IO.Path]::GetPathRoot($mediumPath) -ne [IO.Path]::GetPathRoot($targetPath)) { throw 'Staging and the final EXE must be on the same volume.' }
if ([AIPraat.NativeIntegrity]::ProcessLevel($PID) -lt 0x2000) { throw 'Publish from a Medium-integrity PowerShell process.' }
if ([AIPraat.NativeIntegrity]::FileLevel($mediumPath) -ne 0x2000) { throw 'The staging directory must have Medium integrity.' }
if ([IO.Path]::GetExtension($targetPath) -ne '.exe') { throw 'This publication step is for desktop EXEs only.' }
$expected = Get-NativeSHA256 $sourcePath
if ((Test-Path -LiteralPath $targetPath) -and
    [AIPraat.NativeIntegrity]::FileLevel($targetPath) -eq 0x2000 -and
    (Get-NativeSHA256 $targetPath) -eq $expected) {
    [pscustomobject]@{Path=$targetPath;SHA256=$expected.ToLowerInvariant();Integrity=8192;Backup=$null}
    return
}
$staged = Join-Path $mediumPath ('.aipraat-native-' + [guid]::NewGuid().ToString('N') + '.exe')
$backupPath = $null
try {
    # Create a new object in Medium, copying bytes only. Copying into the Low
    # workspace recreates the recurring status.json failure. icacls /setintegritylevel
    # also requires WRITE_OWNER, which the task token may lack even when it is Medium.
    $inputStream = [IO.File]::OpenRead($sourcePath)
    try {
        $outputStream = [IO.File]::Open($staged,[IO.FileMode]::CreateNew,[IO.FileAccess]::Write)
        try { $inputStream.CopyTo($outputStream) } finally { $outputStream.Dispose() }
    } finally { $inputStream.Dispose() }
    if ([AIPraat.NativeIntegrity]::FileLevel($staged) -ne 0x2000) { throw 'Staged EXE has incorrect integrity; publication stopped.' }
    if ((Get-NativeSHA256 $staged) -ne $expected) { throw 'Staged EXE byte verification failed.' }
    if (Test-Path -LiteralPath $targetPath) {
        $backupDirectory = Join-Path $targetDirectory '.aipraat-backups'
        New-Item -ItemType Directory -Path $backupDirectory -Force | Out-Null
        $backupPath = [IO.Path]::GetFullPath((Join-Path $backupDirectory ([IO.Path]::GetFileNameWithoutExtension($targetPath) + '-' + [guid]::NewGuid().ToString('N') + '.exe')))
        if (-not $backupPath.StartsWith($targetDirectory + [IO.Path]::DirectorySeparatorChar,[StringComparison]::OrdinalIgnoreCase)) { throw 'Backup path must stay within the target directory.' }
        [IO.File]::Move($targetPath,$backupPath)
    }
    try {
        # Same-volume rename preserves the Medium security descriptor. A final
        # Copy-Item here would inherit Low again. Runtime/IPC ACLs stay untouched.
        [IO.File]::Move($staged,$targetPath)
        if ([AIPraat.NativeIntegrity]::FileLevel($targetPath) -ne 0x2000) { throw 'Final EXE has incorrect integrity.' }
        if ((Get-NativeSHA256 $targetPath) -ne $expected) { throw 'Final EXE byte verification failed.' }
    } catch {
        if (Test-Path -LiteralPath $targetPath) { Remove-Item -LiteralPath $targetPath }
        if ($backupPath) { [IO.File]::Move($backupPath,$targetPath) }
        throw
    }
    [pscustomobject]@{Path=$targetPath;SHA256=$expected.ToLowerInvariant();Integrity=8192;Backup=$backupPath}
} finally {
    if (Test-Path -LiteralPath $staged) { Remove-Item -LiteralPath $staged }
}
