"""聊天基频工具共用的声学质量筛查。先以区间相对能量筛底噪，再计算候选中位带与连续段；Hz/s 斜率只累计相邻有效帧，去倍频斜率按首末有效帧跨度归一。贴近显式下限及近倍频跳变标为待复核。已有分析对象无源波形时标未核验；这些启发式不是准确率认证。"""

from __future__ import annotations

from typing import Mapping, Sequence

#: 事实行前缀；``praat_ai.evidence_quality.QUALITY_PREFIX`` 必须是同一个串。
QUALITY_PREFIX = "基频质量："
#: 脉冲（PointProcess）回执前缀；``evidence_quality`` 两者都按“质量回执”解析。
PULSE_PREFIX = "脉冲质量："

#: 相对能量门：区间内强度峰值往下多少分贝。启发式阈值，不是地面真值。
ENERGY_GATE_DB = 15.0
#: 能量门之后再算的中位带（相对中位数）。
BAND_LOW = 0.5
#: 上界保持 1.5 倍：它是排除倍频/次谐波的手段。真实的大幅升降会被挡在带外，
#: 因此带外帧占比高时额外标「可能还有另一个音高层次」并降为待复核，不静默删数据。
BAND_HIGH = 1.5
#: 连续有声的最短帧数；短于这个的孤立候选段不进统计。
MIN_RUN_FRAMES = 3
#: 有效帧低于这个数就不报统计（宁可 undefined，也不输出伪迹结论）。
MIN_VALID_FRAMES = 3
#: 有效帧贴近显式下限的比例：只提示「待复核」，不删帧。
NEAR_FLOOR_RATIO = 1.25
#: 脉冲间隔抖动（相邻间隔平均相对变化）上限：超过就标「待复核」。
#: 真实持续发声的局部 jitter 通常在百分之一量级，10% 说明脉冲串明显不干净。
PULSE_JITTER_LIMIT = 0.10
#: 相邻有效帧比值超过这个数就算一次近倍频跳变：只提示，不做自动倍频纠正。
OCTAVE_JUMP_RATIO = 1.8
#: 区间强度峰值低于这个值就当整段静音，相对能量门不生效（回执写「待复核」）。
SILENT_PEAK_DB = -70.0

#: ``measure`` 里由这套筛查供值的参数 → Praat 变量名（其余参数仍走参数表）。
MEASURE_VALUES: Mapping[str, str] = {
    "mean_pitch": "mean",
    "minimum_pitch": "minimum",
    "maximum_pitch": "maximum",
    "median_pitch": "median",
    "sd_pitch": "usedSd",
    "pitch_slope": "usedSlope",
    "pitch_slope_octave_free": "usedOctaveFreeSlope",
    "pitch_start": "usedStart",
    "pitch_end": "usedEnd",
}


def intensity_floor_for(pitch_floor: float) -> float:
    """强度分析的基频下限要低于待测基频，否则 Intensity 自己就把低音滤掉了。"""

    return min(100.0, max(50.0, float(pitch_floor)))


def _praat_string(value: str) -> str:
    """Praat 字符串字面量；说明文字里不该带双引号，出现了就换成单引号。"""

    return '"' + str(value).replace('"', "'").replace("\n", " ") + '"'


def is_quality_row(row: str) -> bool:
    return str(row).lstrip().startswith(QUALITY_PREFIX)


def _histogram_median_lines(
    *, count: str, array: str, low: str, high: str, out: str, suffix: str
) -> list[str]:
    """按 1 Hz 直方图求中位数。

    Praat 脚本没有排序，逐帧两两比较是 O(n²)；候选帧一般几十到几百，直方图更省，
    误差不超过半个 bin（约 0.5 Hz），用来定中位带完全够。
    """

    bins = f"binCount{suffix}"
    width = f"binWidth{suffix}"
    index = f"binIndex{suffix}"
    total = f"binTotal{suffix}"
    target = f"binTarget{suffix}"
    running = f"binSum{suffix}"
    found = f"binFound{suffix}"
    return [
        f"{out} = undefined",
        f"if {count} > 0 and {low} <> undefined and {high} <> undefined",
        f"    if {high} <= {low}",
        f"        {out} = {low}",
        "    else",
        f"        {bins} = round ({high} - {low})",
        f"        if {bins} < 1",
        f"            {bins} = 1",
        "        endif",
        f"        {width} = ({high} - {low}) / {bins}",
        f"        for {index} from 1 to {bins}",
        f"            {total}[{index}] = 0",
        "        endfor",
        f"        for frameIndex from 1 to {count}",
        f"            {index} = floor (({array}[frameIndex] - {low}) / {width}) + 1",
        f"            if {index} < 1",
        f"                {index} = 1",
        "            endif",
        f"            if {index} > {bins}",
        f"                {index} = {bins}",
        "            endif",
        f"            {total}[{index}] = {total}[{index}] + 1",
        "        endfor",
        f"        {target} = ({count} + 1) / 2",
        f"        {running} = 0",
        f"        {found} = 0",
        f"        for {index} from 1 to {bins}",
        f"            {running} = {running} + {total}[{index}]",
        f"            if {found} = 0 and {running} >= {target}",
        f"                {out} = {low} + ({index} - 0.5) * {width}",
        f"                {found} = 1",
        "            endif",
        "        endfor",
        "    endif",
        "endif",
    ]


