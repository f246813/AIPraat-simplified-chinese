<#
    verify-native-appearance.ps1

    Verifies the Windows editor appearance scheme in real editor windows: the translucent
    shell class, whole-window opacity, the 外观 entry with the panel that one window owns, the
    two spectrogram schemes with the analysis markings, the keyboard and dismissal behaviour of
    that panel, and the completely opaque image that the clipboard commands and the picture
    export produce while the window itself is translucent.

    Evidence: checks.json, menus-*.json, state.json and the PNG captures in -Stage.
    The script drives the windows with real mouse and keyboard input and reads back what the
    window manager, the compositor and the clipboard report; no check rests on the source text.
#>
param(
    [Parameter(Mandatory = $true)][string] $Executable,
    [string] $Stage = (Join-Path (Split-Path -Parent $PSScriptRoot) 'test-records/native-appearance'),
    [ValidateSet('launch','menus','shell','panel','opacity','drag','colour','overlay','selection','isolation',
                 'keyboard','dismiss','clipboard','picture','grid','interact','analysis','teardown')]
    [string] $Phase = 'launch',
    [int] $WaitSeconds = 180
)
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSEdition -eq 'Core') {
    & "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath @PSBoundParameters
    exit $LASTEXITCODE
}
Add-Type -AssemblyName System.Windows.Forms
Add-Type -Path (Join-Path $PSScriptRoot 'NativeWindowProbe.cs') -ReferencedAssemblies 'System.Drawing'

$stage = (New-Item -ItemType Directory -Force -Path $Stage).FullName
$stateFile = Join-Path $stage 'state.json'
$checksFile = Join-Path $stage 'checks.json'
$WORK_AREA = [System.Windows.Forms.SystemInformation]::WorkingArea
$script:checkOrder = @()
$script:checkMap = @{}
# Windows PowerShell cannot convert a generic list with @(): the binder that does it fails, so a list
# is copied with its own ToArray and counted with its own Count. Piping a list is fine.
function Remember-Check($entry) {
    if (-not $entry -or -not ($entry.name -is [string])) { return }
    if (-not $script:checkMap.ContainsKey($entry.name)) { $script:checkOrder += $entry.name }
    $script:checkMap[$entry.name] = $entry
}
if (Test-Path $checksFile) {
    # A previous run may have written the array itself or one object, so walk both shapes.
    foreach ($outer in @(Get-Content -LiteralPath $checksFile -Raw | ConvertFrom-Json)) {
        foreach ($entry in @($outer)) { Remember-Check $entry }
    }
}
function All-Checks { @($script:checkOrder | ForEach-Object { $script:checkMap[$_] } | Where-Object { $null -ne $_ }) }
function Add-Check {
    param([string] $Name, [bool] $Pass, $Detail)
    Remember-Check ([pscustomobject] @{ name = $Name; pass = $Pass; detail = $Detail })
    if (-not $Pass) { Log-Note ("  FAIL {0}  {1}" -f $Name, ($Detail | ConvertTo-Json -Compress -Depth 6)) }
}
function Save-Checks {
    $items = @(foreach ($c in (All-Checks)) { [pscustomobject] @{ name = [string] $c.name; pass = [bool] $c.pass; detail = $c.detail } })
    if ($items.Count -eq 0) { Set-Content -LiteralPath $checksFile -Value '[]' -Encoding UTF8; return }
    ($items | ConvertTo-Json -Depth 12) | Set-Content -LiteralPath $checksFile -Encoding UTF8
}
# The console may not render Chinese, so the same line also goes to a UTF-8 log.
function Log-Note([string] $text) {
    Write-Host $text
    Add-Content -LiteralPath (Join-Path $stage 'diagnostics.log') -Value $text -Encoding UTF8
}

# Strings come from code points, so this file does not depend on its own encoding.
function U([int[]] $codePoints) { -join ($codePoints | ForEach-Object { [char] $_ }) }
$T_APPEARANCE = U 0x5916,0x89C2                                              # 外观
$T_MIST = U 0x96FE,0x84DD,0x7070                                             # 雾蓝灰
$T_CLAY = U 0x9676,0x571F,0x7070                                             # 陶土灰
$T_ORIGINAL = U 0x539F,0x7248                                                # 原版
$T_OPACITY = U 0x4E0D,0x900F,0x660E,0x5EA6                                   # 不透明度
$T_COPY_SCREENSHOT = U 0x590D,0x5236,0x53D1,0x97F3,0x622A,0x56FE             # 复制发音截图
$T_COPY_QUESTION = U 0x590D,0x5236,0x63D0,0x95EE,0x6587,0x5B57               # 复制提问文字
$T_COPY_BOTH = U 0x590D,0x5236,0x622A,0x56FE,0x548C,0x63D0,0x95EE,0xFF08,0x56FE,0x6587,0xFF09
$T_PROMPT_HEAD = U 0x8FD9,0x662F,0x53D1,0x97F3                               # 这是发音
$T_PROMPT_TAIL = U 0xFF0C,0x5E2E,0x6211,0x770B,0x770B,0x662F,0x5426,0x6807,0x51C6
$T_PAINT_SPECTROGRAM = U 0x7ED8,0x5236,0x53EF,0x89C1,0x58F0,0x8C31,0x56FE    # 绘制可见声谱图
$T_ZOOM_TO_SELECTION = U 0x7F29,0x653E,0x5230,0x9009,0x62E9,0x90E8,0x5206    # 缩放到选择部分
$T_ZOOM_BACK = U 0x8FD4,0x56DE,0x4E0A,0x4E00,0x6B21,0x7F29,0x653E           # 返回上一次缩放
$T_PLAY_WINDOW = U 0x64AD,0x653E,0x89C6,0x7A97,0x5185,0x58F0,0x97F3          # 播放视窗内声音
$T_PLAY_OR_STOP = U 0x64AD,0x653E,0x6216,0x505C,0x6B62                       # 播放或停止
$T_INTERRUPT = U 0x4E2D,0x65AD,0x64AD,0x653E                                 # 中断播放

$CHIP_MIST = 101; $CHIP_CLAY = 102; $CHIP_ORIGINAL = 103; $SLIDER_ID = 110; $PERCENT_ID = 111
$VK_LEFT = 37; $VK_RIGHT = 39; $VK_HOME = 36; $VK_END = 35
$VK_NEXT = 34; $VK_PRIOR = 33
$VK_TAB = 9; $VK_RETURN = 13; $VK_ESCAPE = 27; $VK_SPACE = 32

# The design colours as eight-bit channels, in the order Colour-Of looks them up.
$COLOUR_NAMES = @('mist','clay','pulse','pitch','formant','intensity','waveform','coordinate','cursor','selection','grid','tick','legacyHighlight')
$COLOUR_FLAT = [int[]] (
    113,136,155,   # 71889B mist blue grey: the darkest level of that spectrogram ramp
    159,126,113,   # 9F7E71 clay grey: the darkest level of the other ramp
    79,96,84,      # 4F6054 pulse core
    117,97,63,     # 75613F pitch core
    49,95,91,      # 315F5B formant core
    89,97,107,     # 59616B intensity
    63,70,77,      # 3F464D waveform
    52,58,64,      # 343A40 coordinate and menu text
    74,80,86,      # 4A5056 cursor
    213,217,220,   # D5D9DC selection fill
    229,232,234,   # E5E8EA zero line and grid
    115,123,130,   # 737B82 auxiliary ticks
    255,210,210    # FFD2D2 the highlight the annotation rows have always used
)
$COLOUR_INDEX = @{}
for ($i = 0; $i -lt $COLOUR_NAMES.Length; $i++) { $COLOUR_INDEX[$COLOUR_NAMES[$i]] = $i }
function Colour-Of($analysis, [string] $key) {
    if (-not $analysis) { return -1 }
    $index = $COLOUR_INDEX[$key]
    $counts = @($analysis.counts)
    if ($index -ge $counts.Count) { throw "Colour-Of $key needs colour $index but the analysis counted $($counts.Count)" }
    [int64] $counts[$index]
}
# Anti-aliased edges and interpolated ramp levels can put a handful of pixels inside the tolerance
# box of a colour that was never selected. Both schemes are white-based ramps, so the count of their
# darkest tone follows the window size: a scheme is present where its own dark tone stands and the
# other scheme's dark tone does not, in the editor image and in the clipboard copy alike.
function Scheme-Marks-The-Image($analysis, [string] $key, [string] $otherKey) {
    $present = Colour-Of $analysis $key
    $other = Colour-Of $analysis $otherKey
    @{ present = $present; other = $other; drawn = (($present -gt 40) -and ($present -gt 4 * $other)) }
}
# Contrast measured on the rendered pixels: average every pixel the image actually drew in the
# named colour, then take that colour against the white canvas.
function Measured-Contrast([string] $file, [string] $key) {
    $index = $COLOUR_INDEX[$key]
    $mean = [N]::MeanOfColour($file, $COLOUR_FLAT[3 * $index], $COLOUR_FLAT[3 * $index + 1], $COLOUR_FLAT[3 * $index + 2], 6)
    [int64] $pixels = [int64] $mean.count
    [pscustomobject] [ordered] @{
        pixels = $pixels
        colour = (@($mean.red, $mean.green, $mean.blue) -join ',')
        onWhite = $(if ($pixels -lt 40) { 0 } else { [N]::ContrastRatio($mean.red, $mean.green, $mean.blue, 255, 255, 255) })
        drawn = ($pixels -ge 40)
    }
}
function Design-Contrast([string] $key) {
    $index = $COLOUR_INDEX[$key]
    [N]::ContrastRatio($COLOUR_FLAT[3 * $index], $COLOUR_FLAT[3 * $index + 1], $COLOUR_FLAT[3 * $index + 2], 255, 255, 255)
}
# Half a step goes up, the way MulDiv and the alpha byte in the editor do it, so a percentage that
# lands exactly between two bytes is expected where the window really put it.
function Expected-Alpha([int] $percent) { [int] [Math]::Round(255.0 * $percent / 100.0, [System.MidpointRounding]::AwayFromZero) }
function Logical([int] $pixels, [int] $dpi) { [int] [Math]::Round($pixels * $dpi / 96.0, [System.MidpointRounding]::AwayFromZero) }
function HandleOf([string] $value) { [IntPtr] [int64] $value }

function Read-AppState {
    if (-not (Test-Path $stateFile)) { throw "no state.json in $stage; run the launch phase first" }
    $state = Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json
    $process = Get-Process -Id ([int] $state.processId) -ErrorAction SilentlyContinue
    if (-not $process) { throw "Praat is not running any more (process $($state.processId)); run the launch phase again" }
    $state
}
function Window-Of($state, [string] $role) {
    $found = @($state.windows | Where-Object { $_.role -eq $role })
    if ($found.Count -eq 0) { throw "no window recorded for role $role" }
    HandleOf ([string] $found[0].handle)
}
function Top-Of-Process($state) { [N]::TopLevel([int64] $state.processId) }
function Find-By-Class($state, [string] $pattern) { @(Top-Of-Process $state) | Where-Object { [N]::Class($_) -like $pattern } }
function Panel-Window($state) {
    $found = @(Find-By-Class $state 'PraatAppearancePanel*')
    if ($found.Count -eq 0) { return [IntPtr]::Zero }
    $found[0]
}
function Drawing-Area([IntPtr] $root) {
    $found = @([N]::Children($root)) | Where-Object { [N]::Class($_) -like 'PraatDrawingArea*' } | Select-Object -First 1
    if (-not $found) { return [IntPtr]::Zero }
    $found
}
function Bring-Out([IntPtr] $root) {
    for ($attempt = 0; $attempt -lt 3; $attempt++) {
        $null = [N]::ForceForeground($root)
        [N]::BringToTop($root)
        Start-Sleep -Milliseconds 250
        if ([N]::GetForeground() -eq $root) { return }
    }
    throw "Could not foreground the editor: $([N]::Text($root))"
}
function Close-AnyPanel($state) {
    for ($try = 0; $try -lt 4; $try++) {
        $panel = Panel-Window $state
        if ($panel -eq [IntPtr]::Zero -or -not [N]::Visible($panel)) { return }
        $null = [N]::ForceForeground($panel)
        Start-Sleep -Milliseconds 120
        [N]::KeyPress($VK_ESCAPE)
        Start-Sleep -Milliseconds 300
    }
}
function Wait-For-Calm($state) {
    for ($try = 0; $try -lt 25; $try++) {
        if (@(Find-By-Class $state 'PraatPronunciationCopiedToast*').Count -eq 0) { return }
        Start-Sleep -Milliseconds 200
    }
}

function Bar-Entries([IntPtr] $root) {
    $bar = [N]::MenuOf($root)
    if ($bar -eq [IntPtr]::Zero) { return @() }
    0..([N]::MenuCount($bar) - 1) | ForEach-Object { [N]::MenuStringAt($bar, $_) }
}
function Menu-Tree([IntPtr] $root) {
    $bar = [N]::MenuOf($root)
    $tree = @()
    if ($bar -eq [IntPtr]::Zero) { return $tree }
    for ($position = 0; $position -lt [N]::MenuCount($bar); $position++) {
        $sub = [N]::MenuSubAt($bar, $position)
        $items = @()
        if ($sub -ne [IntPtr]::Zero) {
            for ($item = 0; $item -lt [N]::MenuCount($sub); $item++) {
                $items += [pscustomobject] @{ position = $item; text = [N]::MenuStringAt($sub, $item); id = [int64] [N]::MenuIdAt($sub, $item) }
            }
        }
        $tree += [pscustomobject] @{ position = $position; text = [N]::MenuStringAt($bar, $position);
                id = [int64] [N]::MenuIdAt($bar, $position); hasSubMenu = ($sub -ne [IntPtr]::Zero); items = $items }
    }
    $tree
}
# The appearance entry is the bar entry that is a command of its own: no drop-down, but an id.
function Appearance-Entry([IntPtr] $root) {
    $bar = [N]::MenuOf($root)
    if ($bar -eq [IntPtr]::Zero) { return $null }
    for ($position = 0; $position -lt [N]::MenuCount($bar); $position++) {
        if ([N]::MenuSubAt($bar, $position) -eq [IntPtr]::Zero -and [N]::MenuIdAt($bar, $position) -gt 0) {
            return [pscustomobject] @{ position = $position; text = [N]::MenuStringAt($bar, $position); id = [int64] [N]::MenuIdAt($bar, $position) }
        }
    }
    $null
}
function Find-Command([IntPtr] $root, [string[]] $patterns) {
    foreach ($entry in @(Menu-Tree $root)) {
        foreach ($item in $entry.items) {
            foreach ($pattern in $patterns) {
                if ($item.text -like $pattern) {
                    return @{ id = [int] $item.id; bar = $entry.text; item = $item.text; matched = $pattern }
                }
            }
        }
    }
    $null
}
function Command-Id($state, [string] $role, [string[]] $patterns, [string] $what) {
    $found = Find-Command (Window-Of $state $role) $patterns
    if (-not $found) { throw "role $role has no $what command (patterns: $($patterns -join ' | '))" }
    $found
}

