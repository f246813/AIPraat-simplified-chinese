---
name: prosody
description: 音高走向测量与结论
version: 1
phases: [planner, report]
source: AIPraat analysis guidance
---
目标涉及声调、语调或重音时，先核对对象、用户范围、音节边界及语言。先用工具量出音高走向：使用同一提取设置的分段 pitch_statistics，或 measure(parameter="pitch_start,pitch_end") 取得带时间的首尾基频；必要时用 pitch(time="auto") 查看局部观察。只用已筛查的观察描述覆盖范围内的变化，缺测与待复核帧保持原状态。只有全段平均值时不要下音调结论。
pitch_slope 与 pitch_slope_octave_free 都是非负变化幅度，不能判断升降；首尾值只支持两个实际观察时刻之间的净变化，不能证明中间单调或整段平坦。首尾缺测时不能把剩余有声段外推到整个选区。
声调、调值和重音型分类需要音节/音拍边界、完整轮廓、语言、语境与对应判据。请求分类而依据不全时，列出具体缺项；已有基频和时刻照常交付。
