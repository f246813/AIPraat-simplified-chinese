# ============================================================================
# AI 声学测量（对象列表版）
#
# 这个文件由 ai/tools/build_plugin.py 从 ai/praat_ai/measures.tsv 生成，
# 不要手改；要加参数请改表再跑一次生成脚本。
#
# 用法：选中一个 Sound，从 Query（查询）菜单运行「AI 声学测量...」；
#       在声音/TextGrid 编辑器里用「AI 声学测量（圈选段）...」也行。
# 结果：对象列表里多一个 Table「AI 测量结果」，每个参数一行（共 33 个参数）。
# ============================================================================

form: "AI 声学测量"
    comment: "用 measures.tsv 的默认分析设置（基频 75-600 Hz、共振峰 5/5500 Hz 等）。"
    comment: "Parameter 填 all，或者表里的参数名（例如 mean_pitch）。"
    word: "Parameter", "all"
    real: "Start (s)", "0"
    real: "End (s)", "0"
    comment: "Start 和 End 都填 0 就是整个对象。"
    boolean: "Keep temp", 0
endform

soundId = selected ("Sound")
if soundId = 0
    exitScript: "请先在对象列表里选中一个 Sound 对象。"
endif
selectObject: soundId
duration = Get total duration
if start <= 0 and end <= 0
    start = 0
    end = duration
endif
if start < 0
    start = 0
endif
if end > duration
    end = duration
endif
if end <= start
    start = 0
    end = duration
endif
if parameter$ = "all"
    all = 1
else
    all = 0
endif

Create Table with column names: "AI 测量结果", 0, "参数 说明 起点 终点 数值 单位"
tableId = selected ("Table")

# ---- mean_pitch：平均基频 ----
if all = 1 or parameter$ = "mean_pitch"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    median = Get quantile: start, end, 0.5, "Hertz"
    firstFrameTime = Get frame number from time: start
    lastFrameTime = Get frame number from time: end
    nFrames = Get number of frames
    firstFrame = round (firstFrameTime)
    lastFrame = round (lastFrameTime)
    if firstFrame < 1
    firstFrame = 1
    endif
    if lastFrame > nFrames
    lastFrame = nFrames
    endif
    if lastFrame < 1
    lastFrame = 1
    endif
    firstCenter = Get time from frame number: firstFrame
    lastCenter = Get time from frame number: lastFrame
    if firstCenter < start
    firstFrame = firstFrame + 1
    endif
    if lastCenter > end
    lastFrame = lastFrame - 1
    endif
    value = undefined
    if median <> undefined
    ceilingValue = median * 1.5
    floorValue = median * 0.5
    totalValue = 0
    countValue = 0
    for pitchFrame from firstFrame to lastFrame
    f = Get value in frame: pitchFrame, "Hertz"
    if f <> undefined and f >= floorValue and f <= ceilingValue
    totalValue = totalValue + f
    countValue = countValue + 1
    endif
    endfor
    if countValue > 0
    value = totalValue / countValue
    endif
    endif
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "mean_pitch"
    Set string value: row, "说明", "平均基频"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- minimum_pitch：最低基频 ----
if all = 1 or parameter$ = "minimum_pitch"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    median = Get quantile: start, end, 0.5, "Hertz"
    firstFrameTime = Get frame number from time: start
    lastFrameTime = Get frame number from time: end
    nFrames = Get number of frames
    firstFrame = round (firstFrameTime)
    lastFrame = round (lastFrameTime)
    if firstFrame < 1
    firstFrame = 1
    endif
    if lastFrame > nFrames
    lastFrame = nFrames
    endif
    if lastFrame < 1
    lastFrame = 1
    endif
    firstCenter = Get time from frame number: firstFrame
    lastCenter = Get time from frame number: lastFrame
    if firstCenter < start
    firstFrame = firstFrame + 1
    endif
    if lastCenter > end
    lastFrame = lastFrame - 1
    endif
    value = undefined
    if median <> undefined
    ceilingValue = median * 1.5
    floorValue = median * 0.5
    lowest = 0
    for pitchFrame from firstFrame to lastFrame
    f = Get value in frame: pitchFrame, "Hertz"
    if f <> undefined and f >= floorValue and f <= ceilingValue
    if lowest = 0 or f < lowest
    lowest = f
    endif
    endif
    endfor
    if lowest > 0
    value = lowest
    endif
    endif
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "minimum_pitch"
    Set string value: row, "说明", "最低基频"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- maximum_pitch：最高基频 ----
