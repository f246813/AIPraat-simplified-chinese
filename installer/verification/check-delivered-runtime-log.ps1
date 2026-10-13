param([string]$Python='D:\Praat-work\venv-ai-modern\Scripts\python.exe',[switch]$DenyLogs)
$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
$taskAssembly=[Reflection.Assembly]::Load([IO.File]::ReadAllBytes((Join-Path (Get-Location).Path 'AIPraat-paths.exe')))
$taskType=$taskAssembly.GetType('AIPraat.Setup.RuntimeDependencies')
$taskOriginals=@{}
try {
    if($DenyLogs) {
        foreach($taskFolder in @((Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'AIPraat/logs/runtime'),(Join-Path ([IO.Path]::GetTempPath()) 'AIPraat/logs/runtime'))) {
            [IO.Directory]::CreateDirectory($taskFolder) | Out-Null
            $taskOriginal=Get-Acl -LiteralPath $taskFolder
            $taskOriginals[$taskFolder]=$taskOriginal.GetSecurityDescriptorSddlForm([Security.AccessControl.AccessControlSections]::Access)
            $taskDenied=Get-Acl -LiteralPath $taskFolder
            $taskDenied.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new([Security.Principal.WindowsIdentity]::GetCurrent().User,[Security.AccessControl.FileSystemRights]::CreateFiles,[Security.AccessControl.AccessControlType]::Deny))
            Set-Acl -LiteralPath $taskFolder -AclObject $taskDenied
        }
    }
    $taskProbe=$taskType.GetMethod('Probe').Invoke($null,@($Python,'cpu',$false,$null))
    if(-not $taskProbe.Valid -or -not $taskProbe.Report.completed -or $taskProbe.Report.items.Length -ne 13){throw $taskProbe.Message}
    if($DenyLogs -and ($taskProbe.Report.log_path -ne '' -or -not $taskProbe.Message.Contains('日志未保存，不影响检测'))){throw 'Expected a complete no-log report under denied log access.'}
    if(-not $taskProbe.Report.log_path){Write-Output $taskProbe.Message}
} finally {
    foreach($taskFolder in $taskOriginals.Keys) {
        $taskRestored=[Security.AccessControl.DirectorySecurity]::new()
        $taskRestored.SetSecurityDescriptorSddlForm($taskOriginals[$taskFolder],[Security.AccessControl.AccessControlSections]::Access)
        Set-Acl -LiteralPath $taskFolder -AclObject $taskRestored
    }
}
Write-Output ('PASS delivered AIPraat-paths.exe Python runtime '+$taskProbe.Report.items.Length+'/13 ready; log='+$taskProbe.Report.log_path)
$taskFfmpeg=$taskAssembly.GetType('AIPraat.Setup.FfmpegDependencies').GetMethod('Probe').Invoke($null,@(''))
if(-not $taskFfmpeg.Ready){throw $taskFfmpeg.Message}
Write-Output ('PASS delivered AIPraat-paths.exe auto-resolves FFmpeg and decodes OGG to WAV: '+$taskFfmpeg.Executable)
