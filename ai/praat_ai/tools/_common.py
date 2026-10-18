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


class ToolError(ValueError):
    """Raised when a tool request cannot be turned into a safe Praat script."""


TEMPORARY_OBJECT_NAME = "ai-chat-temp"


CUSTOM_SCRIPT_TOOL = "custom_script"


#: 表驱动的测量工具；它的参数表是 ``ai/praat_ai/measures.tsv``。
MEASURE_TOOL = "measure"


#: 跑现成（社区）.praat 脚本的工具；它不走「渲染脚本 → 投递」那条路，
#: 见 :class:`LocalTool` 与 ``ai/praat_ai/external_script.py``。
RUN_SCRIPT_TOOL = "run_praat_script"


# 「有时长概念」的对象类：只有这些类才能用 Get total duration 之类的查询，
# 否则脚本会在 Praat 里直接报 “Command not available”，用户只看到一句英文错误。
TIME_DOMAIN_CLASSES = frozenset(
    {
        "Sound",
        "LongSound",
        "Pitch",
        "Formant",
        "Intensity",
        "Harmonicity",
        "Spectrogram",
        "BarkSpectrogram",
        "MFCC",
        "PointProcess",
        "TextGrid",
        "TextTier",
        "DurationTier",
        "PitchTier",
        "IntensityTier",
        "AmplitudeTier",
        "FormantTier",
        "PowerCepstrogram",
    }
)


# 两类声音均可查询采样率、保存 WAV；分析转换只对内存中的 Sound 可用。
SOUND_CLASSES = frozenset({"Sound", "LongSound"})


ANALYSIS_SOUND_CLASSES = frozenset({"Sound"})


@dataclass(frozen=True, slots=True)
class ObjectRow:
    id: int
    class_name: str
    name: str
    selected: bool
    # Praat 那边打开着这个对象的编辑器时，会多写两列：用户手动拖出来的选区。
    selection_start: float | None = None
    selection_end: float | None = None

    @property
    def selection(self) -> tuple[float, float] | None:
        """编辑器里手动拖出来的区间；只点了光标（首尾相等）时不算选区。"""

        if self.selection_start is None or self.selection_end is None:
            return None
        if self.selection_end - self.selection_start <= 0.0:
            return None
        return (self.selection_start, self.selection_end)

    @property
    def label(self) -> str:
        return f"{self.id}: {self.name}"

    def name_variants(self) -> set[str]:
        """对象名可能带或不带类名前缀，这里把所有可接受的写法都列出来。"""

        variants = {self.name, self.label, f"{self.class_name} {self.name}"}
        for prefix in (f"{self.class_name} ", self.class_name):
            if self.name.startswith(prefix):
                variants.add(self.name[len(prefix) :].strip())
        variants.add(f"{self.class_name} {self.id}")
        return {_normalize_text(item) for item in variants if item}