function Panel-Control($panel, [int] $identifier) {
    $found = @([N]::Children($panel)) | Where-Object { [N]::ControlId($_) -eq $identifier } | Select-Object -First 1
    if (-not $found) { return [IntPtr]::Zero }
    $found
}
function Readout($panel) { [N]::Text((Panel-Control $panel $PERCENT_ID)) }
function Open-Panel($state, [IntPtr] $root) {
    Close-AnyPanel $state
    $entry = Appearance-Entry $root
    if (-not $entry) { throw "the window has no command-only menu-bar entry" }
    $rectangle = [N]::MenuRectOnScreen($root, [N]::MenuOf($root), $entry.position)
    $spotX = [int] (($rectangle[0] + $rectangle[2]) / 2)
    $spotY = [int] (($rectangle[1] + $rectangle[3]) / 2)
    # A bar click can land while another window is still taking the foreground away, and the panel
    # goes back with the deactivation. Clicking again is the same action the user would repeat; the
    # foreground left behind on every failed attempt is recorded so the race stays visible.
    $panel = [IntPtr]::Zero
    $attempts = 0
    while ($panel -eq [IntPtr]::Zero -and $attempts -lt 3) {
        $attempts ++
        Bring-Out $root
        [N]::Click($spotX, $spotY)
        Start-Sleep -Milliseconds 500
        $panel = Panel-Window $state
        if ($panel -eq [IntPtr]::Zero) {
            $front = [N]::GetForeground()
            Log-Note ("  diagnostic bar click attempt $attempts left no panel: entry [$($entry.text)] at $spotX,$spotY, " +
                "foreground $(if ([N]::Alive($front)) { [N]::Text($front) + ' [' + [N]::Class($front) + '] ' + (@([N]::Rect($front)) -join ',') } else { 'none' }), " +
                "root alive $([N]::Alive($root)), root is foreground $([N]::GetForeground() -eq $root)")
        }
    }
    @{ entry = $entry; rectangle = $rectangle; panel = $panel; attempts = $attempts }
}
function Centre($window) {
    $rectangle = [N]::Rect($window)
    @([int] (($rectangle[0] + $rectangle[2]) / 2), [int] (($rectangle[1] + $rectangle[3]) / 2))
}
function Click-Chip($state, [IntPtr] $root, [int] $identifier) {
    $opened = Open-Panel $state $root
    if ($opened.panel -eq [IntPtr]::Zero) { throw "the appearance panel did not open" }
    $spot = Centre (Panel-Control $opened.panel $identifier)
    [N]::Click($spot[0], $spot[1])
    Start-Sleep -Milliseconds 450
    Close-AnyPanel $state
    Wait-For-Calm $state
}
function Set-Opacity($state, [IntPtr] $root, [int] $percent) {
    $opened = Open-Panel $state $root
    if ($opened.panel -eq [IntPtr]::Zero -or -not [N]::Visible($opened.panel)) {
        throw "the panel is not visible while setting the opacity to $percent"
    }
    $slider = Panel-Control $opened.panel $SLIDER_ID
    # SetFocus from another process does nothing, so the pointer gives the slider the keyboard and
    # the keys below put the wanted percentage on it.
    Focus-Slider $slider
    [N]::KeyPress($(if ($percent -le 60) { $VK_HOME } else { $VK_END }))
    Start-Sleep -Milliseconds 200
    for ($guard = 0; $guard -lt 400; $guard++) {
        $position = [N]::TrackbarPosition($slider)
        if ($position -eq $percent) { break }
        [N]::KeyPress($(if ($position -lt $percent) { $VK_RIGHT } else { $VK_LEFT }))
        Start-Sleep -Milliseconds 60
    }
    $position = [N]::TrackbarPosition($slider)
    Close-AnyPanel $state
    Start-Sleep -Milliseconds 150
    $position
}
# The keyboard goes to the control the pointer was pressed on, and pressing the slider is how a user
# gives it that keyboard. Anywhere inside the row works: a press on the groove asks for the position
# under it and the keys afterwards decide the value.
function Focus-Slider($slider) {
    $row = [N]::ClientRectOnScreen($slider)
    [N]::Click([int] (($row[0] + $row[2]) / 2), [int] [Math]::Round(($row[1] + $row[3]) / 2.0))
    Start-Sleep -Milliseconds 250
}
# The circle as the desktop really painted it. The probe reads the middle row of the slider and
# finds the only white stretch that has dark pixels on both sides, which is the inside of the
# handle. This answers only while the window is opaque: below one hundred per cent every pixel of
# the row is blended with whatever stands behind the window.
function Painted-Handle($slider) {
    $painted = [N]::PaintedHandle($slider)
    if (-not $painted) {
        throw 'the painted handle cannot be read: the window has to be opaque and nothing may lie over the row'
    }
    $client = [N]::ClientRectOnScreen($slider)
    @{ centre = @([int] $painted[0], [int] [Math]::Round(($client[1] + $client[3]) / 2.0));
       radius = [int] $painted[1]; interior = [int] $painted[2]; row = $client }
}
# The centre the mapping gives a percentage: one straight line, read in the direction that asks for
# a pointer and in the direction that measures the distance a pointer is from what it got.
function Centre-For-Value($calibration, [int] $percent) {
    @([int] [Math]::Round($calibration.left + $calibration.pixelsPerPercent * ($percent - $calibration.minimum)),
      $calibration.centreY)
}
function Pointer-For-Percent($calibration, [int] $percent) { Centre-For-Value $calibration $percent }
# The row answers the pointer once it has caught up with it: two reads that agree are that answer,
# and nothing before them is taken as what the pointer asked for.
function Wait-For-Stable-Position($slider) {
    $previous = [N]::TrackbarPosition($slider)
    for ($try = 0; $try -lt 12; $try++) {
        Start-Sleep -Milliseconds 40
        $next = [N]::TrackbarPosition($slider)
        if ($next -eq $previous) { return $next }
        $previous = $next
    }
    throw 'the row kept changing its percentage and never settled on one'
}
# The mapping comes from the control's own answers rather than from a layout formula: the row is
# clicked at a series of pointer positions, each click is left to settle, and the percentage the row
# reports for that position is one sample. The straight line those samples lie on is the line every
# drag afterwards follows. Nothing is read from the painted circle here, so the probing works at any
# opacity.
function Slider-Calibration($root, $panel, $slider) {
    $client = [N]::ClientRectOnScreen($slider)
    $minimum = [N]::TrackbarMinimum($slider); $maximum = [N]::TrackbarMaximum($slider)
    $start = [N]::TrackbarPosition($slider)
    $centreY = [int] [Math]::Round(($client[1] + $client[3]) / 2.0)
    # A press that lands on the handle holds it where it is rather than asking for the position under
    # it, so every probe starts by putting the handle at the end of the row away from that probe: the
    # click that is measured then cannot be mistaken for a grab.
    $pairs = New-Object 'System.Collections.Generic.List[object]'
    $probes = 13
    for ($probe = 1; $probe -le $probes; $probe++) {
        $x = [int] [Math]::Round($client[0] + ($client[2] - $client[0]) * $probe / ($probes + 1))
        $park = $(if ($x -lt (($client[0] + $client[2]) / 2)) { $client[2] - 2 } else { $client[0] + 2 })
        [N]::Click($park, $centreY)
        $null = Wait-For-Stable-Position $slider
        [N]::Click($x, $centreY)
        $pairs.Add([pscustomobject] @{ x = $x; value = (Wait-For-Stable-Position $slider) })
    }
    $interior = @($pairs | Where-Object { $_.value -gt $minimum -and $_.value -lt $maximum })
    if (@($interior).Count -lt 8) {
        throw "the row does not answer the pointer positions on it with a percentage ($(@($interior).Count) inside the range)"
    }
    $count = [double] @($interior).Count
    $sumValue = 0.0; $sumPointer = 0.0; $sumSquares = 0.0; $sumProduct = 0.0
    foreach ($pair in $interior) {
        $value = [double] $pair.value; $pointer = [double] $pair.x
        $sumValue += $value; $sumPointer += $pointer
        $sumSquares += ($value * $value); $sumProduct += ($value * $pointer)
    }
    $denominator = ($count * $sumSquares - $sumValue * $sumValue)
    $pixelsPerPercent = ($count * $sumProduct - $sumValue * $sumPointer) / $denominator
    if ($pixelsPerPercent -le 0) { throw 'the percentage mapping of this row does not run to the right' }
    # The fit answers "where is the pointer for a percentage", so its intercept is moved onto the
    # first percentage of the range: left is the pointer the row maps to the minimum value.
    $left = ($sumPointer - $pixelsPerPercent * $sumValue) / $count + $pixelsPerPercent * $minimum
    $worstResidual = 0.0; $worstValueError = 0
    foreach ($pair in $interior) {
        $residual = [Math]::Abs($left + $pixelsPerPercent * ([double] $pair.value - $minimum) - [double] $pair.x)
        if ($residual -gt $worstResidual) { $worstResidual = $residual }
        $valueError = [Math]::Abs([int] [Math]::Round(([double] $pair.x - $left) / $pixelsPerPercent) + $minimum - [int] $pair.value)
        if ($valueError -gt $worstValueError) { $worstValueError = $valueError }
    }
    $calibration = @{ minimum = $minimum; maximum = $maximum; left = [Math]::Round($left, 3);
        right = [Math]::Round($left + $pixelsPerPercent * ($maximum - $minimum), 3); centreY = $centreY;
        travel = [Math]::Round($pixelsPerPercent * ($maximum - $minimum), 3);
        pixelsPerPercent = [Math]::Round($pixelsPerPercent, 4);
        halfStep = [Math]::Round($pixelsPerPercent / 2.0, 3); bound = [Math]::Round($pixelsPerPercent / 2.0 + 1.0, 3);
        dpi = [N]::Dpi($slider); row = $client; startedAt = $start; probed = $pairs.Count;
        mappingSamples = @($interior).Count; mappingWorstResidual = [Math]::Round($worstResidual, 3);
        mappingWorstValueError = $worstValueError; probes = $pairs.ToArray() }
    # The value the user had goes back through the mapping that was just measured, with the keys to
    # land on the whole percentage.
    $back = Pointer-For-Percent $calibration $start
    [N]::Click($back[0], $calibration.centreY)
    for ($guard = 0; $guard -lt 20; $guard++) {
        $position = [N]::TrackbarPosition($slider)
        if ($position -eq $start) { break }
        [N]::KeyPress($(if ($position -lt $start) { $VK_RIGHT } else { $VK_LEFT }))
        Start-Sleep -Milliseconds 60
    }
    $calibration.restored = [N]::TrackbarPosition($slider)
    $calibration
}
# What the control, the read-out, the accessible name and the compositor report at one instant.
function Opacity-Feedback($root, $panel, $slider, [int] $value) {
    $layered = [bool] [N]::IsLayered($root)
    $alpha = [N]::Alpha($root)
    $readout = Readout $panel
    $name = [N]::Text($slider)
    $expectedAlpha = $(if ($value -ge 100) { -1 } else { Expected-Alpha $value })
    $digits = @([regex]::Matches($name, '\d+') | ForEach-Object { [int] $_.Value })
    [pscustomobject] @{
        value = $value; readout = $readout; alpha = $alpha; layered = $layered; expectedAlpha = $expectedAlpha
        accessibleName = $name; accessibleValue = $(if ($digits.Count) { $digits[0] } else { -1 })
        matches = $((((-not $layered) -and $value -ge 100) -or ($layered -and $alpha -eq $expectedAlpha)) -and
            ($readout -eq ($value.ToString() + '%')) -and ($digits.Count -ne 0) -and ($digits[0] -eq $value))
    }
}
# One instant of a held drag: where the pointer is, what the control answers, how far that pointer
# sits from the centre the row's own mapping gives the percentage it answered with, and what the
# desktop compositor was told.
function Drag-Sample($root, $panel, $slider, $calibration, [int] $pointerX, [string] $stageNote) {
    $value = [N]::TrackbarPosition($slider)
    $feedback = Opacity-Feedback $root $panel $slider $value
    $centre = Centre-For-Value $calibration $value
    [pscustomobject] @{
        pointerX = $pointerX; mappingCentreX = $centre[0]
        distance = [Math]::Abs([double] $centre[0] - $pointerX); note = $stageNote
        value = $feedback.value; readout = $feedback.readout; alpha = $feedback.alpha
        layered = $feedback.layered; expectedAlpha = $feedback.expectedAlpha
        accessibleValue = $feedback.accessibleValue; feedbackMatches = $feedback.matches
    }
}
# Which window owns the pointer right now: while a drag is held this is the slider, however far
# outside its row or its panel the pointer has gone, and after the drag is over it is nothing.
function Capture-Checkpoint($state, $slider, [string] $stageNote, [bool] $held) {
    $capture = [N]::CapturedInProcess([int] $state.processId)
    $owns = ($capture -eq $slider)
    @{ note = $stageNote; capture = ([string] $capture); slider = ([string] $slider); holds = $owns;
       expected = $held; correct = ($owns -eq $held); panelVisible = [bool] [N]::Visible((Panel-Window $state)) }
}
# Nobody in that process holds the pointer any more: the drag is over and nothing was left hanging
# on the window manager.
function Capture-Is-Clear($state) { [N]::CapturedInProcess([int] $state.processId) -eq [IntPtr]::Zero }
# A held drag that moves the real pointer one device pixel at a time and samples at every step.
function Drag-Held-Sweep($root, $panel, $slider, $calibration, [int] $fromPercent, [int] $toPercent, [int] $pauseMilliseconds) {
    $from = Pointer-For-Percent $calibration $fromPercent
    $to = Pointer-For-Percent $calibration $toPercent
    [N]::Press($from[0], $from[1])
    $samples = New-Object 'System.Collections.Generic.List[object]'
    $x = $from[0]
    $direction = $(if ($to[0] -ge $from[0]) { 1 } else { -1 })
    while ($true) {
        $samples.Add((Drag-Sample $root $panel $slider $calibration $x 'pointer here'))
        if ($x -eq $to[0]) { break }
        $x += $direction
        [N]::MovePointer($x, $calibration.centreY)
        if ($pauseMilliseconds -gt 0) { Start-Sleep -Milliseconds $pauseMilliseconds }
    }
    [N]::ReleasePointer($to[0], $calibration.centreY)
    $samples.Add((Drag-Sample $root $panel $slider $calibration $to[0] 'after release'))
    @{ samples = $samples.ToArray(); pressX = $from[0]; endX = $to[0] }
}
# What a per-pixel sweep proved: one percent at most per pixel, always in one direction, every
# value in the span reached, no value held for longer than its own pixel width, and no pointer
# further from the centre its own percentage stands for than the quantisation allows.
function Sweep-Metrics($samples) {
    $values = @($samples | ForEach-Object { [int] $_.value })
    $distances = @($samples | ForEach-Object { [double] $_.distance })
    $direction = [Math]::Sign($values[-1] - $values[0])
    $largestStep = 0; $backwards = 0; $run = 1; $longestRun = 1
    for ($index = 1; $index -lt $values.Count; $index++) {
        $delta = $values[$index] - $values[$index - 1]
        if ([Math]::Abs($delta) -gt $largestStep) { $largestStep = [Math]::Abs($delta) }
        if ($delta -ne 0 -and $direction -ne 0 -and ([Math]::Sign($delta) -ne $direction)) { $backwards ++ }
        if ($delta -eq 0) { $run ++; if ($run -gt $longestRun) { $longestRun = $run } } else { $run = 1 }
    }
    $unique = @($values | Sort-Object -Unique)
    $span = $(if ($unique.Count) { $unique[-1] - $unique[0] + 1 } else { 0 })
    $mismatch = @($samples | Where-Object { -not $_.feedbackMatches } | ForEach-Object { "$($_.value):$($_.readout)/$($_.alpha)" })
    @{ samples = $values.Count; first = $values[0]; last = $values[-1]; moved = ($values[-1] -ne $values[0]);
       largestStep = $largestStep; backwards = $backwards; distinct = $unique.Count; span = $span;
       longestRunOfOneValue = $longestRun; maxDistance = ($distances | Measure-Object -Maximum).Maximum;
       feedbackMismatches = $mismatch.Count; feedbackExamples = @($mismatch | Select-Object -First 5) }
}
# How long the shell takes to put 100 % on or take it off: that step swaps the layered style and
# repaints the window, while every other percentage only changes the alpha byte. The held pointer
# has to be on the given percentage already; this moves it to the other one and watches.
function Time-Opacity-Edge($root, $slider, $calibration, [int] $fromPercent, [int] $toPercent) {
    $target = Pointer-For-Percent $calibration $toPercent
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    [N]::MovePointer($target[0], $calibration.centreY)
    $samples = New-Object 'System.Collections.Generic.List[object]'
    $settledAfter = -1.0
    while ($watch.Elapsed.TotalMilliseconds -lt 3000) {
        $value = [N]::TrackbarPosition($slider)
        $layered = [bool] [N]::IsLayered($root)
        $alpha = [N]::Alpha($root)
        $samples.Add([pscustomobject] @{ elapsedMs = [Math]::Round($watch.Elapsed.TotalMilliseconds, 1);
                value = $value; layered = $layered; alpha = $alpha })
        if (($value -eq $toPercent) -and ((-not $layered) -eq ($toPercent -ge 100))) {
            $settledAfter = [Math]::Round($watch.Elapsed.TotalMilliseconds, 1); break
        }
        Start-Sleep -Milliseconds 5
    }
    $watch.Stop()
    @{ from = $fromPercent; to = $toPercent; pointerX = $target[0]; settledAfterMilliseconds = $settledAfter;
       polls = $samples.Count; samples = $samples.ToArray() }
}
# What a big pointer jump is answered with: the values the control reports while the jump settles.
# A handle that chases the pointer walks through everything in between; one that keeps up changes
# once. The list of distinct values tells the two apart.
function Settle-After-Jump($slider, [int] $targetPercent, [int] $tolerance) {
    $watch = [System.Diagnostics.Stopwatch]::StartNew()
    $seen = New-Object 'System.Collections.Generic.List[object]'
    $settledAfter = -1.0
    while ($watch.Elapsed.TotalMilliseconds -lt 2000) {
        $value = [N]::TrackbarPosition($slider)
        if ($seen.Count -eq 0 -or [int] $seen[$seen.Count - 1] -ne $value) { $seen.Add($value) }
        if ([Math]::Abs($value - $targetPercent) -le $tolerance) {
            $settledAfter = [Math]::Round($watch.Elapsed.TotalMilliseconds, 1); break
        }
        Start-Sleep -Milliseconds 5
    }
    $watch.Stop()
    @{ target = $targetPercent; valuesSeen = $seen.ToArray(); distinct = @($seen | Sort-Object -Unique).Count;
       settledAfterMilliseconds = $settledAfter }
}

# The image the user would paste: the editor's own off-screen render of the drawing area.
function Get-EditorImage($state, [string] $role, [string] $name) {
    $root = Window-Of $state $role
    $command = Command-Id $state $role @("*$T_COPY_SCREENSHOT*", '*Copy pronunciation screenshot*') 'screenshot'
    $path = Join-Path $stage "image-$role-$name.png"
    if (Test-Path $path) { Remove-Item -LiteralPath $path -Force }
    Close-AnyPanel $state
    Wait-For-Calm $state
    Bring-Out $root
    $null = [N]::ClearClipboard()
    [N]::SendCommand($root, $command.id)
    $deadline = (Get-Date).AddSeconds(10)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 400
        if ([N]::ClipboardSavePng($path)) { break }
    }
    Wait-For-Calm $state
    if (-not (Test-Path $path)) {
        Log-Note ("  diagnostic {0}: bar [{1}] item [{2}] matched [{3}] id {4}, formats [{5}]" -f $name, $command.bar, $command.item,
            $command.matched, $command.id,
            (([N]::ClipboardFormats() | ForEach-Object { [N]::ClipboardFormatName($_) + '#' + $_ }) -join ', '))
        return $null
    }
    $analysis = [N]::AnalysePng($path, $COLOUR_FLAT, 6)
    $analysis | Add-Member -NotePropertyName file -NotePropertyValue $path -PassThru -Force
}
function Get-ScreenBand([IntPtr] $drawing, [string] $name, [IntPtr] $clipWindow = [IntPtr]::Zero) {
    $canvas = [N]::ClientRectOnScreen($drawing)
    if ($clipWindow -ne [IntPtr]::Zero) {
        $clip = [N]::ClientRectOnScreen($clipWindow)
        $canvas = @([math]::Max($canvas[0], $clip[0]), [math]::Max($canvas[1], $clip[1]),
                    [math]::Min($canvas[2], $clip[2]), [math]::Min($canvas[3], $clip[3]))
    }
    $path = Join-Path $stage "screen-$name.png"
    if (Test-Path $path) { Remove-Item -LiteralPath $path -Force }
    $null = [N]::SaveScreenPng($canvas[0], $canvas[1], ($canvas[2] - $canvas[0]), ($canvas[3] - $canvas[1]), $path)
    $analysis = [N]::AnalysePng($path, $COLOUR_FLAT, 6)
    $analysis | Add-Member -NotePropertyName file -NotePropertyValue $path -PassThru -Force
}

# A solid lime panel that sits under a window: what the desktop shows behind a translucent editor.
function New-Lime-Backdrop([int] $x, [int] $y, [int] $width, [int] $height) {
    $backdrop = New-Object System.Windows.Forms.Form
    $backdrop.FormBorderStyle = [System.Windows.Forms.FormBorderStyle]::None
    $backdrop.StartPosition = [System.Windows.Forms.FormStartPosition]::Manual
    $backdrop.ShowInTaskbar = $false
    $backdrop.TopMost = $false
    $backdrop.BackColor = [System.Drawing.Color]::FromArgb(0, 255, 0)
    $backdrop.Bounds = New-Object System.Drawing.Rectangle($x, $y, $width, $height)
    $backdrop.Show()
    1..6 | ForEach-Object { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 100 }
    $backdrop
}
# Pure lime, plus the lime the eye sees through a white canvas at 20 %. No design colour comes within
# reach of either box, so a match can only come from the backdrop itself.
function Lime-In([string] $path) {
    $seen = [N]::AnalysePng($path, ([int[]] (0,255,0, 51,255,51)), 60)
    [int64] $seen.counts[0] + [int64] $seen.counts[1]
}

# What one of the three copy commands leaves on the clipboard: the PNG the user pastes, the bit the
# image consumers get, the formats that were written and the text that came with them.
function Grab-Copy($state, [string] $role, [int] $commandId, [string] $name) {
    $root = Window-Of $state $role
    $path = Join-Path $stage "clipboard-$name.png"
    if (Test-Path $path) { Remove-Item -LiteralPath $path -Force }
    Close-AnyPanel $state
    $null = [N]::ClearClipboard()
    Bring-Out $root
    [N]::SendCommand($root, $commandId)
    $deadline = (Get-Date).AddSeconds(10)
    $saved = $false
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 300
        $saved = [N]::ClipboardSavePng($path)
        if ($saved) { break }
    }
    Wait-For-Calm $state
    if (-not $saved) { throw "command $commandId left no image on the clipboard ($name)" }
    @{
        path = $path
        image = [N]::AnalysePng($path, $COLOUR_FLAT, 6)
        dib = [N]::ClipboardDib($COLOUR_FLAT, 6)
        formats = @([N]::ClipboardFormats() | ForEach-Object { [N]::ClipboardFormatName($_) })
        text = [N]::ClipboardText()
    }
}

function Dialogs($state) { @(Top-Of-Process $state) | Where-Object { [N]::Class($_) -eq '#32770' } }
function Dialog-Text($dialog) { (@([N]::Children($dialog)) | ForEach-Object { [N]::Text($_) }) -join ' ' }
function Clear-Dialogs($state) {
    $messages = @()
    for ($try = 0; $try -lt 5; $try++) {
        $open = @(Dialogs $state)
        if ($open.Count -eq 0) { break }
        foreach ($dialog in $open) {
            $messages += (Dialog-Text $dialog)
            $null = [N]::ForceForeground($dialog)
            Start-Sleep -Milliseconds 120
            [N]::KeyPress($VK_RETURN)
            Start-Sleep -Milliseconds 250
        }
    }
    $messages
}

