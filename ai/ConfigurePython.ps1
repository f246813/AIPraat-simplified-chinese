param([Parameter(Mandatory=$true)][string]$PythonPath,[ValidateSet('Online','Local')][string]$Mode='Online',[ValidateSet('cpu','cuda')][string]$Device='cpu',[string]$PlanPath='',[string]$ResultPath='',[string]$LogPath='',[switch]$NoLog,[switch]$NoPause,[string]$InstallRoot='')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$env:PYTHONIOENCODING='utf-8'
$exitStatus=1
$downloadDirectory=$null
$webviewDownload=$null
$probeScript=Join-Path $PSScriptRoot 'runtime_dependencies.py'
$requirementsFile=Join-Path $PSScriptRoot 'requirements.txt'
if(-not (Test-Path -LiteralPath $probeScript)) {
    $runtimeSource=Join-Path (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)) 'ai'
    $probeScript=Join-Path $runtimeSource 'runtime_dependencies.py'
    $requirementsFile=Join-Path $runtimeSource 'requirements.txt'
}
$installationError=''
if(-not $InstallRoot){$InstallRoot=if($env:PRAAT_AI_INSTALL_ROOT){$env:PRAAT_AI_INSTALL_ROOT}else{Split-Path -Parent (Split-Path -Parent $probeScript)}}
$InstallRoot=[IO.Path]::GetFullPath($InstallRoot)
$env:PRAAT_AI_INSTALL_ROOT=$InstallRoot
$pipCache=Join-Path $InstallRoot 'cache/pip'
$temporaryDirectory=Join-Path $InstallRoot 'cache/temp'
New-Item -ItemType Directory -Path $temporaryDirectory -Force | Out-Null
$env:TEMP=$temporaryDirectory
$env:TMP=$temporaryDirectory
foreach($entry in @(@('HF_HOME','cache/huggingface'),@('TORCH_HOME','cache/torch'),@('NUMBA_CACHE_DIR','cache/numba'))) {
    if(-not [Environment]::GetEnvironmentVariable($entry[0])){[Environment]::SetEnvironmentVariable($entry[0],(Join-Path $InstallRoot $entry[1]))}
}
$logDirectory=Join-Path $InstallRoot 'logs/runtime'
if($NoLog){$LogPath=''}
elseif(-not $LogPath) {
    $LogPath=Join-Path $logDirectory ('install-'+[DateTime]::UtcNow.ToString('yyyyMMdd-HHmmss-fff')+'Z-'+[guid]::NewGuid().ToString('N').Substring(0,8)+'.log')
}
$transcribing=$false
if($LogPath) {
    try {
        $LogPath=[IO.Path]::GetFullPath($LogPath)
        New-Item -ItemType Directory -Path (Split-Path -Parent $LogPath) -Force | Out-Null
        # Windows PowerShell 5.1 otherwise appends transcripts as ASCII and loses Chinese.
        if(-not (Test-Path -LiteralPath $LogPath) -or (Get-Item -LiteralPath $LogPath).Length -eq 0){[IO.File]::WriteAllText($LogPath,'',[Text.UTF8Encoding]::new($true))}
        Start-Transcript -LiteralPath $LogPath -Append | Out-Null
        $transcribing=$true
    } catch {$LogPath=''}
}
if(-not $LogPath){Write-Host '日志未保存，不影响检测。'}
if(-not $ResultPath){$ResultPath=if($LogPath){[IO.Path]::ChangeExtension($LogPath,'.json')}else{Join-Path ([IO.Path]::GetTempPath()) ('AIPraat-install-'+[guid]::NewGuid().ToString('N')+'.json')}}
$ResultPath=[IO.Path]::GetFullPath($ResultPath)
New-Item -ItemType Directory -Path (Split-Path -Parent $ResultPath) -Force | Out-Null
$script:lastNativeOutput=''

function Invoke-Python([string[]]$Arguments) {
    # Native stderr warnings on Windows PowerShell 5.1 must not abort pip.
    $ErrorActionPreference='Continue'
    $capture=New-Object Text.StringBuilder
    & $PythonPath @Arguments 2>&1 | ForEach-Object { $line=$_.ToString();[void]$capture.AppendLine($line);Write-Host $line }
    $script:lastNativeOutput=$capture.ToString()
    return $LASTEXITCODE
}

