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

from ._common import (CUSTOM_SCRIPT_TOOL, EMPTY_PARAMETERS, LocalTool, MEASURE_TOOL, RUN_SCRIPT_TOOL, Tool, ToolContext, ToolError, _assemble, _formants_arg, _integer_arg, _number_arg, _object_arg, _seconds_arg, _text_arg, _times_arg, _unit_arg)
from .scripts import (_reject_unsupported_arguments, _run_external_script, neutralize_info_commands, validate_script, validate_script_source_types)
from .measurements import (_build_duration, _build_formant_bandwidth, _build_formant_frequency, _build_formant_statistics, _build_hammarberg_index, _build_harmonicity_statistics, _build_hl_ratio, _build_intensity, _build_intensity_slope, _build_intensity_statistics, _build_measure, _build_object_info, _build_peak_to_average_ratio, _build_pitch, _build_pitch_peak_latency, _build_pitch_statistics, _build_spectral_emphasis, _build_vot, _measure_schema, measure_parameter_help, measure_signature)
from .operations import (_build_concatenate, _build_create_sound, _build_duplicate, _build_extract_part, _build_play, _build_read_file, _build_remove, _build_rename, _build_resample, _build_save_sound, _build_select, _build_spectrogram, _build_textgrid_info, _build_textgrid_insert_boundary, _build_textgrid_set_interval, _build_view)