switch ($Phase) {

'launch' {
    $executablePath = (Get-Item -LiteralPath $Executable).FullName
    $previousState = if (Test-Path $stateFile) { Get-Content -LiteralPath $stateFile -Raw | ConvertFrom-Json } else { $null }
    foreach ($stale in @(Get-Process -Name Praat -ErrorAction SilentlyContinue)) {
        $owned = $false
        try {
            $owned = $previousState -and $previousState.processStartedAt -and
                $stale.Id -eq $previousState.processId -and $stale.Path -eq $executablePath -and
                $stale.StartTime.ToUniversalTime().Ticks -eq $previousState.processStartedAt
        } catch { }
        if (-not $owned) { throw "Praat instance $($stale.Id) is already running; close it before native verification so --send cannot target that session" }
        $stale.Kill(); $stale.WaitForExit()
    }
    $wavPath = (Join-Path $stage 'appearance.wav') -replace '\\','/'
    $praatScript = @"
# Real editor windows for the appearance checks: two Sounds, a LongSound,
# a TextGrid that carries the sound and a TextGrid on its own.
snd = Create Sound from formula: "APPEARANCE-W1", 1, 0, 1.5, 22050, "0.22*sin(2*pi*115*x*(0.65+0.35*sin(2*pi*2.7*x)))+0.05*sin(2*pi*2300*x)+0.03*sin(2*pi*3800*x)"
selectObject: snd
Save as WAV file: "$wavPath"
grid = To TextGrid: "words", ""
selectObject: grid
Rename: "APPEARANCE-W4"
snd2 = Create Sound from formula: "APPEARANCE-W2", 1, 0, 1.5, 22050, "0.22*sin(2*pi*115*x*(0.65+0.35*sin(2*pi*2.7*x)))+0.05*sin(2*pi*2300*x)+0.03*sin(2*pi*3800*x)"
gridAlone = Create TextGrid: 0, 1.5, "words", ""
selectObject: gridAlone
Rename: "APPEARANCE-W5"
ls = Open long sound file: "$wavPath"
selectObject: ls
Rename: "APPEARANCE-W3"
selectObject: snd
View & Edit
editor: snd
    Show analyses: "yes", "yes", "yes", "yes", "yes", 10.0
    Spectrogram settings: 0, 5000, 0.005, 50
endeditor
selectObject: snd2
View & Edit
editor: snd2
    Show analyses: "yes", "yes", "yes", "yes", "yes", 10.0
    Spectrogram settings: 0, 5000, 0.005, 50
endeditor
selectObject: grid
plusObject: snd
View & Edit
editor: grid
    Show analyses: "yes", "no", "no", "no", "no", 10.0
    Spectrogram settings: 0, 5000, 0.005, 50
endeditor
selectObject: gridAlone
View & Edit alone
selectObject: ls
View
"@
    Set-Content -LiteralPath (Join-Path $stage 'launch.praat') -Value $praatScript -Encoding UTF8
    # A script named on the command line runs in batch, and batch cannot open an editor, so the
    # windows are created by sending the script to a running graphical instance.
    # A fresh preferences folder keeps the machine's own user buttons out of the menu structure
    # that this run inspects.
    $prefs = (New-Item -ItemType Directory -Force -Path (Join-Path $stage 'prefs')).FullName
    # This is the visible interactive application whose windows the probe exercises.
    $process = Start-Process -FilePath $executablePath -ArgumentList @('--no-plugins', "--pref-dir=$prefs") -PassThru
    $simple = @{ processId = $process.Id }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline) {
        if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) { throw "the graphical instance exited" }
        if (@(Top-Of-Process $simple).Count -gt 0) { break }
        Start-Sleep -Milliseconds 500
    }
    Start-Sleep -Milliseconds 1500
    $null = Start-Process -FilePath $executablePath -ArgumentList @('--send', '--FULL-TRUST', (Join-Path $stage 'launch.praat')) -WindowStyle Hidden -PassThru
    $wanted = @('W1','W2','W3','W4','W5')
    function Snapshot([int] $pid2) {
        @(Top-Of-Process @{ processId = $pid2 } | ForEach-Object {
            [pscustomobject] @{ handle = ([int64] $_.ToInt64()); class = [N]::Class($_); title = [N]::Text($_) }
        })
    }
    $windows = @()
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Milliseconds 1000
        if (-not (Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) { break }
        $windows = Snapshot $process.Id
        $missing = @($wanted | Where-Object { $role = $_; -not @($windows | Where-Object { $_.title -match ('APPEARANCE-' + $role) }).Count })
        if ($missing.Count -eq 0) { break }
    }
    Start-Sleep -Milliseconds 2500
    $windows = Snapshot $process.Id
    $errorTexts = @(Clear-Dialogs $simple)
    Add-Check 'launch.no_script_errors' ($errorTexts.Count -eq 0) ($errorTexts -join ' || ')
    $recorded = @()
    foreach ($role in $wanted) {
        $match = @($windows | Where-Object { $_.title -match ('APPEARANCE-' + $role) } | Select-Object -First 1)
        Add-Check ("launch.window_$role") ($match.Count -eq 1) $(if ($match.Count) { $match[0].title } else { 'not opened' })
        if ($match.Count -eq 0) { continue }
        $recorded += [pscustomobject] @{ role = $role; handle = $match[0].handle; class = $match[0].class; title = $match[0].title }
    }
    $other = @($windows | Where-Object { $_.class -eq 'PraatShell1 Praat' } | Select-Object -First 1)
    $listHandle = [IntPtr]::Zero
    if ($other.Count -gt 0) {
        $listHandle = [IntPtr] $other[0].handle
        $recorded += [pscustomobject] @{ role = 'list'; handle = $other[0].handle; class = $other[0].class; title = $other[0].title }
    }
    Add-Check 'launch.object_list' ($other.Count -eq 1) $(if ($other.Count) { $other[0].title } else { 'missing' })
    $dpi = if ($listHandle -ne [IntPtr]::Zero) { [N]::Dpi($listHandle) } else { 96 }
    @{ processId = $process.Id; processStartedAt = $process.StartTime.ToUniversalTime().Ticks;
       executable = $executablePath; sha256 = [N]::Sha256OfFile($executablePath);
       dpi = $dpi;
       stage = $stage; windows = $recorded; all = $windows } |
        ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $stateFile -Encoding UTF8
    Save-Checks
    Write-Host ("launched pid {0}, {1} recorded windows of {2} visible top-level windows" -f $process.Id, $recorded.Count, $windows.Count)
    $windows | ForEach-Object { Write-Host ("  {0,-28} {1}" -f $_.class, $_.title) }
}

'menus' {
    $state = Read-AppState
    $frontendTitle = U 0x524D,0x7AEF
    foreach ($role in @('W1','W2','W3','W4','W5','list')) {
        $root = Window-Of $state $role
        $tree = @(Menu-Tree $root)
        $tree | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath (Join-Path $stage "menus-$role.json") -Encoding UTF8
        $entry = Appearance-Entry $root
        if ($entry) {
            $frontend = @($tree | Where-Object { $_.text -eq $frontendTitle -or $_.text -eq 'Frontend' })
            if ($frontend.Count -gt 0) {
                Add-Check "menus.$role.appearance_after_frontend" `
                    (($frontend.Count -eq 1) -and ($entry.position -eq ($frontend[0].position + 1))) `
                    @{ bar = @($tree | ForEach-Object { $_.text }); appearancePosition = $entry.position }
            }
        }
        Write-Host ("{0}: {1} bar entries; command-only entry [{2}] id {3}" -f $role, $tree.Count,
            $(if ($entry) { $entry.text } else { '-' }), $(if ($entry) { $entry.id } else { 0 }))
    }
    Write-Host ('language check: ' + ((Bar-Entries (Window-Of $state 'W1')) -join ' | '))
    Save-Checks
}