def screen_lines(
    *,
    sound: str | None,
    floor: float,
    ceiling: float,
    pitch: str = "pitchId",
    source_note: str = "",
) -> list[str]:
    """生成共享筛查片段：能量门 → 候选带 → 连续段 → 同一批有效帧的统计。

    进入条件：Pitch 对象已选中（或 ``pitch`` 指向它），``tmin`` / ``tmax`` 已定义。
    产出：``frameUsed[]`` / ``usedValue[]``（点查询与统计共用同一批帧）、
    ``usedMean`` / ``usedMin`` / ``usedMax`` / ``usedMaxTime`` / ``usedMedian`` /
    ``usedSd`` / ``usedSlope`` / ``usedStart`` / ``usedEnd``，以及事实行 ``quality$``。
    ``source_note`` 是“能量门用的那段原始音频是怎么配到的”，会写进事实行：
    已有 Pitch 对象本身不带波形，配上源 Sound 才能做能量门（见 README/文档）。
    """

    gate = float(ENERGY_GATE_DB)
    lines = [
        "# ---- 共享基频质量筛查（pitch_quality.py 生成；启发式，不是可靠性认证）----",
        f"selectObject: {pitch}",
        "nFrames = Get number of frames",
        "frameStep = Get time step",
        "frameFirst = Get time from frame number: 1",
        "qualityVerified = 0",
        f'qualitySource$ = {_praat_string(source_note)}',
        "gateEnabled = 0",
        "gatePeak = undefined",
        "gateThreshold = 0",
        "gateExcluded = 0",
        "voicedCount = 0",
        "gatedCount = 0",
        "gateLow = undefined",
        "gateHigh = undefined",
        "qualityMedian = undefined",
        "qualityFloor = undefined",
        "qualityCeiling = undefined",
        "bandExcluded = 0",
        "shortExcluded = 0",
        "jumpCount = 0",
        "nearFloorCount = 0",
        "usedCount = 0",
        "usedMean = undefined",
        "usedMin = undefined",
        "usedMax = undefined",
        "usedMaxTime = undefined",
        "usedMedian = undefined",
        "usedSd = undefined",
        "usedSlope = undefined",
        "usedOctaveFreeSlope = undefined",
        "usedStart = undefined",
        "usedStartTime = undefined",
        "usedEnd = undefined",
        "usedEndTime = undefined",
        "for frameIndex from 1 to nFrames",
        "    frameVoiced[frameIndex] = 0",
        "    frameEnergy[frameIndex] = 0",
        "    frameRun[frameIndex] = 0",
        "    frameUsed[frameIndex] = 0",
        "    usedValue[frameIndex] = 0",
        "    frameFrequency[frameIndex] = 0",
        "endfor",
    ]
    if sound is not None:
        lines.extend(
            [
                "# ① 相对能量门：区间内强度峰值往下 ENERGY_GATE_DB 分贝。",
                "#    帧级 strength 在底噪上也是 0.9+，只有相对能量拦得住「底噪贴下限」。",
                "qualityVerified = 1",
                f"selectObject: {sound}",
                f'To Intensity: {intensity_floor_for(floor):.6f}, 0, "yes"',
                'qualityIntensityId = selected ("Intensity")',
                'gatePeak = Get maximum: tmin, tmax, "Parabolic"',
                f"if gatePeak <> undefined and gatePeak > {SILENT_PEAK_DB:.1f}",
                "    gateEnabled = 1",
                f"    gateThreshold = gatePeak - {gate:.1f}",
                "endif",
                f"selectObject: {pitch}",
            ]
        )
    else:
        lines.extend(
            [
                "# 已有 Pitch 对象：没有原始 Sound，能量门与连续段筛查都做不了。",
                "# 事实行会写「未核验」，数值只能当原始候选。",
            ]
        )
    lines.extend(
        [
            "for frameIndex from 1 to nFrames",
            "    frameTime = frameFirst + (frameIndex - 1) * frameStep",
            "    if frameTime >= tmin and frameTime <= tmax",
            '        candidateValue = Get value in frame: frameIndex, "Hertz"',
            "        if candidateValue <> undefined",
            "            frameVoiced[frameIndex] = 1",
            "            frameFrequency[frameIndex] = candidateValue",
            "            voicedCount = voicedCount + 1",
            "            if gateEnabled = 0",
            "                frameEnergy[frameIndex] = 1",
            "            else",
            "                selectObject: qualityIntensityId",
            '                frameLevel = Get value at time: frameTime, "linear"',
            f"                selectObject: {pitch}",
            "                if frameLevel = undefined",
            "                    gateExcluded = gateExcluded + 1",
            "                elsif frameLevel >= gateThreshold",
            "                    frameEnergy[frameIndex] = 1",
            "                else",
            "                    gateExcluded = gateExcluded + 1",
            "                endif",
            "            endif",
            "            if frameEnergy[frameIndex] = 1",
            "                gatedCount = gatedCount + 1",
            "                gatedSample[gatedCount] = candidateValue",
            "                if gateLow = undefined",
            "                    gateLow = candidateValue",
            "                elsif candidateValue < gateLow",
            "                    gateLow = candidateValue",
            "                endif",
            "                if gateHigh = undefined",
            "                    gateHigh = candidateValue",
            "                elsif candidateValue > gateHigh",
            "                    gateHigh = candidateValue",
            "                endif",
            "            endif",
            "        endif",
            "    endif",
            "endfor",
            "# ② 对通过能量门的候选计算中位带。",
        ]
    )
    lines.extend(
        _histogram_median_lines(
            count="gatedCount",
            array="gatedSample",
            low="gateLow",
            high="gateHigh",
            out="qualityMedian",
            suffix="Gate",
        )
    )
    lines.extend(
        [
            "if qualityMedian <> undefined",
            f"    qualityFloor = qualityMedian * {BAND_LOW:.6f}",
            f"    qualityCeiling = qualityMedian * {BAND_HIGH:.6f}",
            "endif",
            "# ③ 连续有声：短于 MIN_RUN_FRAMES 帧的孤立候选段不进统计。",
            "#    先前向记「到这一帧为止的连续长度」，再反向合并成整段长度。",
            "runLength = 0",
            "for frameIndex from 1 to nFrames",
            "    if frameEnergy[frameIndex] = 1",
            "        runLength = runLength + 1",
            "    else",
            "        runLength = 0",
            "    endif",
            "    frameRun[frameIndex] = runLength",
            "endfor",
            "runLength = 0",
            "for stepIndex from 1 to nFrames",
            "    frameIndex = nFrames - stepIndex + 1",
            "    if frameEnergy[frameIndex] = 1",
            "        runLength = runLength + 1",
            f"        if frameRun[frameIndex] + runLength - 1 >= {MIN_RUN_FRAMES}",
            "            frameRun[frameIndex] = 1",
            "        else",
            "            frameRun[frameIndex] = 0",
            "        endif",
            "    else",
            "        runLength = 0",
            "        frameRun[frameIndex] = 0",
            "    endif",
            "endfor",
            "for frameIndex from 1 to nFrames",
            "    if frameEnergy[frameIndex] = 1 and frameRun[frameIndex] = 1 and qualityFloor <> undefined",
            "        if frameFrequency[frameIndex] >= qualityFloor and frameFrequency[frameIndex] <= qualityCeiling",
            "            frameUsed[frameIndex] = 1",
            "            usedValue[frameIndex] = frameFrequency[frameIndex]",
            "        else",
            "            bandExcluded = bandExcluded + 1",
            "        endif",
            "    endif",
            "endfor",
            "for frameIndex from 1 to nFrames",
            "    if frameEnergy[frameIndex] = 1 and frameRun[frameIndex] = 0",
            "        shortExcluded = shortExcluded + 1",
            "    endif",
            "endfor",
            "# ④ 统计、首尾、峰值、斜率共用 frameUsed[] 这一批帧；斜率不跨无声间隔。",
            "usedTotal = 0",
            "usedSquares = 0",
            "for frameIndex from 1 to nFrames",
            "    if frameUsed[frameIndex] = 1",
            "        usedCount = usedCount + 1",
            "        usedTotal = usedTotal + usedValue[frameIndex]",
            "        usedSquares = usedSquares + usedValue[frameIndex] * usedValue[frameIndex]",
            "        usedSample[usedCount] = usedValue[frameIndex]",
            "        if usedMin = undefined",
            "            usedMin = usedValue[frameIndex]",
            "        elsif usedValue[frameIndex] < usedMin",
            "            usedMin = usedValue[frameIndex]",
            "        endif",
            "        if usedMax = undefined",
            "            usedMax = usedValue[frameIndex]",
            "            usedMaxTime = frameFirst + (frameIndex - 1) * frameStep",
            "        elsif usedValue[frameIndex] > usedMax",
            "            usedMax = usedValue[frameIndex]",
            "            usedMaxTime = frameFirst + (frameIndex - 1) * frameStep",
            "        endif",
            "        if usedStart = undefined",
            "            usedStart = usedValue[frameIndex]",
            "            usedStartTime = frameFirst + (frameIndex - 1) * frameStep",
            "        endif",
            "        usedEnd = usedValue[frameIndex]",
            "        usedEndTime = frameFirst + (frameIndex - 1) * frameStep",
            "    endif",
            "endfor",
            "if usedCount > 0",
            "    usedMean = usedTotal / usedCount",
            "endif",
            "if usedCount > 1",
            "    usedVariance = (usedSquares - usedCount * usedMean * usedMean) / (usedCount - 1)",
            "    if usedVariance < 0",
            "        usedVariance = 0",
            "    endif",
            "    usedSd = sqrt (usedVariance)",
            "endif",
        ]
    )
    lines.extend(
        _histogram_median_lines(
            count="usedCount",
            array="usedSample",
            low="usedMin",
            high="usedMax",
            out="usedMedian",
            suffix="Used",
        )
    )
    lines.extend(
        [
            "usedSlopeSum = 0",
            "usedSlopePairs = 0",
            "for frameIndex from 1 to nFrames - 1",
            "    if frameUsed[frameIndex] = 1 and frameUsed[frameIndex + 1] = 1",
            "        usedSlopeSum = usedSlopeSum + abs (usedValue[frameIndex + 1] - usedValue[frameIndex])",
            "        usedSlopePairs = usedSlopePairs + 1",
            "    endif",
            "endfor",
            "if usedSlopePairs > 0 and frameStep > 0",
            "    usedSlope = usedSlopeSum / (usedSlopePairs * frameStep)",
            "endif",
            "# Praat 的去倍频斜率：逐对折叠半音变化，除以首末有效帧的时间跨度。",
            "octaveFreeTotal = 0",
            "previousFrequency = undefined",
            "for frameIndex from 1 to nFrames",
            "    if frameUsed[frameIndex] = 1",
            "        if previousFrequency <> undefined",
            "            semitoneChange = abs (12 * ln (usedValue[frameIndex] / previousFrequency) / ln (2))",
            "            while semitoneChange >= 12",
            "                semitoneChange = semitoneChange - 12",
            "            endwhile",
            "            if semitoneChange > 6",
            "                semitoneChange = 12 - semitoneChange",
            "            endif",
            "            octaveFreeTotal = octaveFreeTotal + semitoneChange",
            "        endif",
            "        previousFrequency = usedValue[frameIndex]",
            "    endif",
            "endfor",
            "if usedCount > 1 and usedEndTime > usedStartTime",
            "    usedOctaveFreeSlope = octaveFreeTotal / (usedEndTime - usedStartTime)",
            "endif",
            "# ⑤ 近倍频孤立跳变与贴下限只提示「待复核」，不删帧、不做自动倍频纠正。",
            "for frameIndex from 1 to nFrames - 1",
            "    if frameUsed[frameIndex] = 1 and frameUsed[frameIndex + 1] = 1",
            "        jumpRatio = usedValue[frameIndex + 1] / usedValue[frameIndex]",
            f"        if jumpRatio >= {OCTAVE_JUMP_RATIO:.2f} or jumpRatio <= {1.0 / OCTAVE_JUMP_RATIO:.4f}",
            "            jumpCount = jumpCount + 1",
            "        endif",
            "    endif",
            "endfor",
            "for frameIndex from 1 to nFrames",
            f"    if frameUsed[frameIndex] = 1 and usedValue[frameIndex] < {floor * NEAR_FLOOR_RATIO:.6f}",
            "        nearFloorCount = nearFloorCount + 1",
            "    endif",
            "endfor",
            "# ---- 质量事实行：稳定可解析；阈值与帧数不是实测基频值 ----",
            'qualityState$ = "已筛查"',
            'qualityFlag$ = ""',
            'qualitySeparator$ = ""',
            "if qualityVerified = 0",
            '    qualityState$ = "未核验"',
            "else",
            "    if gateEnabled = 0",
            '        qualityFlag$ = qualityFlag$ + qualitySeparator$ + "能量门没生效：这一段取不到强度峰值（可能整段静音）"',
            '        qualitySeparator$ = "；"',
            "    endif",
            "endif",
            'qualitySuspect = 0',
            "if usedCount = 0",
            '    qualityFlag$ = qualityFlag$ + qualitySeparator$ + "没有连续、过能量门、又落在中位带里的基频帧"',
            '    qualitySeparator$ = "；"',
            "    qualitySuspect = 1",
            "else",
            "    if shortExcluded > usedCount",
            '        qualityFlag$ = qualityFlag$ + qualitySeparator$ + "断裂/极短段排除 " + fixed$ (shortExcluded, 0) + " 帧，多于保留帧"',
            '        qualitySeparator$ = "；"',
            "        qualitySuspect = 1",
            "    elsif shortExcluded > 0",
            '        qualityFlag$ = qualityFlag$ + qualitySeparator$ + "断裂/极短段排除 " + fixed$ (shortExcluded, 0) + " 帧"',
            '        qualitySeparator$ = "；"',
            "    endif",
            "endif",
            "if jumpCount >= 3",
            '    qualityFlag$ = qualityFlag$ + qualitySeparator$ + "近倍频跳变 " + fixed$ (jumpCount, 0) + " 处（可能是误判，也可能是真实跳变）"',
            '    qualitySeparator$ = "；"',
            "    qualitySuspect = 1",
            "elsif jumpCount > 0",
            '    qualityFlag$ = qualityFlag$ + qualitySeparator$ + "近倍频跳变 " + fixed$ (jumpCount, 0) + " 处"',
            '    qualitySeparator$ = "；"',
            "endif",
            "# 带外帧占比高：可能整段有两个音高层次（真实升降或倍频误判），单一均值不代表全段。",
            "if gatedCount > 0 and bandExcluded * 4 >= gatedCount",
            '    qualityFlag$ = qualityFlag$ + qualitySeparator$ + "有 " + fixed$ (bandExcluded, 0) + " 帧落在中位带外（可能还有另一个音高层次，也可能是倍频误判）"',
            '    qualitySeparator$ = "；"',
            "    qualitySuspect = 1",
            "endif",
            "if nearFloorCount > 0",
            '    qualityFlag$ = qualityFlag$ + qualitySeparator$ + "贴近显式下限的帧 " + fixed$ (nearFloorCount, 0) + "（可能是底噪贴下限，也可能是真实低基频）"',
            '    qualitySeparator$ = "；"',
            "    if usedCount > 0 and nearFloorCount * 2 >= usedCount",
            "        qualitySuspect = 1",
            "    endif",
            "endif",
            'if qualityState$ <> "未核验" and qualitySuspect = 1',
            '    qualityState$ = "待复核"',
            "endif",
            'quality$ = "基频质量：" + qualityState$',
            "if qualityVerified = 0",
            '    quality$ = quality$ + "；参数：已有 Pitch 对象自带的提取频带（对象里读不出具体数值）"',
            '    quality$ = quality$ + "；没有原始 Sound：能量门与连续段筛查都没做"',
            '    quality$ = quality$ + "；候选 " + fixed$ (gatedCount, 0) + " 帧，有效 " + fixed$ (usedCount, 0) + " 帧"',
            "else",
            f'    quality$ = quality$ + "；参数：下限 {floor:.1f} Hz，上限 {ceiling:.1f} Hz"',
            "    if gateEnabled = 1",
            '        quality$ = quality$ + "；能量门：峰值减 " + fixed$ (gatePeak - gateThreshold, 1) + " dB（峰值 " + fixed$ (gatePeak, 1) + " dB、门限 " + fixed$ (gateThreshold, 1) + " dB），排除 " + fixed$ (gateExcluded, 0) + " 帧"',
            "    else",
            '        quality$ = quality$ + "；能量门没生效（区间里取不到强度峰值）"',
            "    endif",
            "    if qualityFloor <> undefined",
            '        quality$ = quality$ + "；候选 " + fixed$ (gatedCount, 0) + " 帧，中位带 " + fixed$ (qualityFloor, 1) + "–" + fixed$ (qualityCeiling, 1) + " Hz 再排除 " + fixed$ (bandExcluded, 0) + " 帧"',
            "    else",
            '        quality$ = quality$ + "；候选 0 帧（能量门之后没有候选，中位带定不出来）"',
            "    endif",
            '    quality$ = quality$ + "；有效 " + fixed$ (usedCount, 0) + " 帧"',
            "endif",
            "if qualityFlag$ <> \"\"",
            "    if qualityState$ = \"待复核\"",
            '        quality$ = quality$ + "；待复核原因：" + qualityFlag$',
            "    else",
            '        quality$ = quality$ + "；附注：" + qualityFlag$',
            "    endif",
            "endif",
            "if qualitySource$ <> \"\"",
            '    quality$ = quality$ + "；源音频：" + qualitySource$',
            "endif",
            'quality$ = quality$ + "；口径：启发式声学筛查，不是正确基频或发声诊断的认证"',
        ]
    )
    return lines