TOOLS: tuple[Tool, ...] = (
    Tool(
        name="object_info",
        summary="查看对象的基本信息：类型、名称、时长、通道数和采样率；有编辑器圈选时额外给出圈选段占时长的百分比。",
        signature="object（可选，对象 id 或名称）",
        build=_build_object_info,
    ),
    Tool(
        name="duration",
        summary="查询对象的时长（秒）。",
        signature="object（可选，对象 id 或名称）",
        build=_build_duration,
    ),
    Tool(
        name="formant_bandwidth",
        summary="只查第 n 共振峰在某一时刻的带宽（不含频率）；对象是 Sound 时自动先做共振峰分析。",
        signature="formant（默认 2，可写 1,2 查多条）、time（秒，默认中点）、unit（hertz/Bark）、object（可选）",
        build=_build_formant_bandwidth,
    ),
    Tool(
        name="formant_frequency",
        summary="查询第 n 共振峰在某一时刻的频率，并同时给出带宽；要「F1/F2 的频率」「频率和带宽」都用这个。",
        signature="formant（默认 2，可写 1,2 查多条）、time（秒，默认中点，可写 0.25,0.75 查多个时刻）、unit（hertz/Bark）、object（可选）",
        build=_build_formant_frequency,
    ),
    Tool(
        name="formant_statistics",
        summary="统计一段时间的共振峰平均值和标准差。要「共振峰平均/F1 平均」时用这个。",
        signature="formant（默认 2，可写 1,2）、from、to（秒，默认整个对象）、unit（hertz/Bark）、object（可选）",
        build=_build_formant_statistics,
    ),
    Tool(
        name="pitch",
        summary="查询某一时刻的基频（Hz）。",
        signature="time（秒，默认中点，可写 0.25,0.75 查多个时刻）、pitch_floor、pitch_ceiling、object（可选）",
        build=_build_pitch,
    ),
    Tool(
        name="pitch_statistics",
        summary="统计一段时间的基频：平均值、最低值、最高值和最高点的时刻（Hz）。要「基频平均/最高/范围」时用这个。",
        signature="from、to（秒，默认整个对象）、pitch_floor、pitch_ceiling、object（可选）",
        build=_build_pitch_statistics,
    ),
    Tool(
        name="intensity",
        summary="查询某一时刻的强度（dB）。",
        signature="time（秒，默认中点，可写 0.25,0.75 查多个时刻）、object（可选）",
        build=_build_intensity,
    ),
    Tool(
        name="intensity_statistics",
        summary="统计一段时间的强度：平均值和最高值（dB）。",
        signature="from、to（秒，默认整个对象）、object（可选）",
        build=_build_intensity_statistics,
    ),
    Tool(
        name="harmonicity_statistics",
        summary="统计谐噪比 HNR（Harmonicity，dB）：平均值和最高值。要「谐噪比」时用这个。",
        signature="from、to（秒，默认整个对象）、pitch_floor（基频下限 Hz，默认 75）、object（可选）",
        build=_build_harmonicity_statistics,
    ),
    Tool(
        name="spectrogram",
        summary="把 Sound 做成频谱图对象（不是图片，画图用 Praat 的绘制菜单）。",
        signature="window_length（默认 0.005 秒）、max_frequency（默认 5000 Hz）、time_step、frequency_step、name、object（可选）",
        build=_build_spectrogram,
    ),
    Tool(
        name="textgrid_info",
        summary="查看 TextGrid 的层数、每层名称、区间/点的数量和标签。要「这个 TextGrid 有几个区间/标了什么」时用这个。",
        signature="maximum_intervals（每层最多列几个，默认 12）、object（可选）",
        build=_build_textgrid_info,
    ),
    Tool(
        name="textgrid_set_interval",
        summary="给 TextGrid 的某一层在指定时间区间写上标签（需要时自动插入边界）。要「把 0.2–0.5 秒标成 a」时用这个。",
        signature="start、end、from、to、finish（都是时间参数，秒，from/to/finish 是 start/end 的别名）、label（写入的文字）、tier（层号，默认 1）、object（可选）",
        build=_build_textgrid_set_interval,
    ),
    Tool(
        name="textgrid_insert_boundary",
        summary="在 TextGrid 某一层的指定时刻插入一个边界。",
        signature="time（秒）、tier（层号，默认 1）、object（可选）",
        build=_build_textgrid_insert_boundary,
    ),
    Tool(
        name="vot",
        summary="算 VOT（嗓音起始时间）：给了 burst（爆破）和 voicing（浊音起始）就直接相减；只给 from/to 就在这个大概范围里自动估计（结果标注是估计值）。结果同时给出爆破与浊音起始占目标时长的百分比。",
        signature="burst、voicing（秒，给全就相减）、from、to（秒，自动估计的范围）、burst_db（默认 6，爆破最小升幅 dB）、pitch_floor（默认 75）、tier（默认 1）、object（可选）",
        build=_build_vot,
    ),
    Tool(
        name="select_object",
        summary="按 id 或名称选中对象。",
        signature="object",
        build=_build_select,
    ),
    Tool(
        name="view_edit",
        summary="用编辑器打开对象。",
        signature="object（可选）",
        build=_build_view,
    ),
    Tool(
        name="play",
        summary="播放选中的 Sound。",
        signature="object（可选）",
        build=_build_play,
    ),
    Tool(
        name="rename_object",
        summary="重命名对象。",
        signature="new_name、object（可选）",
        build=_build_rename,
    ),
    Tool(
        name="remove_object",
        summary="删除对象。",
        signature="object（可选）",
        build=_build_remove,
    ),
    Tool(
        name="create_sound",
        summary="新建一个声音对象：frequency 大于 0 生成纯音，等于 0 生成静音。",
        signature="duration（秒，默认 1）、frequency（Hz，默认 220）、amplitude（0–1）、name（可选）",
        build=_build_create_sound,
    ),
    Tool(
        name="concatenate_sounds",
        summary="把两个 Sound 首尾拼接成一个新 Sound。",
        signature="object、object2（两个声音）、name（可选）",
        build=_build_concatenate,
    ),
    Tool(
        name="extract_part",
        summary="按时间区间从一个 Sound 里截取片段（生成新对象）。",
        signature="start、end、from、to、finish（都是时间参数，秒，from/to/finish 是 start/end 的别名）、name（可选）、object（可选）",
        build=_build_extract_part,
    ),
    Tool(
        name="save_sound",
        summary="把 Sound 另存为 WAV 文件。",
        signature="path（完整路径，例如 D:/out/a.wav）、object（可选）",
        build=_build_save_sound,
    ),
    Tool(
        name="read_file",
        summary=(
            "把磁盘上的文件读进 Praat 变成一个对象（wav / TextGrid / Pitch 等）；"
            "要和「另存为文件」区分开：这个是**读进来**。"
        ),
        signature="path（完整路径，例如 D:/in/a.wav）",
        build=_build_read_file,
    ),
    Tool(
        name="resample_sound",
        summary="改变采样率，生成一个新的 Sound 对象。",
        signature="rate（Hz，常用 16000/22050/44100）、object（可选）",
        build=_build_resample,
    ),
    Tool(
        name="duplicate_object",
        summary="复制一个对象。",
        signature="name（可选，新名称）、object（可选）",
        build=_build_duplicate,
    ),
    Tool(
        name="spectral_emphasis",
        summary=(
            "算谱强调（spectral emphasis）：低通滤波前后损失多少强度（dB），"
            "用于嗓音质量分析（Traunmüller & Eriksson 2000）。"
        ),
        signature=(
            "multiplier（默认 1.5，低通上限 = 平均基频 × 它）、smoothing（默认 20 Hz）、"
            "pitch_floor（默认 75）、pitch_ceiling（默认 600）、object（必须是 Sound）"
        ),
        build=_build_spectral_emphasis,
    ),
    Tool(
        name="hl_ratio",
        summary="算高频段与低频段的能量比 H/L（默认 4–8 kHz ÷ 0–4 kHz）。",
        signature=(
            "low_from（默认 0）、low_to（默认 4000）、high_from（默认 4000）、"
            "high_to（默认 8000）、object（Sound 或 Spectrum）"
        ),
        build=_build_hl_ratio,
    ),
    Tool(
        name="hammarberg_index",
        summary=(
            "算 Hammarberg 指数：0–2 kHz 的 LTAS 最大电平 减去 2–5 kHz 的最大电平"
            "（dB，Hammarberg et al. 1980）。"
        ),
        signature=(
            "low_from（默认 0）、low_to（默认 2000）、high_from（默认 2000）、"
            "high_to（默认 5000）、bandwidth（做 Ltas 的带宽，默认 100）、"
            "object（Sound 或 Ltas）"
        ),
        build=_build_hammarberg_index,
    ),
    Tool(
        name="pitch_peak_latency",
        summary=(
            "算基频峰值延迟：（基频最高点 − 区间起点）÷ 区间时长，"
            "0.5 表示峰值正好在中间。"
        ),
        signature=(
            "from、to（秒，默认整个对象或编辑器圈选）、pitch_floor（默认 75）、"
            "pitch_ceiling（默认 600）、object（可选）"
        ),
        build=_build_pitch_peak_latency,
    ),
    Tool(
        name="peak_to_average_ratio",
        summary=(
            "算峰值/有效值比（peak-to-average，Hillenbrand et al. 1994）："
            "峰值幅度 ÷ RMS。"
        ),
        signature="from、to（秒，默认整个对象或编辑器圈选）、object（必须是 Sound）",
        build=_build_peak_to_average_ratio,
    ),
    Tool(
        name="intensity_slope",
        summary=(
            "算强度曲线的平均斜率（dB/s）：local = 相邻点绝对差的平均 ÷ 时间步长，"
            "global = 首尾差 ÷ 时长。"
        ),
        signature=(
            "method（local/global，默认 local）、from、to（秒，默认整个对象）、"
            "pitch_floor（做 Intensity 用，默认 100）、time_step（默认 0.001 秒）、"
            "object（Sound 或 Intensity）"
        ),
        build=_build_intensity_slope,
    ),
    Tool(
        name=MEASURE_TOOL,
        summary=(
            "表驱动的声学测量：jitter / shimmer、共振峰带宽、频谱重心/偏度/峰度、"
            "CPPS、基频与强度的各种统计量都在这里。parameter 一次可以写多个"
            "（逗号隔开），例如 \"f1,f2\"。"
        ),
        signature=measure_signature(),
        build=_build_measure,
    ),
)


