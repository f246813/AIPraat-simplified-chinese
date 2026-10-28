"""自定义及社区 Praat 脚本的校验和执行包装。"""
from __future__ import annotations

import json
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence
from .. import external_script, measures

from ._common import (LocalEnvironment, ObjectRow, RANGE_END_KEYS, RANGE_START_KEYS, SOUND_CLASSES, ToolContext, ToolError, _assemble, _number, praat_path, quote)


FORBIDDEN_SCRIPT_PATTERNS = re.compile(
    r"\b(?:runSystem|runSubprocess|deleteFile|filedelete|createFolder|createDirectory"
    r"|quit|exit|exitScript|system|shell|execute|python|runScript)\b",
    re.IGNORECASE,
)


SINGLE_QUOTED_LITERAL = re.compile(r"'([^'\n]*)'")


# 模型偶尔会把 Python 代码当成 Praat 脚本填进 script 字段。这类脚本在 Praat 里
# 只会得到一句莫名其妙的英文报错，所以这里直接识别出来并给出中文提示。
PYTHON_SCRIPT_PATTERNS = re.compile(
    r"^\s*(?:import|from)\s+[A-Za-z_][\w.]*(?:\s+import\s+|\s*$)"
    r"|^\s*def\s+\w+\s*\("
    r"|^\s*(?:print|input|len|range)\s*\("
    r"|\bparselmouth\b|\bnumpy\b|np\.\w+|\bos\.\w+\(",
    re.MULTILINE,
)


INFO_LINE_PATTERN = re.compile(
    r"^(\s*)(appendInfoLine|writeInfoLine)\s*:(.*)$", re.IGNORECASE
)


INFO_VALUE_PATTERN = re.compile(
    r"^(\s*)(appendInfo|writeInfo)\s*:(.*)$", re.IGNORECASE
)


INFO_LITERAL_PATTERN = re.compile(
    r"^(\s*)(printline|print|echo)\b(.*)$", re.IGNORECASE
)


INFO_OTHER_PATTERN = re.compile(
    r"^\s*(?:appendInfo|writeInfo|printline|printtab|print|echo|clearinfo)\b[^\n]*$",
    re.IGNORECASE,
)


INFO_NOISE_PATTERN = re.compile(
    r"^\s*(?:printtab|clearinfo)\b[^\n]*$", re.IGNORECASE
)


#: 外部脚本的输出最多回灌几行给对话窗口（长报告别把上下文撑爆）。
EXTERNAL_OUTPUT_LINES = 40


def save_textgrid_script(context: ToolContext, row: ObjectRow, path: Path) -> str:
    """把 TextGrid 另存为文本文件（本地工具导出用；和模板一样带完成标记）。"""

    return _assemble(
        [f"selectObject: {row.id}", f"Save as text file: {quote(praat_path(path))}"],
        context,
    )


def _external_textgrid(
    arguments: Mapping[str, Any], context: ToolContext
) -> ObjectRow | None:
    """要一起交给外部脚本的 TextGrid：显式给的优先，否则用列表里唯一那个。"""

    requested = arguments.get("textgrid")
    if requested not in (None, ""):
        return context.resolve_by_class(
            requested, frozenset({"TextGrid"}), "textgrid 要给一个 TextGrid 对象"
        )
    candidates = [row for row in context.objects if row.class_name == "TextGrid"]
    return candidates[0] if len(candidates) == 1 else None


