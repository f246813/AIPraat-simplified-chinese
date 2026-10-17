<#
.SYNOPSIS
    Publish a GitHub release (notes + installer + Anki addon) for the AI Praat frontend.

.DESCRIPTION
    Creates a NEW release (and its tag) on the public mirror and attaches the files
    listed in -Assets (default: the rebuilt installer and Anki addon). It never touches existing
    releases or their assets: if a release with the same tag already exists the
    script stops unless -AssetOnly is given.

    The script is deliberately ASCII-only so Windows PowerShell 5.1 cannot mangle
    it by reading UTF-8 as ANSI; the Chinese release notes come from the notes
    file, which is always read as UTF-8.

    Token: pass -Token, set $env:GITHUB_TOKEN, or let the script prompt for it
    (hidden input). Use a classic token with public_repo, or a fine-grained token
    with "Contents: write" on that repository only, and revoke it afterwards.

.EXAMPLE
    powershell -NoProfile -ExecutionPolicy Bypass -File installer\publish-release.ps1
.EXAMPLE
    # retry only the asset upload for an already created release
    powershell -NoProfile -ExecutionPolicy Bypass -File installer\publish-release.ps1 -AssetOnly
#>
[CmdletBinding()]
param(
    [string]$Repo = 'f246813/praat-simplified-chinese-ai',
    [string]$Tag = 'v7.0-zh.8',
    [string]$TargetCommitish = 'modern',
    [string]$Notes = '',
    [string[]]$Assets = @(),
    [string]$ReleaseName = '',
    [string]$Token = '',
    [switch]$Draft,
    [switch]$AssetOnly,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Turns the usual HTTP failures into one readable line instead of a PowerShell dump.
function Invoke-GitHub {
    param([string]$Method, [string]$Uri, [hashtable]$Headers, [string]$ContentType, $Body, [string]$InFile)
    $arguments = @{ Method = $Method; Uri = $Uri; Headers = $Headers; TimeoutSec = 1800 }
    if ($ContentType) { $arguments.ContentType = $ContentType }
    if ($null -ne $Body) { $arguments.Body = $Body }
    if ($InFile) { $arguments.InFile = $InFile }
    try { return Invoke-RestMethod @arguments }
    catch {
        $status = 0
        if ($_.Exception.Response) { $status = [int]$_.Exception.Response.StatusCode }
        $hint = switch ($status) {
            401 { 'AUTH FAILED: the token was rejected (wrong, expired, or missing scope).' }
            403 { 'FORBIDDEN: the token lacks write access to this repository, or the API is rate limited.' }
            404 { 'NOT FOUND: check the repository name and the target branch.' }
            422 { 'ALREADY EXISTS: this tag or asset name is taken; nothing was overwritten.' }
            default { 'REQUEST FAILED: ' + $_.Exception.Message }
        }
        throw ($hint + ' [' + $Method + ' ' + $Uri + ']')
    }
}

$root = Split-Path -Parent $PSScriptRoot           # repository root (contains docs\, ai\)
if (-not $Notes) { $Notes = Join-Path $root 'docs\RELEASE-NOTES.zh-CN.md' }
if (-not $Assets -or $Assets.Count -eq 0) {
    $Assets = @(
        (Join-Path $root 'AIPraat-install.exe'),
        (Join-Path $root 'ai\anki_export\AIPraat.ankiaddon')
    )
}

if (-not (Test-Path -LiteralPath $Notes)) { throw "notes file not found: $Notes" }
$assetItems = @()
foreach ($item in $Assets) {
    if (-not (Test-Path -LiteralPath $item)) { throw "asset file not found: $item" }
    $assetItems += Get-Item -LiteralPath $item
}

$body = [IO.File]::ReadAllText($Notes, [Text.Encoding]::UTF8)
$firstLine = ($body -split "`n")[0].Trim()
if (-not $ReleaseName) {
    $ReleaseName = $firstLine.TrimStart('#').Trim()
    if (-not $ReleaseName) { $ReleaseName = $Tag }
}

if (-not $Token) { $Token = $env:GITHUB_TOKEN }
if (-not $Token) {
    $secure = Read-Host -Prompt ('GitHub token for ' + $Repo + ' (hidden, not echoed)') -AsSecureString
    $Token = [Runtime.InteropServices.Marshal]::PtrToStringBSTR(
        [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}
if (-not $Token) { throw 'no token given' }

$headers = @{
    Authorization          = 'Bearer ' + $Token
    Accept                 = 'application/vnd.github+json'
    'X-GitHub-Api-Version' = '2022-11-28'
    'User-Agent'           = 'aipraat-release-script'
}
$api = 'https://api.github.com/repos/' + $Repo

Write-Host ('repository : ' + $Repo)
Write-Host ('tag        : ' + $Tag + '  (new tag points at ' + $TargetCommitish + ')')
Write-Host ('release    : ' + $ReleaseName)
Write-Host ('notes      : ' + $Notes + '  (' + $body.Length + ' chars)')
foreach ($item in $assetItems) {
    Write-Host ('asset      : ' + $item.Name + '  (' + $item.Length + ' bytes)')
}
Write-Host ('draft      : ' + [bool]$Draft)

$existing = $null
try { $existing = Invoke-GitHub -Method Get -Uri ($api + '/releases/tags/' + $Tag) -Headers $headers }
catch { $existing = $null }

if ($existing -and -not $AssetOnly) {
    throw ('release ' + $Tag + ' already exists (' + $existing.html_url + '). ' +
           'Existing releases are never modified; pick another -Tag, or use -AssetOnly to attach a file to it.')
}

if (-not $Force) {
    $answer = Read-Host -Prompt "Type 'yes' to publish"
    if ($answer -ne 'yes') { Write-Host 'aborted; nothing was sent.'; return }
}

if ($AssetOnly) {
    if (-not $existing) { throw ('release ' + $Tag + ' not found, cannot use -AssetOnly') }
    $release = $existing
    Write-Host ('using existing release ' + $release.html_url)
} else {
    $payload = @{
        tag_name         = $Tag
        target_commitish = $TargetCommitish
        name             = $ReleaseName
        body             = $body
        draft            = [bool]$Draft
        prerelease       = $false
    } | ConvertTo-Json -Depth 3
    # ConvertTo-Json escapes non-ASCII as \uXXXX, which is valid JSON, so the
    # Chinese notes survive regardless of the console code page.
    $release = Invoke-GitHub -Method Post -Uri ($api + '/releases') -Headers $headers `
        -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($payload))
    Write-Host ('created release: ' + $release.html_url)
}

$uploaded = @()
foreach ($item in $assetItems) {
    $upload = 'https://uploads.github.com/repos/' + $Repo + '/releases/' + $release.id +
              '/assets?name=' + [Uri]::EscapeDataString($item.Name)
    $asset = Invoke-GitHub -Method Post -Uri $upload -Headers $headers `
        -ContentType 'application/zip' -InFile $item.FullName
    Write-Host ('uploaded: ' + $asset.browser_download_url + $(if ($asset.digest) { '  ' + $asset.digest } else { '' }))
    $uploaded += $asset
}

$check = Invoke-GitHub -Method Get -Uri ($api + '/releases/tags/' + $Tag) -Headers $headers
Write-Host ''
Write-Host ('release page  : ' + $check.html_url)
Write-Host ('tag           : ' + $check.tag_name + '  draft=' + $check.draft)
foreach ($item in $check.assets) {
    Write-Host ('  asset       : ' + $item.name + '  ' + $item.size + ' bytes  ' + $item.state +
                $(if ($item.digest) { '  ' + $item.digest } else { '' }))
}
Write-Host ''
Write-Host 'Done. Revoke the token now if it was created just for this upload.'