TOOL_MAP: dict[str, Tool] = {tool.name: tool for tool in TOOLS}


#: 每个工具的 JSON Schema（原生 function calling 用）。
#:
#: 集中定义在这里，而不是塞进 26 个 ``Tool(...)`` 里，是为了能一眼比对各工具的参数
#: 和必填项；``ai/tests/test_chat_tools.py`` 守着「每个工具都有 schema」和
#: 「schema 里的参数名都出现在 ``Tool.signature`` 里」两条，改动不会悄悄跑偏。
#: 秒数和编号允许字符串（``time`` 可以写 ``"0.25,0.75"``），因为模板本来就接受这种
#: 写法，模型也常这么填。
TOOL_PARAMETERS: dict[str, dict[str, Any]] = {
    "object_info": {
        "type": "object",
        "properties": {"object": _object_arg()},
        "required": [],
    },
    "duration": {
        "type": "object",
        "properties": {"object": _object_arg()},
        "required": [],
    },
    "formant_bandwidth": {
        "type": "object",
        "properties": {
            "formant": _formants_arg('第几共振峰，默认 2，可写 2 或 "1,2"（最多 6 条）'),
            "time": _times_arg("秒，默认对象中点"),
            "unit": _unit_arg(),
            "object": _object_arg(),
        },
        "required": [],
    },
    "formant_frequency": {
        "type": "object",
        "properties": {
            "formant": _formants_arg('第几共振峰，默认 2，可写 2 或 "1,2"（最多 6 条）'),
            "time": _times_arg('秒，可写多个时刻，如 "0.25,0.75"'),
            "unit": _unit_arg(),
            "object": _object_arg(),
        },
        "required": [],
    },
    "formant_statistics": {
        "type": "object",
        "properties": {
            "formant": _formants_arg('第几共振峰，默认 2，可写 1 或 "1,2"'),
            "from": _seconds_arg("起点（秒），不填就是整个对象"),
            "to": _seconds_arg("终点（秒），不填就是整个对象"),
            "unit": _unit_arg(),
            "object": _object_arg(),
        },
        "required": [],
    },
    "pitch": {
        "type": "object",
        "properties": {
            "time": _times_arg('秒，可写多个时刻，如 "0.25,0.75"'),
            "pitch_floor": _number_arg("基频下限 Hz，默认 75"),
            "pitch_ceiling": _number_arg("基频上限 Hz，默认 600"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "pitch_statistics": {
        "type": "object",
        "properties": {
            "from": _seconds_arg("起点（秒），不填就是整个对象"),
            "to": _seconds_arg("终点（秒），不填就是整个对象"),
            "pitch_floor": _number_arg("基频下限 Hz，默认 75"),
            "pitch_ceiling": _number_arg("基频上限 Hz，默认 600"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "intensity": {
        "type": "object",
        "properties": {
            "time": _times_arg('秒，可写多个时刻，如 "0.25,0.75"'),
            "object": _object_arg(),
        },
        "required": [],
    },
    "intensity_statistics": {
        "type": "object",
        "properties": {
            "from": _seconds_arg("起点（秒），不填就是整个对象"),
            "to": _seconds_arg("终点（秒），不填就是整个对象"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "harmonicity_statistics": {
        "type": "object",
        "properties": {
            "from": _seconds_arg("起点（秒），不填就是整个对象"),
            "to": _seconds_arg("终点（秒），不填就是整个对象"),
            "pitch_floor": _number_arg("谐噪比分析的基频下限 Hz，默认 75"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "spectrogram": {
        "type": "object",
        "properties": {
            "window_length": _seconds_arg("窗长（秒），默认 0.005"),
            "max_frequency": _number_arg("最高频率 Hz，默认 5000"),
            "time_step": _seconds_arg("时间步长（秒）；不填就用 Praat 的默认值"),
            "frequency_step": _number_arg("频率步长 Hz；不填就用 Praat 的默认值"),
            "name": _text_arg("新对象的名字"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "textgrid_info": {
        "type": "object",
        "properties": {
            "maximum_intervals": _integer_arg("每层最多列几个区间，默认 12"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "textgrid_set_interval": {
        "type": "object",
        "properties": {
            "start": _seconds_arg("区间起点（秒）；from 是同一个意思"),
            "end": _seconds_arg("区间终点（秒）；to / finish 是同一个意思"),
            "from": _seconds_arg("区间起点（秒），start 的别名"),
            "to": _seconds_arg("区间终点（秒），end 的别名"),
            "label": _text_arg("要写入的文字（空字符串表示清空）"),
            "tier": _integer_arg("层号，默认 1"),
            "object": _object_arg("TextGrid 对象"),
        },
        "required": ["start", "end", "label"],
    },
    "textgrid_insert_boundary": {
        "type": "object",
        "properties": {
            "time": _seconds_arg("插入时刻（秒）"),
            "tier": _integer_arg("层号，默认 1"),
            "object": _object_arg("TextGrid 对象"),
        },
        "required": ["time"],
    },
    "vot": {
        "type": "object",
        "properties": {
            "burst": _seconds_arg(
                "爆破时刻（秒）；只有用户明确说出爆破时刻时才填，两个都要给，"
                "不要填 0 或凭猜测填"
            ),
            "voicing": _seconds_arg(
                "浊音起始时刻（秒）；只有用户明确说出这个时刻时才填，不要填 0"
            ),
            "from": _seconds_arg("自动估计的搜索起点（秒）；用户给了范围才填"),
            "to": _seconds_arg("自动估计的搜索终点（秒）；用户给了范围才填"),
            "burst_db": _number_arg("爆破最小升幅 dB，默认 6"),
            "pitch_floor": _number_arg("基频下限 Hz，默认 75"),
            "tier": _integer_arg("TextGrid 层号，默认 1"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "select_object": {
        "type": "object",
        "properties": {"object": _object_arg("要选中的对象 id 或名称")},
        "required": ["object"],
    },
    "view_edit": {
        "type": "object",
        "properties": {"object": _object_arg()},
        "required": [],
    },
    "play": {
        "type": "object",
        "properties": {"object": _object_arg("要播放的 Sound（必须是 Sound）")},
        "required": [],
    },
    "rename_object": {
        "type": "object",
        "properties": {
            "new_name": _text_arg("新名字"),
            "object": _object_arg(),
        },
        "required": ["new_name"],
    },
    "remove_object": {
        "type": "object",
        "properties": {"object": _object_arg()},
        "required": [],
    },
    "create_sound": {
        "type": "object",
        "properties": {
            "duration": _seconds_arg("时长（秒），默认 1"),
            "frequency": _number_arg("频率 Hz；填 0 生成静音，默认 220"),
            "amplitude": _number_arg("振幅 0–1，默认 0.5"),
            "name": _text_arg("新对象的名字"),
        },
        "required": [],
    },
    "concatenate_sounds": {
        "type": "object",
        "properties": {
            "object": _object_arg("第一个声音"),
            "object2": _object_arg("第二个声音"),
            "name": _text_arg("结果对象的名字"),
        },
        "required": ["object", "object2"],
    },
    "extract_part": {
        "type": "object",
        "properties": {
            "start": _seconds_arg("起点（秒）；from 是同一个意思"),
            "end": _seconds_arg("终点（秒）；to / finish 是同一个意思"),
            "from": _seconds_arg("起点（秒），start 的别名"),
            "to": _seconds_arg("终点（秒），end 的别名"),
            "name": _text_arg("片段对象的名字"),
            "object": _object_arg(),
        },
        "required": ["start", "end"],
    },
    "save_sound": {
        "type": "object",
        "properties": {
            "path": _text_arg("完整路径，例如 D:/out/a.wav"),
            "object": _object_arg(),
        },
        "required": ["path"],
    },
    "read_file": {
        "type": "object",
        "properties": {
            "path": _text_arg("要读入的文件完整路径，例如 D:/in/a.wav"),
        },
        "required": ["path"],
    },
    "resample_sound": {
        "type": "object",
        "properties": {
            "rate": _integer_arg("采样率 Hz，常用 16000 / 22050 / 44100"),
            "object": _object_arg(),
        },
        "required": ["rate"],
    },
    "duplicate_object": {
        "type": "object",
        "properties": {
            "name": _text_arg("副本的新名字"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "spectral_emphasis": {
        "type": "object",
        "properties": {
            "multiplier": _number_arg("低通上限 = 平均基频 × 这个倍数，默认 1.5"),
            "smoothing": _number_arg("低通滤波的平滑带宽 Hz，默认 20"),
            "pitch_floor": _number_arg("求平均基频用的下限 Hz，默认 75"),
            "pitch_ceiling": _number_arg("求平均基频用的上限 Hz，默认 600"),
            "object": _object_arg("要分析的 Sound"),
        },
        "required": [],
    },
    "hl_ratio": {
        "type": "object",
        "properties": {
            "low_from": _number_arg("低频段起点 Hz，默认 0"),
            "low_to": _number_arg("低频段终点 Hz，默认 4000"),
            "high_from": _number_arg("高频段起点 Hz，默认 4000"),
            "high_to": _number_arg("高频段终点 Hz，默认 8000"),
            "object": _object_arg("Sound 或现成的 Spectrum"),
        },
        "required": [],
    },
    "hammarberg_index": {
        "type": "object",
        "properties": {
            "low_from": _number_arg("低频段起点 Hz，默认 0"),
            "low_to": _number_arg("低频段终点 Hz，默认 2000"),
            "high_from": _number_arg("高频段起点 Hz，默认 2000"),
            "high_to": _number_arg("高频段终点 Hz，默认 5000"),
            "bandwidth": _number_arg("做 Ltas 用的带宽 Hz，默认 100"),
            "object": _object_arg("Sound 或现成的 Ltas"),
        },
        "required": [],
    },
    "pitch_peak_latency": {
        "type": "object",
        "properties": {
            "from": _seconds_arg("区间起点（秒），不填就是整个对象或编辑器圈选"),
            "to": _seconds_arg("区间终点（秒），不填就是整个对象或编辑器圈选"),
            "pitch_floor": _number_arg("基频下限 Hz，默认 75"),
            "pitch_ceiling": _number_arg("基频上限 Hz，默认 600"),
            "object": _object_arg(),
        },
        "required": [],
    },
    "peak_to_average_ratio": {
        "type": "object",
        "properties": {
            "from": _seconds_arg("区间起点（秒），不填就是整个对象或编辑器圈选"),
            "to": _seconds_arg("区间终点（秒），不填就是整个对象或编辑器圈选"),
            "object": _object_arg("要分析的 Sound"),
        },
        "required": [],
    },
    "intensity_slope": {
        "type": "object",
        "properties": {
            "method": {
                "type": "string",
                "enum": ["local", "global"],
                "description": "local = 相邻点绝对差的平均 ÷ 时间步长（默认）；global = 首尾差 ÷ 时长",
            },
            "from": _seconds_arg("起点（秒），不填就是整个对象或编辑器圈选"),
            "to": _seconds_arg("终点（秒），不填就是整个对象或编辑器圈选"),
            "pitch_floor": _number_arg("做 Intensity 用的下限 Hz，默认 100"),
            "time_step": _number_arg("Intensity 的时间步长（秒），默认 0.001"),
            "object": _object_arg("Sound 或现成的 Intensity"),
        },
        "required": [],
    },
    CUSTOM_SCRIPT_TOOL: {
        "type": "object",
        "properties": {
            "script": _text_arg(
                "完整 Praat 英文脚本；只有其它工具都做不到时才用它。"
                "字符串用双引号；要输出就写 appendFileLine。"
            )
        },
        "required": ["script"],
    },
}


#: 在前端（Python）里执行的工具。它们不走「渲染脚本 → 投递」，所以单独一张表；
#: 工具说明、schema、catalog 和标签都要一起给模型看（见下面几个函数的合并逻辑）。
LOCAL_TOOLS: dict[str, LocalTool] = {
    RUN_SCRIPT_TOOL: LocalTool(
        name=RUN_SCRIPT_TOOL,
        summary=(
            "跑一个现成的（社区）.praat 脚本：把它当成批处理跑，输入是当前声音的副本"
            "（脚本里表单的默认值会自动填进去）。脚本自己打印的结果会回到这里。"
            "注意批处理里看不到你当前的对象列表，也不能用编辑器/交互窗口；"
            "对编辑器圈选段做分析的脚本要在 Praat 里自己跑。"
        ),
        signature="path（.praat 文件完整路径）、object（可选，输入的声音）、textgrid（可选）、timeout（秒，默认 60）",
        parameters={
            "type": "object",
            "properties": {
                "path": _text_arg("要跑的 .praat 文件完整路径，例如 D:/scripts/vot.praat"),
                "object": _object_arg("作为输入的声音对象；不填就用当前选中的"),
                "textgrid": _object_arg("（可选）一起交给脚本的 TextGrid"),
                "timeout": _number_arg("批处理超时秒数，默认 60"),
            },
            "required": ["path"],
        },
        run=_run_external_script,
    )
}


def tool_parameters(name: str) -> dict[str, Any]:
    """取一个工具的 JSON Schema（没登记就用空参数表）。"""

    return TOOL_PARAMETERS.get(name, EMPTY_PARAMETERS)


def tool_schemas() -> list[dict[str, Any]]:
    """OpenAI 风格的 ``tools`` 参数，交给 llama-server 的原生 function calling。

    对比「把十几条规则和工具清单写进提示词、让模型自己拼 JSON」：schema 里带了
    类型、枚举和必填项，模型编不出不存在的工具名，``unit`` 这种只在两个值里选的
    参数也不会写错（见 guide.md §8.4 与 ai/docs/adr/ADR-003）。
    """

    schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.summary,
                "parameters": tool_parameters(tool.name),
            },
        }
        for tool in TOOLS
    ]
    schemas.extend(
        {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.summary,
                "parameters": tool.parameters,
            },
        }
        for tool in LOCAL_TOOLS.values()
    )
    schemas.append(
        {
            "type": "function",
            "function": {
                "name": CUSTOM_SCRIPT_TOOL,
                "description": (
                    "只有在上面所有工具都无法完成请求时才使用；"
                    "这时把完整 Praat 英文脚本写进 script 字段。"
                ),
                "parameters": tool_parameters(CUSTOM_SCRIPT_TOOL),
            },
        }
    )
    return schemas


#: 一次请求最多执行几个动作。模型会为「0.25 秒和 0.75 秒的基频」这类请求
#: 一次给多个工具调用（实测 Qwen3.5-2B 就会这么做），它们按顺序拼进同一条脚本里
#: 执行；给个上限是怕模型一口气列十几个，把一次投递变成一串没人看过的操作。
MAX_ACTIONS_PER_REQUEST = 3


#: 会**改动 Praat 里的对象**（建/删/改名/标注/写盘）的工具。
#:
#: 对话循环用它做第二道护栏：用户没说「然后再……」时，第二轮之后不许再动这些工具。
#: 实测 0.8B 预设下「打开当前声音的编辑器」会在第二轮顺手做两次频谱图，把选中对象
#: 也换掉，后面的请求全被带偏；只读查询（pitch、object_info 这类）不受限制。
#: 认不出来的工具（例如 custom_script）按「会改动」处理。
MUTATING_TOOLS: frozenset[str] = frozenset(
    {
        "create_sound",
        "extract_part",
        "concatenate_sounds",
        "duplicate_object",
        "resample_sound",
        "read_file",
        "rename_object",
        "remove_object",
        "save_sound",
        "spectrogram",
        "textgrid_insert_boundary",
        "textgrid_set_interval",
        CUSTOM_SCRIPT_TOOL,
        # 现成脚本可能只打印结果，也可能写文件，按「会改动」保守处理。
        RUN_SCRIPT_TOOL,
    }
)


def is_mutating(tool_name: str) -> bool:
    """这个工具会不会改动对象；没登记的工具按「会改动」处理（保守）。"""

    return (tool_name or "").strip() not in (set(TOOL_MAP) - MUTATING_TOOLS)


def tool_labels() -> dict[str, str]:
    """``工具名 -> 一句中文说明``，规划层用它给「模型只想执行、没写回话」兜底。"""

    labels = {tool.name: tool.summary for tool in TOOLS}
    labels.update({tool.name: tool.summary for tool in LOCAL_TOOLS.values()})
    labels[CUSTOM_SCRIPT_TOOL] = "执行模型给出的自定义 Praat 脚本。"
    return labels


def plan_actions(plan: Mapping[str, Any]) -> list[dict[str, Any]]:
    """把规划结果整理成有序的动作列表。

    两种规划接口都认：原生 function calling 给的 ``actions``（可能多个），以及
    老的 JSON 规划给的单个 ``tool`` / ``arguments`` / ``script``。
    """

    actions: list[dict[str, Any]] = []

    def add(item: Mapping[str, Any]) -> None:
        name = str(item.get("tool", "") or "").strip()
        if not name:
            return
        raw = item.get("arguments")
        action = {
            "tool": name,
            "arguments": dict(raw) if isinstance(raw, Mapping) else {},
            "script": str(item.get("script", "") or ""),
        }
        if "id" in item:
            action["id"] = item["id"]
        invalid = item.get("invalid_arguments")
        if invalid:
            action["invalid_arguments"] = str(invalid)
        elif raw is not None and not isinstance(raw, Mapping):
            action["invalid_arguments"] = "工具参数 arguments 必须是 JSON 对象，未执行默认对象上的操作。"
        actions.append(action)

    raw_actions = plan.get("actions")
    if isinstance(raw_actions, Sequence) and not isinstance(raw_actions, (str, bytes)):
        for item in raw_actions:
            if isinstance(item, Mapping):
                add(item)
    if not actions:
        add(plan)
    if not actions:
        script = str(plan.get("script", "") or "")
        if script.strip():
            actions.append(
                {
                    "tool": CUSTOM_SCRIPT_TOOL,
                    "arguments": {"script": script},
                    "script": "",
                }
            )
    return actions[:MAX_ACTIONS_PER_REQUEST]


def catalog_text() -> str:
    lines = [tool.describe() for tool in TOOLS]
    lines.append(
        f"- {MEASURE_TOOL}: 表驱动的声学测量（一行参数 = 一条查询，参数表在"
        f" ai/praat_ai/measures.tsv）。参数：{measure_parameter_help()}。"
    )
    lines.extend(tool.describe() for tool in LOCAL_TOOLS.values())
    lines.append(
        f"- {CUSTOM_SCRIPT_TOOL}: 只有在上面所有工具都无法完成请求时才使用；"
        "这时把完整 Praat 英文脚本写进 script 字段。"
    )
    return "\n".join(lines)


def render(
    tool_name: str,
    arguments: Mapping[str, Any] | None,
    context: ToolContext,
    *,
    custom_script: str = "",
) -> str:
    """Render the Praat script for one tool request."""

    name = (tool_name or "").strip()
    if not name:
        raise ToolError("模型没有选择工具。")
    if name == CUSTOM_SCRIPT_TOOL:
        script = validate_script(custom_script)
        validate_script_source_types(script, context)
        script = neutralize_info_commands(script, context.result_path)
        return _assemble(script.rstrip("\n").splitlines(), context)
    tool = TOOL_MAP.get(name)
    if tool is None:
        raise ToolError(f"未知工具：{name}")
    if arguments is not None and not isinstance(arguments, Mapping):
        raise ToolError("arguments 必须是 JSON 对象。")
    args = dict(arguments or {})
    _reject_unsupported_arguments(name, args)
    return tool.build(args, context)


TOOL_PARAMETERS[MEASURE_TOOL] = _measure_schema()


TOOL_PARAMETERS[RUN_SCRIPT_TOOL] = LOCAL_TOOLS[RUN_SCRIPT_TOOL].parameters