function Test-PipCacheFailure([string]$Text) {
    $cache='(?:pip[\\/]+cache|[\\/]+cache[\\/]+(?:wheels|http[^\\/\s]*))'
    $error='(?:permission denied|access is denied|access denied|拒绝访问|PermissionError|invalid wheel|invalid zip|BadZipFile|corrupt)'
    return $Text -match ('(?is)'+$error+'.{0,800}'+$cache+'|'+$cache+'.{0,800}'+$error)
}

function Invoke-Pip([string[]]$Arguments) {
    Write-Host ('pip install '+($Arguments -join ' '))
    $prefix=@('-I','-m','pip','--isolated','install','--disable-pip-version-check','--cache-dir',$pipCache)
    $status=Invoke-Python -Arguments ($prefix+$Arguments)
    if($status -ne 0 -and (Test-PipCacheFailure $script:lastNativeOutput)) {
        Write-Host '检测到 pip 缓存错误；本次安装禁用缓存重试一次，其他安装仍使用缓存。'
        $status=Invoke-Python -Arguments ($prefix+@('--no-cache-dir')+$Arguments)
    }
    return $status
}

function Test-WebView2 {
    $key='Software\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
    foreach($hive in @([Microsoft.Win32.RegistryHive]::CurrentUser,[Microsoft.Win32.RegistryHive]::LocalMachine)) {
        foreach($view in @([Microsoft.Win32.RegistryView]::Registry32,[Microsoft.Win32.RegistryView]::Registry64)) {
            $base=[Microsoft.Win32.RegistryKey]::OpenBaseKey($hive,$view)
            try {
                $entry=$base.OpenSubKey($key)
                if($entry){try {$version=[string]$entry.GetValue('pv');if($version -and $version -ne '0.0.0.0'){return $true}}finally{$entry.Dispose()}}
            } finally {$base.Dispose()}
        }
    }
    return $false
}

function Install-WebView2([string]$LocalInstaller) {
    if(Test-WebView2){return}
    if($Mode -eq 'Local') {
        if(-not $LocalInstaller -or -not (Test-Path -LiteralPath $LocalInstaller -PathType Leaf)) {
            throw '缺少 WebView2 Runtime：请选择微软 x64 Evergreen 离线完整安装程序，或主动选择联网补齐。'
        }
        if((Split-Path -Leaf $LocalInstaller) -notmatch '^MicrosoftEdgeWebView2RuntimeInstallerX64.*\.exe$') {
            throw '离线方式需要 MicrosoftEdgeWebView2RuntimeInstallerX64.exe 完整包；不能使用联网 Bootstrapper 或其他软件安装器。'
        }
        $bundle=$LocalInstaller
    } else {
        $script:webviewDownload=Join-Path ([IO.Path]::GetTempPath()) ('AIPraat-webview-'+[guid]::NewGuid().ToString('N')+'.exe')
        $bundle=$script:webviewDownload
        [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
        Write-Host '正在下载微软官方 WebView2 Evergreen Bootstrapper…'
        Invoke-WebRequest -UseBasicParsing -Uri 'https://go.microsoft.com/fwlink/p/?LinkId=2124703' -OutFile $bundle
    }
    $signature=Get-AuthenticodeSignature -LiteralPath $bundle
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
        throw 'WebView2 安装程序的微软签名验证失败。'
    }
    $process=Start-Process -FilePath $bundle -ArgumentList @('/silent','/install') -WindowStyle Hidden -PassThru -Wait
    if($process.ExitCode -notin @(0,3010)){throw "WebView2 安装失败，退出码：$($process.ExitCode)。"}
    if(-not (Test-WebView2)){throw 'WebView2 安装后仍未检测到 Runtime。'}
}

function Test-Tk {
    # Python single quotes survive PowerShell 5.1's legacy native argument passing.
    # Catch Python errors so missing Tk is a repairable result, not PowerShell stderr.
    $code=@'
try:
 import tkinter
 r=tkinter.Tk(); r.withdraw(); r.destroy()
except Exception as error:
 print('Tk unavailable: '+str(error))
 raise SystemExit(1)
'@
    & $PythonPath -I -c $code | Out-Host
    return $LASTEXITCODE -eq 0
}

