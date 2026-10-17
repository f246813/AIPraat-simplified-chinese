param([string]$Python='')
$ErrorActionPreference='Stop'
$taskRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
. (Join-Path $PSScriptRoot 'TestEnvironment.ps1')
$Python=Resolve-TestPython -ProjectDirectory $taskRoot -Python $Python
$taskFixture=Join-Path $taskRoot ('installer/verification/python-live-'+[guid]::NewGuid().ToString('N'))
& $Python -m venv --without-pip $taskFixture
if($LASTEXITCODE -ne 0){throw 'Cannot create isolated environment'}
$taskScript=Join-Path $taskFixture "配置 O'Brien & $.ps1"
[IO.File]::WriteAllText($taskScript,[IO.File]::ReadAllText((Join-Path $taskRoot 'installer/src/ConfigurePython.ps1')),[Text.UTF8Encoding]::new($true))
$taskSelected=Join-Path $taskFixture 'Scripts/python.exe'
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $taskScript -PythonPath $taskSelected -NoPause
if($LASTEXITCODE -ne 0){throw 'Fresh dependency installation failed'}
$taskCheck=& $taskSelected -I -c 'import sys,json,numpy,PIL,tkinter,pydantic_ai,pydantic_graph,jsonschema,importlib.metadata; from pydantic_ai.models.openai import OpenAIChatModel; from pydantic_graph import GraphBuilder; assert importlib.metadata.version("pydantic-ai-slim")=="2.52.0"; assert importlib.metadata.version("pydantic-graph")=="2.52.0"; assert importlib.metadata.version("jsonschema")=="4.26.0"; r=tkinter.Tk(); r.withdraw(); r.destroy(); print(json.dumps(dict(prefix=sys.prefix,numpy=numpy.__version__,pillow=PIL.__version__)))' | ConvertFrom-Json
if($LASTEXITCODE -ne 0 -or $taskCheck.prefix -ne $taskFixture -or [int]$taskCheck.numpy.Split('.')[0] -lt 2 -or [int]$taskCheck.pillow.Split('.')[0] -lt 10){throw 'Selected environment verification failed'}
Write-Output 'PASS fresh environment installs pip Pillow NumPy pinned cloud dependencies and validates Tk on Windows PowerShell 5.1'
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $taskScript -PythonPath $taskSelected -NoPause
if($LASTEXITCODE -ne 0){throw 'Repeated configuration failed'}
Write-Output 'PASS repeated configuration succeeds'
& powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $taskScript -PythonPath (Join-Path $taskFixture 'missing/python.exe') -NoPause
if($LASTEXITCODE -ne 1){throw 'Missing interpreter must return failure'}
Write-Output 'PASS missing interpreter returns failure'
exit 0