def point_value_lines(*, pitch: str = "pitchId") -> list[str]:
    """点查询取值：只有落在同一批有效帧里的时刻才给值，底噪/静音处保持 undefined。"""

    return [
        f"selectObject: {pitch}",
        "value = undefined",
        "frameNumber = Get frame number from time: time",
        "frameIndex = round (frameNumber)",
        "if frameIndex < 1",
        "    frameIndex = 1",
        "endif",
        "if frameIndex > nFrames",
        "    frameIndex = nFrames",
        "endif",
        "if frameUsed[frameIndex] = 1",
        "    leftFrame = floor (frameNumber)",
        "    rightFrame = ceiling (frameNumber)",
        "    supported = leftFrame >= 1 and rightFrame <= nFrames",
        "    if supported",
        "        supported = frameUsed[leftFrame] = 1 and frameUsed[rightFrame] = 1",
        "    endif",
        "    frameTime = Get time from frame number: frameIndex",
        "    if supported and abs (time - frameTime) <= frameStep",
        '        value = Get value at time: time, "Hertz", "linear"',
        "    endif",
        "endif",
    ]


def cleanup_lines(*, pitch: str | None = None, sound: bool = True) -> list[str]:
    """删掉临时 Intensity（以及调用方要求删的临时 Pitch），不动用户对象。

    ``sound`` 为假（输入是已有 Pitch 对象）时不写这段：那时根本没有临时 Intensity，
    脚本里也不该出现任何 ``Remove``。
    """

    lines: list[str] = []
    if sound:
        lines.extend(
            [
                "if qualityVerified = 1",
                "    selectObject: qualityIntensityId",
                "    Remove",
                "endif",
            ]
        )
    if pitch is not None:
        lines.extend([f"selectObject: {pitch}", "Remove"])
    return lines


