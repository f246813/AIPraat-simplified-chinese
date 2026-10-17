$ErrorActionPreference='Stop'
Add-Type -AssemblyName System.Windows.Forms,System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$displayRoot=Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$displayPython=Join-Path $PSScriptRoot 'runtime-online-env/Scripts/python.exe'
$displayFlags=[Reflection.BindingFlags]'Instance,NonPublic'
foreach($displayHost in @('paths','install')) {
    $displayAssembly=[Reflection.Assembly]::LoadFile((Join-Path $displayRoot "AIPraat-$displayHost.exe"))
    if($displayHost -eq 'paths') {
        $displaySettings=$displayAssembly.GetType('AIPraat.Setup.PathSettings').GetConstructors()[0].Invoke([object[]]@([string](Join-Path $PSScriptRoot 'display-ai'),[string](Join-Path $PSScriptRoot 'display-ai/ai_config.json'),[string](Join-Path $PSScriptRoot 'display-profile'),[string]$displayPython))
        $displayForm=$displayAssembly.GetType('AIPraat.Setup.PathSettingsForm').GetConstructors()[0].Invoke(@($displaySettings,'',0))
        $displayNote='note'
    } else {
        $displayDefaults=[Activator]::CreateInstance($displayAssembly.GetType('AIPraat.Setup.InstallRequest'))
        $displayDefaults.InstallDirectory=Join-Path $PSScriptRoot 'display-install'
        $displayForm=$displayAssembly.GetType('AIPraat.Setup.WizardForm').GetConstructors()[0].Invoke(@($displayDefaults,$null))
        $displayNote='prerequisiteNote'
    }
    try {
        $displayForm.Show()
        if($displayHost -eq 'install') {
            $displayForm.Controls.Find('Next',$true)[0].PerformClick()
            $displayForm.Controls.Find('PythonPath',$true)[0].Text=$displayPython
        }
        $displayWatch=[Diagnostics.Stopwatch]::StartNew()
        $displayCaptured=$false
        while($displayWatch.ElapsedMilliseconds -lt 90000) {
            [Windows.Forms.Application]::DoEvents()
            $displayText=$displayForm.GetType().GetField($displayNote,$displayFlags).GetValue($displayForm).Text
            if(-not $displayCaptured -and $displayText -like '*：PyTorch') {
                $displayButton=$displayForm.Controls.Find('ConfigureRuntime',$true)[0]
                $displayBrowse=$displayForm.Controls.Find('BrowsePythonPath',$true)[0]
                $displaySummary=$displayButton.Parent.Controls | Where-Object {$_ -is [Windows.Forms.Label]} | Select-Object -First 1
                Write-Output "$displayHost status: $displayText"
                Write-Output "$displayHost summary: $($displaySummary.Text)"
                Write-Output "$displayHost configure: $($displayButton.Size); right=$($displayButton.PointToScreen([Drawing.Point]::new($displayButton.Width,0)).X); browse-right=$($displayBrowse.PointToScreen([Drawing.Point]::new($displayBrowse.Width,0)).X)"
                $displayBitmap=[Drawing.Bitmap]::new($displayForm.Width,$displayForm.Height)
                try {$displayForm.DrawToBitmap($displayBitmap,[Drawing.Rectangle]::new([Drawing.Point]::Empty,$displayForm.Size));$displayBitmap.Save((Join-Path $PSScriptRoot "runtime-display-$displayHost.png"))}finally{$displayBitmap.Dispose()}
                $displayCaptured=$true
            }
            if($displayForm.GetType().GetField('probe',$displayFlags).GetValue($displayForm)){break}
            Start-Sleep -Milliseconds 20
        }
        Write-Output "$displayHost final: $displayText; progress captured=$displayCaptured"
    } finally {$displayForm.Close();$displayForm.Dispose()}
}