if all = 1 or parameter$ = "maximum_pitch"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    median = Get quantile: start, end, 0.5, "Hertz"
    firstFrameTime = Get frame number from time: start
    lastFrameTime = Get frame number from time: end
    nFrames = Get number of frames
    firstFrame = round (firstFrameTime)
    lastFrame = round (lastFrameTime)
    if firstFrame < 1
    firstFrame = 1
    endif
    if lastFrame > nFrames
    lastFrame = nFrames
    endif
    if lastFrame < 1
    lastFrame = 1
    endif
    firstCenter = Get time from frame number: firstFrame
    lastCenter = Get time from frame number: lastFrame
    if firstCenter < start
    firstFrame = firstFrame + 1
    endif
    if lastCenter > end
    lastFrame = lastFrame - 1
    endif
    value = undefined
    if median <> undefined
    ceilingValue = median * 1.5
    floorValue = median * 0.5
    highest = 0
    for pitchFrame from firstFrame to lastFrame
    f = Get value in frame: pitchFrame, "Hertz"
    if f <> undefined and f >= floorValue and f <= ceilingValue
    if f > highest
    highest = f
    endif
    endif
    endfor
    if highest > 0
    value = highest
    endif
    endif
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "maximum_pitch"
    Set string value: row, "说明", "最高基频"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- median_pitch：基频中位数 ----
if all = 1 or parameter$ = "median_pitch"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    value = Get quantile: start, end, 0.5, "Hertz"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "median_pitch"
    Set string value: row, "说明", "基频中位数"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- sd_pitch：基频标准差 ----
if all = 1 or parameter$ = "sd_pitch"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    value = Get standard deviation: start, end, "Hertz"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "sd_pitch"
    Set string value: row, "说明", "基频标准差"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- pitch_slope：基频平均绝对斜率 ----
if all = 1 or parameter$ = "pitch_slope"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    value = Get mean absolute slope: "Hertz"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "pitch_slope"
    Set string value: row, "说明", "基频平均绝对斜率"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz/s（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz/s"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- pitch_slope_octave_free：不含倍频跳变的基频斜率 ----
if all = 1 or parameter$ = "pitch_slope_octave_free"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    value = Get slope without octave jumps
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "pitch_slope_octave_free"
    Set string value: row, "说明", "不含倍频跳变的基频斜率"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "半音/s（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "半音/s"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- pitch_start：起点基频 ----
if all = 1 or parameter$ = "pitch_start"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    value = Get value at time: start, "Hertz", "Linear"
    step = Get time step
    if step <= 0
    step = (end - start) / 100
    endif
    t = start
    while value = undefined and t < end
    t = t + step
    value = Get value at time: t, "Hertz", "Linear"
    endwhile
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "pitch_start"
    Set string value: row, "说明", "起点基频"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- pitch_end：终点基频 ----
if all = 1 or parameter$ = "pitch_end"
    selectObject: soundId
    pitchId = To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000, 15, "no", 0.03, 0.09, 0.50, 0.055, 0.35, 0.14
    selectObject: pitchId
    value = Get value at time: end, "Hertz", "Linear"
    step = Get time step
    if step <= 0
    step = (end - start) / 100
    endif
    t = end
    while value = undefined and t > start
    t = t - step
    value = Get value at time: t, "Hertz", "Linear"
    endwhile
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "pitch_end"
    Set string value: row, "说明", "终点基频"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: pitchId
        Remove
    endif
endif

# ---- mean_intensity：平均强度 ----
if all = 1 or parameter$ = "mean_intensity"
    selectObject: soundId
    intensityId = To Intensity: 100.000000, 0, "yes"
    selectObject: intensityId
    value = Get mean: start, end, "energy"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "mean_intensity"
    Set string value: row, "说明", "平均强度"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: intensityId
        Remove
    endif
endif

# ---- minimum_intensity：最低强度 ----
if all = 1 or parameter$ = "minimum_intensity"
    selectObject: soundId
    intensityId = To Intensity: 100.000000, 0, "yes"
    selectObject: intensityId
    value = Get minimum: start, end, "Parabolic"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "minimum_intensity"
    Set string value: row, "说明", "最低强度"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: intensityId
        Remove
    endif
endif

# ---- maximum_intensity：最高强度 ----
if all = 1 or parameter$ = "maximum_intensity"
    selectObject: soundId
    intensityId = To Intensity: 100.000000, 0, "yes"
    selectObject: intensityId
    value = Get maximum: start, end, "Parabolic"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "maximum_intensity"
    Set string value: row, "说明", "最高强度"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: intensityId
        Remove
    endif
endif

# ---- median_intensity：强度中位数 ----
if all = 1 or parameter$ = "median_intensity"
    selectObject: soundId
    intensityId = To Intensity: 100.000000, 0, "yes"
    selectObject: intensityId
    value = Get quantile: start, end, 0.5
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "median_intensity"
    Set string value: row, "说明", "强度中位数"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: intensityId
        Remove
    endif
endif