'shell' {
    $state = Read-AppState
    $expect = @{ W1 = $true; W2 = $true; W3 = $true; W4 = $true; W5 = $false; list = $false }
    foreach ($role in @('W1','W2','W3','W4','W5','list')) {
        $root = Window-Of $state $role
        $class = [N]::Class($root)
        $entry = Appearance-Entry $root
        Add-Check "shell.$role" ((($class -match '^PraatLayeredShell') -eq $expect[$role]) -and `
                (([bool] $entry) -eq $expect[$role])) `
            @{ role = $role; class = $class; wantsAppearance = $expect[$role]; entry = $(if ($entry) { $entry.text } else { $null }); bar = @(Bar-Entries $root) }
        Add-Check "shell.$role.opaque_at_first" ((-not [N]::IsLayered($root)) -and ([N]::Alpha($root) -eq -1)) `
            @{ role = $role; layered = [N]::IsLayered($root); alpha = [N]::Alpha($root); dpi = [N]::Dpi($root) }
    }
    $sound = Window-Of $state 'W1'
    $entry = Appearance-Entry $sound
    Add-Check 'entry.text_is_appearance' ($entry.text -eq $T_APPEARANCE) @{ text = $entry.text }
    $drawing = Drawing-Area $sound
    Add-Check 'shell.drawing_area_survives' ((($drawing) -ne [IntPtr]::Zero) -and [N]::Visible($drawing)) `
        @{ class = [N]::Class($drawing); rect = (([N]::ClientRectOnScreen($drawing)) -join ',') }
    Save-Checks
}

'panel' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    if ($panel -eq [IntPtr]::Zero) { Add-Check 'panel.opens' $false @{ entry = $opened.entry }; Save-Checks; return }
    $rectangle = [N]::Rect($panel)
    $entryRectangle = $opened.rectangle
    $dpi = [N]::Dpi($root)
    $chips = @([N]::Children($panel)) | Where-Object { [N]::IsOwnerDraw($_) } | Sort-Object { ([N]::Rect($_))[0] }
    $slider = Panel-Control $panel $SLIDER_ID
    Add-Check 'panel.opens' ((([N]::Alive($panel))) -and ([N]::Text($panel) -eq $T_APPEARANCE)) `
        @{ title = [N]::Text($panel); class = [N]::Class($panel); entry = ($entryRectangle -join ','); panel = ($rectangle -join ',') }
    Add-Check 'panel.opaque' ((-not [N]::IsLayered($panel)) -and ([N]::Alpha($panel) -eq -1)) `
        @{ layered = [N]::IsLayered($panel); alpha = [N]::Alpha($panel); exStyle = ('0x' + ([int64] [N]::ExStyle($panel)).ToString('X')) }
    Add-Check 'panel.owned_by_this_editor' (([N]::Owner($panel)) -eq $root) `
        @{ owner = ([string] [N]::Owner($panel)); root = ([string] $root) }
    $chrome = @(Find-By-Class $state 'PraatAppearanceChrome')
    Add-Check 'panel.alpha_edge_layer_is_click_through' `
        (($chrome.Count -eq 1) -and ([N]::IsLayered($chrome[0])) -and ([N]::DoesNotActivate($chrome[0])) -and `
         (([N]::ExStyle($chrome[0]) -band 0x20) -ne 0) -and ([N]::Owner($chrome[0]) -eq $root)) `
        @{ count = $chrome.Count }
    Add-Check 'panel.anchored_below_entry' ($rectangle[1] -ge ($entryRectangle[3] - 2)) `
        @{ panelTop = $rectangle[1]; entryBottom = $entryRectangle[3] }
    Add-Check 'panel.inside_work_area' (($rectangle[0] -ge $WORK_AREA.Left) -and ($rectangle[2] -le $WORK_AREA.Right) -and `
            ($rectangle[1] -ge $WORK_AREA.Top) -and ($rectangle[3] -le $WORK_AREA.Bottom)) `
        @{ panel = ($rectangle -join ','); work = (@($WORK_AREA.Left, $WORK_AREA.Top, $WORK_AREA.Right, $WORK_AREA.Bottom) -join ',') }
    $width = $rectangle[2] - $rectangle[0]; $height = $rectangle[3] - $rectangle[1]
    Add-Check 'panel.design_size' (($width -eq (Logical 328 $dpi)) -and ($height -eq (Logical 248 $dpi))) `
        @{ width = $width; height = $height; dpi = $dpi; expected = @(328, 248) }
    Add-Check 'panel.three_chips' (@($chips).Count -eq 3) `
        @{ chips = (@($chips | ForEach-Object { [N]::ControlId($_) }) -join ',') }
    $chipRectangles = @($chips | ForEach-Object { , ([N]::Rect($_)) })
    Add-Check 'panel.chip_gap' (([int] $chipRectangles[1][0] - [int] $chipRectangles[0][2]) -eq (Logical 8 $dpi)) `
        @{ gap = ([int] $chipRectangles[1][0] - [int] $chipRectangles[0][2]); expected = (Logical 8 $dpi); dpi = $dpi; mist = ($chipRectangles[0] -join ','); clay = ($chipRectangles[1] -join ',') }
    Add-Check 'panel.three_equal_horizontal_choices' `
        (([int] $chipRectangles[0][1] -eq [int] $chipRectangles[1][1]) -and `
         ([int] $chipRectangles[0][1] -eq [int] $chipRectangles[2][1]) -and `
         (($chipRectangles[0][2] - $chipRectangles[0][0]) -eq ($chipRectangles[2][2] - $chipRectangles[2][0])) -and `
         ([N]::ControlId($chips[0]) -eq $CHIP_ORIGINAL) -and ([N]::ControlId($chips[1]) -eq $CHIP_MIST) -and `
         ([N]::ControlId($chips[2]) -eq $CHIP_CLAY)) `
        @{ rectangles = $chipRectangles; ids = @($chips | ForEach-Object { [N]::ControlId($_) }) }
    Add-Check 'panel.palette_accessible_names' `
        (([N]::Text($chips[0]) -eq $T_ORIGINAL) -and ([N]::Text($chips[1]) -eq $T_MIST) -and ([N]::Text($chips[2]) -eq $T_CLAY)) `
        @{ names = @($chips | ForEach-Object { [N]::Text($_) }) }
    Add-Check 'panel.slider_range' (([N]::TrackbarMinimum($slider) -eq 20) -and ([N]::TrackbarMaximum($slider) -eq 100) -and `
            ([N]::TrackbarPosition($slider) -eq 100)) `
        @{ minimum = [N]::TrackbarMinimum($slider); maximum = [N]::TrackbarMaximum($slider); position = [N]::TrackbarPosition($slider) }
    Add-Check 'panel.readout' ((Readout $panel) -eq '100%') @{ readout = (Readout $panel) }
    Add-Check 'panel.slider_name_has_value' (([N]::Text($slider) -match ('(?i)' + [regex]::Escape($T_OPACITY) + '.*100'))) `
        @{ sliderName = [N]::Text($slider) }
    $sliderRectangle = [N]::ClientRectOnScreen($slider)
    $readoutRectangle = [N]::Rect((Panel-Control $panel $PERCENT_ID))
    Add-Check 'panel.readout_above_full_width_slider' `
        (($readoutRectangle[3] -le $sliderRectangle[1]) -and `
         (($sliderRectangle[2] - $sliderRectangle[0]) -eq (Logical 280 $dpi)) -and `
         (($readoutRectangle[2] - $sliderRectangle[2]) -eq 0)) `
        @{ readout = $readoutRectangle; slider = $sliderRectangle }
    Add-Check 'panel.slider_usable_height' (($sliderRectangle[3] - $sliderRectangle[1]) -ge (Logical 32 $dpi)) `
        @{ height = ($sliderRectangle[3] - $sliderRectangle[1]); atLeast = (Logical 32 $dpi) }
    Add-Check 'panel.takes_foreground' (([N]::GetForeground()) -eq $panel) `
        @{ foreground = [N]::Class([N]::GetForeground()) }
    $capture = Join-Path $stage 'panel-open.png'
    $null = [N]::SaveScreenPng($rectangle[0], $rectangle[1], $width, $height, $capture)
    $panelAnalysis = [N]::AnalysePng($capture, $COLOUR_FLAT, 40)
    Add-Check 'panel.draws_both_swatches_and_its_labels' `
        (((Colour-Of $panelAnalysis 'mist') -gt 20) -and ((Colour-Of $panelAnalysis 'clay') -gt 20) -and ((Colour-Of $panelAnalysis 'coordinate') -gt 40)) `
        @{ mistSwatch = (Colour-Of $panelAnalysis 'mist'); claySwatch = (Colour-Of $panelAnalysis 'clay'); text = (Colour-Of $panelAnalysis 'coordinate'); darkest = $panelAnalysis.darkest; tolerance = 40; file = $capture }
    Close-AnyPanel $state
    Add-Check 'panel.closes_on_escape' ((-not [N]::Visible($panel))) `
        @{ alive = [N]::Alive($panel); visible = [N]::Visible($panel) }
    Add-Check 'panel.escape_keeps_the_values' ((-not [N]::IsLayered($root)) -and ([N]::Alpha($root) -eq -1)) `
        @{ alpha = [N]::Alpha($root); layered = [N]::IsLayered($root) }
    Save-Checks
}

'opacity' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $before = [N]::Rect($root)
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    $slider = Panel-Control $panel $SLIDER_ID
    $calibration = Slider-Calibration $root $panel $slider
    Add-Check 'opacity.the_row_reports_its_own_mapping' `
        (($calibration.restored -eq $calibration.startedAt) -and ($calibration.minimum -eq 20) -and
            ($calibration.maximum -eq 100) -and ($calibration.travel -ge (Logical 120 $calibration.dpi)) -and
            ($calibration.mappingSamples -ge 8) -and ($calibration.mappingWorstValueError -le 1) -and
            ($calibration.mappingWorstResidual -le $calibration.bound)) `
        $calibration
    $start = Pointer-For-Percent $calibration ([N]::TrackbarPosition($slider))
    $end = Pointer-For-Percent $calibration 55
    [N]::Drag($start[0], $start[1], $end[0], $end[1], 14)
    Start-Sleep -Milliseconds 400
    $position = [N]::TrackbarPosition($slider)
    Add-Check 'opacity.drag_moves_the_thumb' (($position -gt 20) -and ($position -lt 100)) `
        @{ from = ($start -join ','); to = ($end -join ','); position = $position }
    Add-Check 'opacity.drag_previews' (([N]::IsLayered($root)) -and ([N]::Alpha($root) -eq (Expected-Alpha $position))) `
        @{ position = $position; alpha = [N]::Alpha($root); expected = (Expected-Alpha $position) }
    Add-Check 'opacity.drag_shows_the_same_number' ((Readout $panel) -eq ($position.ToString() + '%')) `
        @{ readout = (Readout $panel); position = $position }
    [N]::KeyPress($VK_HOME)
    Start-Sleep -Milliseconds 350
    Add-Check 'opacity.home_is_20' (([N]::TrackbarPosition($slider) -eq 20) -and ([N]::Alpha($root) -eq (Expected-Alpha 20))) `
        @{ position = [N]::TrackbarPosition($slider); alpha = [N]::Alpha($root); readout = (Readout $panel) }
    [N]::KeyPress($VK_RIGHT)
    Start-Sleep -Milliseconds 300
    Add-Check 'opacity.right_arrow_is_one_percent' (([N]::TrackbarPosition($slider) -eq 21) -and ([N]::Alpha($root) -eq (Expected-Alpha 21))) `
        @{ position = [N]::TrackbarPosition($slider); alpha = [N]::Alpha($root) }
    [N]::KeyPress($VK_LEFT)
    Start-Sleep -Milliseconds 300
    Add-Check 'opacity.left_arrow_is_one_percent' (([N]::TrackbarPosition($slider) -eq 20) -and ([N]::Alpha($root) -eq (Expected-Alpha 20))) `
        @{ position = [N]::TrackbarPosition($slider); alpha = [N]::Alpha($root) }
    Add-Check 'opacity.ex_style_is_layered' (([N]::ExStyle($root) -band 0x80000) -ne 0) `
        @{ exStyle = ('0x' + ([int64] [N]::ExStyle($root)).ToString('X')) }
    Close-AnyPanel $state

    # Does the desktop really show through, or is the alpha byte only reported? The value is
    # committed through the keyboard path, then the window is put over a solid lime backdrop.
    $set20 = Set-Opacity $state $root 20
    Add-Check 'opacity.keyboard_sets_20' (($set20 -eq 20) -and ([N]::Alpha($root) -eq (Expected-Alpha 20))) `
        @{ position = $set20; alpha = [N]::Alpha($root); expected = (Expected-Alpha 20) }
    $rectangle = [N]::Rect($root)
    $x = [int] $rectangle[0]; $y = [int] $rectangle[1]
    $width = [int] ($rectangle[2] - $rectangle[0]); $height = [int] ($rectangle[3] - $rectangle[1])
    # Nothing else may sit between the window and the backdrop, so the other editors are moved off.
    foreach ($role in @('W2','W3','W4','W5','list')) {
        $other = Window-Of $state $role
        $null = [N]::Place($other, [Math]::Max(0, $WORK_AREA.Left), [Math]::Max(0, $WORK_AREA.Top), 300, 200)
    }
    Start-Sleep -Milliseconds 300
    $backdrop = New-Lime-Backdrop $x $y $width $height
    [N]::BringToTop($root)
    Start-Sleep -Milliseconds 400
    $translucentPath = Join-Path $stage 'screen-20-on-lime.png'
    $null = [N]::SaveScreenPng($x, $y, $width, $height, $translucentPath)
    # Green-dominant pixels where the window is only 20 % opaque prove that the backdrop really is
    # the thing behind it: pure lime on its own, or that lime seen through the white canvas.
    $behind = [N]::AnalysePng($translucentPath, ([int[]] (0,255,0, 51,255,51)), 60)
    Add-Check 'opacity.backdrop_is_behind_the_window' `
        ((([int64] $behind.counts[0]) + ([int64] $behind.counts[1])) -gt (0.4 * $width * $height)) `
        @{ pureLime = $behind.counts[0]; blendedLime = $behind.counts[1]; pixels = ($width * $height); file = $translucentPath }
    $set100 = Set-Opacity $state $root 100
    Add-Check 'opacity.keyboard_restores_100' (($set100 -eq 100) -and (-not [N]::IsLayered($root)) -and ([N]::Alpha($root) -eq -1)) `
        @{ position = $set100; layered = [N]::IsLayered($root); alpha = [N]::Alpha($root) }
    [N]::BringToTop($root)
    Start-Sleep -Milliseconds 400
    $opaquePath = Join-Path $stage 'screen-100-on-lime.png'
    $null = [N]::SaveScreenPng($x, $y, $width, $height, $opaquePath)
    $backdrop.Close()
    1..4 | ForEach-Object { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 80 }
    Add-Check 'opacity.restores_to_opaque' ((-not [N]::IsLayered($root)) -and ([N]::Alpha($root) -eq -1)) `
        @{ layered = [N]::IsLayered($root); alpha = [N]::Alpha($root) }
    $bleed = [N]::PairBleed($opaquePath, $translucentPath, 0, 255, 0, 20, 14)
    Add-Check 'opacity.desktop_shows_through' (([int64] $bleed.bleed -gt 20000) -and ([int64] $bleed.changed -gt 20000)) `
        @{ bleed = $bleed.bleed; changed = $bleed.changed; white = $bleed.white; pixels = $bleed.pixels;
           opaque = $opaquePath; translucent = $translucentPath }
    Add-Check 'opacity.window_geometry_untouched' (([N]::Rect($root) -join ',') -eq ($before -join ',')) `
        @{ before = ($before -join ','); after = (@([N]::Rect($root)) -join ',') }
    Close-AnyPanel $state
    $drawing = Drawing-Area $root
    $afterRestore = Get-ScreenBand $drawing 'after-opacity-cycle'
    Add-Check 'opacity.window_still_draws' (([int64] $afterRestore.white -gt 10000) -and ([int64] $afterRestore.nonOpaque -eq 0)) `
        @{ white = $afterRestore.white; nonOpaque = $afterRestore.nonOpaque; size = @($afterRestore.width, $afterRestore.height) }
    Save-Checks
}

'drag' {
    $state = Read-AppState
    $processId = [int] $state.processId
    $root = Window-Of $state 'W1'
    $other = Window-Of $state 'W2'
    $records = New-Object 'System.Collections.Generic.List[object]'
    $captures = New-Object 'System.Collections.Generic.List[object]'
    $timing = New-Object 'System.Collections.Generic.List[object]'
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    if ($panel -eq [IntPtr]::Zero) {
        Add-Check 'drag.panel_opens' $false @{ note = 'the appearance panel did not open on this editor' }
        Save-Checks
        return
    }
    $slider = Panel-Control $panel $SLIDER_ID
    $client = [N]::ClientRectOnScreen($slider)
    # The painted circle is read while the window is still opaque, which is the one moment when the
    # pixels of the row are the panel's own rather than a blend with what stands behind it: that
    # gives the size of the circle a grab has to cover, and the spot the mapping is checked against.
    if ([N]::Alpha($root) -ne -1) {
        $null = Set-Opacity $state $root 100
        $opened = Open-Panel $state $root
        $panel = $opened.panel
        $slider = Panel-Control $panel $SLIDER_ID
        $client = [N]::ClientRectOnScreen($slider)
    }
    $painted = Painted-Handle $slider
    $startValue = [N]::TrackbarPosition($slider)
    $calibration = Slider-Calibration $root $panel $slider
    Add-Check 'drag.travel_and_percentage_come_from_the_row' `
        (($calibration.travel -ge (Logical 120 $calibration.dpi)) -and ($calibration.restored -eq $startValue) -and
            ($calibration.minimum -eq 20) -and ($calibration.maximum -eq 100) -and
            ($calibration.mappingSamples -ge 8) -and ($calibration.mappingWorstValueError -le 1) -and
            ($calibration.mappingWorstResidual -le $calibration.bound)) `
        @{ calibration = $calibration; travelAllowedBelow = (Logical 120 $calibration.dpi) }
    # The circle that was painted and the centre the row reports for the same percentage have to be
    # the same place: this is what ties the mapping the whole phase aims with to what the user sees.
    $circleValue = Pointer-For-Percent $calibration $startValue
    $paintedDistance = [Math]::Abs([double] $painted.centre[0] - $circleValue[0])
    Add-Check 'drag.the_painted_circle_is_where_the_row_puts_it' `
        (($paintedDistance -le $calibration.bound + 1.0) -and ($painted.interior -ge (Logical 8 $calibration.dpi)) -and
            ($painted.radius -ge (Logical 6 $calibration.dpi))) `
        @{ paintedCentre = ($painted.centre -join ','); paintedRadius = $painted.radius;
           paintedInteriorWidth = $painted.interior; mappingCentre = ($circleValue -join ',');
           value = $startValue; distance = [Math]::Round($paintedDistance, 2);
           allowed = [Math]::Round($calibration.bound + 1.0, 2) }
    Log-Note ("  travel {0} device px, {1:N2} px per percent, distance bound {2:N2} px, painted radius {3} px, {4} dpi" -f
        ([Math]::Round($calibration.travel, 1)), $calibration.pixelsPerPercent, $calibration.bound,
        $painted.radius, $calibration.dpi)
    $allowedRun = [Math]::Ceiling($calibration.pixelsPerPercent) + 1
    # The design bound is half a percent step plus a pixel; the mapping is worked out from whole
    # pixel presses and the pointer is aimed in whole pixels as well, so one more pixel is allowed.
    $distanceBound = $calibration.bound
    $distanceTolerance = $calibration.bound + 1.0
    $handleRadius = [int] $painted.radius
    $sixty = Pointer-For-Percent $calibration 60
    [N]::Click($sixty[0], $sixty[1])
    Start-Sleep -Milliseconds 250
    $atSixty = [N]::TrackbarPosition($slider)
    # The first press asked for the position under it; the handle is now there, so the same press
    # again has to hold the handle instead of asking for a percentage a second time.
    [N]::Click($sixty[0], $sixty[1])
    Start-Sleep -Milliseconds 300
    Add-Check 'drag.pressing_the_visible_handle_asks_for_no_value' `
        (($atSixty -eq 60) -and (([N]::TrackbarPosition($slider) -eq $atSixty)) -and
            (([N]::FocusedInProcess($processId)) -eq $slider)) `
        @{ circle = (@(($painted.centre[0] - $handleRadius), $client[1], ($painted.centre[0] + $handleRadius), $client[3]) -join ',');
           centre = ($sixty -join ','); row = ($client -join ',');
           before = $atSixty; after = [N]::TrackbarPosition($slider); focused = ([string] ([N]::FocusedInProcess($processId))) }

    # 整十附近慢拖 and 从非整十值开始: one held drag a device pixel at a time over the whole track
    # and straight back, so every integer between the two non-round ends has to be settled on its own.
    $up = Drag-Held-Sweep $root $panel $slider $calibration 21 99 30
    $upMetrics = Sweep-Metrics $up.samples
    $records.Add([pscustomobject] @{ check = 'drag.sweep_up'; from = 21; to = 99; metrics = $upMetrics; samples = $up.samples })
    Add-Check 'drag.sweep_up_is_continuous' `
        (($upMetrics.moved) -and ($upMetrics.first -le 22) -and ($upMetrics.last -ge 98) -and
            ($upMetrics.largestStep -le 1) -and ($upMetrics.backwards -eq 0) -and
            ($upMetrics.distinct -eq $upMetrics.span) -and ($upMetrics.maxDistance -le $distanceTolerance) -and
            ($upMetrics.feedbackMismatches -eq 0)) `
        @{ metrics = $upMetrics; distanceBound = [Math]::Round($distanceBound, 2); allowedDistance = [Math]::Round($distanceTolerance, 2) }
    $down = Drag-Held-Sweep $root $panel $slider $calibration 99 21 30
    $downMetrics = Sweep-Metrics $down.samples
    $records.Add([pscustomobject] @{ check = 'drag.sweep_back'; from = 99; to = 21; metrics = $downMetrics; samples = $down.samples })
    Add-Check 'drag.sweep_back_is_continuous' `
        (($downMetrics.moved) -and ($downMetrics.first -ge 98) -and ($downMetrics.last -le 22) -and
            ($downMetrics.largestStep -le 1) -and ($downMetrics.backwards -eq 0) -and
            ($downMetrics.distinct -eq $downMetrics.span) -and ($downMetrics.maxDistance -le $distanceTolerance) -and
            ($downMetrics.feedbackMismatches -eq 0)) `
        @{ metrics = $downMetrics; distanceBound = [Math]::Round($distanceBound, 2); allowedDistance = [Math]::Round($distanceTolerance, 2) }

    # The same per-pixel drag through the neighbourhood of each round number, from a non-round value
    # on either side: the tens must not hold the pointer any longer than a percentage holds it.
    $roundFailures = @()
    foreach ($round in @(30, 40, 50, 60, 70, 80, 90)) {
        foreach ($pair in @(@(($round - 2), ($round + 2)), @(($round + 2), ($round - 2)))) {
            $sweep = Drag-Held-Sweep $root $panel $slider $calibration $pair[0] $pair[1] 25
            $metrics = Sweep-Metrics $sweep.samples
            $records.Add([pscustomobject] @{ check = "drag.round_$round"; from = $pair[0]; to = $pair[1];
                    allowedRun = $allowedRun; metrics = $metrics; samples = $sweep.samples })
            if (-not (($metrics.moved) -and ($metrics.distinct -eq $metrics.span) -and ($metrics.distinct -ge 4) -and
                    ($metrics.largestStep -le 1) -and ($metrics.backwards -eq 0) -and
                    ($metrics.longestRunOfOneValue -le $allowedRun) -and ($metrics.feedbackMismatches -eq 0))) {
                $roundFailures += [pscustomobject] @{ round = $round; from = $pair[0]; to = $pair[1]; metrics = $metrics }
            }
        }
    }
    Add-Check 'drag.no_sticking_on_either_side_of_the_round_numbers' (@($roundFailures).Count -eq 0) `
        @{ failures = @($roundFailures); allowedRun = $allowedRun; pixelsPerPercent = $calibration.pixelsPerPercent }

    # 手柄中心、圆周与抓取区: the centre, the edge of the drawn circle and the corners of a band of
    # the size the design asks for. Every one of them holds on to the handle, so the press itself
    # never asks for the percentage the pointer happens to be over.
    $handle = @(($painted.centre[0] - $handleRadius), $client[1], ($painted.centre[0] + $handleRadius), $client[3])
    $bandHalfWidth = Logical 11 $calibration.dpi
    $bandHalfHeight = [int] (($client[3] - $client[1]) / 2) - 1
    $pressFailures = @()
    $pressRecords = New-Object 'System.Collections.Generic.List[object]'
    foreach ($dx in @(0, $handleRadius, (-$handleRadius), $bandHalfWidth, (-$bandHalfWidth))) {
        foreach ($dy in @(0, $bandHalfHeight, (-$bandHalfHeight))) {
            $centre = Centre-For-Value $calibration ([N]::TrackbarPosition($slider))
            $before = [N]::TrackbarPosition($slider)
            $x = $centre[0] + $dx; $y = $centre[1] + $dy
            [N]::Press($x, $y)
            $atPress = [N]::TrackbarPosition($slider)
            $captures.Add((Capture-Checkpoint $state $slider "grab at $dx,$dy" $true))
            [N]::ReleasePointer($x, $y)
            Start-Sleep -Milliseconds 60
            $after = [N]::TrackbarPosition($slider)
            $record = [pscustomobject] @{ offsetX = $dx; offsetY = $dy; pointerX = $x; pointerY = $y;
                    mappingCentreX = $centre[0]; before = $before; atPress = $atPress; after = $after }
            $pressRecords.Add($record)
            if (-not (($atPress -eq $before) -and ($after -eq $before))) { $pressFailures += $record }
        }
    }
    Add-Check 'drag.press_inside_the_grab_area_holds_the_handle' (@($pressFailures).Count -eq 0) `
        @{ handle = ($handle -join ','); radius = $handleRadius; bandHalfWidth = $bandHalfWidth;
           bandHalfHeight = $bandHalfHeight; failures = @($pressFailures); presses = $pressRecords.ToArray() }

    # The offset a grab was made at is kept for the whole drag: the handle moves as far as the
    # pointer moves, it does not come back under the pointer.
    $offsetFailures = @()
    $offsetRecords = New-Object 'System.Collections.Generic.List[object]'
    foreach ($offset in @($bandHalfWidth, (-$bandHalfWidth), $handleRadius, (-$handleRadius))) {
        $centre = Centre-For-Value $calibration ([N]::TrackbarPosition($slider))
        $before = [N]::TrackbarPosition($slider)
        $pressX = $centre[0] + $offset
        [N]::Press($pressX, $centre[1])
        $atPress = [N]::TrackbarPosition($slider)
        $captures.Add((Capture-Checkpoint $state $slider "held grab at offset $offset" $true))
        $movedX = $pressX - [Math]::Round(2 * $calibration.pixelsPerPercent)
        [N]::MovePointer($movedX, $centre[1])
        Start-Sleep -Milliseconds 80
        $duringMove = [N]::TrackbarPosition($slider)
        $centreAfterMove = [double] (Centre-For-Value $calibration $duringMove)[0]
        $distanceFromOffset = [Math]::Abs($centreAfterMove - ($movedX - $offset))
        [N]::ReleasePointer($movedX, $centre[1])
        Start-Sleep -Milliseconds 80
        $after = [N]::TrackbarPosition($slider)
        $captures.Add((Capture-Checkpoint $state $slider 'offset grab released' $false))
        $record = [pscustomobject] @{ offset = $offset; before = $before; atPress = $atPress; movedPointerX = $movedX;
                during = $duringMove; after = $after; mappingCentreX = $centreAfterMove;
                distanceFromOffset = [Math]::Round($distanceFromOffset, 2); allowedDistance = [Math]::Round($distanceTolerance, 2) }
        $offsetRecords.Add($record)
        if (-not (($atPress -eq $before) -and ($distanceFromOffset -le $distanceTolerance) -and
                ([Math]::Abs(($duringMove - $before) + 2) -le 1) -and ($after -eq $duringMove))) { $offsetFailures += $record }
    }
    Add-Check 'drag.a_grab_keeps_its_offset_while_the_pointer_moves' (@($offsetFailures).Count -eq 0) `
        @{ failures = @($offsetFailures); records = $offsetRecords.ToArray(); allowedDistance = [Math]::Round($distanceTolerance, 2) }

    # 轨道点击与按住: a press on the channel goes straight to that position, a pointer that then
    # stays where it is gets nothing more, and a pointer that moves on is carried on as a drag.
    $channelFailures = @()
    $channelRecords = New-Object 'System.Collections.Generic.List[object]'
    foreach ($target in @(22, 35, 49, 78, 91)) {
        $pointer = Pointer-For-Percent $calibration $target
        [N]::Press($pointer[0], $pointer[1])
        Start-Sleep -Milliseconds 80
        $atPress = [N]::TrackbarPosition($slider)
        $distanceAfterPress = [Math]::Abs([double] (Centre-For-Value $calibration $atPress)[0] - $pointer[0])
        $captures.Add((Capture-Checkpoint $state $slider "channel press at $target" $true))
        Start-Sleep -Milliseconds 600
        $afterHold = [N]::TrackbarPosition($slider)
        $heldFeedback = Opacity-Feedback $root $panel $slider $afterHold
        $towards = $(if ($target -le 60) { 3 } else { -3 })
        $movedX = $pointer[0] + [Math]::Round($towards * $calibration.pixelsPerPercent)
        [N]::MovePointer($movedX, $calibration.centreY)
        Start-Sleep -Milliseconds 80
        $afterMove = [N]::TrackbarPosition($slider)
        $distanceAfterMove = [Math]::Abs([double] (Centre-For-Value $calibration $afterMove)[0] - $movedX)
        [N]::ReleasePointer($movedX, $calibration.centreY)
        Start-Sleep -Milliseconds 80
        $afterRelease = [N]::TrackbarPosition($slider)
        $record = [pscustomobject] @{ target = $target; pointerX = $pointer[0]; atPress = $atPress;
                distanceAfterPress = [Math]::Round($distanceAfterPress, 2); afterHold = $afterHold;
                afterMove = $afterMove; movedPointerX = $movedX; distanceAfterMove = [Math]::Round($distanceAfterMove, 2);
                afterRelease = $afterRelease; feedbackWhileHeld = [bool] $heldFeedback.matches }
        $channelRecords.Add($record)
        if (-not (([Math]::Abs($atPress - $target) -le 1) -and ($distanceAfterPress -le $distanceTolerance) -and
                ($afterHold -eq $atPress) -and ($heldFeedback.matches) -and
                ([Math]::Abs(($afterMove - $atPress) - $towards) -le 1) -and ($distanceAfterMove -le $distanceTolerance) -and
                ($afterRelease -eq $afterMove))) { $channelFailures += $record }
    }
    Add-Check 'drag.channel_press_positions_holds_and_continues_as_a_drag' (@($channelFailures).Count -eq 0) `
        @{ failures = @($channelFailures); records = $channelRecords.ToArray() }

    # 离轨与面板外松手: the held pointer leaves the row, leaves the panel and crosses over the editor
    # caption; the vertical distance changes nothing, the ends clamp, and releasing out there finishes
    # the operation for good.
    $inRow = Pointer-For-Percent $calibration 70
    [N]::Press($inRow[0], $inRow[1])
    Start-Sleep -Milliseconds 100
    $panelRectangle = [N]::Rect($panel)
    $rootRectangle = [N]::Rect($root)
    $leftOfEnds = $calibration.left - (Logical 30 $calibration.dpi)
    $rightOfEnds = $calibration.right + (Logical 30 $calibration.dpi)
    $offSteps = @(@{ x = ($inRow[0] - 20); y = $inRow[1]; where = 'in the row' },
                  @{ x = ($inRow[0] - 20); y = ($client[1] - (Logical 8 $calibration.dpi)); where = 'above the row, inside the panel' },
                  @{ x = ($inRow[0] - 20); y = ($panelRectangle[1] - (Logical 6 $calibration.dpi)); where = 'above the panel' },
                  @{ x = $leftOfEnds; y = ($panelRectangle[1] - (Logical 6 $calibration.dpi)); where = 'past the left end, above the panel' },
                  @{ x = $rightOfEnds; y = $inRow[1]; where = 'past the right end' },
                  @{ x = (Pointer-For-Percent $calibration 55)[0]; y = $inRow[1]; where = 'back at 55 per cent' },
                  @{ x = (Pointer-For-Percent $calibration 55)[0]; y = ($rootRectangle[1] + 4); where = 'over the caption, outside the panel' })
    $offSamples = New-Object 'System.Collections.Generic.List[object]'
    foreach ($step in $offSteps) {
        [N]::MovePointer($step.x, $step.y)
        Start-Sleep -Milliseconds 90
        $value = [N]::TrackbarPosition($slider)
        $feedback = Opacity-Feedback $root $panel $slider $value
        $offSamples.Add([pscustomobject] @{ where = $step.where; pointerX = $step.x; pointerY = $step.y;
                value = $value; distance = [Math]::Round([Math]::Abs([double] (Centre-For-Value $calibration $value)[0] - $step.x), 2);
                readout = $feedback.readout; alpha = $feedback.alpha; layered = $feedback.layered;
                feedbackMatches = $feedback.matches })
        $captures.Add((Capture-Checkpoint $state $slider "held $($step.where)" $true))
    }
    $releaseX = $offSamples[6].pointerX
    [N]::ReleasePointer($releaseX, $offSamples[6].pointerY)
    Start-Sleep -Milliseconds 120
    $released = [N]::TrackbarPosition($slider)
    $captures.Add((Capture-Checkpoint $state $slider 'released over the caption' $false))
    $afterReleaseSamples = New-Object 'System.Collections.Generic.List[object]'
    foreach ($wander in @((Pointer-For-Percent $calibration 30), (Pointer-For-Percent $calibration 95), (Centre $root))) {
        [N]::MovePointer($wander[0], $wander[1])
        Start-Sleep -Milliseconds 120
        $afterReleaseSamples.Add([pscustomobject] @{ pointerX = $wander[0]; pointerY = $wander[1];
                value = [N]::TrackbarPosition($slider); alpha = [N]::Alpha($root) })
    }
    $inRowValue = [int] $offSamples[0].value
    $aboveRowValue = [int] $offSamples[1].value
    $abovePanelValue = [int] $offSamples[2].value
    $backAtFiftyFive = [int] $offSamples[5].value
    $outsideValue = [int] $offSamples[6].value
    Add-Check 'drag.tracking_continues_outside_the_row_and_the_panel' `
        (($inRowValue -eq $aboveRowValue) -and ($aboveRowValue -eq $abovePanelValue) -and
            ($backAtFiftyFive -eq $outsideValue) -and ($released -eq $outsideValue) -and
            (@($offSamples | Where-Object { (-not $_.feedbackMatches) -or
                (($_.value -gt $calibration.minimum -and $_.value -lt $calibration.maximum) -and
                    ($_.distance -gt $distanceTolerance)) }).Count -eq 0)) `
        @{ steps = $offSamples.ToArray(); distanceAllowed = [Math]::Round($distanceTolerance, 2);
           note = 'past the two ends the handle clamps, so only the positions inside the range are measured against the pointer' }
    Add-Check 'drag.the_ends_clamp_the_value_and_the_way_back_is_kept' `
        (([int] $offSamples[3].value -eq 20) -and ([int] $offSamples[4].value -eq 100) -and
            ([Math]::Abs($backAtFiftyFive - 55) -le 1)) `
        @{ left = $offSamples[3]; right = $offSamples[4]; back = $offSamples[5] }
    Add-Check 'drag.releasing_outside_the_panel_ends_the_operation' `
        ((@($afterReleaseSamples | Where-Object { $_.value -ne $released }).Count -eq 0) -and
            (Capture-Is-Clear $state)) `
        @{ releasedAt = $releaseX; value = $released; alpha = [N]::Alpha($root); later = $afterReleaseSamples.ToArray() }

    # 快速往返与端点: large jumps settle on the pointer at once instead of walking the track to it.
    $fastFailures = @()
    $fastRecords = New-Object 'System.Collections.Generic.List[object]'
    foreach ($pair in @(@(25, 92), @(92, 31), @(31, 88), @(88, 24), @(24, 100), @(100, 20))) {
        $from = Pointer-For-Percent $calibration $pair[0]
        [N]::Press($from[0], $from[1])
        Start-Sleep -Milliseconds 80
        $atPress = [N]::TrackbarPosition($slider)
        $to = Pointer-For-Percent $calibration $pair[1]
        [N]::MovePointer($to[0], $calibration.centreY)
        $settled = Settle-After-Jump $slider $pair[1] 1
        $afterSettle = [N]::TrackbarPosition($slider)
        $distance = [Math]::Abs([double] (Centre-For-Value $calibration $afterSettle)[0] - $to[0])
        [N]::ReleasePointer($to[0], $calibration.centreY)
        Start-Sleep -Milliseconds 80
        $record = [pscustomobject] @{ from = $pair[0]; atPress = $atPress; to = $pair[1]; pointerX = $to[0];
                jumpedTo = $settled.settledAfterMilliseconds; valuesSeen = $settled.valuesSeen;
                distinct = $settled.distinct; afterRelease = [N]::TrackbarPosition($slider);
                distance = [Math]::Round($distance, 2) }
        $fastRecords.Add($record)
        if (-not (($settled.settledAfterMilliseconds -ge 0) -and ($settled.distinct -le 2) -and
                ($distance -le $distanceTolerance) -and ([N]::TrackbarPosition($slider) -eq $afterSettle))) { $fastFailures += $record }
    }
    Add-Check 'drag.fast_jumps_do_not_accumulate_lag' (@($fastFailures).Count -eq 0) `
        @{ failures = @($fastFailures); records = $fastRecords.ToArray(); distanceAllowed = [Math]::Round($distanceTolerance, 2) }

    # 取消与重入: Escape and a lost foreground end the drag where it stands, keep the value that was
    # applied, and leave nothing holding the pointer; a panel opened again grabs at once.
    $escapeTarget = Pointer-For-Percent $calibration 45
    $centre = Centre-For-Value $calibration ([N]::TrackbarPosition($slider))
    [N]::Press($centre[0], $centre[1])
    [N]::MovePointer($escapeTarget[0], $escapeTarget[1])
    Start-Sleep -Milliseconds 120
    $beforeEscape = [N]::TrackbarPosition($slider)
    [N]::KeyPress($VK_ESCAPE)
    Start-Sleep -Milliseconds 450
    $captures.Add((Capture-Checkpoint $state $slider 'escape with the button still held' $false))
    $alphaAfterEscape = [N]::Alpha($root)
    [N]::ReleasePointer($escapeTarget[0], $escapeTarget[1])
    Start-Sleep -Milliseconds 150
    [N]::MovePointer((Pointer-For-Percent $calibration 80)[0], (Pointer-For-Percent $calibration 80)[1])
    Start-Sleep -Milliseconds 150
    Add-Check 'drag.escape_ends_the_drag_and_keeps_the_value' `
        (((Panel-Window $state) -eq [IntPtr]::Zero) -and ($alphaAfterEscape -eq (Expected-Alpha $beforeEscape)) -and
            ([N]::Alpha($root) -eq $alphaAfterEscape) -and (Capture-Is-Clear $state)) `
        @{ valueAtEscape = $beforeEscape; alphaAtEscape = $alphaAfterEscape; alphaLater = [N]::Alpha($root);
           capture = ([string] [N]::CapturedInProcess($processId)); panelVisible = ((Panel-Window $state) -ne [IntPtr]::Zero) }
    $again = Open-Panel $state $root
    $panel = $again.panel
    if ($panel -eq [IntPtr]::Zero) {
        Add-Check 'drag.reopens_and_grabs_at_once' $false @{ note = 'the panel did not open again after the escape' }
        Save-Checks
        return
    }
    $slider = Panel-Control $panel $SLIDER_ID
    $reopenedValue = [N]::TrackbarPosition($slider)
    # The panel came back where the entry is, so the mapping is measured again rather than trusted
    # from before the window moved; the value it restores is the one the escape had left.
    $calibration = Slider-Calibration $root $panel $slider
    $centre = Centre-For-Value $calibration $reopenedValue
    [N]::Click($centre[0], $centre[1])
    Start-Sleep -Milliseconds 250
    $grabTarget = Pointer-For-Percent $calibration 72
    [N]::Press($centre[0], $centre[1])
    [N]::MovePointer($grabTarget[0], $grabTarget[1])
    Start-Sleep -Milliseconds 120
    $afterRegrab = [N]::TrackbarPosition($slider)
    [N]::ReleasePointer($grabTarget[0], $grabTarget[1])
    Add-Check 'drag.reopens_and_grabs_at_once' `
        (($reopenedValue -eq $beforeEscape) -and ([Math]::Abs($afterRegrab - 72) -le 1)) `
        @{ reopened = $reopenedValue; escapedAt = $beforeEscape; readout = (Readout $panel); draggedTo = $afterRegrab }

    # The foreground going elsewhere is the same exit as a click outside, and it is the only one that
    # can be made with a button that is already held down: a second press cannot be synthesised.
    $lossTarget = Pointer-For-Percent $calibration 30
    $centre = Centre-For-Value $calibration ([N]::TrackbarPosition($slider))
    [N]::Press($centre[0], $centre[1])
    [N]::MovePointer($lossTarget[0], $lossTarget[1])
    Start-Sleep -Milliseconds 120
    $beforeLoss = [N]::TrackbarPosition($slider)
    $null = [N]::ForceForeground($other)
    Start-Sleep -Milliseconds 500
    $captures.Add((Capture-Checkpoint $state $slider 'foreground went to another window' $false))
    $alphaAfterLoss = [N]::Alpha($root)
    [N]::ReleasePointer($lossTarget[0], $lossTarget[1])
    Start-Sleep -Milliseconds 200
    [N]::MovePointer((Pointer-For-Percent $calibration 90)[0], (Pointer-For-Percent $calibration 90)[1])
    Start-Sleep -Milliseconds 200
    Add-Check 'drag.losing_the_foreground_ends_the_drag' `
        ((-not [N]::Visible($panel)) -and ($alphaAfterLoss -eq (Expected-Alpha $beforeLoss)) -and
            ([N]::Alpha($root) -eq $alphaAfterLoss) -and (Capture-Is-Clear $state)) `
        @{ valueAtLoss = $beforeLoss; alphaAtLoss = $alphaAfterLoss; alphaLater = [N]::Alpha($root);
           capture = ([string] [N]::CapturedInProcess($processId)); panelVisible = [bool] [N]::Visible($panel) }
    Bring-Out $root
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    $slider = Panel-Control $panel $SLIDER_ID
    # The presses that measure the mapping also put the keyboard on the slider, which is exactly what
    # the keys below are answered by.
    $calibration = Slider-Calibration $root $panel $slider

    # 键盘与辅助功能: the keys the design keeps, from a non-round value, with the read-out, the
    # accessible name and the compositor agreeing at every one of them. Home and forty-four steps
    # get that value the way a keyboard user would, without a second pointer on the track.
    [N]::KeyPress($VK_HOME)
    Start-Sleep -Milliseconds 250
    1..44 | ForEach-Object { [N]::KeyPress($VK_RIGHT); Start-Sleep -Milliseconds 45 }
    $set64 = [N]::TrackbarPosition($slider)
    $keyboard = New-Object 'System.Collections.Generic.List[object]'
    $keyboardFailures = @()
    foreach ($key in @(@{ code = $VK_RIGHT; name = 'RIGHT'; from = 64; to = 65 },
                      @{ code = $VK_LEFT; name = 'LEFT'; from = 65; to = 64 },
                      @{ code = $VK_NEXT; name = 'NEXT'; from = 64; to = 74 },
                      @{ code = $VK_PRIOR; name = 'PRIOR'; from = 74; to = 64 },
                      @{ code = $VK_HOME; name = 'HOME'; from = 64; to = 20 },
                      @{ code = $VK_END; name = 'END'; from = 20; to = 100 })) {
        $before = [N]::TrackbarPosition($slider)
        [N]::KeyPress($key.code)
        Start-Sleep -Milliseconds 260
        $after = [N]::TrackbarPosition($slider)
        $feedback = Opacity-Feedback $root $panel $slider $after
        $record = [pscustomobject] @{ key = $key.name; from = $before; to = $after; expected = $key.to;
                readout = $feedback.readout; alpha = $feedback.alpha; layered = $feedback.layered;
                accessibleValue = $feedback.accessibleValue; feedbackMatches = $feedback.matches }
        $keyboard.Add($record)
        if (-not (($before -eq $key.from) -and ($after -eq $key.to) -and ($feedback.matches))) { $keyboardFailures += $record }
    }
    Add-Check 'drag.keyboard_steps_are_one_and_ten_percent' (@($keyboardFailures).Count -eq 0) `
        @{ failures = @($keyboardFailures); keys = $keyboard.ToArray(); setTo64 = $set64 }
    # One operation, one result: the keys above each put their own value in effect and nothing moved
    # on its own afterwards.
    $stillAfterKeys = [N]::TrackbarPosition($slider)
    Start-Sleep -Milliseconds 600
    Add-Check 'drag.nothing_moves_on_its_own_after_an_operation' `
        (([N]::TrackbarPosition($slider) -eq $stillAfterKeys) -and (Capture-Is-Clear $state)) `
        @{ position = $stillAfterKeys; later = [N]::TrackbarPosition($slider); capture = ([string] [N]::CapturedInProcess($processId)) }

    # 合成反馈 at 99 % and 100 %: that step swaps the layered window style and repaints the window,
    # while every other percentage only changes one byte. Timed on its own, in both directions.
    $at99 = Pointer-For-Percent $calibration 99
    [N]::Click($at99[0], $at99[1])
    Start-Sleep -Milliseconds 300
    foreach ($round in 1..3) {
        $centre = Centre-For-Value $calibration ([N]::TrackbarPosition($slider))
        [N]::Press($centre[0], $centre[1])
        $up100 = Time-Opacity-Edge $root $slider $calibration 99 100
        [N]::ReleasePointer($up100.pointerX, $calibration.centreY)
        Start-Sleep -Milliseconds 250
        $centre = Centre-For-Value $calibration ([N]::TrackbarPosition($slider))
        [N]::Press($centre[0], $centre[1])
        $down99 = Time-Opacity-Edge $root $slider $calibration 100 99
        [N]::ReleasePointer($down99.pointerX, $calibration.centreY)
        Start-Sleep -Milliseconds 250
        $timing.Add([pscustomobject] @{ round = $round; up = $up100; down = $down99 })
    }
    $unsettled = @($timing | Where-Object { $_.up.settledAfterMilliseconds -lt 0 -or $_.down.settledAfterMilliseconds -lt 0 })
    $slowest = (@($timing | ForEach-Object { [double] $_.up.settledAfterMilliseconds }) +
        @($timing | ForEach-Object { [double] $_.down.settledAfterMilliseconds }) | Measure-Object -Maximum).Maximum
    Add-Check 'drag.the_99_and_100_boundary_settles_and_is_recorded' `
        ((@($unsettled).Count -eq 0) -and ($slowest -le 1500)) `
        @{ slowestMilliseconds = $slowest; rounds = @($timing | ForEach-Object { [pscustomobject] @{
                round = $_.round; upTo100 = $_.up.settledAfterMilliseconds; backTo99 = $_.down.settledAfterMilliseconds;
                upPolls = $_.up.polls; downPolls = $_.down.polls } }) }

    # Every place a button was held, the slider owned the pointer; every place it was let go, nobody
    # did. Recorded as it happened, so a hanging capture cannot hide between two phases.
    $wrongCaptures = @($captures | Where-Object { -not $_.correct })
    Add-Check 'drag.the_slider_owns_the_pointer_only_while_the_button_is_held' (@($wrongCaptures).Count -eq 0) `
        @{ checkpoints = @($captures | ForEach-Object { [pscustomobject] @{ note = $_.note; expected = $_.expected;
                holds = $_.holds; panelVisible = $_.panelVisible } }); wrong = @($wrongCaptures) }
    $null = Set-Opacity $state $root 100
    Close-AnyPanel $state
    Add-Check 'drag.finishes_opaque' ((-not [N]::IsLayered($root)) -and ([N]::Alpha($root) -eq -1)) `
        @{ layered = [N]::IsLayered($root); alpha = [N]::Alpha($root) }
    $sampleCount = 0
    foreach ($record in $records) { $sampleCount += @($record.samples).Count }
    [pscustomobject] @{
        method = 'real mouse and keyboard input into the running editor window, aimed with the percentage mapping the row reports for every pointer position on it'
        dpi = $calibration.dpi
        row = @($calibration.row)
        pointerMappingSamples = $calibration.mappingSamples
        pointerMappingWorstResidual = $calibration.mappingWorstResidual
        pointerMappingWorstValueError = $calibration.mappingWorstValueError
        pointerMappingProbes = $calibration.probes
        travelDevicePixels = $calibration.travel
        paintedCircleCentre = ($painted.centre -join ',')
        paintedCircleRadius = $painted.radius
        paintedCircleInterior = $painted.interior
        pixelsPerPercent = [Math]::Round($calibration.pixelsPerPercent, 3)
        designDistanceBound = [Math]::Round($distanceBound, 3)
        allowedDistance = [Math]::Round($distanceTolerance, 3)
        longestAllowedRunOfOneValue = $allowedRun
        records = $records.ToArray()
        captureCheckpoints = $captures.ToArray()
        boundaryTiming = $timing.ToArray()
        samples = $sampleCount
        generatedAt = (Get-Date).ToString('s')
    } | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath (Join-Path $stage 'drag-input.json') -Encoding UTF8
    Add-Check 'drag.trajectory_recorded' ((Test-Path (Join-Path $stage 'drag-input.json')) -and ($sampleCount -gt 500)) `
        @{ file = (Join-Path $stage 'drag-input.json'); records = $records.Count; samples = $sampleCount;
           captures = $captures.Count }
    Save-Checks
}

'colour' {
    $state = Read-AppState
    $original = Get-EditorImage $state 'W1' 'original'
    if (-not $original) { Add-Check 'colour.captures' $false @{ note = 'the editor put no image on the clipboard' }; Save-Checks; return }
    Add-Check 'colour.original_has_no_scheme' (((Colour-Of $original 'mist') -le 40) -and ((Colour-Of $original 'clay') -le 40)) `
        @{ mist = (Colour-Of $original 'mist'); clay = (Colour-Of $original 'clay'); white = $original.white; size = @($original.width, $original.height); file = $original.file }
    Click-Chip $state (Window-Of $state 'W1') $CHIP_MIST
    $mist = Get-EditorImage $state 'W1' 'mist'
    $oldPitchReadout = [N]::MeanOfColour($mist.file, 153, 0, 102, 3)
    Add-Check 'colour.pitch_readout_uses_the_scheme' ([int64] $oldPitchReadout.count -le 2) `
        @{ oldMagentaPixels = $oldPitchReadout.count; file = $mist.file }
    Add-Check 'colour.mist_present' ((Scheme-Marks-The-Image $mist 'mist' 'clay').drawn) `
        @{ mist = (Colour-Of $mist 'mist'); clay = (Colour-Of $mist 'clay'); white = $mist.white; file = $mist.file }
    Add-Check 'colour.mist_markings' (((Colour-Of $mist 'pulse') -gt 20) -and ((Colour-Of $mist 'pitch') -gt 20) -and `
            ((Colour-Of $mist 'formant') -gt 20) -and ((Colour-Of $mist 'intensity') -gt 20) -and ((Colour-Of $mist 'waveform') -gt 200)) `
        @{ pulse = (Colour-Of $mist 'pulse'); pitch = (Colour-Of $mist 'pitch'); formant = (Colour-Of $mist 'formant'); intensity = (Colour-Of $mist 'intensity'); waveform = (Colour-Of $mist 'waveform') }
    $marks = [ordered] @{
        pulse_vertical_strokes = [N]::Strokes($mist.file, 79,96,84, 10, 0, 8)
        pitch_horizontal_strokes = [N]::Strokes($mist.file, 117,97,63, 10, 1, 4)
        formant_horizontal_strokes = [N]::Strokes($mist.file, 49,95,91, 10, 1, 2)
        intensity_horizontal_strokes = [N]::Strokes($mist.file, 89,97,107, 10, 1, 2)
        waveform_vertical_strokes = [N]::Strokes($mist.file, 63,70,77, 10, 0, 2)
    }
    # Pulses, pitch tracks, formant bands and the intensity contour are separate marks, so they are
    # counted. The waveform is one continuous trace across the window, so it is judged by coverage.
    Add-Check 'colour.every_marking_is_drawn' (([int64] $marks.pulse_vertical_strokes.strokes -gt 4) -and `
            ([int64] $marks.pitch_horizontal_strokes.strokes -gt 4) -and ([int64] $marks.formant_horizontal_strokes.strokes -gt 4) -and `
            ([int64] $marks.intensity_horizontal_strokes.strokes -gt 4) -and ([int64] $marks.waveform_vertical_strokes.pixels -gt 20000)) $marks
    Add-Check 'colour.cores_have_the_measured_width' (([int64] $marks.pulse_vertical_strokes.medianWidth -ge 1) -and `
            ([int64] $marks.pitch_horizontal_strokes.medianWidth -ge 1) -and ([int64] $marks.formant_horizontal_strokes.medianWidth -ge 2) -and `
            ([int64] $marks.intensity_horizontal_strokes.medianWidth -le 3)) $marks
    $measured = [pscustomobject] [ordered] @{
        mist = Measured-Contrast $mist.file 'mist'
        pulse = Measured-Contrast $mist.file 'pulse'
        pitch = Measured-Contrast $mist.file 'pitch'
        formant = Measured-Contrast $mist.file 'formant'
        intensity = Measured-Contrast $mist.file 'intensity'
        waveform = Measured-Contrast $mist.file 'waveform'
        coordinate = Measured-Contrast $mist.file 'coordinate'
    }
    Click-Chip $state (Window-Of $state 'W1') $CHIP_CLAY
    $clay = Get-EditorImage $state 'W1' 'clay'
    Add-Check 'colour.clay_present' ((Scheme-Marks-The-Image $clay 'clay' 'mist').drawn) `
        @{ clay = (Colour-Of $clay 'clay'); mist = (Colour-Of $clay 'mist'); file = $clay.file }
    Add-Check 'colour.clay_keeps_the_functionals' (((Colour-Of $clay 'pitch') -gt 20) -and ((Colour-Of $clay 'waveform') -gt 200)) `
        @{ pitch = (Colour-Of $clay 'pitch'); waveform = (Colour-Of $clay 'waveform'); pulse = (Colour-Of $clay 'pulse') }
    $clayMeasured = Measured-Contrast $clay.file 'clay'
    $ratio = [pscustomobject] [ordered] @{
        mist = $measured.mist.onWhite; clay = $clayMeasured.onWhite
        pulse = $measured.pulse.onWhite; pitch = $measured.pitch.onWhite
        formant = $measured.formant.onWhite; intensity = $measured.intensity.onWhite
        waveform = $measured.waveform.onWhite; coordinate = $measured.coordinate.onWhite
    }
    $undrawn = @()
    foreach ($entry in $measured.PSObject.Properties) {
        if (-not $entry.Value.drawn) { $undrawn += $entry.Name }
    }
    Add-Check 'colour.rendered_schemes_balance' (($undrawn.Count -eq 0) -and $clayMeasured.drawn -and `
            ([math]::Abs($ratio.mist - $ratio.clay) -lt 0.35) -and `
            ($ratio.mist -gt 3.0) -and ($ratio.mist -lt 4.5) -and ($ratio.clay -gt 3.0) -and ($ratio.clay -lt 4.5)) `
        @{ rendered = $ratio; tooFewPixels = $undrawn; pixels = [pscustomobject] [ordered] @{ mist = $measured.mist.pixels; clay = $clayMeasured.pixels };
           drawnMist = $measured.mist.colour; drawnClay = $clayMeasured.colour }
    Add-Check 'colour.rendered_functionals_are_darker' (($ratio.pulse -gt $ratio.mist) -and ($ratio.pitch -gt $ratio.mist) -and `
            ($ratio.formant -gt $ratio.mist) -and ($ratio.intensity -gt $ratio.mist) -and ($ratio.waveform -gt 5) -and ($ratio.coordinate -gt 8)) `
        @{ rendered = $ratio; colours = [pscustomobject] [ordered] @{ pulse = $measured.pulse.colour; pitch = $measured.pitch.colour;
             formant = $measured.formant.colour; intensity = $measured.intensity.colour; waveform = $measured.waveform.colour } }
    $design = [pscustomobject] [ordered] @{
        mist = Design-Contrast 'mist'; clay = Design-Contrast 'clay'; pulse = Design-Contrast 'pulse'
        pitch = Design-Contrast 'pitch'; formant = Design-Contrast 'formant'; intensity = Design-Contrast 'intensity'
        waveform = Design-Contrast 'waveform'; coordinate = Design-Contrast 'coordinate'
    }
    Add-Check 'colour.design_contrast_arithmetic' (([math]::Abs($design.mist - $design.clay) -lt 0.2) -and `
            ($design.mist -gt 3.0) -and ($design.mist -lt 4.5) -and ($design.clay -gt 3.0) -and ($design.clay -lt 4.5) -and `
            ($design.pulse -gt $design.mist) -and ($design.pitch -gt $design.mist) -and ($design.formant -gt $design.mist) -and `
            ($design.waveform -gt 5) -and ($design.coordinate -gt 8)) `
        @{ note = 'arithmetic on the design constants, not evidence by itself'; design = $design }
    Add-Check 'colour.legend_and_grid_are_drawn' (((Colour-Of $mist 'coordinate') -gt 20) -and ((Colour-Of $mist 'grid') -gt 20)) `
        @{ coordinate = (Colour-Of $mist 'coordinate'); grid = (Colour-Of $mist 'grid'); tick = (Colour-Of $mist 'tick') }
    Click-Chip $state (Window-Of $state 'W1') $CHIP_ORIGINAL
    $restored = Get-EditorImage $state 'W1' 'restored'
    Add-Check 'colour.original_restores_exactly' (([string] $original.sha256 -eq [string] $restored.sha256) -and `
            ((Colour-Of $restored 'mist') -le 40) -and ((Colour-Of $restored 'clay') -le 40)) `
        @{ sameBytes = ([string] $original.sha256 -eq [string] $restored.sha256); mist = (Colour-Of $restored 'mist'); clay = (Colour-Of $restored 'clay');
           originalSha = $original.sha256; restoredSha = $restored.sha256 }
    Save-Checks
}

'overlay' {
    $state = Read-AppState
    $first = Window-Of $state 'W1'
    $second = Window-Of $state 'W2'
    Click-Chip $state $second $CHIP_ORIGINAL
    $null = Set-Opacity $state $first 100
    $place = @(320, 90, 900, 620)
    $null = [N]::Place($second, $place[0], $place[1], $place[2], $place[3])
    $null = [N]::Place($first, $place[0], $place[1], $place[2], $place[3])
    $drawing = Drawing-Area $first
    Bring-Out $second
    $underneath = Get-ScreenBand $drawing 'overlay-underneath-original-window'
    $meanUnder = [N]::MeanColour($underneath.file, 40, 40, ([int] $underneath.width) - 40, ([int] $underneath.height) - 40)
    foreach ($scheme in @(@('mist',$CHIP_MIST), @('clay',$CHIP_CLAY))) {
        Click-Chip $state $first $scheme[1]
        $null = Set-Opacity $state $first 100
        Bring-Out $first
        $opaque = Get-ScreenBand $drawing "overlay-$($scheme[0])-at-100"
        $meanOpaque = [N]::MeanColour($opaque.file, 40, 40, ([int] $opaque.width) - 40, ([int] $opaque.height) - 40)
        foreach ($percent in @(20,50)) {
            $position = Set-Opacity $state $first $percent
            Bring-Out $first
            $mixed = Get-ScreenBand $drawing "overlay-$($scheme[0])-at-$percent"
            $meanMixed = [N]::MeanColour($mixed.file, 40, 40, ([int] $mixed.width) - 40, ([int] $mixed.height) - 40)
            $blendError = 0
            for ($channel = 0; $channel -lt 3; $channel++) {
                $expected = $meanOpaque[$channel] * $percent / 100.0 + $meanUnder[$channel] * (100-$percent) / 100.0
                $blendError += [math]::Abs($meanMixed[$channel] - $expected)
            }
            Add-Check "overlay.$($scheme[0])_over_original_at_$percent" (($position -eq $percent) -and `
                    ([N]::Alpha($first) -eq (Expected-Alpha $percent)) -and ($blendError -lt 6)) `
                @{ opaqueMean = $meanOpaque; mixedMean = $meanMixed; underneathMean = $meanUnder; channelError = $blendError; file = $mixed.file }
        }
    }
    $null = Set-Opacity $state $first 100
    Click-Chip $state $first $CHIP_ORIGINAL
    Save-Checks
}
'selection' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $other = Window-Of $state 'W2'
    # Earlier phases move these windows around, so both are put back at a known size first and the
    # drag is expressed as a fraction of the window rather than as fixed pixels.
    $place = @([Math]::Max(0, $WORK_AREA.Left + 40), [Math]::Max(0, $WORK_AREA.Top + 20), 900, 620)
    $null = [N]::Place($other, $place[0], $place[1], $place[2], $place[3])
    $null = [N]::Place($root, $place[0], $place[1], $place[2], $place[3])
    Click-Chip $state $root $CHIP_MIST
    Bring-Out $root
    $drawing = Drawing-Area $root
    $canvas = [N]::ClientRectOnScreen($drawing)
    # The waveform band at the top is the one place where the fill lies on plain white: lower down
    # the spectrogram covers the background, so a fill there would prove nothing either way.
    $bandY = [int] ($canvas[1] + ($canvas[3] - $canvas[1]) * 0.15)
    $fromX = [int] ($canvas[0] + ($canvas[2] - $canvas[0]) * 0.32)
    $toX = [int] ($canvas[0] + ($canvas[2] - $canvas[0]) * 0.58)
    # A click collapses whatever a previous run left selected, so the “before” picture is a blank slate.
    [N]::Click([int] ($canvas[0] + ($canvas[2] - $canvas[0]) * 0.2), $bandY)
    Start-Sleep -Milliseconds 500
    $beforeMist = Get-ScreenBand $drawing 'selection-mist-before'
    [N]::Drag($fromX, $bandY, $toX, $bandY, 10)
    Start-Sleep -Milliseconds 600
    $withScheme = Get-ScreenBand $drawing 'selection-mist'
    $greyBeforeMist = Colour-Of $beforeMist 'selection'
    $greyMist = Colour-Of $withScheme 'selection'
    $changedMist = [N]::Diff($beforeMist.file, $withScheme.file)
    # Some spectrogram levels are already this grey, so the evidence is the increase the drag causes.
    Add-Check 'selection.the_scheme_fills_with_neutral_grey' (([int64] $greyMist -gt 3000) -and `
            (([int64] $greyMist - [int64] $greyBeforeMist) -gt 3000) -and ([int64] $changedMist.changed -gt 5000)) `
        @{ greyBefore = $greyBeforeMist; greyAfter = $greyMist; changed = $changedMist.changed; file = $withScheme.file }
    Bring-Out $other
    $drawingOther = Drawing-Area $other
    $canvasOther = [N]::ClientRectOnScreen($drawingOther)
    $bandOther = [int] ($canvasOther[1] + ($canvasOther[3] - $canvasOther[1]) * 0.15)
    $fromOther = [int] ($canvasOther[0] + ($canvasOther[2] - $canvasOther[0]) * 0.32)
    $toOther = [int] ($canvasOther[0] + ($canvasOther[2] - $canvasOther[0]) * 0.58)
    [N]::Click([int] ($canvasOther[0] + ($canvasOther[2] - $canvasOther[0]) * 0.2), $bandOther)
    Start-Sleep -Milliseconds 500
    $beforeOriginal = Get-ScreenBand $drawingOther 'selection-original-before'
    [N]::Drag($fromOther, $bandOther, $toOther, $bandOther, 10)
    Start-Sleep -Milliseconds 600
    $withHighlight = Get-ScreenBand $drawingOther 'selection-original'
    $greyBeforeOriginal = Colour-Of $beforeOriginal 'selection'
    $greyOriginal = Colour-Of $withHighlight 'selection'
    $changedOriginal = [N]::Diff($beforeOriginal.file, $withHighlight.file)
    # The original window does show a selection, but it is not the new neutral fill: whatever grey
    # the base editor already draws stays within a few antialiased pixels of what it drew before.
    Add-Check 'selection.the_original_highlight_is_unchanged' (([int64] $changedOriginal.changed -gt 5000) -and `
            ([math]::Abs([int64] $greyOriginal - [int64] $greyBeforeOriginal) -lt 60)) `
        @{ greyBefore = $greyBeforeOriginal; greyAfter = $greyOriginal; changed = $changedOriginal.changed; file = $withHighlight.file }
    Add-Check 'selection.the_two_fills_are_distinguishable' (([int64] $greyMist - [int64] $greyOriginal) -gt 3000) `
        @{ schemeGrey = $greyMist; originalGrey = $greyOriginal }
    Click-Chip $state $root $CHIP_ORIGINAL
    Save-Checks
}

'isolation' {
    $state = Read-AppState
    $third = Window-Of $state 'W3'
    $first = Window-Of $state 'W1'
    # The LongSound window was shrunk to a corner by an earlier phase; at that size it draws almost
    # nothing, so put it back where its own spectrogram is visible.
    $null = [N]::Place($third, [Math]::Max(0, $WORK_AREA.Left + 40), [Math]::Max(0, $WORK_AREA.Top + 20), 900, 620)
    $null = [N]::Place($first, [Math]::Max(0, $WORK_AREA.Left + 40), [Math]::Max(0, $WORK_AREA.Top + 20), 900, 620)
    Start-Sleep -Milliseconds 500
    Click-Chip $state $third $CHIP_CLAY
    $position = Set-Opacity $state $third 40
    Start-Sleep -Milliseconds 300
    $clayInLongSound = Get-EditorImage $state 'W3' 'clay-alone'
    $imageInSound = Get-EditorImage $state 'W1' 'untouched'
    $report = [ordered] @{
        W1 = @{ layered = [N]::IsLayered((Window-Of $state 'W1')); alpha = [N]::Alpha((Window-Of $state 'W1')) }
        W2 = @{ layered = [N]::IsLayered((Window-Of $state 'W2')); alpha = [N]::Alpha((Window-Of $state 'W2')) }
        W3 = @{ layered = [N]::IsLayered($third); alpha = [N]::Alpha($third); position = $position }
        W4 = @{ layered = [N]::IsLayered((Window-Of $state 'W4')); alpha = [N]::Alpha((Window-Of $state 'W4')) }
        W5 = @{ layered = [N]::IsLayered((Window-Of $state 'W5')); alpha = [N]::Alpha((Window-Of $state 'W5')) }
    }
    Add-Check 'isolation.state_is_per_window' (([int64] $report.W3.alpha -eq (Expected-Alpha 40)) -and `
            (-not $report.W1.layered) -and (-not $report.W2.layered) -and (-not $report.W4.layered) -and (-not $report.W5.layered)) $report
    Add-Check 'isolation.scheme_is_per_window' (((Scheme-Marks-The-Image $clayInLongSound 'clay' 'mist').drawn) -and `
            ((Colour-Of $imageInSound 'clay') -le 40) -and ((Colour-Of $imageInSound 'mist') -le 40)) `
        @{ longSoundClay = (Colour-Of $clayInLongSound 'clay'); soundClay = (Colour-Of $imageInSound 'clay'); soundMist = (Colour-Of $imageInSound 'mist') }
    Click-Chip $state $third $CHIP_ORIGINAL
    $null = Set-Opacity $state $third 100
    Add-Check 'isolation.the_other_window_can_be_reset' ((-not [N]::IsLayered($third)) -and ([N]::Alpha($third) -eq -1)) `
        @{ layered = [N]::IsLayered($third); alpha = [N]::Alpha($third) }
    Save-Checks
}

'keyboard' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $null = [N]::Place($root, 40, 45, 1200, 740)
    $processId = [int] $state.processId
    Click-Chip $state $root $CHIP_ORIGINAL
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    $originalChip = Panel-Control $panel $CHIP_ORIGINAL
    $mistChip = Panel-Control $panel $CHIP_MIST
    $clayChip = Panel-Control $panel $CHIP_CLAY
    $focused = [N]::FocusedInProcess($processId)
    Add-Check 'keyboard.the_current_scheme_chip_starts_with_the_focus' ($focused -eq $originalChip) `
        @{ focused = ('0x' + ([int64] $focused).ToString('X')); originalChip = ('0x' + ([int64] $originalChip).ToString('X'))
           foreground = [N]::Class([N]::GetForeground()) }
    foreach ($target in @($mistChip, $clayChip, (Panel-Control $panel $SLIDER_ID), $originalChip)) {
        [N]::KeyPress($VK_TAB)
        Start-Sleep -Milliseconds 150
        Add-Check ('keyboard.tab_visual_order_' + [N]::ControlId($target)) (([N]::FocusedInProcess($processId)) -eq $target) @{}
    }
    [N]::KeyPress($VK_RIGHT)
    Start-Sleep -Milliseconds 250
    $focused = [N]::FocusedInProcess($processId)
    Add-Check 'keyboard.right_moves_to_the_next_chip' ($focused -eq $mistChip) `
        @{ focused = ('0x' + ([int64] $focused).ToString('X')); mistChip = ('0x' + ([int64] $mistChip).ToString('X')) }
    [N]::KeyPress($VK_RETURN)
    Start-Sleep -Milliseconds 600
    Close-AnyPanel $state
    $activated = Get-EditorImage $state 'W1' 'keyboard-space'
    Add-Check 'keyboard.enter_activates_the_focused_chip' ((Scheme-Marks-The-Image $activated 'mist' 'clay').drawn) `
        @{ mist = (Colour-Of $activated 'mist'); clay = (Colour-Of $activated 'clay'); file = $activated.file }
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    $focused = [N]::FocusedInProcess($processId)
    Add-Check 'keyboard.reopening_follows_the_scheme' ($focused -eq (Panel-Control $panel $CHIP_MIST)) `
        @{ focused = ('0x' + ([int64] $focused).ToString('X')); mistChip = ('0x' + ([int64] (Panel-Control $panel $CHIP_MIST)).ToString('X')) }
    [N]::KeyPress($VK_RIGHT)
    Start-Sleep -Milliseconds 200
    [N]::KeyPress($VK_RIGHT)
    Start-Sleep -Milliseconds 200
    $focused = [N]::FocusedInProcess($processId)
    Add-Check 'keyboard.the_navigation_wraps' ($focused -eq (Panel-Control $panel $CHIP_ORIGINAL)) `
        @{ focused = ('0x' + ([int64] $focused).ToString('X')); originalChip = ('0x' + ([int64] (Panel-Control $panel $CHIP_ORIGINAL)).ToString('X')) }
    [N]::KeyPress($VK_LEFT)
    Start-Sleep -Milliseconds 200
    $focused = [N]::FocusedInProcess($processId)
    Add-Check 'keyboard.left_wraps_back_to_a_scheme' ($focused -eq (Panel-Control $panel $CHIP_CLAY)) `
        @{ focused = ('0x' + ([int64] $focused).ToString('X')); clayChip = ('0x' + ([int64] (Panel-Control $panel $CHIP_CLAY)).ToString('X')) }
    [N]::KeyPress($VK_SPACE)
    Start-Sleep -Milliseconds 600
    Close-AnyPanel $state
    $wrapped = Get-EditorImage $state 'W1' 'keyboard-wrap'
    Add-Check 'keyboard.the_wrapped_chip_draws_its_scheme' ((Scheme-Marks-The-Image $wrapped 'clay' 'mist').drawn) `
        @{ clay = (Colour-Of $wrapped 'clay'); mist = (Colour-Of $wrapped 'mist'); file = $wrapped.file }
    Click-Chip $state $root $CHIP_ORIGINAL
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    $slider = Panel-Control $panel $SLIDER_ID
    Focus-Slider $slider
    Add-Check 'keyboard.the_slider_takes_the_focus_from_a_click' (([N]::FocusedInProcess($processId)) -eq $slider) `
        @{ focused = ('0x' + ([int64] [N]::FocusedInProcess($processId)).ToString('X')); slider = ('0x' + ([int64] $slider).ToString('X')) }
    [N]::KeyPress($VK_END)
    Start-Sleep -Milliseconds 300
    1..5 | ForEach-Object { [N]::KeyPress($VK_LEFT); Start-Sleep -Milliseconds 90 }
    $position = [N]::TrackbarPosition($slider)
    Add-Check 'keyboard.the_slider_steps_by_one' (($position -eq 95) -and ([N]::Alpha($root) -eq (Expected-Alpha 95)) -and ((Readout $panel) -eq '95%')) `
        @{ position = $position; alpha = [N]::Alpha($root); readout = (Readout $panel) }
    [N]::KeyPress($VK_ESCAPE)
    Start-Sleep -Milliseconds 350
    Add-Check 'keyboard.escape_closes_and_keeps' (((Panel-Window $state) -eq [IntPtr]::Zero) -and ([N]::Alpha($root) -eq (Expected-Alpha 95))) `
        @{ panels = @(Find-By-Class $state 'PraatAppearancePanel*' | Where-Object { [N]::Visible($_) }).Count; alpha = [N]::Alpha($root) }
    Add-Check 'keyboard.the_editor_gets_the_foreground_back' (([N]::GetForeground()) -eq $root) `
        @{ foreground = [N]::Class([N]::GetForeground()); title = [N]::Text([N]::GetForeground()) }
    $null = Set-Opacity $state $root 100
    Click-Chip $state $root $CHIP_ORIGINAL
    Save-Checks
}

'dismiss' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $opened = Open-Panel $state $root
    $panel = $opened.panel
    $slider = Panel-Control $panel $SLIDER_ID
    Focus-Slider $slider
    [N]::KeyPress($VK_HOME)
    Start-Sleep -Milliseconds 350
    $rectangle = [N]::Rect($root)
    [N]::Click([int] ($rectangle[0] + 60), [int] ($rectangle[1] + 4))     # the caption: outside the panel
    Start-Sleep -Milliseconds 500
    Add-Check 'dismiss.a_click_elsewhere_closes' ((Panel-Window $state) -eq [IntPtr]::Zero) `
        @{ visiblePanels = @(Find-By-Class $state 'PraatAppearancePanel*' | Where-Object { [N]::Visible($_) }).Count }
    Add-Check 'dismiss.no_alpha_edge_layer_left_visible' (@(Find-By-Class $state 'PraatAppearanceChrome').Count -eq 0) @{}
    Add-Check 'dismiss.the_values_survive' ([N]::Alpha($root) -eq (Expected-Alpha 20)) @{ alpha = [N]::Alpha($root) }
    $again = Open-Panel $state $root
    Add-Check 'dismiss.reopens_with_the_current_state' ((Readout $again.panel) -eq '20%') `
        @{ readout = (Readout $again.panel); position = [N]::TrackbarPosition((Panel-Control $again.panel $SLIDER_ID)) }
    $click = [N]::MenuRectOnScreen($root, [N]::MenuOf($root), $again.entry.position)
    [N]::Click([int] (($click[0] + $click[2]) / 2), [int] (($click[1] + $click[3]) / 2))
    Start-Sleep -Milliseconds 450
    Add-Check 'dismiss.a_second_click_on_the_entry_closes' ((Panel-Window $state) -eq [IntPtr]::Zero) `
        @{ entry = $again.entry.text }
    Add-Check 'dismiss.entry_click_hides_the_edge_layer' (@(Find-By-Class $state 'PraatAppearanceChrome').Count -eq 0) @{}
    $null = Set-Opacity $state $root 100
    Save-Checks
}

'clipboard' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $foundScreenshot = Command-Id $state 'W1' @("*$T_COPY_SCREENSHOT*", '*Copy pronunciation screenshot*') 'screenshot'
    $foundQuestion = Command-Id $state 'W1' @("*$T_COPY_QUESTION*") 'question'
    $foundBoth = Command-Id $state 'W1' @("*$T_COPY_BOTH*") 'image and question'
    Add-Check 'clipboard.commands_exist' $true @{ screenshot = $foundScreenshot; question = $foundQuestion; both = $foundBoth }
    # A known size makes the crop measurements of this phase reproducible.
    $null = [N]::Place($root, 320, 90, 900, 620)
    Start-Sleep -Milliseconds 500
    Click-Chip $state $root $CHIP_MIST
    $null = Set-Opacity $state $root 100

    $opaque = Grab-Copy $state 'W1' $foundScreenshot.id 'mist-at-100'
    Add-Check 'clipboard.image_is_fully_opaque' ((([int64] $opaque.dib.minAlpha -eq 255) -and ([int64] $opaque.dib.nonOpaque -eq 0)) -and `
            ([int64] $opaque.image.white -gt 5000)) `
        @{ minAlpha = $opaque.dib.minAlpha; maxAlpha = $opaque.dib.maxAlpha; nonOpaque = $opaque.dib.nonOpaque; white = $opaque.image.white;
           size = @($opaque.image.width, $opaque.image.height); file = $opaque.path }
    $carries = Scheme-Marks-The-Image $opaque.image 'mist' 'clay'
    Add-Check 'clipboard.image_has_the_scheme' ($carries.drawn -and ([int64] $opaque.dib.minAlpha -eq 255)) `
        @{ mist = $carries.present; clay = $carries.other; white = $opaque.image.white; file = $opaque.path }
    # The screenshot command writes the image on its own; the text and the HTML live with the combined command.
    Add-Check 'clipboard.image_formats' ((($opaque.formats -contains 'PNG') -and ($opaque.formats -contains 'CF_DIB') -and `
            ($opaque.formats -contains 'CF_DIBV5')) -and (-not ($opaque.formats -contains 'CF_UNICODETEXT')) -and `
            (-not ($opaque.formats -contains 'HTML Format'))) `
        @{ formats = $opaque.formats; alpha = [N]::Alpha($root) }
    # What the user sees in the window at this moment, measured the same way as the copy.
    $drawing = Drawing-Area $root
    $onScreen = Get-ScreenBand $drawing 'clipboard-mist-at-100'
    $spanInCopy = [N]::Strokes($opaque.path, 79,96,84, 10, 0, 8)
    $spanOnScreen = [N]::Strokes($onScreen.file, 79,96,84, 10, 0, 8)
    Add-Check 'clipboard.copy_shows_the_visible_span' (([int64] $spanInCopy.strokes -gt 4) -and `
            ([math]::Abs(([int64] $spanInCopy.strokes) - ([int64] $spanOnScreen.strokes)) -le 1)) `
        @{ inCopy = $spanInCopy; onScreen = $spanOnScreen }

    Click-Chip $state $root $CHIP_CLAY
    $clayCopy = Grab-Copy $state 'W1' $foundScreenshot.id 'clay-at-100'
    $carriesClay = Scheme-Marks-The-Image $clayCopy.image 'clay' 'mist'
    $betweenSchemes = [N]::Diff($opaque.path, $clayCopy.path)
    Add-Check 'clipboard.copy_follows_the_window_scheme' (($carriesClay.drawn) -and (([int64] $betweenSchemes.changed) -gt 0.1 * [int64] $betweenSchemes.pixels)) `
        @{ clay = $carriesClay.present; mist = $carriesClay.other; changed = $betweenSchemes.changed; pixels = $betweenSchemes.pixels;
           meanDifference = $betweenSchemes.meanDifference }
    Click-Chip $state $root $CHIP_MIST

    $null = Set-Opacity $state $root 20
    $alpha = [N]::Alpha($root)
    Add-Check 'clipboard.the_window_is_translucent' (($alpha -gt 0) -and ($alpha -lt 100)) `
        @{ alpha = $alpha; layered = [N]::IsLayered($root) }
    $translucent = Grab-Copy $state 'W1' $foundScreenshot.id 'mist-at-20'
    Add-Check 'clipboard.image_does_not_depend_on_window_alpha' (([string] $translucent.image.sha256 -eq [string] $opaque.image.sha256) -and `
            ((@($translucent.image.width, $translucent.image.height) -join 'x') -eq (@($opaque.image.width, $opaque.image.height) -join 'x')) -and `
            ([int64] $translucent.dib.minAlpha -eq 255)) `
        @{ at20 = @{ sha = $translucent.image.sha256; size = @($translucent.image.width, $translucent.image.height); minAlpha = $translucent.dib.minAlpha };
           at100 = @{ sha = $opaque.image.sha256; size = @($opaque.image.width, $opaque.image.height) } }

    # The design asks for a pattern behind the window: if any of it reached the copy, the copy would be
    # a photograph of the screen rather than a redraw of the editor.
    $rectangle = [N]::Rect($root)
    $x = [int] $rectangle[0]; $y = [int] $rectangle[1]
    $width = [int] ($rectangle[2] - $rectangle[0]); $height = [int] ($rectangle[3] - $rectangle[1])
    foreach ($role in @('W2','W3','W4','W5','list')) {
        $other = Window-Of $state $role
        $null = [N]::Place($other, [Math]::Max(0, $WORK_AREA.Left), [Math]::Max(0, $WORK_AREA.Top), 300, 200)
    }
    Start-Sleep -Milliseconds 300
    $backdrop = New-Lime-Backdrop $x $y $width $height
    [N]::BringToTop($root)
    Start-Sleep -Milliseconds 400
    $limePath = Join-Path $stage 'screen-mist-at-20-on-lime.png'
    if (Test-Path $limePath) { Remove-Item -LiteralPath $limePath -Force }
    $null = [N]::SaveScreenPng($x, $y, $width, $height, $limePath)
    $limeOnScreen = Lime-In $limePath
    Add-Check 'clipboard.background_is_visible_on_screen' (([int64] $limeOnScreen) -gt 0.4 * $width * $height) `
        @{ lime = $limeOnScreen; pixels = ($width * $height); file = $limePath }
    $onLime = Grab-Copy $state 'W1' $foundScreenshot.id 'mist-at-20-on-lime'
    $limeInCopy = Lime-In $onLime.path
    Add-Check 'clipboard.no_background_enters_the_copy' ((([int64] $limeInCopy) -eq 0) -and `
            ([string] $onLime.image.sha256 -eq [string] $translucent.image.sha256) -and (([int64] $onLime.image.nonOpaque) -eq 0)) `
        @{ limeOnScreen = $limeOnScreen; limeInCopy = $limeInCopy; screenFile = $limePath; copyFile = $onLime.path;
           sameBytesAsWithoutBackdrop = ([string] $onLime.image.sha256 -eq [string] $translucent.image.sha256) }
    $backdrop.Close()
    1..4 | ForEach-Object { [System.Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 80 }
    $null = Set-Opacity $state $root 100

    # The crop the design keeps: everything from just under the top of the drawing area to its bottom
    # edge, and none of the selection viewer. The viewer is switched on the way a user switches it.
    $canvas = [N]::ClientRectOnScreen($drawing)
    $canvasWidth = [int] ($canvas[2] - $canvas[0]); $canvasHeight = [int] ($canvas[3] - $canvas[1])
    $cropWithoutViewer = @($opaque.image.width, $opaque.image.height)
    $cutAtTheTop = $canvasHeight - [int] $cropWithoutViewer[1]
    Add-Check 'clipboard.copy_spans_the_drawing_area' ((([math]::Abs([int] $cropWithoutViewer[0] - $canvasWidth) -le 4)) -and `
            (($cutAtTheTop -ge 0) -and ($cutAtTheTop -le 40))) `
        @{ image = $cropWithoutViewer; canvas = @($canvasWidth, $canvasHeight); cutAtTheTop = $cutAtTheTop }

    $null = [N]::ClearClipboard()
    [N]::SendCommand($root, $foundQuestion.id)
    Start-Sleep -Milliseconds 1500
    $text = [N]::ClipboardText()
    $formatsText = @([N]::ClipboardFormats() | ForEach-Object { [N]::ClipboardFormatName($_) })
    Add-Check 'clipboard.question_text_only' ((($text -eq ($T_PROMPT_HEAD + 'APPEARANCE-W1' + $T_PROMPT_TAIL)) -or `
            ($text -like ($T_PROMPT_HEAD + '*' + $T_PROMPT_TAIL))) -and (-not ($formatsText -contains 'CF_DIB'))) `
        @{ text = $text; formats = $formatsText }
    $null = [N]::ClearClipboard()
    [N]::SendCommand($root, $foundBoth.id)
    Start-Sleep -Milliseconds 3000
    $dibBoth = [N]::ClipboardDib($COLOUR_FLAT, 6)
    $formatsBoth = @([N]::ClipboardFormats() | ForEach-Object { [N]::ClipboardFormatName($_) })
    $textBoth = [N]::ClipboardText()
    Add-Check 'clipboard.image_and_text_together' ((($formatsBoth -contains 'CF_UNICODETEXT') -and ($formatsBoth -contains 'HTML Format') -and `
            ($formatsBoth -contains 'CF_DIB') -and ($formatsBoth -contains 'PNG')) -and ([int64] $dibBoth.minAlpha -eq 255) -and `
            ($textBoth -like ($T_PROMPT_HEAD + '*' + $T_PROMPT_TAIL))) `
        @{ formats = $formatsBoth; text = $textBoth; minAlpha = $dibBoth.minAlpha; white = $dibBoth.white }
    # The three copy commands live in the sound editor alone (v_hasAiToolbar), and the sound editor draws
    # no selection viewer, so the crop the rule keeps is the whole drawing area: the width check above is
    # that rule measured where it is actually reachable. The TextGrid editor carries the appearance state
    # but has no pronunciation-screenshot command, so its image output is checked as a Praat Picture.
    Click-Chip $state $root $CHIP_ORIGINAL
    Save-Checks
}

