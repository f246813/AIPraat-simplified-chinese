"""tools 包的一个分片；行为与原 tools.py 完全一致。"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from .. import external_script, measures

from ._common import (ANALYSIS_SOUND_CLASSES, SOUND_CLASSES, TEMPORARY_OBJECT_NAME, TIME_DOMAIN_CLASSES, ToolContext, ToolError, _assemble, _echoes_editor_selection, _formant_lines, _formant_numbers, _insert_boundary_block, _integer, _integer_arg, _number, _number_arg, _object_arg, _pitch_band, _pitch_extreme_lines, _point_query_lines, _range_lines, _range_pair, _rise_onset_lines, _seconds_arg, _seconds_argument, _tier_number, _unit_arg, _unit_display, _unit_literal, _vot_time, _write_result, quote)


def _build_duration(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_object(arguments.get("object"))
    context.require_class(row, TIME_DOMAIN_CLASSES, "这个对象没有时长")
    lines = [
        f"selectObject: {row.id}",
        "duration = Get total duration",
        _write_result(
            context,
            [quote(f"{row.name} 总时长 = "), "fixed$ (duration, 6)", quote(" 秒")],
        ),
    ]
    return _assemble(lines, context)


def _build_formant_bandwidth(arguments: Mapping[str, Any], context: ToolContext) -> str:
    return _assemble(
        _formant_lines(arguments, context, command="Get bandwidth at time", label="带宽"),
        context,
    )


def _build_formant_frequency(arguments: Mapping[str, Any], context: ToolContext) -> str:
    return _assemble(
        _formant_lines(
            arguments,
            context,
            command="Get value at time",
            label="频率",
            with_bandwidth=True,
        ),
        context,
    )


def _build_pitch(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Pitch"}),
        "基频分析需要 Sound 或 Pitch 对象",
    )
    temporary = row.class_name != "Pitch"
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    if temporary:
        floor, ceiling = _pitch_band(arguments)
        lines.extend(
            [
                f"To Pitch: 0, {floor:.6f}, {ceiling:.6f}",
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    lines.extend(
        _point_query_lines(
            arguments,
            context,
            command='Get value at time: time, "Hertz", "linear"',
            label="基频",
            unit_text=" Hz",
            undefined_message=(
                " 秒处）无法计算：该时刻没有周期性声源（可能是无声段或清音）"
            ),
            row=row,
        )
    )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return _assemble(lines, context)


def _build_pitch_statistics(
    arguments: Mapping[str, Any],
    context: ToolContext,
) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Pitch"}),
        "基频分析需要 Sound 或 Pitch 对象",
    )
    temporary = row.class_name != "Pitch"
    # 注意：Praat 里 from / to / end 都是保留字，变量名只能用 tmin / tmax。
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    lines.extend(_range_lines(arguments, row))
    if temporary:
        floor, ceiling = _pitch_band(arguments)
        lines.extend(
            [
                f"To Pitch: 0, {floor:.6f}, {ceiling:.6f}",
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    lines.extend(['nFrames = Get number of frames'])
    lines.extend(_pitch_extreme_lines())
    lines.extend(
        [
            'rawMean = Get mean: tmin, tmax, "Hertz"',
            "if rawMean = undefined",
            _write_result(
                context,
                [
                    quote("基频统计（"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）无法计算：该区间没有周期性声源（可能是无声段或清音）"),
                    "rangeNote$",
                ],
            ),
            "else",
            # ``mean`` 来自逐帧限幅循环，和最低/最高同一批帧；median 取不到时
            # （理论上 rawMean 有值就取得到）退回 Praat 自己的均值，宁可退回也不报 undefined。
            "if mean = undefined",
            "    mean = rawMean",
            "endif",
            'meanNote$ = ""',
            "if abs (mean - rawMean) > 1",
            '    meanNote$ = "（含被剔除帧的原始平均为 " + fixed$ (rawMean, 1) + " Hz）"',
            "endif",
            "if minimum = undefined or maximum = undefined",
            _write_result(
                context,
                [
                    quote("基频统计（"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）：平均 "),
                    "fixed$ (mean, 3)",
                    quote(" Hz（这一段里没有可信的最高/最低值）"),
                    "rangeNote$",
                    "pitchLimit$",
                    "meanNote$",
                ],
            ),
            "else",
            _write_result(
                context,
                [
                    quote("基频统计（"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）：平均 "),
                    "fixed$ (mean, 3)",
                    quote(" Hz，最低 "),
                    "fixed$ (minimum, 3)",
                    quote(" Hz，最高 "),
                    "fixed$ (maximum, 3)",
                    quote(" Hz，最高点出现在 "),
                    "fixed$ (maxtime, 3)",
                    quote(" 秒"),
                    "rangeNote$",
                    "pitchLimit$",
                    "meanNote$",
                ],
            ),
            "endif",
            "endif",
        ]
    )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return _assemble(lines, context)


def _build_intensity(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Intensity"}),
        "强度分析需要 Sound 或 Intensity 对象",
    )
    temporary = row.class_name != "Intensity"
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    if temporary:
        lines.extend(
            [
                'To Intensity: 100, 0, "yes"',
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    lines.extend(
        _point_query_lines(
            arguments,
            context,
            command='Get value at time: time, "cubic"',
            label="强度",
            unit_text=" dB",
            undefined_message=" 秒处）无法计算：该时刻没有强度数据",
            row=row,
        )
    )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return _assemble(lines, context)


def _build_intensity_statistics(
    arguments: Mapping[str, Any],
    context: ToolContext,
) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Intensity"}),
        "强度分析需要 Sound 或 Intensity 对象",
    )
    temporary = row.class_name != "Intensity"
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    lines.extend(_range_lines(arguments, row))
    if temporary:
        lines.extend(
            [
                'To Intensity: 100, 0, "yes"',
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    lines.extend(
        [
            'mean = Get mean: tmin, tmax, "energy"',
            'maximum = Get maximum: tmin, tmax, "Parabolic"',
            _write_result(
                context,
                [
                    quote("强度统计（"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）：平均 "),
                    "fixed$ (mean, 3)",
                    quote(" dB，最高 "),
                    "fixed$ (maximum, 3)",
                    quote(" dB"),
                    "rangeNote$",
                ],
            ),
        ]
    )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return _assemble(lines, context)


def _build_object_info(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_object(arguments.get("object"))
    has_duration = row.class_name in TIME_DOMAIN_CLASSES
    is_sound = row.class_name in SOUND_CLASSES
    lines = [f"selectObject: {row.id}"]
    if has_duration:
        lines.append("duration = Get total duration")
    if row.class_name == "Sound":
        lines.append("channels = Get number of channels")
    if is_sound:
        lines.append("rate = Get sampling frequency")
    fragments = [quote(f"{row.class_name}「{row.name}」（id {row.id}）")]
    if has_duration:
        fragments.extend([quote("：时长 "), "fixed$ (duration, 3)", quote(" 秒")])
    if row.class_name == "Sound":
        fragments.extend(
            [
                quote("，通道数 "),
                "fixed$ (channels, 0)",
            ]
        )
    if is_sound:
        fragments.extend(
            [
                quote("，采样率 "),
                "fixed$ (rate, 0)",
                quote(" Hz"),
            ]
        )
    if not has_duration:
        fragments.append(quote("：这类对象没有时长信息"))
    # 输出选区覆盖比例，供报告定位；时间比例不能单独确定音素身份。
    selection = row.selection
    if has_duration and selection and selection[0] is not None and selection[1] is not None:
        lines.extend([f"selStart = {float(selection[0]):.6f}", f"selEnd = {float(selection[1]):.6f}"])
        fragments.extend(
            [
                quote("；圈选 "),
                "fixed$ (selStart, 3)",
                quote("–"),
                "fixed$ (selEnd, 3)",
                quote(" 秒（占时长 "),
                "fixed$ (selStart / duration * 100, 0)",
                quote("–"),
                "fixed$ (selEnd / duration * 100, 0)",
                quote("%）"),
            ]
        )
    lines.append(_write_result(context, fragments))
    return _assemble(lines, context)


def _build_formant_statistics(
    arguments: Mapping[str, Any],
    context: ToolContext,
) -> str:
    """一段时间内的共振峰平均值与标准差（「共振峰平均是多少」用这个）。"""

    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Formant"}),
        "共振峰分析需要 Sound 或 Formant 对象",
    )
    formatns = _formant_numbers(arguments)
    unit = _unit_literal(arguments, "hertz")
    unit_text = _unit_display(arguments, "hertz")
    temporary = row.class_name != "Formant"
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    lines.extend(_range_lines(arguments, row))
    if temporary:
        lines.extend(
            [
                "To Formant (burg): 0, 5, 5500, 0.025, 50",
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    for formant in formatns:
        # 每条共振峰各写一行：脚本里 mean/std 会被覆盖，不能等循环结束再统一写。
        lines.append(f"mean = Get mean: {formant}, tmin, tmax, {unit}")
        lines.append(f"std = Get standard deviation: {formant}, tmin, tmax, {unit}")
        lines.append(
            _write_result(
                context,
                [
                    quote(f"第 {formant} 共振峰平均（"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）= "),
                    "fixed$ (mean, 1)",
                    quote(unit_text),
                    quote("（标准差 "),
                    "fixed$ (std, 1)",
                    quote("）"),
                    "rangeNote$",
                ],
            )
        )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return _assemble(lines, context)


def _build_harmonicity_statistics(
    arguments: Mapping[str, Any],
    context: ToolContext,
) -> str:
    """谐噪比 HNR（Praat 的 Harmonicity，单位 dB）。"""

    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Harmonicity"}),
        "谐噪比分析需要 Sound 或 Harmonicity 对象",
    )
    temporary = row.class_name != "Harmonicity"
    # 谐噪比的基频下限使用 measures.tsv 的 harmonicity_floor（20–1000 Hz），
    # 与默认 75 Hz 不同；越界时明确报错。
    try:
        floor = _number(arguments, "pitch_floor", 75.0, 20.0, 1000.0)
    except ToolError as error:
        raise ToolError(
            "谐噪比分析的基频下限（harmonicity_floor，参数名 pitch_floor）"
            "必须在 20 与 1000 Hz 之间。") from error
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    lines.extend(_range_lines(arguments, row))
    if temporary:
        lines.extend(
            [
                f"To Harmonicity (cc): 0.01, {floor:g}, 0.1, 1",
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    lines.extend(
        [
            "mean = Get mean: tmin, tmax",
            'maximum = Get maximum: tmin, tmax, "Parabolic"',
            "if mean = undefined",
            _write_result(
                context,
                [
                    quote("谐噪比 HNR（"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）无法计算：该区间没有周期性声源"),
                    "rangeNote$",
                ],
            ),
            "else",
            _write_result(
                context,
                [
                    quote("谐噪比 HNR（"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）：平均 "),
                    "fixed$ (mean, 2)",
                    quote(" dB，最高 "),
                    "fixed$ (maximum, 2)",
                    quote(" dB"),
                    "rangeNote$",
                ],
            ),
            "endif",
        ]
    )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return _assemble(lines, context)


def _build_vot_explicit(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """VOT（嗓音起始时间）= 浊音起始时刻 − 爆破/除阻时刻。

    这里不做自动检测：VOT 依赖"爆破"和"浊音起始"这两个点的判断，靠脚本猜很容易
    给出看起来精确、其实错得离谱的数字。所以要么用用户/标注给出的两个时刻相减，
    要么由 TextGrid 的边界来定，并把用的两个时刻写进结果，让用户能核对。
    """

    row = context.resolve_by_class(
        arguments.get("object"),
        frozenset({"TextGrid", "Sound"}),
        "算 VOT 需要一个 TextGrid（按层补边界）或 Sound（直接按两个时刻相减）",
    )
    if arguments.get("burst", None) in (None, ""):
        raise ToolError(
            "算 VOT 需要 burst 参数：爆破/除阻时刻（秒）。"
            "前端不做自动检测，请先看波形或标出这个点。"
        )
    if arguments.get("voicing", arguments.get("onset", None)) in (None, ""):
        raise ToolError("算 VOT 需要 voicing 参数：浊音起始时刻（秒）。")
    burst = _number(arguments, "burst", 0.0, 0.0, 36000.0)
    voicing = _number(
        {"voicing": arguments.get("voicing", arguments.get("onset", None))},
        "voicing",
        0.0,
        0.0,
        36000.0,
    )
    if voicing <= burst:
        raise ToolError(
            f"浊音起始必须晚于爆破时刻（现在 burst={burst:.3f} 秒、"
            f"voicing={voicing:.3f} 秒）；先浊后爆（prevoiced）的负 VOT 需要原生动作，那条路线已暂停。"
        )
    tier = _tier_number(arguments)
    lines = [
        f"selectObject: {row.id}",
        f"t1 = {burst:.6f}",
        f"t2 = {voicing:.6f}",
        "vot = t2 - t1",
    ]
    if row.class_name == "TextGrid":
        lines.extend(
            [
                "duration = Get total duration",
                f"tier = {tier}",
                "inserted = 0",
                *_insert_boundary_block(tier, "t1", "inserted", "v1"),
                *_insert_boundary_block(tier, "t2", "inserted", "v2"),
                'note$ = ""',
                "if inserted > 0",
                '    note$ = "，并在该层补上了边界"',
                "endif",
                _write_result(
                    context,
                    [
                        quote("VOT = "),
                        "fixed$ (vot, 4)",
                        quote(" 秒（"),
                        "fixed$ (vot * 1000, 1)",
                        quote(" 毫秒）：第 "),
                        "fixed$ (tier, 0)",
                        quote(" 层 "),
                        "fixed$ (t1, 3)",
                        quote(" 秒（爆破，占时长 "),
                        "fixed$ (t1 / duration * 100, 0)",
                        quote("%）→ "),
                        "fixed$ (t2, 3)",
                        quote(" 秒（浊音起始，占时长 "),
                        "fixed$ (t2 / duration * 100, 0)",
                        quote("%）"),
                        "note$",
                    ],
                ),
            ]
        )
    else:
        lines.append("duration = Get total duration")
        lines.append(
            _write_result(
                context,
                [
                    quote("VOT = "),
                    "fixed$ (vot, 4)",
                    quote(" 秒（"),
                    "fixed$ (vot * 1000, 1)",
                    quote(" 毫秒）："),
                    "fixed$ (t1, 3)",
                    quote(" 秒（爆破，占时长 "),
                    "fixed$ (t1 / duration * 100, 0)",
                    quote("%）→ "),
                    "fixed$ (t2, 3)",
                    quote(" 秒（浊音起始，占时长 "),
                    "fixed$ (t2 / duration * 100, 0)",
                    quote("%）；按给出的两个时刻相减，未做自动检测"),
                ],
            )
        )
    return _assemble(lines, context)


def _build_vot_auto(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """在用户圈出的大概范围里自动估计 VOT（爆破 → 浊音起始）。

    爆破和浊音起始分成两步测，结果里分别写清楚，好让人判断是哪一步不稳：

    1. 爆破（释放瞬态）：把声音带通到 2–8 kHz 再算强度包络（1 ms 步长、3.2 ms
       窗口，比全频段窗口短，免得把爆破点平均到前面去），取最陡的一段上升沿，
       再退到这段上升沿之前的低能量帧。升幅不到 ``burst_db``（默认 6 dB）就换
       全频段包络兜底，两个都不够就报"找不到爆破瞬态"。不用"区间峰值 − x dB"
       当阈值：范围里只要还夹着别的强段，这个阈值就会漂。
    2. 浊音起始：``To Pitch (ac)``（2 ms 步长、默认浊音阈值，走的就是短时自相关）
       连续 3 帧（6 ms）都判出基频才算声带开始振动；谐噪比只截起点之后 50 ms
       那一小段算峰值，当谐波结构的依据写进结果——整段声音做交叉相关太慢
       （10 秒的声音上要 0.3 秒，用户能感觉出卡）。
       注意：``To Harmonicity (cc)`` 的 periodsPerWindow 给 0.5 会让 Praat 7.0.02
       直接在 Sound_to_Pitch.cpp 断言崩溃，只能 ≥ 1；而 4 ms 的窗口（minPitch 250）
       在 220 Hz 上会判成"全是噪声"，所以窗口就用 1/minPitch。
    3. VOT = 浊音起始 − 爆破时刻。

    范围整段都在浊音里时报"起点已是浊音"；范围内后面还有第二段浊音（中间隔了
    ≥20 毫秒的低谐噪比段）时只报第一个候选，并提示范围偏大。

    范围来自用户话里的 from/to，或者他在波形上手动拖出来的选区
    （``chat_context.tsv`` 的 ``sel_start``/``sel_end``）；模型把圈选抄成
    from/to 时结果里仍注明"按编辑器圈选"。

    这是估计值，不是人工标注：结果里写明两步各自的依据、范围和时间分辨率。
    """

    row = context.resolve_by_class(
        arguments.get("object"), ANALYSIS_SOUND_CLASSES, "自动检测 VOT 需要一个 Sound 对象"
    )
    burst_db = _number(arguments, "burst_db", 6.0, 3.0, 30.0)
    pitch_floor, _ = _pitch_band(arguments, with_ceiling=False)
    start_arg, end_arg = _range_pair(arguments)
    editor_selection = row.selection
    # 模型常把编辑器里的圈选原样抄进 from/to；数值对得上就还按圈选报，
    # 否则回话里会丢掉「这段范围是用户自己在波形上圈的」这个出处。
    if editor_selection is not None and _echoes_editor_selection(
        start_arg, end_arg, editor_selection
    ):
        start_arg = None
        end_arg = None
    had_range = start_arg not in (None, "") or end_arg not in (None, "")
    if had_range:
        editor_selection = None
    lines = [
        f"selectObject: {row.id}",
        "duration = Get total duration",
        "tmin = 0",
        "tmax = duration",
    ]
    if editor_selection is not None:
        lines.append(f"tmin = {editor_selection[0]:.6f}")
        lines.append(f"tmax = {editor_selection[1]:.6f}")
    elif start_arg not in (None, ""):
        lines.append(f"tmin = {_seconds_argument(start_arg, 'from'):.6f}")
    if editor_selection is None and end_arg not in (None, ""):
        lines.append(f"tmax = {_seconds_argument(end_arg, 'to'):.6f}")
    if editor_selection is not None:
        note_lines = [
            f'rangeNote$ = "（按编辑器圈选 {editor_selection[0]:.3f}–'
            f'{editor_selection[1]:.3f} 秒）"'
        ]
    else:
        note_lines = [
            'rangeNote$ = "（未指定范围，按整个对象搜索）"',
            "if tmin > 0 or tmax < duration",
            '    rangeNote$ = ""',
            "endif",
        ]
    lines.extend(
        [
            "if tmin < 0",
            "    tmin = 0",
            "endif",
            "if tmax > duration",
            "    tmax = duration",
            "endif",
            "if tmin > tmax - 0.002",
            _write_result(context, [quote(
                "自动检测失败：搜索范围无效、超出声音时长或不足 2 毫秒。"
                "请在声音内重新圈选爆破和浊音起始附近的范围；未改用整个声音。"
            )]),
            "else",
            *note_lines,
            # ① 爆破（释放瞬态）：带通到 2–8 kHz 再求强度包络的最陡上升沿
            f"selectObject: {row.id}",
            "Filter (pass Hann band): 2000, 8000, 100",
            'hfSound = selected ("Sound")',
            'To Intensity: 2000, 0.001, "yes"',
            'hfEnv = selected ("Intensity")',
            *_rise_onset_lines("hf", "hfEnv"),
            f"selectObject: {row.id}",
            'To Intensity: 1000, 0.001, "yes"',
            'bbEnv = selected ("Intensity")',
            *_rise_onset_lines("bb", "bbEnv"),
            "burstTime = -1",
            "burstRise = 0",
            'burstBand$ = ""',
            f"if hfRise >= {burst_db:.1f}",
            "    burstTime = hfOnset",
            "    burstRise = hfRise",
            '    burstBand$ = "高频带 2–8 kHz"',
            f"elsif bbRise >= {burst_db:.1f}",
            "    burstTime = bbOnset",
            "    burstRise = bbRise",
            '    burstBand$ = "全频段"',
            "endif",
            "selectObject: hfEnv",
            "plusObject: bbEnv",
            "plusObject: hfSound",
            "Remove",
            # ② 浊音起始：自相关基频连续 3 帧（6 ms）成立；谐噪比另取一小段当佐证
            f"selectObject: {row.id}",
            f'To Pitch (ac): 0.002, {pitch_floor:.6f}, 15, "no", 0.03, '
            '0.45, 0.01, 0.35, 0.14, 600',
            "pn = Get number of frames",
            "voicingTime = -1",
            "f0Onset = 0",
            "firstVoicedTime = -1",
            "run = 0",
            "runStart = -1",
            "runStartF0 = 0",
            "lastVoiced = -1",
            "candTime = -1",
            "secondVoicingTime = -1",
            "for i from 1 to pn",
            "    ft = Get time from frame number: i",
            "    if ft >= tmin and ft <= tmax",
            '        v = Get value in frame: i, "Hertz"',
            "        if v <> undefined",
            "            run = run + 1",
            "            if run = 1",
            "                runStart = ft",
            "                runStartF0 = v",
            "            endif",
            "            if firstVoicedTime < 0",
            "                firstVoicedTime = ft",
            "            endif",
            "            if voicingTime < 0 and run >= 3 and runStart >= burstTime",
            "                voicingTime = runStart",
            "                f0Onset = runStartF0",
            "            endif",
            "            if candTime >= 0",
            "                if secondVoicingTime < 0",
            "                    secondVoicingTime = candTime",
            "                endif",
            "            elsif lastVoiced >= 0 and voicingTime >= 0 and ft - lastVoiced >= 0.02",
            "                candTime = ft",
            "            endif",
            "            lastVoiced = ft",
            "        else",
            "            run = 0",
            "            candTime = -1",
            "        endif",
            "    endif",
            "endfor",
            "Remove",
            "if firstVoicedTime >= 0 and firstVoicedTime <= tmin + 0.01",
            _write_result(
                context,
                [
                    quote("自动检测失败：范围起点附近（"),
                    "fixed$ (firstVoicedTime, 3)",
                    quote(" 秒）已经是浊音（自相关基频连续成立），"
                          "说明爆破不在这个范围里。请把 from 提前到闭音段"
                          "（爆破之前），或直接给出 burst 和 voicing 两个时刻。"),
                    "rangeNote$",
                ],
            ),
            "elsif burstTime < 0",
            _write_result(
                context,
                [
                    quote("自动检测失败："),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(f" 秒里高频带和全频段能量都没有 {burst_db:.0f} dB 以上的陡升，"
                          "找不到爆破瞬态。请把范围收紧到爆破附近，"
                          "或直接给出 burst 和 voicing 两个时刻。"),
                    "rangeNote$",
                ],
            ),
            "elsif voicingTime < 0",
            _write_result(
                context,
                [
                    quote("自动检测失败：爆破点 "),
                    "fixed$ (burstTime, 3)",
                    quote(" 秒之后没有连续 3 帧以上的浊音（自相关基频），"
                          "范围里可能没有浊音段。"),
                    "rangeNote$",
                ],
            ),
            "else",
            # 谐噪比只当佐证，就只算起点之后 50 ms 那一小段：整段声音做交叉相关
            # 在 10 秒的声音上要 0.3 秒，用户能感觉出卡。
            "    hnrEnd = voicingTime + 0.05",
            "    if hnrEnd > duration",
            "        hnrEnd = duration",
            "    endif",
            "    hnrMax = 0",
            "    if hnrEnd - voicingTime >= 0.03",
            f"        selectObject: {row.id}",
            '        Extract part: voicingTime, hnrEnd, "rectangular", 1, "no"',
            '        ex = selected ("Sound")',
            f'        To Harmonicity (cc): 0.002, {pitch_floor:.6f}, 0.1, 1.0',
            "        hn = Get number of frames",
            "        if hn >= 1",
            "            for i from 1 to hn",
            "                hv = Get value in frame: i",
            "                if hv > hnrMax",
            "                    hnrMax = hv",
            "                endif",
            "            endfor",
            "        endif",
            "        Remove",
            "        selectObject: ex",
            "        Remove",
            "    endif",
            "    vot = voicingTime - burstTime",
            '    caution$ = ""',
            "    if vot < 0.005",
            '        caution$ = "（不到 5 毫秒：可能范围起点已在浊音里，'
            '也可能是不送气塞音，请核对）"',
            "    endif",
            '    secondNote$ = ""',
            "    if secondVoicingTime >= 0",
            '        secondNote$ = "；范围偏大：后面还有第 2 段浊音从 " + '
            'fixed$ (secondVoicingTime, 3) + " 秒开始，本次只报了第一个候选，建议收紧范围"',
            "    endif",
            _write_result(
                context,
                [
                    quote("VOT 估计值 = "),
                    "fixed$ (vot, 4)",
                    quote(" 秒（"),
                    "fixed$ (vot * 1000, 1)",
                    quote(" 毫秒）：爆破 "),
                    "fixed$ (burstTime, 3)",
                    quote(" 秒（占时长 "),
                    "fixed$ (burstTime / duration * 100, 0)",
                    quote("%，"),
                    "burstBand$",
                    quote(" 能量升 "),
                    "fixed$ (burstRise, 1)",
                    quote(" dB）→ 浊音起始 "),
                    "fixed$ (voicingTime, 3)",
                    quote(" 秒（占时长 "),
                    "fixed$ (voicingTime / duration * 100, 0)",
                    quote("%，自相关基频 "),
                    "fixed$ (f0Onset, 1)",
                    quote(" Hz，之后 50 毫秒内谐噪比最高 "),
                    "fixed$ (hnrMax, 1)",
                    quote(" dB）；范围 "),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒（占时长 "),
                    "fixed$ (tmin / duration * 100, 0)",
                    quote("–"),
                    "fixed$ (tmax / duration * 100, 0)",
                    quote("%）；爆破与浊音起始分开估计，请对着语图核对"),
                    "caution$",
                    "secondNote$",
                    "rangeNote$",
                ],
            ),
            "endif",
            "endif",  # valid search range
        ]
    )
    return _assemble(lines, context)


def _build_vot(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """给了爆破/浊音两个时刻就相减，否则在大概范围里自动估计。

    ``burst`` / ``voicing`` 是可选字段，但模型（尤其走原生 tool calling 之后）会把
    可选字段"填满"，给两个 ``0``。两条都是 0 的 VOT 本来就没有意义，所以这里把它
    当成"没给"，继续走自动估计——比拿它报「浊音起始必须晚于爆破时刻」更贴近用户
    的意图（实测「提取这段语音的 vot」就是这么被卡住的）。
    """

    burst = _vot_time(arguments, "burst")
    voicing = _vot_time(arguments, "voicing", alias="onset")
    if burst == 0.0 and voicing == 0.0:
        burst = voicing = None
    if burst is not None and voicing is not None:
        return _build_vot_explicit(arguments, context)
    if burst is not None or voicing is not None:
        raise ToolError(
            "VOT 需要同时给出 burst（爆破）和 voicing（浊音起始）两个时刻；"
            "只想自动估计的话两个都不填，改用 from/to 给出大概范围。"
        )
    return _build_vot_auto(arguments, context)


def _build_spectral_emphasis(
    arguments: Mapping[str, Any], context: ToolContext
) -> str:
    """谱强调（spectral emphasis）。

    照抄 ``plugin_SpectralEmphasis/spectralEmphasis.praat``：
    谱强调 = 原声音强度 − 低通滤波后的强度，低通上限 = 该段平均基频 × multiplier。
    用于嗓音质量分析，见 Traunmüller & Eriksson (2000)。
    """

    row = context.resolve_by_class(
        arguments.get("object"), ANALYSIS_SOUND_CLASSES, "谱强调需要 Sound 对象"
    )
    multiplier = _number(arguments, "multiplier", 1.5, 0.1, 10.0)
    smoothing = _number(arguments, "smoothing", 20.0, 0.1, 5000.0)
    floor, ceiling = _pitch_band(arguments)
    lines = [
        f"selectObject: {row.id}",
        "duration = Get total duration",
        "intensity = Get intensity (dB)",
        f"To Pitch: 0, {floor:.6f}, {ceiling:.6f}",
        'meanPitch = Get mean: 0, 0, "Hertz"',
        "Remove",
        f"selectObject: {row.id}",
        "if meanPitch = undefined",
        _write_result(
            context,
            [quote("谱强调无法计算：这段声音里没有可用的基频（全是清音或无声段）。")],
        ),
        "else",
        f"cutoff = meanPitch * {multiplier:.6f}",
        f"Filter (pass Hann band): 0, cutoff, {smoothing:.6f}",
        # Filter 会新建 "<名字>_band" 的 Sound 并选中它。显式记录该对象并按 id 删除，
        # 不依赖当前选择状态，也绝不删除原对象。
        'filteredId = selected ("Sound")',
        "filtered = Get intensity (dB)",
        f"if filteredId <> 0 and filteredId <> {row.id}",
        "    selectObject: filteredId",
        "    Remove",
        "endif",
        f"selectObject: {row.id}",
        "emphasis = intensity - filtered",
        _write_result(
            context,
            [
                quote("谱强调（Spectral emphasis）= "),
                "fixed$ (emphasis, 2)",
                quote(" dB：低通上限 "),
                "fixed$ (cutoff, 1)",
                quote(" Hz（该段平均基频 "),
                "fixed$ (meanPitch, 1)",
                quote(f" Hz × {multiplier:g}），平滑 {smoothing:g} Hz"),
            ],
        ),
        "endif",
    ]
    return _assemble(lines, context)


def _build_hl_ratio(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """高/低频段能量比（H/L）。

    照抄 ``plugin_HL/HL.praat``：``Get band energy`` 两段的能量相除
    （默认 0–4000 Hz 与 4000–8000 Hz）。输入是 Sound 时先做 ``To Spectrum``。
    """

    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Spectrum"}),
        "H/L 需要 Sound 或 Spectrum 对象",
    )
    low_from = _number(arguments, "low_from", 0.0, 0.0, 96000.0)
    low_to = _number(arguments, "low_to", 4000.0, 1.0, 96000.0)
    high_from = _number(arguments, "high_from", 4000.0, 1.0, 96000.0)
    high_to = _number(arguments, "high_to", 8000.0, 2.0, 96000.0)
    if low_from >= low_to:
        raise ToolError("低频段的起点必须小于终点（默认 0–4000 Hz）。")
    if high_from >= high_to:
        raise ToolError("高频段的起点必须小于终点（默认 4000–8000 Hz）。")
    if low_to > high_from:
        raise ToolError("低频段上限不能高于高频段下限（默认 0–4000 与 4000–8000 Hz）。")
    temporary = row.class_name != "Spectrum"
    lines = [f"selectObject: {row.id}"]
    if temporary:
        lines.extend(['To Spectrum: "yes"', f"Rename: {quote(TEMPORARY_OBJECT_NAME)}"])
    lines.extend(
        [
            f"lowBand = Get band energy: {low_from:.6f}, {low_to:.6f}",
            f"highBand = Get band energy: {high_from:.6f}, {high_to:.6f}",
            "if lowBand = 0",
            _write_result(
                context,
                [quote("H/L 无法计算：低频段能量为 0（可能是静音，或者低频段选得太窄）。")],
            ),
            "else",
            "hl = highBand / lowBand",
            _write_result(
                context,
                [
                    quote("H/L = "),
                    "fixed$ (hl, 4)",
                    quote(f"（高频 {high_from:g}–{high_to:g} Hz 能量 ÷ 低频 "),
                    quote(f"{low_from:g}–{low_to:g} Hz 能量）"),
                ],
            ),
            "endif",
        ]
    )
    if temporary:
        lines.append("Remove")
    lines.append(f"selectObject: {row.id}")
    return _assemble(lines, context)


def _build_hammarberg_index(
    arguments: Mapping[str, Any], context: ToolContext
) -> str:
    """Hammarberg 指数。

    照抄 ``plugin_HammarbergIndex/hammarbergIndex.praat``：
    0–2000 Hz 的 LTAS 最大电平 减去 2000–5000 Hz 的 LTAS 最大电平（dB）。
    见 Hammarberg et al. (1980)。输入是 Sound 时先做 ``To Ltas``。
    """

    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Ltas"}),
        "Hammarberg 指数需要 Sound 或 Ltas 对象",
    )
    low_from = _number(arguments, "low_from", 0.0, 0.0, 96000.0)
    low_to = _number(arguments, "low_to", 2000.0, 1.0, 96000.0)
    high_from = _number(arguments, "high_from", 2000.0, 1.0, 96000.0)
    high_to = _number(arguments, "high_to", 5000.0, 2.0, 96000.0)
    bandwidth = _number(arguments, "bandwidth", 100.0, 1.0, 1000.0)
    if low_from >= low_to:
        raise ToolError("低频段的起点必须小于终点（默认 0–2000 Hz）。")
    if high_from >= high_to:
        raise ToolError("高频段的起点必须小于终点（默认 2000–5000 Hz）。")
    temporary = row.class_name != "Ltas"
    lines = [f"selectObject: {row.id}"]
    if temporary:
        lines.extend([f"To Ltas: {bandwidth:.6f}", f"Rename: {quote(TEMPORARY_OBJECT_NAME)}"])
    lines.extend(
        [
            f'lowBand = Get maximum: {low_from:.6f}, {low_to:.6f}, "Parabolic"',
            f'highBand = Get maximum: {high_from:.6f}, {high_to:.6f}, "Parabolic"',
            "hammarberg = lowBand - highBand",
            _write_result(
                context,
                [
                    quote("Hammarberg 指数 = "),
                    "fixed$ (hammarberg, 2)",
                    quote(" dB（0–2000 Hz 最大电平 "),
                    "fixed$ (lowBand, 2)",
                    quote(" dB − 2000–5000 Hz 最大电平 "),
                    "fixed$ (highBand, 2)",
                    quote(" dB）"),
                ],
            ),
        ]
    )
    if temporary:
        lines.append("Remove")
    lines.append(f"selectObject: {row.id}")
    return _assemble(lines, context)


def _build_pitch_peak_latency(
    arguments: Mapping[str, Any], context: ToolContext
) -> str:
    """基频峰值延迟。

    照抄 ``plugin_PitchPeakLatency/pitchPeakLatency.praat``：
    （峰值时刻 − 区间起点）÷ 区间时长，0.5 表示峰值正好在区间中间。
    """

    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Pitch"}),
        "基频峰值延迟需要 Sound 或 Pitch 对象",
    )
    temporary = row.class_name != "Pitch"
    floor, ceiling = _pitch_band(arguments)
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    lines.extend(_range_lines(arguments, row))
    if temporary:
        lines.extend(
            [
                f"To Pitch: 0, {floor:.6f}, {ceiling:.6f}",
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    lines.extend(['nFrames = Get number of frames'])
    # 峰值时刻复用基频候选过滤，过滤规则的限制同 _pitch_extreme_lines。
    lines.extend(_pitch_extreme_lines())
    lines.extend(
        [
            "peakTime = maxtime",
            "if peakTime = undefined",
            _write_result(
                context,
                [
                    quote("基频峰值延迟无法计算："),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒这一段里没有可用的基频（全是清音或无声段）。"),
                ],
            ),
            "else",
            "latency = (peakTime - tmin) / (tmax - tmin)",
            _write_result(
                context,
                [
                    quote("基频峰值延迟 = "),
                    "fixed$ (latency, 3)",
                    quote("（峰值 "),
                    "fixed$ (peakTime, 3)",
                    quote(" 秒出现在 "),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒这一段里；0.5 = 正好在中间）"),
                    "rangeNote$",
                ],
            ),
            "endif",
        ]
    )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return _assemble(lines, context)


def _build_peak_to_average_ratio(
    arguments: Mapping[str, Any], context: ToolContext
) -> str:
    """峰值/平均值比（peak-to-average ratio）。

    出处 Hillenbrand et al. (1994)，参考 ``plugin_PA/peak_to_average.praat``。

    **和原脚本的一处差别**：原脚本用 ``Get mean``（有符号均值）当分母，对一段语音来说
    那个值接近 0，比值会跑到几万、同一个音两次能差一个数量级。这里用同一套「幅度
    统计」里稳定的 **RMS（有效值）** 当分母，结果落在 1–5 这种可比较的范围内，
    同时把峰值和有效值都写进结果，便于核对。
    """

    row = context.resolve_by_class(
        arguments.get("object"), ANALYSIS_SOUND_CLASSES, "峰值/平均值比需要 Sound 对象"
    )
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    lines.extend(_range_lines(arguments, row))
    lines.extend(
        [
            'peak = Get maximum: tmin, tmax, "Sinc70"',
            "rms = Get root-mean-square: tmin, tmax",
            "if rms = 0",
            _write_result(
                context,
                [
                    quote("峰值/平均值比无法计算："),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒这一段是静音（有效值为 0）。"),
                ],
            ),
            "else",
            "ratio = peak / rms",
            _write_result(
                context,
                [
                    quote("峰值/有效值 = "),
                    "fixed$ (ratio, 2)",
                    quote("（峰值 "),
                    "fixed$ (peak, 4)",
                    quote(" Pa ÷ 有效值 "),
                    "fixed$ (rms, 4)",
                    quote(" Pa，"),
                    "fixed$ (tmin, 3)",
                    quote("–"),
                    "fixed$ (tmax, 3)",
                    quote(" 秒）"),
                    "rangeNote$",
                ],
            ),
            "endif",
        ]
    )
    return _assemble(lines, context)


def _build_intensity_slope(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """强度曲线的平均斜率（dB/s）。

    照抄 ``plugin_IntensitySlope/meanIntensitySlope.praat``：

    - ``local``：相邻两点绝对差 ÷ 时间步长，再取平均，
      即 mean{|I[t+1]−I[t]| / dt}；
    - ``global``：区间首尾之差 ÷ 区间时长，即 {I[tmax]−I[tmin]} / (tmax−tmin)。

    范围默认整段；给了 from/to（或编辑器圈选）时，先按这个范围把声音截出来再分析。
    """

    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Intensity"}),
        "强度斜率需要 Sound 或 Intensity 对象",
    )
    method = str(arguments.get("method", "local") or "local").strip().casefold()
    if method not in {"local", "global", "局部", "整体"}:
        raise ToolError("method 只能是 local（局部）或 global（整体）。")
    local = method in {"local", "局部"}
    pitch_floor, _ = _pitch_band(arguments, floor_default=100.0, with_ceiling=False)
    time_step = _number(arguments, "time_step", 0.001, 0.0001, 1.0)
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    extracted = ""
    temporary = row.class_name == "Sound"
    if temporary:
        # 整段分析时不会截片段，所以先给 extractedId 一个初值（0 = 没截）。
        lines.append("extractedId = 0")
        lines.extend(_range_lines(arguments, row))
        # 只分析一段时先把这一段截出来（Intensity 对象本身没有「取片段」的命令）。
        lines.extend(
            [
                "if tmin > 0 or tmax < duration",
                "extractedId = Extract part: tmin, tmax, \"rectangular\", 1, \"no\"",
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
                "tmin = 0",
                "tmax = Get total duration",
                "endif",
            ]
        )
        extracted = "extractedId"
    else:
        # Intensity / Pitch 这类对象没有「取片段」命令：只能整段算，别假装能按范围算。
        if any(value not in (None, "") for value in _range_pair(arguments)):
            raise ToolError(
                "对已经提取出来的 Intensity 对象只能整段算斜率；"
                "要按时间范围算，请直接对声音对象用这个工具。"
            )
        lines.extend(["tmin = 0", "tmax = duration", 'rangeNote$ = ""'])
    lines.append(
        f'intensityId = To Intensity: {pitch_floor:.6f}, {time_step:.6f}, "yes"'
        if temporary else f"intensityId = {row.id}"
    )
    lines.extend(
        [
            "Down to Matrix",
            'matrixId = selected ("Matrix")',
            "colsNum = Get number of columns",
            "timeStep = Get column distance",
            # 静音/空录音上算出来的 0 dB/s 没有实际意义，先说清楚再给数字。
            'flatNote$ = ""',
            "if colsNum > 1",
            "    maxEl = Get maximum",
            "    minEl = Get minimum",
            "    if maxEl - minEl < 0.5",
            '        flatNote$ = "——整段几乎没有强度变化；如果是静音或空录音，这个数值没有实际意义。"',
            "    endif",
            "endif",
        ]
    )
    if local:
        result_lines = [
            'Formula: "abs(self[col+1]-self[col])/dx"',
            "matSum = Get sum",
            "lastEl = Get value in cell: 1, colsNum",
            "slope = (matSum - lastEl) / (colsNum - 1)",
            _write_result(
                context,
                [
                    quote("强度局部斜率（相邻点绝对差 ÷ 时间步长，取平均）= "),
                    "fixed$ (slope, 2)",
                    quote(" dB/s（"),
                    "fixed$ (colsNum, 0)",
                    quote(" 个点，步长 "),
                    "fixed$ (timeStep, 6)",
                    quote(" 秒）"),
                    "flatNote$",
                    "rangeNote$",
                ],
            ),
        ]
    else:
        result_lines = [
            "firstTime = Get lowest x",
            "lastTime = Get highest x",
            "firstEl = Get value in cell: 1, 1",
            "lastEl = Get value in cell: 1, colsNum",
            "slope = (lastEl - firstEl) / (lastTime - firstTime)",
            _write_result(
                context,
                [
                    quote("强度整体斜率（首尾差 ÷ 时长）= "),
                    "fixed$ (slope, 2)",
                    quote(" dB/s（"),
                    "fixed$ (firstEl, 2)",
                    quote(" dB → "),
                    "fixed$ (lastEl, 2)",
                    quote(" dB）"),
                    "flatNote$",
                    "rangeNote$",
                ],
            ),
        ]
    lines.extend(result_lines)
    # 收尾：把中间对象删掉，恢复用户原来的选中对象。
    lines.append("selectObject: matrixId")
    lines.append("Remove")
    if temporary:
        lines.append("selectObject: intensityId")
        lines.append("Remove")
    if extracted:
        # extractedId = 0 表示这次没截片段（整段分析），别去删一个不存在的对象。
        lines.append(f"if {extracted} <> 0")
        lines.append(f"    selectObject: {extracted}")
        lines.append("    Remove")
        lines.append("endif")
    lines.append(f"selectObject: {row.id}")
    return _assemble(lines, context)


#: 走表时 ``arguments`` 里这些键是「怎么查」，不是分析设置。
_MEASURE_RESERVED_KEYS = frozenset(
    {"parameter", "parameters", "object", "from", "to", "start", "end", "unit"}
)


#: 已经分析好的对象能直接顶上对应派生对象时用（选中 Pitch 就别再 To Pitch 一次）。
_MEASURE_CLASS_DERIVATIONS: dict[str, str] = {
    "Pitch": "pitch",
    "Intensity": "intensity",
    "Formant": "formant",
    "Spectrum": "spectrum",
    "Ltas": "ltas",
    "Harmonicity": "harmonicity",
    "PointProcess": "pointProcess",
    "PowerCepstrogram": "powerCepstrogram",
}


def measure_parameter_help() -> str:
    """``measure`` 能测的参数名 + 中文说明（工具 JSON Schema 和 catalog 共用）。"""

    return "、".join(
        f"{entry.parameter}（{entry.label}）" for entry in measures.load_table().entries
    )


def measure_signature() -> str:
    """``measure`` 的参数说明（设置项也由表生成，schema 才不会漏改）。"""

    keys = "、".join(measures.load_table().settings)
    return (
        'parameter（参数名，可写多个如 "f1,f2"）、from、to（秒，默认整个对象或编辑器圈选）'
        "、unit（hertz/Bark）、object（可选）"
        f"、{keys}"
    )


#: ``parameter`` 里分隔多个参数名的字符（半角/全角逗号、顿号、分号）。
MEASURE_SEPARATORS = ",，、;；"


def _measure_pattern() -> str:
    """从参数表生成 parameter 的 pattern，支持单个参数名及由 MEASURE_SEPARATORS 分隔的列表，与逐项校验保持同源。"""

    names = "|".join(re.escape(name) for name in measures.load_table().parameters())
    return rf"^\s*(?:{names})(?:\s*[{MEASURE_SEPARATORS}]\s*(?:{names}))*\s*$"


def _measure_schema() -> dict[str, Any]:
    table = measures.load_table()
    properties: dict[str, Any] = {
        "parameter": {
            "type": "string",
            "pattern": _measure_pattern(),
            "description": "要测哪一个参数（多个用逗号隔开，如 \"f1,f2\"）：" + measure_parameter_help(),
        },
        "from": _seconds_arg("起点（秒），不填就是整个对象或编辑器圈选"),
        "to": _seconds_arg("终点（秒），不填就是整个对象或编辑器圈选"),
        "unit": _unit_arg(),
        "object": _object_arg(),
    }
    for key, setting in table.settings.items():
        description = f"{setting.note}（默认 {setting.value}）"
        if setting.is_integer:
            properties[key] = _integer_arg(description)
        else:
            properties[key] = _number_arg(description)
    return {"type": "object", "properties": properties, "required": ["parameter"]}


def _measure_names(arguments: Mapping[str, Any]) -> list[str]:
    """``parameter`` 可以是名字、``"f1,f2"`` 或列表（JSON 规划那条路会传列表）。"""

    raw = arguments.get("parameter", arguments.get("parameters"))
    if raw is None or raw == "":
        raise ToolError("measure 要用 parameter 指定参数名。")
    if isinstance(raw, (list, tuple, set)):
        pieces = [str(item) for item in raw]
    else:
        pieces = re.split(rf"[{MEASURE_SEPARATORS}\s]+", str(raw))
    names = [piece.strip() for piece in pieces if piece.strip()]
    if not names:
        raise ToolError("measure 要用 parameter 指定参数名。")
    return names


def _measure_entries(arguments: Mapping[str, Any]) -> list[measures.Entry]:
    table = measures.load_table()
    raw = arguments.get("parameter", arguments.get("parameters"))
    entries: list[measures.Entry] = []
    # 整串先作为一个参数尝试：带空格的常见写法（"jitter local"、"第 1 共振峰"）
    # 不会被拆成多个未知参数。
    if isinstance(raw, str) and raw.strip():
        try:
            return [table.find(raw)]
        except KeyError:
            pass
    for name in _measure_names(arguments):
        try:
            entries.append(table.find(name))
        except KeyError as error:
            known = "、".join(table.parameters())
            raise ToolError(
                f"measure 不认识参数「{name}」。可用参数有：{known}。"
            ) from error
    return entries


def _measure_settings(
    arguments: Mapping[str, Any], table: measures.Table, entries: Sequence[measures.Entry] = ()
) -> dict[str, int | float]:
    """按表里的 ``@@ settings`` 取值：默认值在表里，模型可以用同名参数覆盖。"""

    values: dict[str, int | float] = {}
    for key, setting in table.settings.items():
        if setting.is_integer:
            values[key] = _integer(
                arguments,
                key,
                int(float(setting.value)),
                int(float(setting.minimum)),
                int(float(setting.maximum)),
            )
        else:
            values[key] = _number(
                arguments,
                key,
                float(setting.value),
                float(setting.minimum),
                float(setting.maximum),
            )
    # 上下限写反的话 Praat 会弹一句英文错误框（还会卡住后面的消息），这里先拦下。
    floor = values.get("pitch_floor")
    ceiling = values.get("pitch_ceiling")
    if isinstance(floor, (int, float)) and isinstance(ceiling, (int, float)):
        if floor >= ceiling:
            raise ToolError("pitch_floor（基频下限）必须小于 pitch_ceiling（基频上限）。")
    # 用户给 pitch_floor 时，谐噪比派生使用独立的 harmonicity_floor 占位符。
    # 只有本次确实请求 hnr 时才折算，避免影响纯基频测量的参数范围。
    needs_harmonicity = any("harmonicity" in measures.source_keys(entry.source) for entry in entries)
    if (needs_harmonicity and arguments.get("pitch_floor") not in (None, "")
            and arguments.get("harmonicity_floor") in (None, "")):
        setting = table.settings["harmonicity_floor"]
        candidate = float(values["pitch_floor"])
        if not float(setting.minimum) <= candidate <= float(setting.maximum):
            raise ToolError(
                f"谐噪比分析的基频下限（harmonicity_floor）必须在 {setting.minimum} 与 "
                f"{setting.maximum} 之间；pitch_floor={candidate:g} 超出这个范围，"
                "请直接指定 harmonicity_floor。")
        values["harmonicity_floor"] = candidate
    return values


def _measure_expand(
    text: str,
    settings: Mapping[str, int | float],
    *,
    unit_literal: str,
    unit_text: str,
) -> str:
    """把 ``{pitch_floor}`` / ``{unit}`` / ``{unit_text}`` 换成实际值。"""

    expanded = text
    for key, value in settings.items():
        rendered = str(value) if isinstance(value, int) else f"{value:.6f}"
        expanded = expanded.replace("{" + key + "}", rendered)
    expanded = expanded.replace("{unit}", unit_literal)
    expanded = expanded.replace("{unit_text}", unit_text)
    return expanded


def _measure_plan(
    table: measures.Table,
    entries: Sequence[measures.Entry],
    variables: Mapping[str, str],
) -> list[str]:
    """按表的顺序算出要建哪些派生对象（只用到的才建，依赖排在前面）。"""

    wanted: set[str] = set()
    pending: list[str] = []
    for entry in entries:
        for key in measures.source_keys(entry.source):
            if key != "sound" and key not in variables and key not in wanted:
                wanted.add(key)
                pending.append(key)
    while pending:
        key = pending.pop()
        derivation = table.derivations.get(key)
        if derivation is None:
            raise ToolError(f"measure 的表里没有「{key}」的派生规则。")
        source = derivation.source.strip()
        if source and source != "Sound" and source not in variables and source not in wanted:
            wanted.add(source)
            pending.append(source)
    return [key for key in table.derivations if key in wanted]


def _measure_select_lines(keys: Sequence[str], variables: Mapping[str, str]) -> list[str]:
    """一条查询要选中哪些对象：第一个 ``selectObject``，其余 ``plusObject``。"""

    lines: list[str] = []
    for index, key in enumerate(keys):
        target = variables.get(key)
        if target is None:
            raise ToolError(
                f"这个参数要对「{key}」算，但当前对象只能直接给「"
                + "、".join(sorted(variables))
                + "」；请在 Praat 里选中声音对象再试。"
            )
        lines.append(("selectObject: " if index == 0 else "plusObject: ") + target)
    return lines


def _build_measure(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """表驱动的测量：一行参数 = 一条 Praat 查询（表在 ai/praat_ai/measures.tsv）。"""

    from .schema import TOOL_MAP  # 延迟导入：打断注册表 <-> builder 的环
    table = measures.load_table()
    entries = _measure_entries(arguments)
    delegated = [entry for entry in entries if entry.kind == "dedicated"]
    if delegated:
        if len(entries) > 1:
            names = "、".join(f"{e.parameter}（改用 {e.tool}）" for e in delegated)
            raise ToolError(
                f"{names} 要好几步才能算出来，得单独调用各自的专用工具，"
                "一次 measure 里别和别的参数混在一起。"
            )
        entry = delegated[0]
        tool = TOOL_MAP.get(entry.tool)
        if tool is None:
            raise ToolError(
                f"measure 的表把 {entry.parameter} 指向了不存在的工具 {entry.tool}。"
            )
        merged = {
            key: value
            for key, value in arguments.items()
            if key not in {"parameter", "parameters"}
        }
        merged.update(json.loads(entry.arguments or "{}"))
        return tool.build(merged, context)

    queries = [entry for entry in entries if entry.kind == "query"]
    editor_only = [entry for entry in entries if entry.kind == "editor"]
    required_sources = {
        key for entry in queries for key in measures.source_keys(entry.source)
    }
    supported_classes = {"Sound"}
    supported_classes.update(
        class_name for class_name, alias in _MEASURE_CLASS_DERIVATIONS.items()
        if required_sources and required_sources <= {alias}
    )
    row = context.resolve_by_class(
        arguments.get("object"), frozenset(supported_classes),
        "这些测量需要 Sound 声音对象，或对应的已有分析对象",
    )
    variables: dict[str, str] = {}
    if row.class_name == "Sound":
        variables["sound"] = str(row.id)
    else:
        alias = _MEASURE_CLASS_DERIVATIONS.get(row.class_name)
        if alias is None:
            raise ToolError(
                f"measure 需要声音对象（对象 {row.id} 是 {row.class_name}）："
                "声学测量都是对声音做的，或者把已经分析好的 Pitch/Intensity 等对象选上。"
            )
        variables[alias] = str(row.id)

    settings = _measure_settings(arguments, table, entries)
    unit_literal = _unit_literal(arguments, "hertz")
    unit_text = _unit_display(arguments, "hertz").strip()
    created = _measure_plan(table, entries, variables)

    uses_range = any(
        "tmin" in entry.command or "tmax" in entry.command for entry in queries
    )
    lines = [f"selectObject: {row.id}"]
    if uses_range:
        lines.append("duration = Get total duration")
        lines.extend(_range_lines(arguments, row))
    for key in created:
        derivation = table.derivations[key]
        # Praat 的 To X 生成的新对象会顶掉当前选中：每建一个派生对象之前都要把
        # 它该从哪个对象来重新选一遍，否则第二个派生对象就是对中间对象做了。
        source = derivation.source.strip() or "Sound"
        lines.extend(
            _measure_select_lines(
                ["sound" if source == "Sound" else source], variables
            )
        )
        command = _measure_expand(
            derivation.command,
            settings,
            unit_literal=unit_literal,
            unit_text=unit_text,
        )
        lines.append(f"{key}Id = {command}")
        variables[key] = f"{key}Id"

    for entry in queries:
        lines.extend(_measure_select_lines(measures.source_keys(entry.source), variables))
        command = _measure_expand(
            entry.command, settings, unit_literal=unit_literal, unit_text=unit_text
        )
        statements = [piece.strip() for piece in command.split(";") if piece.strip()]
        if not any("=" in statement for statement in statements):
            statements[0] = f"value = {statements[0]}"
        lines.extend(statements)
        unit = _measure_expand(
            entry.unit, settings, unit_literal=unit_literal, unit_text=unit_text
        )
        decimals = int(entry.decimals)
        entry_range = "tmin" in entry.command or "tmax" in entry.command
        if entry_range:
            prefix = [
                quote(f"{entry.label}（"),
                "fixed$ (tmin, 3)",
                quote("–"),
                "fixed$ (tmax, 3)",
                quote(" 秒）= "),
            ]
            missing = [
                quote(f"{entry.label}（"),
                "fixed$ (tmin, 3)",
                quote("–"),
                "fixed$ (tmax, 3)",
                quote(" 秒）无法计算：这一段里没有可用的数据"),
            ]
        else:
            prefix = [quote(f"{entry.label} = ")]
            missing = [quote(f"{entry.label} 无法计算：这个对象里没有可用的数据")]
        measured = prefix + [f"fixed$ (value, {decimals})"]
        if unit:
            measured.append(quote(f" {unit}"))
        lines.append("if value = undefined")
        lines.extend(
            [_write_result(context, missing + (["rangeNote$"] if entry_range else []))]
        )
        lines.append("else")
        lines.extend(
            [
                _write_result(
                    context,
                    measured + (["rangeNote$"] if entry_range else []),
                )
            ]
        )
        lines.append("endif")

    # 编辑器专属参数在对象列表上不可用：执行前拒绝并给出替代做法，
    # 不能只写一行说明却记作成功测量。
    if editor_only:
        labels = "、".join(f"{entry.parameter}（{entry.label}）" for entry in editor_only)
        raise ToolError(
            f"{labels}只能在 Praat 的编辑器里查（Voice report），对象列表没有对应命令；"
            "请在 Praat 里打开声音 → View & Edit → Voice report，或改用可计算的参数"
            "（例如 hnr、local_jitter、cpps）。")

    for key in reversed(created):
        lines.append(f"selectObject: {key}Id")
        lines.append("Remove")
    lines.append(f"selectObject: {row.id}")
    return _assemble(lines, context)