def _normalize_text(value: Any) -> str:
    text = str(value or "").casefold()
    for character in ('"', "'", "“", "”", "‘", "’"):
        text = text.replace(character, "")
    text = text.replace("_", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


@dataclass(frozen=True, slots=True)
class ToolContext:
    objects: tuple[ObjectRow, ...]
    result_path: Path
    state_path: Path

    def default_object(self) -> ObjectRow:
        for row in self.objects:
            if row.selected:
                return row
        if self.objects:
            return self.objects[-1]
        raise ToolError("Praat 对象列表为空，请先在 Praat 中打开或新建对象。")

    def resolve_object(self, requested: Any = None) -> ObjectRow:
        if requested is None or requested == "":
            return self.default_object()
        text = str(requested).strip()
        for row in self.objects:
            if text == str(row.id):
                return row
        exact = [
            row for row in self.objects
            if text in {row.name, f"{row.class_name} {row.name}", row.label}
        ]
        if len(exact) == 1:
            return exact[0]
        if len(exact) > 1:
            self._ambiguous_objects(text, exact)
        normalized = _normalize_text(text)
        # 「2 号」「#2」「id 2」都当成 id 处理。
        identifier = re.fullmatch(r"(?:id\s*)?#?(\d+)\s*(?:号|对象)?", normalized)
        if identifier:
            wanted = int(identifier.group(1))
            for row in self.objects:
                if row.id == wanted:
                    return row
        matches = [row for row in self.objects if normalized in row.name_variants()]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            self._ambiguous_objects(text, matches)
        # 模型常把 "Sound tone" 简写成 "tone"，或反过来只写类名加编号；
        # 唯一命中就接受，多个候选就让用户挑，避免猜错对象。
        partial = [
            row
            for row in self.objects
            if any(
                normalized in variant or variant in normalized
                for variant in row.name_variants()
            )
        ]
        if len(partial) == 1:
            return partial[0]
        if len(partial) > 1:
            self._ambiguous_objects(text, partial)
        raise ToolError(f"对象列表里没有“{text}”，请先确认 Praat 中选中的对象。")

    def _ambiguous_objects(self, requested: str, rows: Sequence[ObjectRow]) -> None:
        options = "、".join(f"{row.id}: {row.class_name} {row.name}" for row in rows)
        raise ToolError(f"“{requested}”对应多个对象，请用对象 id 指定：{options}")

    def require_class(
        self,
        row: ObjectRow,
        classes: frozenset[str],
        description: str,
    ) -> None:
        if row.class_name not in classes:
            conversion_hint = (
                "请先用截取片段将 LongSound 提取成 Sound，再进行分析。"
                if row.class_name == "LongSound" and "Sound" in classes else ""
            )
            raise ToolError(
                f"{description}（对象 {row.id} 是 {row.class_name}）。{conversion_hint}"
            )

    def resolve_by_class(
        self,
        requested: Any,
        classes: frozenset[str],
        description: str,
    ) -> ObjectRow:
        """显式指定的对象必须支持命令；默认选择才允许按类型找唯一候选。"""

        if requested not in (None, ""):
            row = self.resolve_object(requested)
            self.require_class(row, classes, description)
            return row
        selected = [item for item in self.objects if item.selected and item.class_name in classes]
        if len(selected) == 1:
            return selected[0]
        if len(selected) > 1:
            self._ambiguous_objects("当前选择", selected)
        candidates = [item for item in self.objects if item.class_name in classes]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            self._ambiguous_objects("当前操作", candidates)
        row = self.default_object()
        self.require_class(row, classes, description)
        return row


def parse_object_context(text: str) -> tuple[ObjectRow, ...]:
    """解析对象快照中的对象行，支持额外的 sel_start / sel_end 编辑器选区列，忽略非对象元数据行。"""

    rows: list[ObjectRow] = []
    for line in (text or "").splitlines():
        parts = [part.strip() for part in line.split("\t")]
        if len(parts) < 4 or not parts[0].isdigit():
            continue
        # 名字里可能有制表符（Praat 允许）：从右边判断尾部是 selected 还是
        # selected+sel_start+sel_end，多出来的列属于名字，不能整行错位
        # （错位会把 selected 读成 sel_start，对象变成"未选中"）。
        tail = 1
        if len(parts) >= 6 and parts[-3] in ("0", "1"):
            try:
                for piece in (parts[-2], parts[-1]):
                    if piece:
                        float(piece)
                tail = 3
            except ValueError:
                tail = 1
        name = "\t".join(parts[2:len(parts) - tail])
        rows.append(
            ObjectRow(
                id=int(parts[0]),
                class_name=parts[1],
                name=name,
                selected=parts[len(parts) - tail] == "1",
                selection_start=_optional_seconds(parts, len(parts) - 2) if tail == 3 else None,
                selection_end=_optional_seconds(parts, len(parts) - 1) if tail == 3 else None,
            )
        )
    return tuple(rows)


def _optional_seconds(parts: list[str], index: int) -> float | None:
    if len(parts) <= index or not parts[index]:
        return None
    try:
        value = float(parts[index])
    except ValueError:
        return None
    # nan/inf 会一路写进脚本（``tmin = nan``），这里直接当没有圈选。
    return value if math.isfinite(value) else None


def praat_text(value: Any) -> str:
    """Render text that is safe inside a Praat double-quoted string."""

    text = str(value).replace("\\", "/").replace('"', '""')
    return text.replace("\r", " ").replace("\n", " ")


def quote(value: Any) -> str:
    return f'"{praat_text(value)}"'


def praat_path(path: Path) -> str:
    return praat_text(str(path))


def _number(
    arguments: Mapping[str, Any],
    key: str,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    value = arguments.get(key, None)
    if value is None or value == "":
        return default
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ToolError(f"参数 {key} 必须是数字，收到：{value!r}") from error
    if not minimum <= number <= maximum:
        raise ToolError(f"参数 {key} 必须在 {minimum} 与 {maximum} 之间。")
    return number


def _integer(
    arguments: Mapping[str, Any],
    key: str,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    number = _number(arguments, key, float(default), float(minimum), float(maximum))
    rounded = int(round(number))
    if abs(number - rounded) > 1e-6:
        raise ToolError(f"参数 {key} 必须是整数。")
    return rounded


#: 基频上下限在所有工具里共用一套界限（以前 pitch/vot/measure 各有一套，同一句话
#: 会因为模型选了不同工具而成功或失败）。与 measures.tsv 的 @@ settings 保持一致。
PITCH_FLOOR_BOUNDS = (10.0, 2000.0)


PITCH_CEILING_BOUNDS = (20.0, 5000.0)


#: 范围端点的同义写法：说明书写过 finish，代码只读 to/end 时
#: ``from`` + ``finish`` 会被静默当作整段测量。
RANGE_START_KEYS = ("from", "start")


RANGE_END_KEYS = ("to", "end", "finish")


def _coerce_number(value: Any) -> float | None:
    """能当成有限数就返回它，否则 ``None``（不抛异常，供同义写法比较用）。"""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _range_argument(
    arguments: Mapping[str, Any],
    keys: Sequence[str],
    default: Any = None,
) -> Any:
    """按同义写法取一个范围端点；同时给了互相矛盾的写法就报错。"""

    given = [(key, arguments[key]) for key in keys
             if key in arguments and arguments[key] not in (None, "")]
    if not given:
        return default
    first_key, first = given[0]
    first_number = _coerce_number(first)
    for key, value in given[1:]:
        other = _coerce_number(value)
        if first_number is None or other is None or abs(first_number - other) > 1e-9:
            raise ToolError(f"{first_key} 与 {key} 给了互相矛盾的范围，请只保留一个。")
    return first


def _range_pair(arguments: Mapping[str, Any]) -> tuple[Any, Any]:
    """范围端点 ``(起点, 终点)``，支持 from/start 与 to/end/finish，并检查顺序。

    顺序以前只在 Praat 脚本里 ``exitScript``，等于白跑一次投递；所有用范围的地方
    都在这里统一拒绝（2026-10-07）。
    """

    start = _range_argument(arguments, RANGE_START_KEYS)
    end = _range_argument(arguments, RANGE_END_KEYS)
    first, second = _coerce_number(start), _coerce_number(end)
    if first is not None and second is not None and first > second:
        raise ToolError(f"起点必须早于终点（现在 from={first:g}、to={second:g}）。")
    return start, end


def _pitch_band(arguments: Mapping[str, Any], *, floor_default: float = 75.0,
                ceiling_default: float = 600.0, with_ceiling: bool = True) -> tuple[float, float | None]:
    """统一的基频上下限：同一套界限 + 下限必须小于上限。"""

    floor = _number(arguments, "pitch_floor", floor_default, *PITCH_FLOOR_BOUNDS)
    ceiling = (_number(arguments, "pitch_ceiling", ceiling_default, *PITCH_CEILING_BOUNDS)
               if with_ceiling else None)
    if ceiling is not None and floor >= ceiling:
        raise ToolError("pitch_floor（基频下限）必须小于 pitch_ceiling（基频上限）。")
    return floor, ceiling


def _named_target(context: "ToolContext", arguments: Mapping[str, Any], action: str) -> "ObjectRow":
    """破坏性操作（删除/重命名）要一个**明确**的目标。

    ``ToolContext.default_object`` 在没有选中对象时会退回对象列表的最后一行；对
    ``duration`` 这类只读工具只是不精确，对删除/重命名就是误删（2026-10-07 实测：
    什么都没选中时 ``remove_object`` 渲染出 ``selectObject: 3`` 删掉一个 Intensity）。
    """

    requested = arguments.get("object")
    if requested in (None, ""):
        selected = [row for row in context.objects if row.selected]
        if len(selected) != 1:
            raise ToolError(f"{action}需要明确的目标：请先在 Praat 里选中唯一对象，或给出编号/名称。")
    return context.resolve_object(requested)


def _query_times(
    arguments: Mapping[str, Any],
    *,
    fallback: str,
    maximum: int = 8,
) -> list[str]:
    """``time`` 可以是数字、列表或 "0.25,0.75" 这类文本，返回 Praat 时间表达式。

    用户常一次问多个时刻（「0.25 秒和 0.75 秒的基频」），所以这里统一成列表，
    由模板逐个时刻写一行结果。
    """

    raw = arguments.get("time", arguments.get("times", None))
    if raw is None or raw == "":
        return [fallback]
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,，、;；\s]+", str(raw))
    values: list[str] = []
    for item in items:
        if str(item).strip() == "":
            continue
        try:
            number = _number({"value": item}, "value", 0.0, 0.0, 36000.0)
        except ToolError as error:
            raise ToolError(f"时间只能是 0–36000 之间的秒数，收到：{item!r}") from error
        values.append(f"{number:.6f}")
    if not values:
        return [fallback]
    if len(values) > maximum:
        raise ToolError(f"一次最多查询 {maximum} 个时刻（收到 {len(values)} 个）；"
                        "请减少时刻，或拆成多次调用。")
    return values


def _clamp_time_lines(suffix_variable: str = "note$") -> list[str]:
    """把 ``time`` 夹进对象时长，并说明是否被截断。

    时间超出对象范围时 Praat 不会报错，而是返回 ``--undefined--``；
    这里先夹到有效区间，再让模板把 ``--undefined--`` 换成中文说明。
    """

    return [
        f'{suffix_variable} = ""',
        "if time < 0",
        "    time = 0",
        f'    {suffix_variable} = "（已按对象时长截断）"',
        "endif",
        "if time > duration",
        "    time = duration",
        f'    {suffix_variable} = "（已按对象时长截断）"',
        "endif",
    ]


def _clamp_range_lines() -> list[str]:
    """把 ``tmin`` / ``tmax`` 夹进对象时长（Praat 的 from/to/end 是保留字）。"""

    return [
        "if tmin < 0",
        "    tmin = 0",
        "endif",
        "if tmax > duration",
        "    tmax = duration",
        "endif",
        "if tmin > tmax",
        '    exitScript: "查询范围无效或超出对象时长，请在对象内重新指定起止时间。"',
        "endif",
    ]


def _unit_literal(arguments: Mapping[str, Any], default: str) -> str:
    value = str(arguments.get("unit", default) or default).strip()
    lowered = value.casefold()
    if lowered in {"bark", "巴克"}:
        return '"Bark"'
    if lowered in {"hertz", "hz", "赫兹"}:
        return '"hertz"'
    raise ToolError(f"单位 {value!r} 不受支持，只能用 hertz 或 Bark。")


def _unit_display(arguments: Mapping[str, Any], default: str) -> str:
    return " Bark" if _unit_literal(arguments, default) == '"Bark"' else " Hz"


def _formant_numbers(arguments: Mapping[str, Any]) -> list[int]:
    """支持 ``formant`` 写成一个数字、列表或 "1,2" 这类文本。"""

    raw = arguments.get("formant", arguments.get("formants", None))
    if raw is None or raw == "":
        return [2]
    items = raw if isinstance(raw, (list, tuple)) else re.split(r"[,，、;\s]+", str(raw))
    numbers: list[int] = []
    for item in items:
        if str(item).strip() == "":
            continue
        try:
            number = _integer({"value": item}, "value", 1, 1, 20)
        except ToolError as error:
            raise ToolError(
                f"共振峰编号只能是 1–20 的整数，收到：{item!r}"
            ) from error
        if number not in numbers:
            numbers.append(number)
    if not numbers:
        return [2]
    if len(numbers) > 6:
        raise ToolError("一次最多查询 6 条共振峰。")
    return numbers


def _finish(context: ToolContext) -> list[str]:
    return [f"appendFileLine: {quote(context.state_path)}, \"done\""]


def _id_fragment(variable: str = "newId") -> str:
    """将新建对象的 ID 写入结果，供后续动作引用真实创建的对象。"""

    return f"{quote('（id ')}, fixed$ ({variable}, 0), {quote('）')}"


def _write_result(context: ToolContext, fragments: Sequence[str]) -> str:
    joined = ", ".join(fragments)
    return f"appendFileLine: {quote(context.result_path)}, {joined}"


def _assemble(lines: Sequence[str], context: ToolContext) -> str:
    body = [line for line in lines if line and line.strip()]
    body.extend(_finish(context))
    return "\n".join(body) + "\n"


def _formant_lines(
    arguments: Mapping[str, Any],
    context: ToolContext,
    *,
    command: str,
    label: str,
    with_bandwidth: bool = False,
) -> list[str]:
    row = context.resolve_by_class(
        arguments.get("object"), frozenset({"Sound", "Formant"}),
        "共振峰分析需要 Sound 或 Formant 对象",
    )
    formatns = _formant_numbers(arguments)
    unit = _unit_literal(arguments, "hertz")
    unit_text = _unit_display(arguments, "hertz")
    times = _query_times(arguments, fallback=_default_time_expression(row, arguments))
    temporary = row.class_name != "Formant"
    lines = [f"selectObject: {row.id}", "duration = Get total duration"]
    if temporary:
        lines.extend(
            [
                "To Formant (burg): 0, 5, 5500, 0.025, 50",
                f"Rename: {quote(TEMPORARY_OBJECT_NAME)}",
            ]
        )
    for time_expression in times:
        lines.append(f"time = {time_expression}")
        lines.extend(_clamp_time_lines())
        for formant in formatns:
            lines.append(f'value = {command}: {formant}, time, {unit}, "linear"')
            if with_bandwidth:
                # 「F1 的频率和带宽」一次问完：带宽缺失时只省略带宽那半句。
                lines.append(
                    f'bandwidth = Get bandwidth at time: {formant}, time, {unit}, "linear"'
                )
                lines.append(
                    f'bandwidthText$ = "，带宽 " + fixed$ (bandwidth, 3) + "{unit_text}"'
                )
                lines.extend(
                    [
                        "if bandwidth = undefined",
                        '    bandwidthText$ = ""',
                        "endif",
                    ]
                )
            measured = "频率" if with_bandwidth else label
            lines.extend(
                [
                    "if value = undefined",
                    _write_result(
                        context,
                        [
                            quote(f"第 {formant} 共振峰{measured}（"),
                            "fixed$ (time, 3)",
                            quote(" 秒处）无法计算：该时刻没有可用的共振峰数据"),
                            "note$",
                        ],
                    ),
                    "else",
                    _write_result(
                        context,
                        (
                            [
                                quote(f"第 {formant} 共振峰（"),
                                "fixed$ (time, 3)",
                                quote(" 秒处）：频率 "),
                                "fixed$ (value, 3)",
                                quote(unit_text),
                                "bandwidthText$",
                                "note$",
                            ]
                            if with_bandwidth
                            else [
                                quote(f"第 {formant} 共振峰{label}（"),
                                "fixed$ (time, 3)",
                                quote(" 秒处）= "),
                                "fixed$ (value, 3)",
                                quote(unit_text),
                                "note$",
                            ]
                        ),
                    ),
                    "endif",
                ]
            )
    if temporary:
        lines.extend(["Remove", f"selectObject: {row.id}"])
    return lines


def _pitch_extreme_lines() -> list[str]:
    """遍历基频帧取得最高、最低值与峰值时刻。实现按中位数的 1.5 倍限制候选最大值，并报告原始超限值；该启发式可能排除真实高基频，不能保证所有倍频误判均被识别。"""

    return [
        'median = Get quantile: tmin, tmax, 0.5, "Hertz"',
        "minimum = undefined",
        "maximum = undefined",
        "maxtime = undefined",
        "mean = undefined",
        "pitchLimit$ = \"\"",
        "if median <> undefined",
        # 注意两点：① Get frame number from time 返回的是**小数**（0.25 秒 → 31.5），
        # 而 Get value in frame 要整数，必须 round；② Praat **不允许把命令嵌在公式里**
        # （``round (Get frame number from time: tmin)`` 会报 Unknown symbol），
        # 所以先取值再取整。整段分析时两端恰好落在整数帧上，圈选（0.25–0.5 秒）
        # 就会直接报「should be a whole number」。
        "    firstFrameTime = Get frame number from time: tmin",
        "    lastFrameTime = Get frame number from time: tmax",
        "    firstFrame = round (firstFrameTime)",
        "    lastFrame = round (lastFrameTime)",
        "    if firstFrame < 1",
        "        firstFrame = 1",
        "    endif",
        "    if lastFrame > nFrames",
        "        lastFrame = nFrames",
        "    endif",
        "    if lastFrame < 1",
        "        lastFrame = 1",
        "    endif",
        # 上限：中位数的 1.5 倍；下限：中位数的一半（次谐波误判）。
        "    ceilingValue = median * 1.5",
        "    floorValue = median * 0.5",
        "    highest = 0",
        "    lowest = 0",
        # 平均值与最高/最低统计使用同一批过滤后的基频帧。
        "    pitchTotal = 0",
        "    pitchCount = 0",
        "    for pitchFrame from firstFrame to lastFrame",
        '        frameValue = Get value in frame: pitchFrame, "Hertz"',
        "        if frameValue <> undefined and frameValue <= ceilingValue and frameValue > highest",
        "            highest = frameValue",
        "            maxtime = Get time from frame number: pitchFrame",
        "        endif",
        "        if frameValue <> undefined and frameValue >= floorValue",
        "            if lowest = 0 or frameValue < lowest",
        "                lowest = frameValue",
        "            endif",
        "        endif",
        "        if frameValue <> undefined and frameValue <= ceilingValue and frameValue >= floorValue",
        "            pitchTotal = pitchTotal + frameValue",
        "            pitchCount = pitchCount + 1",
        "        endif",
        "    endfor",
        "    if highest > 0",
        "        maximum = highest",
        "    endif",
        "    if lowest > 0",
        "        minimum = lowest",
        "    endif",
        "    if pitchCount > 0",
        "        mean = pitchTotal / pitchCount",
        "    endif",
        "endif",
        'rawMaximum = Get maximum: tmin, tmax, "Hertz", "Parabolic"',
        "if rawMaximum <> undefined and median <> undefined and rawMaximum > median * 1.5",
        '    pitchLimit$ = "（该区间有一帧被自相关误判成 " + fixed$ (rawMaximum, 1)'
        ' + " Hz，约为中位数 " + fixed$ (median, 1) + " Hz 的 "'
        ' + fixed$ (rawMaximum / median, 1) + " 倍，已按倍频误判剔除；'
        '平均、最低、最高都只统计剔除后的帧）"',
        "endif",
    ]


def _point_query_lines(
    arguments: Mapping[str, Any],
    context: ToolContext,
    *,
    command: str,
    label: str,
    unit_text: str,
    undefined_message: str,
    row: ObjectRow | None = None,
) -> list[str]:
    """按 ``time``（可以是多个时刻）写若干行「某时刻 = 数值」的结果。"""

    lines: list[str] = []
    fallback = "duration / 2" if row is None else _default_time_expression(row, arguments)
    for expression in _query_times(arguments, fallback=fallback):
        lines.append(f"time = {expression}")
        lines.extend(_clamp_time_lines())
        lines.extend(
            [
                f"value = {command}",
                "if value = undefined",
                _write_result(
                    context,
                    [
                        quote(f"{label}（"),
                        "fixed$ (time, 3)",
                        quote(undefined_message),
                        "note$",
                    ],
                ),
                "else",
                _write_result(
                    context,
                    [
                        quote(f"{label}（"),
                        "fixed$ (time, 3)",
                        quote(" 秒处）= "),
                        "fixed$ (value, 3)",
                        quote(unit_text),
                        "note$",
                    ],
                ),
                "endif",
            ]
        )
    return lines


def _selection_range(
    arguments: Mapping[str, Any], row: ObjectRow | None
) -> tuple[float, float] | None:
    """这句话该用编辑器里圈出来的选区，就返回它，否则返回 ``None``。

    两种情况算"用选区"：用户没在话里给范围；或者模型把 ``sel_start``/``sel_end``
    原样抄进了 ``from``/``to``（数值对得上 ±2 ms 时是同一段范围，仍按圈选报，
    回话里才有"按编辑器圈选"的出处）。显式给了别的范围时不抢用户的数。
    """

    if row is None or arguments.get('_whole_object'):
        return None
    selection = row.selection
    if selection is None:
        return None
    start, end = _range_pair(arguments)
    if start in (None, "") and end in (None, ""):
        return selection
    if _echoes_editor_selection(start, end, selection):
        return selection
    return None


def _range_note(selection: tuple[float, float]) -> str:
    return (
        f'rangeNote$ = "（按编辑器圈选 {selection[0]:.3f}–{selection[1]:.3f} 秒）"'
    )


def _range_lines(
    arguments: Mapping[str, Any], row: ObjectRow | None = None
) -> list[str]:
    """设置 ``tmin`` / ``tmax``（默认整个对象）并夹进对象时长。

    用户没在话里给 from/to 时用编辑器里圈出来的选区，``rangeNote$`` 负责在回话里
    注明这段范围是圈出来的，不能静默换范围。
    """

    selection = _selection_range(arguments, row)
    lines = ["tmin = 0", "tmax = duration"]
    if selection is not None:
        lines.append(f"tmin = {selection[0]:.6f}")
        lines.append(f"tmax = {selection[1]:.6f}")
        lines.append(_range_note(selection))
    else:
        lines.append('rangeNote$ = ""')
        start, end = _range_pair(arguments)
        start = _number({"from": start}, "from", 0.0, 0.0, 36000.0) if start not in (None, "") else None
        end = _number({"to": end}, "to", 0.0, 0.0, 36000.0) if end not in (None, "") else None
        if start is not None and end is not None and start > end:
            # 以前只在 Praat 脚本里 exitScript，白跑一次投递；在 Python 层就说清楚范围反了。
            raise ToolError(f"起点必须早于终点（现在 from={start:g}、to={end:g}）。")
        if start is not None:
            lines.append(f"tmin = {start:.6f}")
        if end is not None:
            lines.append(f"tmax = {end:.6f}")
    lines.extend(_clamp_range_lines())
    return lines


def _default_time_expression(row: ObjectRow, arguments: Mapping[str, Any] | None = None) -> str:
    """点查询没给时刻时：圈了选区就用选区中点，否则用对象中点。"""

    selection = None if (arguments or {}).get('_whole_object') else row.selection
    if selection is None:
        return "duration / 2"
    return f"{(selection[0] + selection[1]) / 2:.6f}"


def _textgrid_row(arguments: Mapping[str, Any], context: ToolContext) -> ObjectRow:
    return context.resolve_by_class(
        arguments.get("object"),
        frozenset({"TextGrid"}),
        "只有 TextGrid 对象可以做标注",
    )


def _tier_number(arguments: Mapping[str, Any]) -> int:
    value = arguments.get("tier", arguments.get("tier_number", 1))
    try:
        return _integer({"value": value}, "value", 1, 1, 100)
    except ToolError as error:
        raise ToolError("层号必须是 1–100 的整数。") from error


def _insert_boundary_block(
    tier: int,
    time_variable: str,
    counter_variable: str,
    suffix: str,
) -> list[str]:
    """在 ``time_variable`` 处插入边界，跳过端点和已经有边界的位置。

    Praat 的 ``Insert boundary`` 遇到已存在的边界会直接报错，而 TextGrid 的起止点
    本来就各有一个边界，所以插入前先自己找一遍已有边界。
    """

    flag = f"needBoundary{suffix}"
    loop = f"boundaryIndex{suffix}"
    return [
        f"{flag} = 1",
        f"if {time_variable} <= 0",
        f"    {flag} = 0",
        "endif",
        f"if {time_variable} >= duration",
        f"    {flag} = 0",
        "endif",
        f"nIntervals = Get number of intervals: {tier}",
        f"for {loop} from 1 to nIntervals",
        f"    boundaryTime = Get start time of interval: {tier}, {loop}",
        f"    if abs (boundaryTime - {time_variable}) < 0.000001",
        f"        {flag} = 0",
        "    endif",
        "endfor",
        f"if {flag} = 1",
        f"    Insert boundary: {tier}, {time_variable}",
        f"    {counter_variable} = {counter_variable} + 1",
        "endif",
    ]


def _echoes_editor_selection(
    start: Any, end: Any, selection: tuple[float, float]
) -> bool:
    """模型把编辑器里的圈选原样抄进 from/to 时，数值上应该和选区对得上。"""

    if start in (None, "") and end in (None, ""):
        return False
    for value, bound in ((start, selection[0]), (end, selection[1])):
        if value in (None, ""):
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        if abs(number - bound) > 0.002:
            return False
    return True


def _seconds_argument(value: Any, key: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as error:
        raise ToolError(f"参数 {key} 必须是数字，收到：{value!r}") from error
    return min(max(number, 0.0), 36000.0)


def _rise_onset_lines(prefix: str, env_var: str) -> list[str]:
    """在强度包络里找最陡的一段上升沿，写进 ``<prefix>Onset``/``<prefix>Rise``。

    判定靠"升幅 + 上升沿之前那段低能量"，不用"区间峰值 − x dB"：范围里只要还夹着
    别的强段（比如后面的元音），后者就会漂，同一个音两次能差好几毫秒。
    """

    return [
        f"selectObject: {env_var}",
        f"{prefix}N = Get number of frames",
        f"{prefix}Rise = -1000",
        f"{prefix}Best = -1",
        f"for i from 1 to {prefix}N",
        "    ft = Get time from frame number: i",
        "    back = i - 3",
        "    if back >= 1 and ft >= tmin and ft <= tmax",
        "        bt = Get time from frame number: back",
        "        if bt >= tmin",
        "            v = Get value in frame: i",
        "            v0 = Get value in frame: back",
        "            later = v",
        "            j = i + 5",
        f"            if j <= {prefix}N",
        "                later = Get value in frame: j",
        "            endif",
        # 上升沿之后能量要守得住才算一次真瞬态：被硬切出来的爆音"来了就走"，
        # 用它当爆破会把 VOT 报大几十毫秒。
        f"            if later >= v - 10 and v - v0 > {prefix}Rise",
        f"                {prefix}Rise = v - v0",
        f"                {prefix}Best = i",
        "            endif",
        "        endif",
        "    endif",
        "endfor",
        f"{prefix}Onset = tmin",
        f"if {prefix}Best >= 1",
        f"    {prefix}Onset = Get time from frame number: {prefix}Best",
        f"    {prefix}Ref = Get value in frame: {prefix}Best",
        f"    k = {prefix}Best - 1",
        "    found = 0",
        "    while k >= 1 and found = 0",
        "        vt = Get time from frame number: k",
        "        v = Get value in frame: k",
        f"        if vt < tmin or v < {prefix}Ref - 8",
        "            found = 1",
        "        else",
        f"            {prefix}Onset = vt",
        "            k = k - 1",
        "        endif",
        "    endwhile",
        "endif",
    ]


def _vot_time(
    arguments: Mapping[str, Any], key: str, alias: str = ""
) -> float | None:
    """读一个可选的时刻参数；没给返回 ``None``，给了不是数字就报中文错。"""

    raw = arguments.get(key, arguments.get(alias, None) if alias else None)
    if raw in (None, ""):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        raise ToolError(f"参数 {key} 必须是数字（秒），收到：{raw!r}") from None


def _new_name(arguments: Mapping[str, Any], default: str) -> str:
    name = str(
        arguments.get("name", "") or arguments.get("new_name", "") or ""
    ).strip()
    return name or default


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    summary: str
    signature: str
    build: Callable[[Mapping[str, Any], ToolContext], str]

    def describe(self) -> str:
        return f"- {self.name}: {self.summary} 参数：{self.signature}"


@dataclass(frozen=True, slots=True)
class LocalEnvironment:
    """本地（不在 Praat 里跑）的工具需要的外部能力。

    由对话窗口提供：``execute(脚本)`` 把脚本投给**正在运行的 Praat**（见
    ``chat._send_script``），返回 ``(是否成功, 结果行, 失败说明)``；
    ``praat_executable`` 是批处理要用的 Praat，``runtime_directory`` 是
    ``ai/runtime``。
    """

    execute: Callable[[str], tuple[bool, list[str], str]]
    praat_executable: str
    runtime_directory: Path
    #: 用户是否已按下「停止」：批处理和多步流程在执行每一步前检查。
    cancelled: Callable[[], bool] | None = None


@dataclass(frozen=True, slots=True)
class LocalTool:
    """在前端（Python）里执行的工具：不需要渲染 Praat 脚本模板。

    `build` 那套模板适合「一句话 → 一条固定脚本」的查询；像「把现成 .praat
    脚本跑起来」（C3）这种要起批处理进程、还要读文件的工具，走这里。
    """

    name: str
    summary: str
    signature: str
    parameters: dict[str, Any]
    run: Callable[
        [Mapping[str, Any], ToolContext, LocalEnvironment],
        tuple[bool, list[str], str],
    ]

    def describe(self) -> str:
        return f"- {self.name}: {self.summary} 参数：{self.signature}"


#: 没有参数的工具（或没登记 schema 的工具）用这个。
EMPTY_PARAMETERS: dict[str, Any] = {"type": "object", "properties": {}, "required": []}


def _object_arg(description: str = "对象 id 或名称；不填就用当前选中的对象") -> dict[str, Any]:
    return {"type": ["integer", "string"], "description": description}


def _seconds_arg(description: str) -> dict[str, Any]:
    """时刻或时长的秒数 schema，仅接受数字。"""

    return {"type": "number", "description": description}


def _times_arg(description: str, maximum: int = 8) -> dict[str, Any]:
    """可以一次给多个时刻的 ``time``：数字或 ``"0.25,0.75"`` 这样的文本。

    上限写进 schema 描述里：真机实测里模型一次点了 9 个时刻被拒，白花一个请求——
    与其让它撞墙后重试，不如把「一次最多几个」直接摆在参数说明里。
    """

    return {"type": ["number", "string"],
            "description": f"{description}；一次最多 {maximum} 个时刻，要更多请拆成多次调用"}


def _number_arg(description: str) -> dict[str, Any]:
    return {"type": "number", "description": description}


def _integer_arg(description: str) -> dict[str, Any]:
    return {"type": "integer", "description": description}


def _formants_arg(description: str) -> dict[str, Any]:
    return {"description":description, "anyOf":[
        {"type":"integer", "minimum":1, "maximum":20},
        {"type":"string", "pattern":r"^\s*\d+(?:\s*[,，、;\s]\s*\d+)*\s*$"},
        {"type":"array", "items":{"type":"integer", "minimum":1, "maximum":20}, "minItems":1, "maxItems":6},
    ]}


def _text_arg(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description}


def _unit_arg() -> dict[str, Any]:
    return {
        "type": "string",
        "enum": ["hertz", "Bark"],
        "description": "单位，默认 hertz",
    }