'picture' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $found = Command-Id $state 'W1' @("Paint visible spectrogram*", "*$T_PAINT_SPECTROGRAM*") 'paint visible spectrogram'
    Add-Check 'picture.command_found' $true $found
    $before = @(Top-Of-Process $state | ForEach-Object { ([int64] $_.ToInt64()).ToString() })
    Click-Chip $state $root $CHIP_MIST
    $null = Set-Opacity $state $root 20
    Bring-Out $root
    [N]::SendCommand($root, $found.id)
    Start-Sleep -Milliseconds 1200
    $dialog = @(Top-Of-Process $state) | Where-Object { (([N]::MenuOf($_)) -eq [IntPtr]::Zero) -and `
            (([N]::Class($_)) -notmatch 'Praat(Shell|LayeredShell|DrawingArea|AppearancePanel|PronunciationCopiedToast)') } |
        Select-Object -Last 1
    if ($dialog) {
        $null = [N]::ForceForeground($dialog)
        Start-Sleep -Milliseconds 250
        [N]::KeyPress($VK_RETURN)
        Start-Sleep -Milliseconds 3000
        Add-Check 'picture.form_accepted' $true @{ dialog = [N]::Class($dialog); text = [N]::Text($dialog) }
    } else {
        Add-Check 'picture.form_accepted' $false @{ note = 'no form window appeared'; windows = @(Top-Of-Process $state).Count }
    }
    $new = @(Top-Of-Process $state) | Where-Object { [N]::Text($_) -eq 'Praat Picture' }
    Add-Check 'picture.window_opened' (@($new).Count -ge 1) `
        @{ titles = (@($new | ForEach-Object { [N]::Text($_) + ' [' + [N]::Class($_) + ']' })) }
    if (@($new).Count -ge 1) {
        $picture = $new[0]
        $drawing = Drawing-Area $picture
        Bring-Out $picture
        $captured = Get-ScreenBand $drawing 'picture-window' $picture
        Add-Check 'picture.painted_with_the_scheme' (((Scheme-Marks-The-Image $captured 'mist' 'clay').drawn) -and `
                (([int64] $captured.nonOpaque -eq 0) -and ([int64] $captured.white -gt 2000))) `
            @{ mist = (Colour-Of $captured 'mist'); clay = (Colour-Of $captured 'clay'); white = $captured.white; nonOpaque = $captured.nonOpaque; size = @($captured.width, $captured.height); file = $captured.file }
    }
    Log-Note 'picture: native viewport captured'
    # The running instance has the painted picture; a new instance has no coloured content.
    $nameFile = Join-Path $stage 'picture-name.txt'
    $exportPath = (Join-Path $stage 'picture-export.png') -replace '\\','/'
    $textFile = Join-Path $stage 'picture-send.praat'
    foreach ($file in @($nameFile, $exportPath)) { if (Test-Path $file) { Remove-Item -LiteralPath $file -Force } }
    Set-Content -LiteralPath $textFile -Value @"
Save as 300-dpi PNG file: "$exportPath"
appendFileLine: "$(($nameFile -replace '\\','/'))", "written"
"@ -Encoding UTF8
    $sender = Start-Process -FilePath $state.executable -ArgumentList @('--no-pref-files', '--FULL-TRUST', '--send', $textFile) -WindowStyle Hidden -PassThru
    if (-not $sender.WaitForExit(20000)) { $sender.Kill(); throw 'Picture export did not finish within 20 seconds' }
    Log-Note 'picture: export command delivered'
    Start-Sleep -Milliseconds 1500
    $reported = ''
    if (Test-Path $nameFile) { $reported = [IO.File]::ReadAllText($nameFile) }
    Add-Check 'picture.ipc_reaches_the_running_instance' ((Test-Path $nameFile) -and ($reported -match 'written')) `
        @{ file = $nameFile; content = $reported; senderExitCode = $sender.ExitCode }
    if (Test-Path $exportPath) {
        $saved = [N]::AnalysePng($exportPath, $COLOUR_FLAT, 6)
        Log-Note 'picture: exported PNG measured'
        Add-Check 'picture.exported_file_uses_the_scheme' (((Scheme-Marks-The-Image $saved 'mist' 'clay').drawn) -and `
                ([int64] $saved.nonOpaque -eq 0) -and ([int64] $saved.white -gt 5000) -and ([N]::Alpha($root) -eq (Expected-Alpha 20))) `
            @{ mist = (Colour-Of $saved 'mist'); clay = (Colour-Of $saved 'clay'); nonOpaque = $saved.nonOpaque; size = @($saved.width, $saved.height); file = $exportPath }
        Log-Note 'picture: export assertion recorded'
    } else {
        Add-Check 'picture.exported_file_uses_the_scheme' $false @{ note = 'no PNG written'; content = $reported }
    }
    $null = Set-Opacity $state $root 100
    Log-Note 'picture: editor opacity restored'
    Click-Chip $state $root $CHIP_ORIGINAL
    Log-Note 'picture: editor palette restored'
    Save-Checks
}

'grid' {
    $state = Read-AppState
    $root = Window-Of $state 'W4'
    $null = [N]::Place($root, 320, 90, 900, 620)
    Click-Chip $state $root $CHIP_ORIGINAL
    Bring-Out $root
    $drawing = Drawing-Area $root
    $canvas = [N]::ClientRectOnScreen($drawing)
    $band = [int] ($canvas[1] + ($canvas[3] - $canvas[1]) * 0.15)
    [N]::Click($canvas[0] + 180, $band)
    Start-Sleep -Milliseconds 300
    $before = Get-ScreenBand $drawing 'grid-original'
    [N]::Drag($canvas[0] + 240, $band, $canvas[0] + 450, $band, 8)
    Start-Sleep -Milliseconds 400
    $originalSelection = Get-ScreenBand $drawing 'grid-original-selection'
    [N]::Click($canvas[0] + 180, $band)
    Click-Chip $state $root $CHIP_MIST
    Bring-Out $root
    $after = Get-ScreenBand $drawing 'grid-mist'
    Add-Check 'grid.scheme_reaches_the_sound_panes' ((Scheme-Marks-The-Image $after 'mist' 'clay').drawn) `
        @{ mist = (Colour-Of $after 'mist'); clay = (Colour-Of $after 'clay'); waveform = (Colour-Of $after 'waveform'); file = $after.file }
    [N]::Drag($canvas[0] + 240, $band, $canvas[0] + 450, $band, 8)
    Start-Sleep -Milliseconds 400
    $withSelection = Get-ScreenBand $drawing 'grid-mist-selection'
    $greyDelta = [int64] (Colour-Of $withSelection 'selection') - [int64] (Colour-Of $after 'selection')
    Add-Check 'grid.the_sound_panes_take_the_scheme_fill' ($greyDelta -gt 500) `
        @{ greyBefore = (Colour-Of $after 'selection'); greyAfter = (Colour-Of $withSelection 'selection'); delta = $greyDelta; file = $withSelection.file }
    # The annotation band uses its own yellow/orange theme, so compare the same selection directly.
    # Exclude the two time-boundary hairlines: their neutral scheme colour is intentional.
    $selectedRows = @([N]::RowChanges($originalSelection.file, $withSelection.file, 4, 250, 440))
    $blankRows = @([N]::RowChanges($before.file, $after.file, 4, 250, 440))
    $dragRows = @([N]::RowChanges($before.file, $originalSelection.file, 4, 250, 440))
    $first = [int] [math]::Ceiling($after.height * 0.70 / 4)
    $last = [int] [math]::Floor($after.height * 0.82 / 4)
    $selectionDifference = 0; $blankDifference = 0; $dragDifference = 0
    for ($row = $first; $row -le $last; $row++) {
        $selectionDifference += [int] $selectedRows[$row]
        $blankDifference += [int] $blankRows[$row]
        $dragDifference += [int] $dragRows[$row]
    }
    Add-Check 'grid.the_annotation_rows_keep_their_own_highlight' (($selectionDifference -eq 0) -and ($dragDifference -gt 500)) `
        @{ changedByScheme = $selectionDifference; changedBySelection = $dragDifference; files = @($originalSelection.file,$withSelection.file) }
    Add-Check 'grid.the_annotation_area_keeps_its_look' ($blankDifference -eq 0) @{ changedByScheme = $blankDifference }
    Click-Chip $state $root $CHIP_ORIGINAL
    Save-Checks
}
'interact' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    function Visible-Length([string] $name) {
        $output = (Join-Path $stage "visible-$name.txt") -replace '\\','/'
        $scriptFile = Join-Path $stage "visible-$name.praat"
        if (Test-Path $output) { Remove-Item -LiteralPath $output }
        $content = @'
selectObject: "Sound APPEARANCE-W1"
sound = selected ("Sound")
editor: sound
    length = Get length of visible part
endeditor
writeFileLine: "{0}", length
'@ -f $output
        Set-Content -LiteralPath $scriptFile -Value $content -Encoding UTF8
        $sender = Start-Process -FilePath $state.executable -ArgumentList @('--FULL-TRUST','--send',$scriptFile) -WindowStyle Hidden -PassThru
        if (-not $sender.WaitForExit(15000)) { $sender.Kill(); throw 'Visible range query did not finish' }
        $deadline = (Get-Date).AddSeconds(5)
        while (-not (Test-Path $output) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
        [double]::Parse([IO.File]::ReadAllText($output).Trim(), [Globalization.CultureInfo]::InvariantCulture)
    }
    $null = [N]::Place($root, 320, 90, 900, 620)
    $null = Set-Opacity $state $root 100
    Click-Chip $state $root $CHIP_MIST
    Close-AnyPanel $state
    Wait-For-Calm $state
    $drawing = Drawing-Area $root
    Bring-Out $root
    $canvas = [N]::ClientRectOnScreen($drawing)
    $band = [int] ($canvas[1] + ($canvas[3] - $canvas[1]) * 0.86)
    [N]::Drag($canvas[0] + 190, $band, $canvas[0] + 330, $band, 8)
    Start-Sleep -Milliseconds 500
    $zoom = Command-Id $state 'W1' @('Zoom to selection*', "*$T_ZOOM_TO_SELECTION*") 'zoom to selection'
    $wide = Get-EditorImage $state 'W1' 'before-zoom'
    $wideStrokes = [N]::Strokes($wide.file, 79, 96, 84, 10, 0, 8)
    $wideLength = Visible-Length 'wide'
    [N]::SendCommand($root, $zoom.id)
    Start-Sleep -Milliseconds 1500
    $narrow = Get-EditorImage $state 'W1' 'after-zoom'
    $narrowStrokes = [N]::Strokes($narrow.file, 79, 96, 84, 10, 0, 8)
    $narrowLength = Visible-Length 'narrow'
    Add-Check 'interact.zoom_shows_a_shorter_span' (($narrowLength -gt 0) -and ($narrowLength -lt $wideLength)) `
        @{ beforeSeconds = $wideLength; afterSeconds = $narrowLength; command = $zoom }
    Add-Check 'interact.zoom_keeps_pulses_visible' (([int64] $narrowStrokes.longestMedian -ge 8) -and `
            ([int64] $narrowStrokes.medianWidth -ge 1)) $narrowStrokes
    $back = Command-Id $state 'W1' @('Zoom back*', "*$T_ZOOM_BACK*") 'zoom back'
    [N]::SendCommand($root, $back.id)
    Start-Sleep -Milliseconds 1200
    $undone = Get-EditorImage $state 'W1' 'after-zoom-back'
    $undoneStrokes = [N]::Strokes($undone.file, 79, 96, 84, 10, 0, 8)
    Add-Check 'interact.zoom_back_restores_the_span' ([math]::Abs([int64] $undoneStrokes.strokes - [int64] $wideStrokes.strokes) -le 2) `
        @{ wide = [int64] $wideStrokes.strokes; undone = [int64] $undoneStrokes.strokes }
    # playing: the picture has to change while the sound runs, and nothing may break
    $play = Command-Id $state 'W1' @('Play window*', "*$T_PLAY_WINDOW*", 'Play or stop*', "*$T_PLAY_OR_STOP*") 'play'
    Bring-Out $root
    $first = Get-ScreenBand $drawing 'play-first'
    [N]::SendCommand($root, $play.id)
    Start-Sleep -Milliseconds 400
    $second = Get-ScreenBand $drawing 'play-second'
    Start-Sleep -Milliseconds 800
    $third = Get-ScreenBand $drawing 'play-third'
    $moving = [N]::Diff($first.file, $second.file)
    $movingOn = [N]::Diff($second.file, $third.file)
    $stop = Command-Id $state 'W1' @('Interrupt playing*', "*$T_INTERRUPT*") 'interrupt playing'
    [N]::SendCommand($root, $stop.id)
    Start-Sleep -Milliseconds 600
    $extra = @(Top-Of-Process $state) | Where-Object { (([N]::Class($_)) -notmatch 'Praat(Shell|ChildWindow|LayeredShell|DrawingArea|AppearancePanel|PronunciationCopiedToast)' ) }
    Add-Check 'interact.playing_redraws_the_window' ((([double] $moving.changed -gt 200) -or ([double] $movingOn.changed -gt 200))) `
        @{ firstToSecond = $moving; secondToThird = $movingOn; command = $play }
    Add-Check 'interact.playing_leaves_the_window_usable' (((Get-Process -Id ([int] $state.processId) -ErrorAction SilentlyContinue) -ne $null) -and `
            (@($extra).Count -eq 0) -and ([N]::Alive($root))) `
        @{ extra = (@($extra) | ForEach-Object { [N]::Text($_) + ' [' + [N]::Class($_) + ']' }); windows = @(Top-Of-Process $state).Count }

    # Scrolling: the wheel has to reach the drawing area of the new shell and move the view.
    [N]::SendCommand($root, $zoom.id)
    Start-Sleep -Milliseconds 1200
    $canvas = [N]::ClientRectOnScreen($drawing)
    $localX = [int] (($canvas[0] + $canvas[2]) / 2)
    $localY = [int] (($canvas[1] + $canvas[3]) / 2)
    $beforeScroll = Get-ScreenBand $drawing 'scroll-before'
    $null = [N]::Send($drawing, 0x020A, ((240 -shl 16) -bor 4), (($localY -shl 16) -bor ($localX -band 0xFFFF)))
    Start-Sleep -Milliseconds 900
    $afterScroll = Get-ScreenBand $drawing 'scroll-after'
    $scrollChange = [N]::Diff($beforeScroll.file, $afterScroll.file)
    Add-Check 'interact.the_wheel_moves_the_view' ((([int64] $scrollChange.changed) -gt 200) -and `
            ((Scheme-Marks-The-Image $afterScroll 'mist' 'clay').drawn)) `
        @{ changed = $scrollChange.changed; meanDifference = $scrollChange.meanDifference; mist = (Colour-Of $afterScroll 'mist');
           clay = (Colour-Of $afterScroll 'clay'); files = @($beforeScroll.file, $afterScroll.file) }
    $null = [N]::Send($drawing, 0x020A, ((-240 -shl 16) -bor 4), (($localY -shl 16) -bor ($localX -band 0xFFFF)))
    Start-Sleep -Milliseconds 900

    # Maximise and restore: the window keeps drawing its scheme, and the panel lays itself out inside
    # the work area of the bigger window.
    $geometryBefore = @([N]::Rect($root))
    $null = [N]::Send($root, 0x0112, 0xF030, 0)      # SC_MAXIMIZE
    Start-Sleep -Milliseconds 1600
    $maximised = Get-ScreenBand (Drawing-Area $root) 'maximised'
    Add-Check 'interact.maximised_keeps_drawing_the_scheme' ((Scheme-Marks-The-Image $maximised 'mist' 'clay').drawn) `
        @{ mist = (Colour-Of $maximised 'mist'); clay = (Colour-Of $maximised 'clay'); size = @($maximised.width, $maximised.height); file = $maximised.file }
    $maximisedPanel = Open-Panel $state $root
    if ($maximisedPanel.panel -ne [IntPtr]::Zero) {
        $panelRectangle = @([N]::Rect($maximisedPanel.panel))
        Add-Check 'interact.the_panel_stays_inside_the_work_area' ((([int64] $panelRectangle[0]) -ge ([int64] $WORK_AREA.Left)) -and `
                (([int64] $panelRectangle[2]) -le ([int64] $WORK_AREA.Right)) -and (([int64] $panelRectangle[3]) -le ([int64] $WORK_AREA.Bottom))) `
            @{ panel = ($panelRectangle -join ','); workArea = @($WORK_AREA.Left, $WORK_AREA.Top, $WORK_AREA.Right, $WORK_AREA.Bottom) }
        Close-AnyPanel $state
    } else {
        Add-Check 'interact.the_panel_stays_inside_the_work_area' $false @{ note = 'the panel did not open on the maximised window' }
    }
    $null = [N]::Send($root, 0x0112, 0xF120, 0)      # SC_RESTORE
    Start-Sleep -Milliseconds 1400
    Add-Check 'interact.restoring_returns_to_the_previous_geometry' (([N]::Rect($root) -join ',') -eq ($geometryBefore -join ',')) `
        @{ before = ($geometryBefore -join ','); now = (@([N]::Rect($root)) -join ',') }
    [N]::SendCommand($root, $back.id)
    Start-Sleep -Milliseconds 800
    Save-Checks
}