def quality_result_line(result_path_quoted: str) -> str:
    """把事实行写成一行结果（便于测试与 ``evidence_quality`` 解析）。"""

    return f"appendFileLine: {result_path_quoted}, quality$"


def receipt_lines(path: str, *, method: str, floor: float, ceiling: float) -> list[str]:
    """Persist tracker observations and screening decisions before aggregation."""
    bounds = (str(floor), str(ceiling)) if method == 'filtered_autocorrelation' else ('undefined', 'undefined')
    return [
        f'appendFileLine: {path}, "@aipraat-pitch-v1", tab$, fixed$ (tmin, 9), tab$, fixed$ (tmax, 9), tab$, fixed$ (frameStep, 9), tab$, "{method}", tab$, {bounds[0]}, tab$, {bounds[1]}, tab$, qualityState$',
        'for frameIndex from 1 to nFrames',
        '    frameTime = frameFirst + (frameIndex - 1) * frameStep',
        '    if frameTime >= tmin and frameTime <= tmax',
        f'        appendFileLine: {path}, "@aipraat-frame-v1", tab$, fixed$ (frameTime, 9), tab$, fixed$ (frameFrequency[frameIndex], 9), tab$, frameUsed[frameIndex], tab$, frameEnergy[frameIndex], tab$, frameRun[frameIndex]',
        '    endif', 'endfor',
    ]


