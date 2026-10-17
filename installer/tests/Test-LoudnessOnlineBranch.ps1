param([Parameter(Mandatory=$true)][string]$PythonPath,[Parameter(Mandatory=$true)][string]$RepairPath,[Parameter(Mandatory=$true)][string]$Index,[Parameter(Mandatory=$true)][string]$ResultPath)
# Execute the shipping Online branch unchanged, but inject a real local HTTP
# package index into pip. Never alters pip configuration or contacts PyPI.
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING='utf-8'
$script:commands=@()
function Invoke-Pip([string[]]$Arguments) {
    $script:commands+=,([string[]]$Arguments)
    Write-Host ('TEST TRANSPORT pip install '+($Arguments -join ' '))
    & $PythonPath -I -m pip --isolated install --disable-pip-version-check --no-cache-dir --index-url $Index @Arguments | ForEach-Object {Write-Host $_}
    return $LASTEXITCODE
}
$repair=Get-Content -LiteralPath $RepairPath -Raw -Encoding UTF8 | ConvertFrom-Json
$Device='cpu'
$source=Join-Path $PSScriptRoot '../src/ConfigurePython.ps1'
$tokens=$null;$errors=$null
$ast=[Management.Automation.Language.Parser]::ParseFile((Resolve-Path $source),[ref]$tokens,[ref]$errors)
if($errors.Count){throw 'ConfigurePython.ps1 parse failed'}
$branches=@($ast.FindAll({param($node) $node -is [Management.Automation.Language.IfStatementAst] -and $node.ElseClause -and $node.ElseClause.Extent.Text.Contains('$constraintsFile=')},$true))
if($branches.Count -ne 1){throw 'Expected one shipping Online branch'}
$body=$branches[0].ElseClause.Extent.Text
& ([ScriptBlock]::Create($body.Substring(1,$body.Length-2)))
[IO.File]::WriteAllText(([IO.Path]::ChangeExtension($ResultPath,'.commands.json')), (ConvertTo-Json -InputObject @($script:commands) -Depth 5),[Text.UTF8Encoding]::new($false))