'analysis' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    function Snapshot-Analysis([string] $name) {
        $output = (Join-Path $stage "analysis-$name.txt") -replace '\\','/'
        $scriptFile = Join-Path $stage "analysis-$name.praat"
        if (Test-Path $output) { Remove-Item -LiteralPath $output }
        $content = @'
selectObject: "Sound APPEARANCE-W1"
sound = selected ("Sound")
editor: sound
    Select: 0.2, 0.8
    pitch = Get pitch
    formant = Get first formant
    intensity = Get intensity
    pulses$ = Pulse listing
endeditor
writeFileLine: "{0}", pitch, tab$, formant, tab$, intensity
appendFile: "{0}", pulses$
'@ -f $output
        Set-Content -LiteralPath $scriptFile -Value $content -Encoding UTF8
        $sender = Start-Process -FilePath $state.executable -ArgumentList @('--FULL-TRUST','--send',$scriptFile) -WindowStyle Hidden -PassThru
        if (-not $sender.WaitForExit(15000)) { $sender.Kill(); throw 'Analysis query did not finish' }
        $deadline = (Get-Date).AddSeconds(10)
        while (-not (Test-Path $output) -and (Get-Date) -lt $deadline) { Start-Sleep -Milliseconds 100 }
        if (-not (Test-Path $output)) { throw 'The editor did not write its analysis query' }
        Get-Content -LiteralPath $output -Raw
    }
    $null = Set-Opacity $state $root 100
    Click-Chip $state $root $CHIP_ORIGINAL
    $before = Snapshot-Analysis 'original'
    foreach ($scheme in @(@('mist',$CHIP_MIST), @('clay',$CHIP_CLAY))) {
        Click-Chip $state $root $scheme[1]
        $null = Set-Opacity $state $root 20
        $after = Snapshot-Analysis $scheme[0]
        Add-Check ("analysis.$($scheme[0])_preserves_the_queries_and_pulse_times") ($before -ceq $after) `
            @{ baseline = 'analysis-original.txt'; compared = "analysis-$($scheme[0]).txt" }
    }
    # Applying Grey explicitly must exit the temporary scheme even when Grey is already the saved preference.
    $scriptFile = Join-Path $stage 'restore-colour-map.praat'
    Set-Content -LiteralPath $scriptFile -Encoding UTF8 -Value @'
selectObject: "Sound APPEARANCE-W1"
sound = selected ("Sound")
editor: sound
    Spectrogram colour settings: "Grey", "no", "yes"
endeditor
'@
    $sender = Start-Process -FilePath $state.executable -ArgumentList @('--FULL-TRUST','--send',$scriptFile) -WindowStyle Hidden -PassThru
    if (-not $sender.WaitForExit(15000)) { $sender.Kill(); throw 'Colour settings did not finish' }
    $opened = Open-Panel $state $root
    $focused = [N]::FocusedInProcess([int] $state.processId)
    Add-Check 'analysis.explicit_grey_exits_the_scheme' ($focused -eq (Panel-Control $opened.panel $CHIP_ORIGINAL)) @{}
    Close-AnyPanel $state
    $null = Set-Opacity $state $root 100
    Save-Checks
}

'teardown' {
    $state = Read-AppState
    $root = Window-Of $state 'W1'
    $before = @(Top-Of-Process $state).Count
    $opened = Open-Panel $state $root
    Add-Check 'close.the_panel_is_open_before' ((Panel-Window $state) -ne [IntPtr]::Zero) @{ panel = ([string] (Panel-Window $state)) }
    $null = [N]::Send($root, 0x0112, 0xF060, 0)          # WM_SYSCOMMAND, SC_CLOSE
    Start-Sleep -Milliseconds 2000
    $panels = @(Find-By-Class $state 'PraatAppearancePanel*' | Where-Object { [N]::Visible($_) })
    $after = @(Top-Of-Process $state).Count
    Add-Check 'close.the_panel_goes_with_the_editor' (($panels.Count -eq 0) -and (-not [N]::Alive($root))) `
        @{ panels = @($panels).Count; rootAlive = [N]::Alive($root); windowsBefore = $before; windowsAfter = $after }
    $alive = @()
    foreach ($role in @('W2','W3','W4','W5')) {
        $candidate = Window-Of $state $role
        $entry = Appearance-Entry $candidate
        $alive += [pscustomobject] @{ role = $role; alive = [bool] [N]::Alive($candidate); entry = $(if ($entry) { $entry.text } else { $null }) }
    }
    Add-Check 'close.the_other_editors_survive_and_still_work' ((@($alive | Where-Object { -not $_.alive }).Count -eq 0) -and `
            ((Get-Process -Id ([int] $state.processId) -ErrorAction SilentlyContinue) -ne $null)) $alive
    $all = @(All-Checks)
    $failed = @($all | Where-Object { -not [bool] $_.pass })
    Save-Checks
    [pscustomobject] @{
        executable = $state.executable
        sha256 = $state.sha256
        stage = $stage
        method = 'real editor windows: menu-bar click, owner-drawn chips, trackbar drag and keyboard, layered-window attributes, desktop compositing, clipboard bytes, --send to the running instance'
        dpi = [N]::Dpi((Window-Of $state 'W2'))
        limits = @('one 96 dpi monitor: the scaled layout could only be measured at 100 % display scaling',
                   'the white halo under a core stroke is invisible on the white canvas, so only the core width is measured',
                   'audio output is not captured; playing is proven by the repainting of the window')
        checks = @($all | ForEach-Object { $_.name })
        failed = @($failed | ForEach-Object { [pscustomobject] @{ name = $_.name; detail = $_.detail } })
        passed = ($failed.Count -eq 0)
        total = $all.Count
        generatedAt = (Get-Date).ToString('s')
    } | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath (Join-Path $stage 'verification.json') -Encoding UTF8
    Write-Host ("{0} checks, {1} failed" -f $all.Count, $failed.Count)
    $failed | ForEach-Object { Write-Warning ("{0}: {1}" -f $_.name, ($_.detail | ConvertTo-Json -Compress -Depth 6)) }
    Stop-Process -Id ([int] $state.processId) -Force -ErrorAction SilentlyContinue
    if ($failed.Count -gt 0) { throw "$($failed.Count) native appearance checks failed; see verification.json" }
}

}
Write-Host ("phase {0} finished: {1} checks recorded" -f $Phase, @(All-Checks).Count)


