$ErrorActionPreference='Stop'
$runtimeSource=Join-Path (Split-Path -Parent $PSScriptRoot) 'src/ConfigurePython.ps1'
$runtimeTokens=$null;$runtimeErrors=$null
$runtimeAst=[Management.Automation.Language.Parser]::ParseFile($runtimeSource,[ref]$runtimeTokens,[ref]$runtimeErrors)
if($runtimeErrors.Count){throw 'PowerShell parse failed'}
$runtimeFunction=$runtimeAst.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq 'Install-WebView2'},$true)
. ([scriptblock]::Create($runtimeFunction.Extent.Text))
$runtimeFolder=Join-Path ([IO.Path]::GetTempPath()) ('AIPraat-webview-test-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $runtimeFolder | Out-Null
$runtimeInstaller=Join-Path $runtimeFolder 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'
[IO.File]::WriteAllText($runtimeInstaller,'fixture only; installer process is mocked')
$script:runtimePresent=$false;$script:runtimeExit=0;$script:runtimeSignature='Valid';$script:runtimeStarts=0
function Test-WebView2 {return $script:runtimePresent}
function Get-AuthenticodeSignature {param($LiteralPath) return [pscustomobject]@{Status=$script:runtimeSignature;SignerCertificate=[pscustomobject]@{Subject='CN=Microsoft Corporation, O=Microsoft Corporation'}}}
function Start-Process {param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,[switch]$Wait)
    if($FilePath -ne $runtimeInstaller -or ($ArgumentList -join ' ') -ne '/silent /install' -or $WindowStyle -ne 'Hidden'){throw 'Incorrect official installer invocation'}
    $script:runtimeStarts++;$script:runtimePresent=$script:runtimeExit -eq 0
    [pscustomobject]@{ExitCode=$script:runtimeExit}
}
$Mode='Local'
try {
    $rejected=$false;try{Install-WebView2 ''}catch{$rejected=$_.Exception.Message -match '缺少 WebView2'}
    if(-not $rejected){throw 'Missing WebView2 did not request offline bundle'}
    Write-Output 'PASS missing WebView2 requires a local runtime installer'
    $rejected=$false;try{Install-WebView2 (Join-Path $runtimeFolder 'MicrosoftEdgeWebview2Setup.exe')}catch{$rejected=$true}
    if(-not $rejected){throw 'Online bootstrapper accepted as offline installer'}
    Write-Output 'PASS offline mode rejects bootstrapper'
    $script:runtimeSignature='NotSigned';$rejected=$false
    try{Install-WebView2 $runtimeInstaller}catch{$rejected=$_.Exception.Message -match '签名'}
    if(-not $rejected -or $script:runtimeStarts){throw 'Unsigned bundle executed'}
    Write-Output 'PASS invalid Microsoft signature prevents execution'
    $script:runtimeSignature='Valid';$script:runtimeExit=1603;$rejected=$false
    try{Install-WebView2 $runtimeInstaller}catch{$rejected=$_.Exception.Message -match '1603'}
    if(-not $rejected){throw 'Installer failure ignored'}
    Write-Output 'PASS WebView2 failure preserves unready status'
    $script:runtimeExit=0;Install-WebView2 $runtimeInstaller
    if(-not $script:runtimePresent){throw 'Runtime not rechecked'}
    $starts=$script:runtimeStarts;Install-WebView2 ''
    if($script:runtimeStarts -ne $starts){throw 'Existing runtime unnecessarily reinstalled'}
    Write-Output 'PASS official silent installation rechecks runtime and preserves existing install'
} finally {
    Remove-Item -LiteralPath $runtimeInstaller
    Remove-Item -LiteralPath $runtimeFolder
}