def _run_external_script(
    arguments: Mapping[str, Any],
    context: ToolContext,
    environment: LocalEnvironment,
) -> tuple[bool, list[str], str]:
    """跑一个现成的 ``.praat`` 脚本，把它的输出读回来。"""

    from .schema import render  # 延迟导入：打断注册表 <-> builder 的环
    cancelled = environment.cancelled
    if cancelled is not None and cancelled():
        raise ToolError("已取消：这个脚本没有开始跑。")
    raw_path = str(arguments.get("path", "") or "").strip()
    if not raw_path:
        raise ToolError("要跑哪个脚本？请给出 .praat 文件的完整路径（path）。")
    timeout = _number(arguments, "timeout", 60.0, 5.0, 900.0)
    target = Path(raw_path).expanduser()
    try:
        source = external_script.read_script(target)
        fields = external_script.parse_form(source)
        payload = external_script.run_arguments(fields)
    except external_script.ExternalScriptError as error:
        raise ToolError(str(error)) from error

    row = context.resolve_by_class(
        arguments.get("object"), SOUND_CLASSES, "外部脚本的输入需要声音对象"
    )
    work = environment.runtime_directory / "external" / uuid.uuid4().hex
    work.mkdir(parents=True, exist_ok=True)

    # 1) 让开着的 Praat 存一份声音副本（用户的对象一个都不动）。
    sound_path = work / "input.wav"
    ok, _results, failure = environment.execute(
        render("save_sound", {"path": str(sound_path), "object": row.id}, context)
    )
    if not ok or not sound_path.is_file():
        return False, [], f"没能把当前声音导出成 WAV：{failure or '文件没有生成'}"

    # 2) 有 TextGrid 就一起导出（标注类脚本都要它）。
    grid = _external_textgrid(arguments, context)
    grid_path: Path | None = None
    if grid is not None:
        candidate = work / "input.TextGrid"
        ok, _results, failure = environment.execute(
            save_textgrid_script(context, grid, candidate)
        )
        if ok and candidate.is_file():
            grid_path = candidate

    # 3) 批处理里跑现成脚本。
    wrapper = work / "wrapper.praat"
    wrapper.write_text(
        external_script.wrapper_script(
            sound_path=sound_path,
            grid_path=grid_path,
            target=target,
            arguments=payload,
            sound_name=row.name,
            grid_name=grid.name if grid is not None else "",
        ),
        encoding="utf-8",
        newline="\n",
    )
    before = external_script.folder_snapshot(target.parent)
    ok, output = external_script.run_batch(
        environment.praat_executable,
        wrapper,
        timeout=timeout,
        working_directory=work,
        cancelled=cancelled,
    )
    after = external_script.folder_snapshot(target.parent)

    if not ok:
        return (
            False,
            [],
            f"外部脚本 {target.name} 在批处理里没跑成：{output}\n"
            "（注意：批处理里看不到你当前的对象列表，脚本拿到的是导出的声音副本；"
            "需要编辑器或交互的脚本请直接在 Praat 里运行。）",
        )

    lines: list[str] = [
        f"外部脚本 {target.name} 跑完了（在批处理里跑的，没有动你开着的 Praat）。"
    ]
    if fields:
        lines.append(
            f"脚本的表单按它自己的默认值填了 {len(fields)} 个字段"
            "（批处理里不显示表单）："
            + "、".join(f"{field.label or field.kind}={field.default}" for field in fields)
        )
    lines.append(
        f"输入：{row.class_name}「{row.name}」的副本"
        + (f"，外加 TextGrid「{grid.name}」" if grid is not None else "")
    )
    if output:
        for line in output.splitlines()[:EXTERNAL_OUTPUT_LINES]:
            if line.strip():
                lines.append(f"脚本输出：{line.strip()}")
    else:
        lines.append("脚本没有打印任何结果（它可能把结果写进了文件或新的对象里）。")
    written = external_script.changed_files(before, after)
    if written:
        shown = "、".join(written[:10])
        lines.append(f"脚本在自己那个目录里新增/改动了 {len(written)} 个文件：{shown}")
    return True, lines, ""