def report_summary() -> str:
    """给文档/测试用的一句话口径说明。"""

    return (
        f"{QUALITY_PREFIX}已筛查/待复核/未核验；"
        f"能量门=区间强度峰值减 {ENERGY_GATE_DB:g} dB（启发式）；"
        f"候选带=能量门后中位数的 {BAND_LOW:g}–{BAND_HIGH:g} 倍；"
        f"连续段≥{MIN_RUN_FRAMES} 帧；有效帧<{MIN_VALID_FRAMES} 帧不报统计"
    )


def measure_value(parameter: str) -> str | None:
    """``measure`` 的基频参数取哪个 Praat 变量；不是基频参数返回 ``None``。"""

    return MEASURE_VALUES.get(str(parameter))


def quality_parameters(names: Sequence[str]) -> list[str]:
    """这批参数里哪几个由共享筛查供值（保持顺序）。"""

    return [name for name in names if name in MEASURE_VALUES]


# ---------------------------------------------------------------------------
# 脉冲（PointProcess）：声门脉冲的读取与同一套相对能量门
# ---------------------------------------------------------------------------

def pulse_screen_lines(*, sound: str | None, floor: float, point_process: str = "pulseProcessId",
                       source_note: str = "") -> list[str]:
    """生成脉冲读取片段（``PointProcess`` 已选中，``tmin`` / ``tmax`` 已定义）。

    产出 ``pulseUsed`` / ``pulseFirst`` / ``pulseLast`` / ``pulseGaps`` /
    ``pulseGapMean`` / ``pulseGapMin`` / ``pulseGapMax`` / ``pulseJitter`` 与事实行
    ``pulseQuality$``。口径与基频一致：能量门（相对区间强度峰值）先筛脉冲点，
    再算相邻间隔；没有源 Sound 时写「未核验」，间隔只能当原始候选。

    ``Get time from index``（不带冒号）、``Get number of points`` 是本机 Praat 实测
    可用的命令；``Get jitter (local)`` 等命令参数签名随版本变化，这里不依赖它们，
    抖动按 ``平均|Δ间隔| / 平均间隔`` 自己算。
    """

    gate = float(ENERGY_GATE_DB)
    lines = [
        "# ---- 脉冲（PointProcess）读取与能量门（pitch_quality.py 生成）----",
        f"selectObject: {point_process}",
        "pulseTotal = Get number of points",
        "pulseVerified = 0",
        "pulseGateEnabled = 0",
        "pulseGatePeak = undefined",
        "pulseGateThreshold = 0",
        f'pulseSource$ = {_praat_string(source_note)}',
    ]
    if sound is not None:
        lines.extend(
            [
                f"selectObject: {sound}",
                f'To Intensity: {intensity_floor_for(floor):.6f}, 0, "yes"',
                'pulseIntensityId = selected ("Intensity")',
                'pulseGatePeak = Get maximum: tmin, tmax, "Parabolic"',
                f"if pulseGatePeak <> undefined and pulseGatePeak > {SILENT_PEAK_DB:.1f}",
                "    pulseVerified = 1",
                "    pulseGateEnabled = 1",
                f"    pulseGateThreshold = pulseGatePeak - {gate:.1f}",
                "endif",
            ]
        )
    lines.extend(
        [
            f"selectObject: {point_process}",
            "pulseUsed = 0",
            "pulseFirst = undefined",
            "pulseLast = undefined",
            "pulsePrevious = undefined",
            "pulseGaps = 0",
            "pulseGapSum = 0",
            "pulseGapMin = undefined",
            "pulseGapMax = undefined",
            "pulseLastGap = undefined",
            "pulseJitterSum = 0",
            "pulseJitterCount = 0",
            "pulseGapMean = undefined",
            "pulseJitter = undefined",
            "for pulseIndex from 1 to pulseTotal",
            "    pulseTime = Get time from index: pulseIndex",
            "    pulsePass = 1",
            "    if pulseTime < tmin or pulseTime > tmax",
            "        pulsePass = 0",
            "    endif",
            "    if pulsePass = 1 and pulseGateEnabled = 1",
            '        selectObject: pulseIntensityId',
            '        pulseLevel = Get value at time: pulseTime, "linear"',
            "        if pulseLevel = undefined",
            "            pulsePass = 0",
            "        elsif pulseLevel < pulseGateThreshold",
            "            pulsePass = 0",
            "        endif",
            f"        selectObject: {point_process}",
            "    endif",
            "    if pulsePass = 1",
            "        pulseUsed = pulseUsed + 1",
            "        if pulseFirst = undefined",
            "            pulseFirst = pulseTime",
            "        endif",
            "        pulseLast = pulseTime",
            "        if pulsePrevious <> undefined",
            "            pulseGap = pulseTime - pulsePrevious",
            "            if pulseGap > 0",
            "                pulseGaps = pulseGaps + 1",
            "                pulseGapSum = pulseGapSum + pulseGap",
            "                if pulseGapMin = undefined or pulseGap < pulseGapMin",
            "                    pulseGapMin = pulseGap",
            "                endif",
            "                if pulseGapMax = undefined or pulseGap > pulseGapMax",
            "                    pulseGapMax = pulseGap",
            "                endif",
            "                if pulseLastGap <> undefined",
            "                    pulseJitterSum = pulseJitterSum + abs (pulseGap - pulseLastGap)",
            "                    pulseJitterCount = pulseJitterCount + 1",
            "                endif",
            "                pulseLastGap = pulseGap",
            "            endif",
            "        endif",
            "        pulsePrevious = pulseTime",
            "    endif",
            "endfor",
            "if pulseGaps > 0",
            "    pulseGapMean = pulseGapSum / pulseGaps",
            "    if pulseJitterCount > 0 and pulseGapMean > 0",
            "        pulseJitter = pulseJitterSum / pulseJitterCount / pulseGapMean",
            "    endif",
            "endif",
            "# 间隔抖动（相邻间隔平均相对变化）超过 10% 说明脉冲串不干净：",
            "# 真实持续发声的局部 jitter 通常在百分之一量级，连接语流里也远低于 10%。",
            "pulseSuspect = 0",
            "if pulseGaps = 0",
            "    pulseSuspect = 1",
            "endif",
            f"if pulseJitter <> undefined and pulseJitter > {PULSE_JITTER_LIMIT:.6f}",
            "    pulseSuspect = 1",
            "endif",
            'pulseQuality$ = "脉冲质量："',
            "if pulseVerified = 0",
            '    pulseQuality$ = pulseQuality$ + "未核验；没有源 Sound：能量门没做，脉冲间隔只能当原始候选"',
            "else",
            '    pulseQuality$ = pulseQuality$ + "已筛查"',
            "    if pulseGateEnabled = 1",
            '        pulseQuality$ = pulseQuality$ + "；能量门：峰值减 " + fixed$ (pulseGatePeak - pulseGateThreshold, 1) + " dB（峰值 " + fixed$ (pulseGatePeak, 1) + " dB、门限 " + fixed$ (pulseGateThreshold, 1) + " dB）"',
            "    else",
            '        pulseQuality$ = pulseQuality$ + "；能量门没生效（区间里取不到强度峰值）"',
            "    endif",
            "    if pulseSuspect = 1",
            '        pulseQuality$ = "脉冲质量：待复核" + "；参数：已按能量门筛查"',
            "    endif",
            "endif",
            'if pulseGaps = 0',
            '    pulseFlag$ = "区间里没有可用的脉冲间隔（脉冲点不足 2 个，或都被能量门排除）"',
            "else",
            '    pulseFlag$ = ""',
            "    if pulseJitter <> undefined and pulseJitter > " + f"{PULSE_JITTER_LIMIT:.6f}",
            '        pulseFlag$ = "间隔抖动 " + fixed$ (pulseJitter * 100, 1) + "%（脉冲串不规则：可能含误检或少检，间隔统计不能直接当基频）"',
            "    endif",
            "endif",
            'if pulseFlag$ <> ""',
            '    pulseQuality$ = pulseQuality$ + "；待复核原因：" + pulseFlag$',
            "endif",
            'pulseQuality$ = pulseQuality$ + "；区间内脉冲 " + fixed$ (pulseUsed, 0) + "/" + fixed$ (pulseTotal, 0) + " 个"',
            "if pulseGaps > 0",
            '    pulseQuality$ = pulseQuality$ + "；间隔 " + fixed$ (pulseGaps, 0) + " 个"',
            "endif",
            'if pulseSource$ <> ""',
            '    pulseQuality$ = pulseQuality$ + "；源音频：" + pulseSource$',
            "endif",
            'pulseQuality$ = pulseQuality$ + "；口径：启发式声学筛查，不是发声诊断的认证"',
        ]
    )
    return lines


