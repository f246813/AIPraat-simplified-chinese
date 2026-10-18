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

from ._common import (ANALYSIS_SOUND_CLASSES, RANGE_END_KEYS, RANGE_START_KEYS, SOUND_CLASSES, ToolContext, ToolError, _assemble, _id_fragment, _insert_boundary_block, _integer, _named_target, _new_name, _normalize_text, _number, _range_argument, _range_note, _selection_range, _textgrid_row, _tier_number, _write_result, quote)


def _build_spectrogram(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """从 Sound 生成频谱图对象（「做成频谱图」用这个）。"""

    row = context.resolve_by_class(
        arguments.get("object"), ANALYSIS_SOUND_CLASSES, "频谱图分析需要 Sound 对象"
    )
    window_length = _number(arguments, "window_length", 0.005, 0.0001, 1.0)
    maximum_frequency = _number(arguments, "max_frequency", 5000.0, 100.0, 96000.0)
    time_step = _number(arguments, "time_step", 0.002, 0.0001, 1.0)
    frequency_step = _number(arguments, "frequency_step", 20.0, 1.0, 1000.0)
    name = _new_name(arguments, "频谱图")
    # 模型有时会把源对象的名字当新名字传进来，那样对象列表里会出现两个同名对象。
    if _normalize_text(name) in row.name_variants():
        name = f"{name} 频谱图"
    lines = [
        f"selectObject: {row.id}",
        f"newId = To Spectrogram: {window_length:.6f}, {maximum_frequency:.6f}, "
        f"{time_step:.6f}, {frequency_step:.6f}, \"Gaussian\"",
        f"Rename: {quote(name)}",
        "frames = Get number of frames",
        _write_result(
            context,
            [
                quote("已生成频谱图："),
                quote(name),
                _id_fragment(),
                quote(f"，分析窗口 {window_length * 1000:.1f} ms，最高频率 "),
                f"fixed$ ({maximum_frequency:.6f}, 0)",
                quote(" Hz，共 "),
                "fixed$ (frames, 0)",
                quote(" 帧（可用 Praat 的「绘制」菜单画到 Picture 窗口）"),
            ],
        ),
    ]
    return _assemble(lines, context)


def _build_textgrid_info(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = _textgrid_row(arguments, context)
    maximum = _integer(arguments, "maximum_intervals", 12, 1, 100)
    lines = [
        f"selectObject: {row.id}",
        "duration = Get total duration",
        "tiers = Get number of tiers",
        _write_result(
            context,
            [
                quote(f"{row.name}：时长 "),
                "fixed$ (duration, 3)",
                quote(" 秒，共 "),
                "fixed$ (tiers, 0)",
                quote(" 层"),
            ],
        ),
        "for tier from 1 to tiers",
        "    tierName$ = Get tier name: tier",
        # Praat 里不能在 if 条件里直接调用命令，必须先赋值再判断。
        "    isInterval = Is interval tier: tier",
        "    if isInterval = 1",
        "        count = Get number of intervals: tier",
        _write_result(
            context,
            [
                quote("第 "),
                "fixed$ (tier, 0)",
                quote(" 层「"),
                "tierName$",
                quote("」（区间层）："),
                "fixed$ (count, 0)",
                quote(" 个区间"),
            ],
        ),
        "        shown = 0",
        "        for part from 1 to count",
        f"            if shown < {maximum}",
        "                startTime = Get start time of interval: tier, part",
        "                endTime = Get end time of interval: tier, part",
        "                label$ = Get label of interval: tier, part",
        _write_result(
            context,
            [
                quote("    区间 "),
                "fixed$ (part, 0)",
                quote("："),
                "fixed$ (startTime, 3)",
                quote("–"),
                "fixed$ (endTime, 3)",
                quote(" 秒，标签「"),
                "label$",
                quote("」"),
            ],
        ),
        "                shown = shown + 1",
        "            endif",
        "        endfor",
        f"        if count > {maximum}",
        _write_result(
            context,
            [
                quote("    ……还有 "),
                "fixed$ (count - shown, 0)",
                quote(" 个区间未列出（可用参数 maximum_intervals 调整）"),
            ],
        ),
        "        endif",
        "    else",
        "        count = Get number of points: tier",
        _write_result(
            context,
            [
                quote("第 "),
                "fixed$ (tier, 0)",
                quote(" 层「"),
                "tierName$",
                quote("」（点层）："),
                "fixed$ (count, 0)",
                quote(" 个点"),
            ],
        ),
        "        shown = 0",
        "        for part from 1 to count",
        f"            if shown < {maximum}",
        "                pointTime = Get time of point: tier, part",
        "                label$ = Get label of point: tier, part",
        _write_result(
            context,
            [
                quote("    点 "),
                "fixed$ (part, 0)",
                quote("："),
                "fixed$ (pointTime, 3)",
                quote(" 秒，标签「"),
                "label$",
                quote("」"),
            ],
        ),
        "                shown = shown + 1",
        "            endif",
        "        endfor",
        "    endif",
        "endfor",
    ]
    return _assemble(lines, context)


def _build_textgrid_set_interval(
    arguments: Mapping[str, Any],
    context: ToolContext,
) -> str:
    row = _textgrid_row(arguments, context)
    tier = _tier_number(arguments)
    # from/to 与 start/end 作为时间参数别名，按相同优先级读取。
    start = _number(
        {"start": _range_argument(arguments, RANGE_START_KEYS)},
        "start", 0.0, 0.0, 36000.0,
    )
    raw_end = _range_argument(arguments, RANGE_END_KEYS)
    if raw_end in (None, ""):
        # 「把这一段标成 x」：结束时间没给时用编辑器里圈出来的选区。
        selection = _selection_range(arguments, row)
        if selection is None:
            raise ToolError("标注区间需要 end 参数（结束时间，单位秒）。")
        start, end = selection
        range_note = _range_note(selection)
    else:
        end = _number({"end": raw_end}, "end", 0.0, 0.0, 36000.0)
        range_note = 'rangeNote$ = ""'
    if start >= end:
        raise ToolError("开始时间必须小于结束时间。")
    label = str(
        arguments.get("label", "")
        or arguments.get("text", "")
        or ""
    ).strip()
    if not label:
        raise ToolError("标注区间需要 label 参数（要写进 TextGrid 的文字）。")
    lines = [
        f"selectObject: {row.id}",
        f"tier = {tier}",
        "duration = Get total duration",
        range_note,
        f"t1 = {start:.6f}",
        f"t2 = {end:.6f}",
        "if t1 < 0",
        "    t1 = 0",
        "endif",
        "if t2 > duration",
        "    t2 = duration",
        "endif",
        "inserted = 0",
    ]
    lines.extend(_insert_boundary_block(tier, "t1", "inserted", "1"))
    lines.extend(_insert_boundary_block(tier, "t2", "inserted", "2"))
    lines.extend(
        [
            "t2i = t2 - 0.000001",
            "if t2i < t1",
            "    t2i = t1",
            "endif",
            f"i1 = Get interval at time: {tier}, t1",
            f"i2 = Get interval at time: {tier}, t2i",
            "if i2 < i1",
            "    i2 = i1",
            "endif",
            "count = i2 - i1 + 1",
            "for part from i1 to i2",
            f"    Set interval text: {tier}, part, {quote(label)}",
            "endfor",
            _write_result(
                context,
                [
                    quote("已把第 "),
                    "fixed$ (tier, 0)",
                    quote(" 层的 "),
                    "fixed$ (t1, 3)",
                    quote("–"),
                    "fixed$ (t2, 3)",
                    quote(" 秒（"),
                    "fixed$ (count, 0)",
                    quote(" 个区间）标成「"),
                    quote(label),
                    quote("」"),
                    "rangeNote$",
                ],
            ),
        ]
    )
    return _assemble(lines, context)


def _build_textgrid_insert_boundary(
    arguments: Mapping[str, Any],
    context: ToolContext,
) -> str:
    row = _textgrid_row(arguments, context)
    tier = _tier_number(arguments)
    value = arguments.get("time", arguments.get("at", None))
    if value in (None, ""):
        raise ToolError("插入边界需要 time 参数（时间，单位秒）。")
    time_value = _number(arguments, "time", 0.0, 0.0, 36000.0)
    lines = [
        f"selectObject: {row.id}",
        f"tier = {tier}",
        "duration = Get total duration",
        f"t1 = {time_value:.6f}",
        "if t1 < 0",
        "    t1 = 0",
        "endif",
        "if t1 > duration",
        "    t1 = duration",
        "endif",
        "inserted = 0",
    ]
    lines.extend(_insert_boundary_block(tier, "t1", "inserted", "b"))
    lines.extend(
        [
            "if inserted = 1",
            _write_result(
                context,
                [
                    quote("已在第 "),
                    "fixed$ (tier, 0)",
                    quote(" 层的 "),
                    "fixed$ (t1, 3)",
                    quote(" 秒处插入边界"),
                ],
            ),
            "else",
            _write_result(
                context,
                [
                    quote("第 "),
                    "fixed$ (tier, 0)",
                    quote(" 层的 "),
                    "fixed$ (t1, 3)",
                    quote(" 秒处已经有边界（或在 TextGrid 起止点上），没有改动"),
                ],
            ),
            "endif",
        ]
    )
    return _assemble(lines, context)


def _build_select(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_object(arguments.get("object"))
    lines = [
        f"selectObject: {row.id}",
        _write_result(context, [quote("已选中："), quote(row.name)]),
    ]
    return _assemble(lines, context)


def _build_view(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_object(arguments.get("object"))
    lines = [
        f"selectObject: {row.id}",
        "View & Edit",
        _write_result(context, [quote("已打开编辑器："), quote(row.name)]),
    ]
    return _assemble(lines, context)


def _build_play(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), SOUND_CLASSES, "只有声音对象可以直接播放"
    )
    lines = [
        f"selectObject: {row.id}",
        "Play part: 0, 0" if row.class_name == "LongSound" else "Play",
        _write_result(context, [quote("已播放："), quote(row.name)]),
    ]
    return _assemble(lines, context)


def _build_rename(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = _named_target(context, arguments, "重命名")
    new_name = str(arguments.get("new_name", "") or "").strip()
    if not new_name:
        raise ToolError("重命名需要 new_name 参数。")
    lines = [
        f"selectObject: {row.id}",
        f"Rename: {quote(new_name)}",
        _write_result(context, [quote(f"{row.name} 已重命名为 {new_name}")]),
    ]
    return _assemble(lines, context)


def _build_remove(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = _named_target(context, arguments, "删除")
    lines = [
        f"selectObject: {row.id}",
        "Remove",
        _write_result(context, [quote("已删除："), quote(row.name)]),
    ]
    return _assemble(lines, context)


def _build_create_sound(arguments: Mapping[str, Any], context: ToolContext) -> str:
    name = _new_name(arguments, "新建声音")
    duration = _number(arguments, "duration", 1.0, 0.001, 3600.0)
    frequency = _number(arguments, "frequency", 220.0, 0.0, 20000.0)
    amplitude = _number(arguments, "amplitude", 0.5, 0.0, 1.0)
    if frequency <= 0.0:
        lines = [
            f"newId = Create Sound from formula: {quote(name)}, 1, 0, "
            f"{duration:.6f}, 44100, ~ 0",
            "duration = Get total duration",
        ]
        description = "静音"
    else:
        lines = [
            f"newId = Create Sound as pure tone: {quote(name)}, 1, 0, "
            f"{duration:.6f}, 44100, {frequency:.6f}, {amplitude:.6f}, 0.01, 0.01",
            "duration = Get total duration",
        ]
        description = f"{frequency:.1f} Hz 纯音"
    lines.append(
        _write_result(
            context,
            [
                quote(f"已创建{description}："),
                quote(name),
                _id_fragment(),
                quote("，时长 "),
                "fixed$ (duration, 3)",
                quote(" 秒"),
            ],
        )
    )
    return _assemble(lines, context)


def _build_concatenate(arguments: Mapping[str, Any], context: ToolContext) -> str:
    first = context.resolve_by_class(
        arguments.get("object"), ANALYSIS_SOUND_CLASSES, "拼接需要 Sound 对象"
    )
    other = arguments.get("object2", arguments.get("other", None))
    if other in (None, ""):
        raise ToolError("拼接需要 object2 参数，指出第二个声音对象。")
    second = context.resolve_by_class(other, ANALYSIS_SOUND_CLASSES, "拼接需要 Sound 对象")
    if first.id == second.id:
        raise ToolError("拼接需要两个不同的声音对象。")
    name = _new_name(arguments, "拼接结果")
    lines = [
        f"selectObject: {first.id}",
        f"plusObject: {second.id}",
        "newId = Concatenate",
        f"Rename: {quote(name)}",
        "duration = Get total duration",
        _write_result(
            context,
            [
                quote(f"已把 {first.name} 和 {second.name} 拼成："),
                quote(name),
                _id_fragment(),
                quote("，时长 "),
                "fixed$ (duration, 3)",
                quote(" 秒"),
            ],
        ),
    ]
    return _assemble(lines, context)


def _build_extract_part(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), SOUND_CLASSES, "只有声音对象可以截取片段"
    )
    start = _number(
        {"start": _range_argument(arguments, RANGE_START_KEYS)},
        "start", 0.0, 0.0, 36000.0,
    )
    raw_end = _range_argument(arguments, RANGE_END_KEYS)
    if raw_end in (None, ""):
        # 「把这段截出来」：结束时间没给时用编辑器里圈出来的选区。
        selection = _selection_range(arguments, row)
        if selection is None:
            raise ToolError("截取片段需要 end 参数（结束时间，单位秒）。")
        start, end = selection
        range_note = _range_note(selection)
    else:
        # finish 是 end 的别名，取值要按别名来，否则会被当成没给。
        end = _number({"end": raw_end}, "end", 0.0, 0.0, 36000.0)
        range_note = 'rangeNote$ = ""'
    if start >= end:
        raise ToolError("开始时间必须小于结束时间。")
    name = _new_name(arguments, "片段")
    lines = [
        f"selectObject: {row.id}",
        "duration = Get total duration",
        range_note,
        # Praat 里 start / end 不是保留字，但为了和其它模板一致，统一用 t1 / t2。
        f"t1 = {start:.6f}",
        f"t2 = {end:.6f}",
        "if t2 > duration",
        "    t2 = duration",
        "endif",
        "if t1 > t2 - 0.001",
        "    t1 = t2 - 0.001",
        "endif",
        "if t1 < 0",
        "    t1 = 0",
        "endif",
        ('newId = Extract part: t1, t2, "no"' if row.class_name == "LongSound"
         else 'newId = Extract part: t1, t2, "rectangular", 1, "no"'),
        f"Rename: {quote(name)}",
        "duration = Get total duration",
        _write_result(
            context,
            [
                quote("已截取片段："),
                quote(name),
                _id_fragment(),
                quote("，区间 "),
                "fixed$ (t1, 3)",
                quote("–"),
                "fixed$ (t2, 3)",
                quote(" 秒，共 "),
                "fixed$ (duration, 3)",
                quote(" 秒"),
                "rangeNote$",
            ],
        ),
    ]
    return _assemble(lines, context)


def _build_read_file(arguments: Mapping[str, Any], context: ToolContext) -> str:
    """将磁盘文件读入为 Praat 对象。脚本执行时通过 fileReadable() 核对文件，以支持同一请求中先创建文件再读入。"""

    raw = str(arguments.get("path", "") or arguments.get("file", "") or "").strip()
    raw = raw.strip('"')
    if not raw:
        raise ToolError("需要给出要读取的文件路径（path），例如 D:/in/a.wav。")
    # 路径只转义一次：praat_path() 已处理引号，再交给 quote() 会使 Praat
    # 解析到与用户输入不同的路径。
    path = str(Path(raw)).replace("\\", "/")
    lines = [
        f"if not fileReadable ({quote(path)})",
        f"exitScript: {quote('找不到要读取的文件：' + path + '（请检查路径是否存在）')}",
        "else",
        f"Read from file: {quote(path)}",
        "readAiCount__ = numberOfSelected ()",
        "for readAiIndex__ from 1 to readAiCount__",
        "readAiObject__ = selected (readAiIndex__)",
        "readAiName$ = selected$ (readAiIndex__)",
        _write_result(
            context,
            [
                quote("已读入文件："),
                "readAiName$",
                _id_fragment("readAiObject__"),
                quote("（"),
                quote(path),
                quote("）"),
            ],
        ),
        "endfor",
        "endif",
    ]
    return _assemble(lines, context)


def _build_save_sound(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), SOUND_CLASSES, "只有声音对象可以保存为 WAV 文件"
    )
    path = str(
        arguments.get("path", "") or arguments.get("file", "") or ""
    ).strip().strip('"')
    if not path:
        raise ToolError("保存声音需要 path 参数，例如 D:/out/a.wav。")
    if not re.match(r"^(?:[A-Za-z]:[\\/]|\\\\|/)", path):
        raise ToolError(f"保存路径必须是完整路径，收到：{path}")
    if not path.lower().endswith(".wav"):
        path += ".wav"
    # Praat 的「Save as WAV file」不会自己建文件夹，缺目录时只会报一句英文。
    # 这里先把目标文件夹建好，用户说「另存到 D:/out/a.wav」就直接能用。
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ToolError(
            f"保存路径的文件夹无法创建：{target.parent}（{error}）"
        ) from error
    lines = [
        f"selectObject: {row.id}",
        f"Save as WAV file: {quote(path)}",
        _write_result(context, [quote("已保存 WAV 文件："), quote(path)]),
    ]
    return _assemble(lines, context)


def _build_resample(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_by_class(
        arguments.get("object"), ANALYSIS_SOUND_CLASSES, "改变采样率需要 Sound 对象"
    )
    rate = _integer(arguments, "rate", 16000, 1000, 768000)
    lines = [
        f"selectObject: {row.id}",
        f"newId = Resample: {rate}, 50",
        "actual = Get sampling frequency",
        _write_result(
            context,
            [
                quote("已重采样为新对象，采样率 "),
                "fixed$ (actual, 0)",
                quote(" Hz"),
                _id_fragment(),
                quote("（原对象保持不变）"),
            ],
        ),
    ]
    return _assemble(lines, context)


def _build_duplicate(arguments: Mapping[str, Any], context: ToolContext) -> str:
    row = context.resolve_object(arguments.get("object"))
    default_name = row.name if row.name else f"对象 {row.id}"
    name = _new_name(arguments, f"{default_name} 副本")
    # 模型常把「复制一份」理解成「用同一个名字复制」，那样看不出哪个是副本。
    if _normalize_text(name) in row.name_variants():
        name = f"{name} 副本"
    lines = [
        f"selectObject: {row.id}",
        f"newId = Copy: {quote(name)}",
        _write_result(context, [quote("已复制为："), quote(name), _id_fragment()]),
    ]
    return _assemble(lines, context)