def normalize_script(script: str) -> str:
    """Fix the most common model mistakes in free-form Praat scripts."""

    text = (script or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    text = text.strip()
    if not text:
        return ""
    # Praat uses single quotes for variable interpolation, so a model writing
    # 'Sound x' almost always means the string "Sound x".
    text = SINGLE_QUOTED_LITERAL.sub(lambda match: f'"{match.group(1)}"', text)
    return text.rstrip() + "\n"


def validate_script(script: str, *, maximum_length: int = 6000) -> str:
    normalized = normalize_script(script)
    if not normalized:
        raise ToolError("模型没有给出可执行的 Praat 脚本。")
    if len(normalized) > maximum_length:
        raise ToolError("模型生成的脚本过长。")
    if PYTHON_SCRIPT_PATTERNS.search(normalized):
        raise ToolError(
            "模型给出的是 Python 代码，不是 Praat 脚本。"
            "请换用内置工具描述这个操作，或让模型改用 Praat 英文命令。"
        )
    if FORBIDDEN_SCRIPT_PATTERNS.search(normalized):
        raise ToolError("模型生成的脚本包含被禁止的命令。")
    for line in normalized.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        if stripped.count('"') % 2 != 0:
            raise ToolError(f"脚本这一行的双引号不配对：{stripped}")
    return normalized


def praat_literal(text: str) -> str:
    """把一段字面文字变成 Praat 字符串字面量（双引号写成两个）。"""

    return '"' + text.replace('"', '""') + '"'


def validate_script_source_types(script: str, context: ToolContext) -> None:
    """Reject the reproduced To Pitch mismatch when its source is known."""
    selected_classes = [row.class_name for row in context.objects if row.selected]
    for line in script.splitlines():
        select_match = re.match(r'\s*selectObject\s*:\s*(.*?)\s*$', line, re.I)
        legacy_match = re.match(r'\s*select\s+([^\n]+)$', line, re.I)
        if select_match or legacy_match:
            literal = (select_match or legacy_match).group(1).strip().strip('"')
            try:
                selected_classes = [context.resolve_object(literal).class_name]
            except ToolError:
                selected_classes = None
        elif re.match(r'\s*(?:\w+\s*=\s*)?To Pitch(?:\s*\([^)]*\))?\s*:', line, re.I):
            if selected_classes is not None and any(cls != 'Sound' for cls in selected_classes):
                raise ToolError('To Pitch 转换需要 Sound；当前脚本选中对象类型不适用，未投递。')
            selected_classes = ['Pitch']
        elif re.match(r'\s*(?:\w+\s*=\s*)?(?:To |Create |Extract |Copy\s*:|Read )', line, re.I) or re.match(r'\s*(?:plusObject|minusObject|plus |minus )', line, re.I):
            # A newly produced or variable selection is not an original row.
            selected_classes = None


def neutralize_info_commands(script: str, result_path: Path | str) -> str:
    """将 Info 输出命令改写为结果文件输出，保留可见字面内容；printtab 与 clearinfo 用说明注释替代。"""

    target = quote(str(result_path))
    lines: list[str] = []
    for line in script.splitlines():
        match = INFO_LINE_PATTERN.match(line) or INFO_VALUE_PATTERN.match(line)
        if match:
            indent, _command, arguments = match.groups()
            payload = arguments.strip() or '""'
            lines.append(f"{indent}appendFileLine: {target}, {payload}")
            continue
        literal_match = INFO_LITERAL_PATTERN.match(line)
        if literal_match:
            indent, _command, rest = literal_match.groups()
            # Praat 这几条命令把关键字后面那**一个**空格当作分隔，再往后的都是内容。
            text = rest[1:] if rest.startswith(" ") else rest
            lines.append(f"{indent}appendFileLine: {target}, {praat_literal(text)}")
            continue
        if INFO_NOISE_PATTERN.match(line):
            lines.append(
                "# praat-ai: 已省略只影响 Info 窗口的命令：" f"{line.strip()}"
            )
            continue
        lines.append(line)
    return "\n".join(lines).rstrip("\n") + "\n"


#: 应用自己塞进去的作用域标记，不是模型参数。
_INTERNAL_ARGUMENTS = frozenset({"_whole_object"})


#: 各工具 build 函数真正会读、但不在自己 schema 里的同义写法（见 build 函数里的
#: ``_range_argument`` / ``_new_name`` / ``_query_times`` 等取值处）。
ALIAS_ARGUMENTS: dict[str, frozenset[str]] = {
    "extract_part": frozenset({"start", "end", "from", "to", "finish"}),
    "textgrid_set_interval": frozenset({"start", "end", "from", "to", "finish", "label", "text"}),
    "textgrid_insert_boundary": frozenset({"time", "at"}),
    "rename_object": frozenset({"name"}),
    "duplicate_object": frozenset({"new_name"}),
    "read_file": frozenset({"file"}),
    "save_sound": frozenset({"file"}),
    "measure": frozenset({"parameters", "start", "end", "finish"}),
    "pitch": frozenset({"times", "start", "end", "finish"}),
    "intensity": frozenset({"times", "start", "end", "finish"}),
    "formant_frequency": frozenset({"times", "formants"}),
    "formant_bandwidth": frozenset({"times", "formants"}),
    "concatenate_sounds": frozenset({"objects", "other"}),
    "vot": frozenset({"onset", "start", "end", "finish", "tier_number"}),
    "custom_script": frozenset({"script"}),
}


#: 这些工具按定义作用于整个对象：给出范围参数说明请求未被理解，
#: 静默按整段测量会丢掉用户明确的范围。
WHOLE_OBJECT_ONLY = frozenset({
    "duration", "object_info", "spectral_emphasis", "hl_ratio", "hammarberg_index",
    "spectrogram", "resample_sound", "save_sound",
})


def _reject_unsupported_arguments(name: str, arguments: dict[str, Any]) -> None:
    """执行前拒绝未知参数及工具不支持的范围字段，避免 build 忽略用户要求。"""

    from .schema import TOOL_PARAMETERS  # 延迟导入：打断注册表 <-> builder 的环
    schema = TOOL_PARAMETERS.get(name) or {}
    properties = set(schema.get("properties") or {})
    # 「只作用于整个对象」的工具先给可照做的说明，再谈参数名（否则用户看到的是
    # 「参数名不认识：from、to」，不知道该改成 extract_part）。
    if name in WHOLE_OBJECT_ONLY:
        given = [key for key in (*RANGE_START_KEYS, *RANGE_END_KEYS)
                 if arguments.get(key) not in (None, "")]
        if given:
            raise ToolError(
                f"{name} 只能对整个对象做，不接受 {'、'.join(given)}。"
                "要只看一段，请先用 extract_part 截取那段，再对它测量。")
    allowed = properties | set(ALIAS_ARGUMENTS.get(name, ())) | set(_INTERNAL_ARGUMENTS)
    # 有范围端点的工具一律收全部同义写法，免得模型写 finish 就被判成参数名不认识。
    if properties & {"from", "to", "start", "end"}:
        allowed |= set(RANGE_START_KEYS) | set(RANGE_END_KEYS)
    unknown = sorted(key for key in arguments if key not in allowed)
    if unknown:
        shown = "、".join(sorted(properties))
        raise ToolError(f"参数名不认识：{'、'.join(unknown)}。{name} 可用参数：{shown}。")


def describe_script(script: str, *, maximum_lines: int = 14) -> str:
    lines = [line for line in script.splitlines() if line.strip()]
    shown = lines[:maximum_lines]
    text = "\n".join(shown)
    if len(lines) > maximum_lines:
        text += f"\n…（共 {len(lines)} 行）"
    return text
