# Shared preflight for installer tests. Never installs dependencies or downloads models.
function Invoke-TestExecutable([string]$Executable,[string]$Arguments) {
    $start=New-Object Diagnostics.ProcessStartInfo
    $start.FileName=$Executable; $start.Arguments=$Arguments
    $start.UseShellExecute=$false; $start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true; $start.RedirectStandardError=$true
    $start.StandardOutputEncoding=[Text.Encoding]::UTF8; $start.StandardErrorEncoding=[Text.Encoding]::UTF8
    $start.EnvironmentVariables['PYTHONIOENCODING']='utf-8'
    $process=New-Object Diagnostics.Process
    $process.StartInfo=$start
    try {
        [void]$process.Start()
        $stdout=$process.StandardOutput.ReadToEndAsync(); $stderr=$process.StandardError.ReadToEndAsync()
        if(-not $process.WaitForExit(600000)){$process.Kill();throw 'Test environment detection timed out.'}
        [pscustomobject]@{ExitCode=$process.ExitCode;Output=$stdout.Result;Error=$stderr.Result}
    } finally {$process.Dispose()}
}

function Resolve-TestPython([string]$ProjectDirectory,[string]$Python='') {
    $explicit=-not [string]::IsNullOrWhiteSpace($Python)
    if(-not $explicit -and $env:AIPRAAT_TEST_PYTHON){$Python=$env:AIPRAAT_TEST_PYTHON;$explicit=$true}
    $candidates=@()
    if($explicit){$candidates=@($Python)}else {
        foreach($base in @($ProjectDirectory,(Split-Path -Parent $ProjectDirectory))) {
            foreach($relative in @('.venv/Scripts/python.exe','venv-ai-modern/Scripts/python.exe','venv-ai/Scripts/python.exe')) {
                $candidates+=Join-Path $base $relative
            }
        }
        $command=Get-Command python -ErrorAction SilentlyContinue
        if($command -and $command.Source -notmatch '[\\/]Microsoft[\\/]WindowsApps[\\/]'){$candidates+=$command.Source}
    }
    $notes=@()
    foreach($candidate in ($candidates | Select-Object -Unique)) {
        if(Test-Path -LiteralPath $candidate -PathType Container){$candidate=Join-Path $candidate 'Scripts/python.exe'}
        if(-not (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            if($explicit){$notes+="Interpreter not found: $candidate"}
            continue
        }
        $candidate=[IO.Path]::GetFullPath($candidate)
        $result=$null
        try {
            $arguments='-I "'+(Join-Path $ProjectDirectory 'ai/runtime_dependencies.py')+'" --requirements "'+(Join-Path $ProjectDirectory 'ai/requirements.txt')+'" --device cpu'
            $result=Invoke-TestExecutable $candidate $arguments
            $line=($result.Output.Trim() -split '\r?\n')[-1]
            $report=$line | ConvertFrom-Json
            if($null -eq $report){throw "No runtime report (exit $($result.ExitCode)): $($result.Error)"}
            if($result.ExitCode -eq 0 -and $report.supported -and $report.completed -and $report.ready) {
                Write-Host "Test Python: $candidate (Python $($report.version), runtime ready)"
                return $candidate
            }
            $missing=($report.items | Where-Object {-not $_.ok} | ForEach-Object {$_.label}) -join ', '
            $notes+="$candidate (Python $($report.version)): $($report.message) Missing/unavailable: $missing"
        } catch {$notes+="$candidate : $($_.Exception.Message) $(if($result){$result.Error})"}
    }
    throw ("测试环境未就绪，请用 -Python 指定 Python 3.10–3.12 x64 且运行库齐全的 python.exe。"+[Environment]::NewLine+($notes -join [Environment]::NewLine))
}

function Resolve-TestFFmpeg([string]$ProjectDirectory,[string]$FFmpeg='') {
    $explicit=-not [string]::IsNullOrWhiteSpace($FFmpeg)
    $candidates=@($FFmpeg)
    if(-not $explicit) {
        $candidates=@((Join-Path $ProjectDirectory 'ffmpeg.exe'),(Join-Path $ProjectDirectory 'tools/ffmpeg.exe'),(Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AIPraat/ffmpeg/ffmpeg.exe'))
        $command=Get-Command ffmpeg -ErrorAction SilentlyContinue
        if($command){$candidates+=$command.Source}
    }
    $notes=@()
    foreach($candidate in ($candidates | Select-Object -Unique)) {
        if(-not (Test-Path -LiteralPath $candidate -PathType Leaf)){if($explicit){$notes+="Executable not found: $candidate"};continue}
        $candidate=[IO.Path]::GetFullPath($candidate)
        try {
            $result=Invoke-TestExecutable $candidate '-nostdin -version'
            if($result.ExitCode -eq 0 -and $result.Output.StartsWith('ffmpeg version ')){Write-Host "Test FFmpeg: $candidate";return $candidate}
            $notes+="$candidate : $($result.Error)"
        } catch {$notes+="$candidate : $($_.Exception.Message)"}
    }
    throw ("FFmpeg 测试环境未就绪，请用 -FFmpeg 指定可用的 ffmpeg.exe。"+[Environment]::NewLine+($notes -join [Environment]::NewLine))
}