def pulse_cleanup_lines(*, point_process: str | None = None) -> list[str]:
    """删掉临时 Intensity（以及调用方要求删的临时 PointProcess），不动用户对象。"""

    lines = ["# ---- 清理临时对象 ----", "if pulseGateEnabled = 1 or pulseVerified = 1"]
    lines.append("    selectObject: pulseIntensityId")
    lines.append("    Remove")
    lines.append("endif")
    if point_process is not None:
        lines.extend([f"selectObject: {point_process}", "Remove"])
    return lines


def pulse_value_lines(*, point_process: str = "pulseProcessId") -> list[str]:
    """脉冲统计结果行：数量、首末时刻、平均/最短/最长间隔与抖动。"""

    return [
        'if pulseGaps = 0',
        '    pulseText$ = "脉冲（" + fixed$ (tmin, 3) + "–" + fixed$ (tmax, 3) + " 秒）无法计算：区间里没有可用的脉冲间隔（脉冲点不足 2 个，或都被能量门排除）"',
        "else",
        '    pulseText$ = "脉冲（" + fixed$ (tmin, 3) + "–" + fixed$ (tmax, 3) + " 秒）：脉冲点 " + fixed$ (pulseUsed, 0) + " 个；平均间隔 " + fixed$ (pulseGapMean, 5) + " 秒（约 " + fixed$ (1 / pulseGapMean, 1) + " Hz）；最短 " + fixed$ (pulseGapMin, 5) + " 秒、最长 " + fixed$ (pulseGapMax, 5) + " 秒"',
        "    if pulseJitter <> undefined",
        '        pulseText$ = pulseText$ + "；相邻间隔抖动 " + fixed$ (pulseJitter * 100, 1) + "%"',
        "    endif",
        "endif",
    ]