function Get-TkRegistration($metadata) {
    $registrations=@()
    foreach($registry in @('HKCU:\Software\Python\PythonCore','HKLM:\Software\Python\PythonCore','HKLM:\Software\WOW6432Node\Python\PythonCore')) {
        foreach($key in @(Get-ChildItem -LiteralPath $registry -ErrorAction SilentlyContinue)) {
            $install=Get-Item -LiteralPath (Join-Path $key.PSPath 'InstallPath') -ErrorAction SilentlyContinue
            if($install) {
                $installedPath=([string]$install.GetValue('')).TrimEnd('\')
                if($installedPath) {
                    $registrations+= [pscustomobject]@{Path=$installedPath;Family=$key.PSChildName;Hive=$registry.Substring(0,5);AllUsers=[int]$registry.StartsWith('HKLM:')}
                }
            }
        }
    }
    $selected=@($registrations | Where-Object {$_.Path -eq ([string]$metadata.base).TrimEnd('\')})
    if($selected.Count -ne 1 -or $metadata.implementation -ne 'cpython') {
        throw '此 Python 缺少 Tcl/Tk，且未找到唯一的官方 Python 注册项。请用该环境的安装器补装 Tcl/Tk，或选择包含 Tk 的 Python 环境；NumPy/Pillow 已处理。'
    }
    # python.org's bootstrapper prefers HKCU feature state. Avoid repairing any
    # same-family user/system installation when a second registration exists.
    $family=@($registrations | Where-Object {$_.Family -eq $selected[0].Family})
    if($family.Count -ne 1){throw '检测到同版本 Python 的多个用户/系统安装目录。请用所选 Python 的安装器补装 Tcl/Tk 后重试。'}
    return $selected[0]
}

function Install-Tk($metadata) {
    # Conda owns Tcl/Tk in its prefix; do not modify a different active environment.
    if(Test-Path -LiteralPath (Join-Path $metadata.base 'conda-meta')) {
        $candidates=@((Join-Path $metadata.base 'Scripts/conda.exe'))
        $baseParent=Split-Path -Parent $metadata.base
        if((Split-Path -Leaf $baseParent) -eq 'envs') {$candidates+=(Join-Path (Split-Path -Parent $baseParent) 'Scripts/conda.exe')}
        if($env:CONDA_EXE){$candidates+=$env:CONDA_EXE}
        $conda=$candidates | Where-Object {Test-Path -LiteralPath $_ -PathType Leaf} | Select-Object -First 1
        if(-not $conda){throw '没有找到 Conda 管理器。请通过该环境的 Conda 安装 tk 组件后重试。'}
        & $conda install --yes --prefix $metadata.base tk
        if($LASTEXITCODE -ne 0){throw 'Conda 安装 Tk 失败，请查看上方信息后重试。'}
        return
    }

    # Only repair a registered python.org runtime at this exact base path.
    # A venv shares Tk with its base runtime, rather than a pip package named tk.
    $registration=Get-TkRegistration $metadata
    $version=[string]$metadata.version
    $architecture=[string]$metadata.machine
    $label=switch($architecture){'win-amd64'{'64-bit'};'win-arm64'{'ARM64'};'win32'{'32-bit'};default{throw '不支持自动修复此 Python 架构。'}}
    $bundle=$null
    $uninstallRoots=@($registration.Hive+'\Software\Microsoft\Windows\CurrentVersion\Uninstall')
    if($registration.AllUsers){$uninstallRoots+= 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'}
    foreach($registry in $uninstallRoots) {
        foreach($key in @(Get-ChildItem -LiteralPath $registry -ErrorAction SilentlyContinue)) {
            $entry=Get-ItemProperty -LiteralPath $key.PSPath
            if($entry.DisplayName -eq "Python $version ($label)" -and $entry.ModifyPath -match '^"([^"]+\.exe)"') {
                if(Test-Path -LiteralPath $matches[1]) {$bundle=$matches[1]}
            }
        }
    }
    if(-not $bundle) {
        $suffix=switch($architecture){'win-amd64'{'-amd64'};'win-arm64'{'-arm64'};'win32'{''}}
        $script:downloadDirectory=Join-Path ([IO.Path]::GetTempPath()) ('AIPraat-tk-'+[guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $script:downloadDirectory | Out-Null
        $bundle=Join-Path $script:downloadDirectory "python-$version$suffix.exe"
        [Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12
        Write-Host "正在获取 Python $version 的官方安装组件…"
        Invoke-WebRequest -UseBasicParsing -Uri "https://www.python.org/ftp/python/$version/python-$version$suffix.exe" -OutFile $bundle
    }
    $signature=Get-AuthenticodeSignature -LiteralPath $bundle
    if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {throw 'Python 安装器签名验证失败，已停止修复。'}
    Write-Host '正在补装 Tcl/Tk 组件；系统级 Python 可能显示管理员权限提示…'
    $targetArguments=@(('InstallAllUsers='+$registration.AllUsers),('TargetDir="'+$registration.Path+'"'))
    $process=Start-Process -FilePath $bundle -ArgumentList (@('/modify','/passive','/norestart','Include_tcltk=1','Include_pip=1')+$targetArguments) -WindowStyle Hidden -PassThru -Wait
    if($process.ExitCode -notin @(0,3010)){throw "Python Tcl/Tk 组件安装失败，退出码：$($process.ExitCode)。"}
    if(-not (Test-Tk)) {
        $process=Start-Process -FilePath $bundle -ArgumentList (@('/repair','/passive','/norestart')+$targetArguments) -WindowStyle Hidden -PassThru -Wait
        if($process.ExitCode -notin @(0,3010)){throw "Python 修复失败，退出码：$($process.ExitCode)。"}
    }
}

try {
    if(-not (Test-Path -LiteralPath $PythonPath -PathType Leaf)){throw '所选 python.exe 不存在，请返回重新选择。'}
    Write-Host "配置所选 Python：$PythonPath"
    $metadataJson=& $PythonPath -I -c 'import sys,json,platform,sysconfig; print(json.dumps(dict(base=sys.base_prefix,version=platform.python_version(),machine=sysconfig.get_platform(),implementation=sys.implementation.name,version_info=list(sys.version_info[:2]))))'
    if($LASTEXITCODE -ne 0){throw '无法运行所选 Python。'}
    $metadata=$metadataJson | ConvertFrom-Json
    if($metadata.version_info[0] -ne 3 -or $metadata.version_info[1] -notin @(10,11,12) -or $metadata.machine -ne 'win-amd64') {throw '需要 Python 3.10–3.12 x64（推荐 3.12）；Parselmouth 是必需运行库。'}
    if(-not (Test-Path -LiteralPath $probeScript)){throw '共用运行库检测脚本缺失。'}
    $repairFile=[IO.Path]::ChangeExtension($ResultPath,'.repair.json')
    $repairArguments=@('-I',$probeScript,'--requirements',$requirementsFile,'--device',$Device,'--repair-plan','--output',$repairFile)
    if($PlanPath -and (Test-Path -LiteralPath $PlanPath)){$repairArguments+=@('--before-plan',$PlanPath)}
    if((Invoke-Python -Arguments $repairArguments) -ne 0){throw '无法根据逐项检测结果生成补齐清单。'}
    $repair=Get-Content -LiteralPath $repairFile -Raw -Encoding UTF8 | ConvertFrom-Json
    $needsPip=if($Mode -eq 'Local'){$true}else{[bool]($repair.torch -or @($repair.requirements).Count)}
    # Capture even a silent native command: a GUI/redirected PowerShell host
    # may not have a readable console buffer (Python 3.12 returns Win32 0x5).
    $pipProbeExit=if($needsPip){Invoke-Python @('-I','-c','import importlib.util; raise SystemExit(0 if importlib.util.find_spec(''pip'') else 1)')}else{0}
    if($needsPip -and $pipProbeExit -ne 0) {
        Write-Host '正在配置 pip…'
        if((Invoke-Python @('-I','-m','ensurepip','--upgrade')) -ne 0){throw 'pip 配置失败，请修复所选 Python 的 pip 组件后重试。'}
    }
    $localWebview=''
    if($Mode -eq 'Local') {
        if(-not $PlanPath -or -not (Test-Path -LiteralPath $PlanPath)){throw '本地安装需要 PlanPath JSON：packages、dependency_folder、webview2。'}
        $plan=Get-Content -LiteralPath $PlanPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $localWebview=[string]$plan.webview2
        Write-Host '正在验证逐项 wheel 名称、版本、Python 和架构，并离线解析间接依赖…'
        if((Invoke-Python @('-I',$probeScript,'--requirements',$requirementsFile,'--local-plan',$PlanPath)) -ne 0) {
            throw '本地包安装失败：具体缺包或不匹配项见上方 pip/校验日志。请补包后重试，或主动选择联网补齐。'
        }
    } else {
        $constraintsFile=[IO.Path]::ChangeExtension($ResultPath,'.constraints.txt')
        [IO.File]::WriteAllLines($constraintsFile,[string[]]$repair.constraints,[Text.UTF8Encoding]::new($false))
        if($repair.torch) {
            $torchIndex=if($Device -eq 'cpu'){'https://download.pytorch.org/whl/cpu'}else{'https://download.pytorch.org/whl/cu128'}
            if((Invoke-Pip -Arguments @('--only-binary=:all:','--upgrade',[string]$repair.torch,'--index-url',$torchIndex,'-c',$constraintsFile)) -ne 0){throw 'PyTorch 安装失败，请查看上方信息。'}
        }
        $missing=@($repair.requirements)
        if($missing.Count) {
            Write-Host ('正在补齐未就绪的 Python 运行库：'+($missing -join '、'))
            if((Invoke-Pip -Arguments (@('--prefer-binary','--upgrade','-c',$constraintsFile)+$missing)) -ne 0) {throw '依赖安装失败，请查看网络、权限或版本冲突信息。'}
        } else {Write-Host '其他 Python 运行库已就绪，无需重新安装。'}
        # Normal --upgrade can leave a broken same-version installation untouched.
        # Resolve dependencies above, then replace ONLY the failed distributions.
        foreach($target in @($repair.reinstall)) {
            Write-Host ('定向重装版本满足但功能失败项（不重装其健康依赖）：'+$target)
            $arguments=@('--only-binary=:all:','--force-reinstall','--no-deps','-c',$constraintsFile,[string]$target)
            if($target -match '^torch[<>=]'){$arguments+=@('--index-url',$torchIndex)}
            if((Invoke-Pip -Arguments $arguments) -ne 0){throw ('定向重装失败：'+$target)}
        }
    }
    if($repair.tk) {
        if($Mode -eq 'Local'){throw 'Tcl/Tk 缺失：请用所选 Python/Conda 的本地安装组件修复 Tk，或主动选择联网补齐。'}
        Install-Tk $metadata
    }
    if($repair.webview2){Install-WebView2 $localWebview}
    $exitStatus=0
} catch {
    $installationError=$_.Exception.Message
    Write-Host ("配置未完成："+$installationError) -ForegroundColor Red
} finally {
    # Every caller consumes this same per-item contract, including failed installs.
    if((Test-Path -LiteralPath $PythonPath -PathType Leaf) -and (Test-Path -LiteralPath $probeScript)) {
        $probeExit=Invoke-Python @('-I',$probeScript,'--requirements',$requirementsFile,'--device',$Device,'--smoke','--output',$ResultPath)
        if($probeExit -ne 0){$exitStatus=1}
        if(Test-Path -LiteralPath $ResultPath) {
            $result=Get-Content -LiteralPath $ResultPath -Raw -Encoding UTF8 | ConvertFrom-Json
            if($installationError) {
                $result.ready=$false
                $result | Add-Member -NotePropertyName installation_error -NotePropertyValue $installationError -Force
            }
            $result | Add-Member -NotePropertyName log_path -NotePropertyValue $LogPath -Force
            [IO.File]::WriteAllText($ResultPath,($result | ConvertTo-Json -Depth 8),[Text.UTF8Encoding]::new($false))
            Write-Host "逐项检测结果：$ResultPath"
        }
    }
    if($exitStatus -eq 0){Write-Host '全部必需运行库已通过复检和新前端窗口启动检查。' -ForegroundColor Green}
    # Remove only the specific installer downloaded by this invocation.
    if($downloadDirectory) {
        Get-ChildItem -LiteralPath $downloadDirectory -File | Remove-Item
        Remove-Item -LiteralPath $downloadDirectory
    }
    if($webviewDownload -and (Test-Path -LiteralPath $webviewDownload)){Remove-Item -LiteralPath $webviewDownload}
    if($LogPath) {
        foreach($extension in @('.repair.json','.constraints.txt')) {
            $workingFile=[IO.Path]::ChangeExtension($ResultPath,$extension)
            $savedFile=[IO.Path]::ChangeExtension($LogPath,$extension)
            if($workingFile -ne $savedFile -and (Test-Path -LiteralPath $workingFile)) {
                try {Copy-Item -LiteralPath $workingFile -Destination $savedFile -Force}
                catch {Write-Host '补齐清单或版本约束未保存，不影响检测。'}
            }
        }
        Write-Host "配置日志：$LogPath"
    }
    if($transcribing){try {Stop-Transcript | Out-Null}catch {Write-Host '日志未完整保存，不影响检测。'}}
}
if(-not $NoPause){[void](Read-Host '按 Enter 关闭 PowerShell 并返回配置界面')}
exit $exitStatus
