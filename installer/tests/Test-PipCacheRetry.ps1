$ErrorActionPreference='Stop'
$runtimeScript=Join-Path (Split-Path -Parent $PSScriptRoot) 'src/ConfigurePython.ps1'
$runtimeTokens=$null;$runtimeErrors=$null
$runtimeAst=[Management.Automation.Language.Parser]::ParseFile($runtimeScript,[ref]$runtimeTokens,[ref]$runtimeErrors)
if($runtimeErrors.Count){throw 'PowerShell parse failed'}
foreach($name in @('Test-PipCacheFailure','Invoke-Pip')) {
    $function=$runtimeAst.Find({param($node) $node -is [Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name},$true)
    if(-not $function){throw "Missing $name"}
    . ([scriptblock]::Create($function.Extent.Text))
}
$script:attempts=@();$script:errors=@();$script:codes=@()
function Invoke-Python([string[]]$Arguments) {
    $index=$script:attempts.Count
    $script:attempts+=,@($Arguments)
    $script:lastNativeOutput=$script:errors[$index]
    return $script:codes[$index]
}
function Case($name,$messages,$codes,$expectedAttempts,$expectedCode) {
    $script:attempts=@();$script:errors=$messages;$script:codes=$codes
    $result=Invoke-Pip -Arguments @('--prefer-binary','pillow>=10.0')
    if($result -ne $expectedCode -or $script:attempts.Count -ne $expectedAttempts){throw "${name}: wrong retry count/exit code"}
    if($script:attempts[0] -contains '--no-cache-dir'){throw "${name}: initial install disabled cache"}
    if($expectedAttempts -eq 2 -and $script:attempts[1] -notcontains '--no-cache-dir'){throw "${name}: retry did not disable cache"}
    Write-Output "PASS $name"
}
Case 'success uses cache with no retry' @('Using cached Pillow.whl') @(0) 1 0
Case 'cache permission failure retries once' @("ERROR: Permission denied: 'C:\Users\test\pip\cache\wheels\Pillow.whl'",'OK') @(1,0) 2 0
Case 'network failure does not bypass cache' @('ERROR: connection timed out') @(1) 1 1
Case 'site-packages permission failure does not bypass cache' @("ERROR: Permission denied: 'C:\Python312\Lib\site-packages\PIL'") @(1) 1 1
Case 'second failure stops after one retry' @("ERROR: Access is denied: 'C:\pip\cache\wheels\Pillow.whl'",'ERROR: connection timed out') @(1,1) 2 1
Case 'corrupt cache wheel retries once' @('ERROR: Invalid wheel: C:\pip\cache\wheels\Pillow.whl','OK') @(1,0) 2 0