# ---- sd_intensity：强度标准差 ----
if all = 1 or parameter$ = "sd_intensity"
    selectObject: soundId
    intensityId = To Intensity: 100.000000, 0, "yes"
    selectObject: intensityId
    value = Get standard deviation: start, end
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "sd_intensity"
    Set string value: row, "说明", "强度标准差"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: intensityId
        Remove
    endif
endif

# ---- hnr：谐噪比 HNR ----
if all = 1 or parameter$ = "hnr"
    selectObject: soundId
    harmonicityId = To Harmonicity (cc): 0.01, 75.000000, 0.1, 1
    selectObject: harmonicityId
    value = Get mean: start, end
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "hnr"
    Set string value: row, "说明", "谐噪比 HNR"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: harmonicityId
        Remove
    endif
endif

# ---- rms：有效值 RMS ----
if all = 1 or parameter$ = "rms"
    selectObject: soundId
    value = Get root-mean-square: start, end
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "rms"
    Set string value: row, "说明", "有效值 RMS"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Pa（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Pa"
    endif
endif

# ---- peak_amplitude：峰值幅度 ----
if all = 1 or parameter$ = "peak_amplitude"
    selectObject: soundId
    value = Get maximum: start, end, "Sinc70"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "peak_amplitude"
    Set string value: row, "说明", "峰值幅度"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Pa（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Pa"
    endif
endif

# ---- mean_amplitude：平均幅度 ----
if all = 1 or parameter$ = "mean_amplitude"
    selectObject: soundId
    value = Get mean: 0, start, end
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "mean_amplitude"
    Set string value: row, "说明", "平均幅度"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Pa（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Pa"
    endif
endif

# ---- f1：第 1 共振峰平均 ----
if all = 1 or parameter$ = "f1"
    selectObject: soundId
    formantId = To Formant (burg): 0, 5, 5500.000000, 0.025000, 50.000000
    selectObject: formantId
    value = Get mean: 1, start, end, "hertz"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "f1"
    Set string value: row, "说明", "第 1 共振峰平均"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: formantId
        Remove
    endif
endif

# ---- f2：第 2 共振峰平均 ----
if all = 1 or parameter$ = "f2"
    selectObject: soundId
    formantId = To Formant (burg): 0, 5, 5500.000000, 0.025000, 50.000000
    selectObject: formantId
    value = Get mean: 2, start, end, "hertz"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "f2"
    Set string value: row, "说明", "第 2 共振峰平均"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: formantId
        Remove
    endif
endif

# ---- f3：第 3 共振峰平均 ----
if all = 1 or parameter$ = "f3"
    selectObject: soundId
    formantId = To Formant (burg): 0, 5, 5500.000000, 0.025000, 50.000000
    selectObject: formantId
    value = Get mean: 3, start, end, "hertz"
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "f3"
    Set string value: row, "说明", "第 3 共振峰平均"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: formantId
        Remove
    endif
endif

# ---- b1：第 1 共振峰带宽中位数 ----
if all = 1 or parameter$ = "b1"
    selectObject: soundId
    formantId = To Formant (burg): 0, 5, 5500.000000, 0.025000, 50.000000
    selectObject: formantId
    value = Get quantile of bandwidth: 1, start, end, "hertz", 0.5
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "b1"
    Set string value: row, "说明", "第 1 共振峰带宽中位数"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: formantId
        Remove
    endif
endif

# ---- b2：第 2 共振峰带宽中位数 ----
if all = 1 or parameter$ = "b2"
    selectObject: soundId
    formantId = To Formant (burg): 0, 5, 5500.000000, 0.025000, 50.000000
    selectObject: formantId
    value = Get quantile of bandwidth: 2, start, end, "hertz", 0.5
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "b2"
    Set string value: row, "说明", "第 2 共振峰带宽中位数"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: formantId
        Remove
    endif
endif

# ---- b3：第 3 共振峰带宽中位数 ----
if all = 1 or parameter$ = "b3"
    selectObject: soundId
    formantId = To Formant (burg): 0, 5, 5500.000000, 0.025000, 50.000000
    selectObject: formantId
    value = Get quantile of bandwidth: 3, start, end, "hertz", 0.5
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "b3"
    Set string value: row, "说明", "第 3 共振峰带宽中位数"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: formantId
        Remove
    endif
endif

# ---- centre_of_gravity：频谱重心 ----
if all = 1 or parameter$ = "centre_of_gravity"
    selectObject: soundId
    spectrumId = To Spectrum: "yes"
    selectObject: spectrumId
    value = Get centre of gravity: 2
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "centre_of_gravity"
    Set string value: row, "说明", "频谱重心"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "Hz（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "Hz"
    endif
    if keep_temp = 0
        selectObject: spectrumId
        Remove
    endif
endif

# ---- skewness：频谱偏度 ----
if all = 1 or parameter$ = "skewness"
    selectObject: soundId
    spectrumId = To Spectrum: "yes"
    selectObject: spectrumId
    value = Get skewness: 2
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "skewness"
    Set string value: row, "说明", "频谱偏度"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", ""
    endif
    if keep_temp = 0
        selectObject: spectrumId
        Remove
    endif
endif

# ---- kurtosis：频谱峰度 ----
if all = 1 or parameter$ = "kurtosis"
    selectObject: soundId
    spectrumId = To Spectrum: "yes"
    selectObject: spectrumId
    value = Get kurtosis: 2
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "kurtosis"
    Set string value: row, "说明", "频谱峰度"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", ""
    endif
    if keep_temp = 0
        selectObject: spectrumId
        Remove
    endif
endif

# ---- cpps：倒谱峰突出 CPPS ----
if all = 1 or parameter$ = "cpps"
    selectObject: soundId
    powerCepstrogramId = To PowerCepstrogram: 60.000000, 0.002, 5000, 50
    selectObject: powerCepstrogramId
    value = Get CPPS (hillenbrand): "yes", 0.001, 0.00005, 60.000000, 330.000000
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "cpps"
    Set string value: row, "说明", "倒谱峰突出 CPPS"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: powerCepstrogramId
        Remove
    endif
endif

# ---- local_jitter：局部基频微扰 jitter ----
if all = 1 or parameter$ = "local_jitter"
    selectObject: soundId
    pointProcessId = To PointProcess (periodic, cc): 75.000000, 600.000000
    selectObject: pointProcessId
    value = Get jitter (local): start, end, 0.0001, 0.02, 1.3
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "local_jitter"
    Set string value: row, "说明", "局部基频微扰 jitter"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "%（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "%"
    endif
    if keep_temp = 0
        selectObject: pointProcessId
        Remove
    endif
endif

# ---- rap_jitter：rap 基频微扰 jitter ----
if all = 1 or parameter$ = "rap_jitter"
    selectObject: soundId
    pointProcessId = To PointProcess (periodic, cc): 75.000000, 600.000000
    selectObject: pointProcessId
    value = Get jitter (rap): start, end, 0.0001, 0.02, 1.3
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "rap_jitter"
    Set string value: row, "说明", "rap 基频微扰 jitter"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "%（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "%"
    endif
    if keep_temp = 0
        selectObject: pointProcessId
        Remove
    endif
endif

# ---- local_shimmer_percent：局部振幅微扰 shimmer ----
if all = 1 or parameter$ = "local_shimmer_percent"
    selectObject: soundId
    pointProcessId = To PointProcess (periodic, cc): 75.000000, 600.000000
    selectObject: pointProcessId
    plusObject: soundId
    value = Get shimmer (local): start, end, 0.0001, 0.02, 1.3, 1.6
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "local_shimmer_percent"
    Set string value: row, "说明", "局部振幅微扰 shimmer"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "%（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "%"
    endif
    if keep_temp = 0
        selectObject: pointProcessId
        Remove
    endif
endif

# ---- local_shimmer_db：局部振幅微扰 shimmer ----
if all = 1 or parameter$ = "local_shimmer_db"
    selectObject: soundId
    pointProcessId = To PointProcess (periodic, cc): 75.000000, 600.000000
    selectObject: pointProcessId
    plusObject: soundId
    value = Get shimmer (local_dB): start, end, 0.0001, 0.02, 1.3, 1.6
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "local_shimmer_db"
    Set string value: row, "说明", "局部振幅微扰 shimmer"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "dB（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "dB"
    endif
    if keep_temp = 0
        selectObject: pointProcessId
        Remove
    endif
endif

# ---- apq3_shimmer：apq3 振幅微扰 shimmer ----
if all = 1 or parameter$ = "apq3_shimmer"
    selectObject: soundId
    pointProcessId = To PointProcess (periodic, cc): 75.000000, 600.000000
    selectObject: pointProcessId
    plusObject: soundId
    value = Get shimmer (apq3): start, end, 0.0001, 0.02, 1.3, 1.6
    selectObject: tableId
    Append row
    row = Get number of rows
    Set string value: row, "参数", "apq3_shimmer"
    Set string value: row, "说明", "apq3 振幅微扰 shimmer"
    Set numeric value: row, "起点", start
    Set numeric value: row, "终点", end
    if value = undefined
        Set string value: row, "单位", "%（这一段没法算）"
    else
        Set numeric value: row, "数值", value
        Set string value: row, "单位", "%"
    endif
    if keep_temp = 0
        selectObject: pointProcessId
        Remove
    endif
endif

# 收尾：结果表留着一行行看，用户的 Sound 保持选中。
selectObject: tableId
rows = Get number of rows
if rows = 0
    Append row
    Set string value: 1, "参数", "（没有匹配的参数）"
    Set string value: 1, "说明", parameter$ + " 不在参数表里，请看 ai/praat_ai/measures.tsv。"
endif
selectObject: soundId
plusObject: tableId
