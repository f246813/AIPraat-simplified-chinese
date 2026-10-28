"""在独立 Praat 批处理进程里运行现成的社区脚本。"""

from __future__ import annotations

import os
import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


class ExternalScriptError(ValueError):
    """脚本没法包装（读不到、表单认不出来、跑失败）。"""


#: 表单字段类型 → 传参方式。
NUMERIC_KINDS = frozenset(
    {
        "real",
        "positive",
        "nonnegative",
        "integer",
        "natural",
        "natural0",
        "natural1",
        "boolean",
        "choice",
        "optionmenu",
    }
)
STRING_KINDS = frozenset(
    {"word", "sentence", "text", "infile", "outfile", "folder", "folderorfile"}
)
VECTOR_KINDS = frozenset(
    {
        "realvector",
        "nonnegativevector",
        "positivevector",
        "integervector",
        "naturalvector",
        "natural0vector",
        "natural1vector",
    }
)
#: 不占 `runScript` 参数的字段（Praat 自己也会跳过它们）。
IGNORED_KINDS = frozenset({"comment", "button", "option"})

#: 用户按了「停止」时批处理这边的说明。
CANCELLED_BATCH = "已取消：这个脚本不再等结果（批处理进程已经结束）。"

FORM_OPEN = re.compile(r"^\s*form\s*:\s*(?P<title>.*)$", re.IGNORECASE)
FORM_CLOSE = re.compile(r"^\s*endform\s*$", re.IGNORECASE)
FIELD = re.compile(r"^\s*(?P<kind>[A-Za-z][A-Za-z0-9]*)\s*:\s*(?P<rest>.*)$")


@dataclass(frozen=True, slots=True)
class FormField:
    kind: str
    label: str
    default: str


def split_arguments(text: str) -> list[str]:
    """按逗号切分表单字段的参数，逗号在引号里不算（``realvector`` 的格式里有逗号）。"""

    pieces: list[str] = []
    current: list[str] = []
    quoted = False
    index = 0
    while index < len(text):
        character = text[index]
        if character == '"':
            if quoted and index + 1 < len(text) and text[index + 1] == '"':
                current.append('""')
                index += 2
                continue
            quoted = not quoted
            current.append(character)
            index += 1
            continue
        if character == "," and not quoted:
            pieces.append("".join(current).strip())
            current = []
            index += 1
            continue
        current.append(character)
        index += 1
    pieces.append("".join(current).strip())
    return [piece for piece in pieces if piece != ""]


def unquote(text: str) -> str:
    stripped = text.strip()
    if len(stripped) >= 2 and stripped[0] == '"' and stripped[-1] == '"':
        return stripped[1:-1].replace('""', '"')
    return stripped


def quote(text: str) -> str:
    return '"' + str(text).replace("\\", "/").replace('"', '""') + '"'


def parse_form(script: str) -> tuple[FormField, ...]:
    """取脚本开头那个 ``form ... endform`` 的字段（没有表单就返回空）。

    只认第一个表单：Praat 的 ``form`` 必须在脚本最前面，而 ``runScript`` 用的
    参数就是按这个表单的字段顺序传的。
    """

    lines = (script or "").splitlines()
    start = None
    for number, line in enumerate(lines):
        if FORM_OPEN.match(line):
            start = number
            break
    if start is None:
        return ()
    fields: list[FormField] = []
    for line in lines[start + 1 :]:
        if FORM_CLOSE.match(line):
            break
        match = FIELD.match(line)
        if not match:
            continue
        kind = match.group("kind").casefold()
        parts = split_arguments(match.group("rest"))
        if kind in IGNORED_KINDS:
            continue
        if kind not in NUMERIC_KINDS | STRING_KINDS | VECTOR_KINDS:
            raise ExternalScriptError(
                f"这个脚本的表单里有前端不认识的字段类型「{kind}」，"
                "不能自动填默认值（可以改用手写脚本，或者在 Praat 里直接运行它）。"
            )
        label = unquote(parts[0]) if parts else ""
        default = unquote(parts[-1]) if len(parts) > 1 else ""
        fields.append(FormField(kind=kind, label=label, default=default))
    return tuple(fields)


def run_arguments(fields: tuple[FormField, ...]) -> list[str]:
    """把表单字段的默认值变成 ``runScript`` 的参数。"""

    arguments: list[str] = []
    for field in fields:
        if field.kind in NUMERIC_KINDS:
            value = field.default.strip()
            if not re.fullmatch(r"[-+]?[0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?", value):
                raise ExternalScriptError(
                    f"表单字段「{field.label}」的默认值 {field.default!r} 不是数字，"
                    "不知道该怎么传给脚本。"
                )
            arguments.append(value)
            continue
        if field.kind in VECTOR_KINDS:
            arguments.append(quote(field.default))
            continue
        arguments.append(quote(field.default))
    return arguments


def wrapper_script(
    *,
    sound_path: Path,
    grid_path: Path | None,
    target: Path,
    arguments: list[str],
    sound_name: str = "",
    grid_name: str = "",
) -> str:
    """生成包装脚本：读回导出的对象 → 跑现成脚本。

    读回来的对象会改回**原来的名字**：脚本作者按对象名找对象时（``select Sound
    "tone"``）也能找到，看到的名字也和在用户自己的 Praat 里一致。
    """

    lines = [
        "# 由 praat_ai 生成：在批处理里运行现成脚本的包装。",
        "# 现成脚本看不到用户开着的那个 Praat 的对象列表，这里的副本就是它的输入。",
        f"Read from file: {quote(str(sound_path))}",
        'soundId = selected ("Sound")',
        "if soundId = 0",
        '    exitScript: "导出的声音读不回来。"',
        "endif",
    ]
    if sound_name:
        lines.append(f"Rename: {quote(sound_name)}")
    if grid_path is not None:
        lines.extend(
            [
                f"Read from file: {quote(str(grid_path))}",
                'gridId = selected ("TextGrid")',
                f"Rename: {quote(grid_name)}" if grid_name else "# TextGrid 保持原文件名",
                "selectObject: soundId",
                "if gridId <> 0",
                "    plusObject: gridId",
                "endif",
            ]
        )
    call = [quote(str(target))]
    if arguments:
        call.extend(arguments)
    lines.append("runScript: " + ", ".join(call))
    lines.append('writeInfoLine: "praat_ai: 外部脚本执行完毕。"')
    return "\n".join(lines) + "\n"


def decode_console(data: bytes) -> str:
    """Praat 在 Windows 上把批处理输出写成 UTF-16LE 的控制台字节流。"""

    if not data:
        return ""
    if os.name == "nt":
        text = data.decode("utf-16-le", "replace")
    else:
        text = data.decode("utf-8", "replace")
    return text.replace("\x00", "").strip()


def run_batch(
    praat_executable: str,
    wrapper: Path,
    *,
    timeout: float = 60.0,
    working_directory: Path | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[bool, str]:
    """跑批处理，返回 ``(是否成功, 输出/错误文本)``。

    ``cancelled`` 是对话窗口那个「停止」按钮的回调：每 0.2 秒问一次，用户一按就
    把批处理进程结束掉——不然跑一个卡住的社区脚本要一直等到超时（C7）。
    """

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    if cancelled is not None and cancelled():
        return False, CANCELLED_BATCH
    try:
        process = subprocess.Popen(
            [praat_executable, "--FULL-TRUST", "--run", str(wrapper)],
            cwd=str(working_directory) if working_directory else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=creation_flags,
        )
    except OSError as error:
        return False, f"启动批处理 Praat 失败：{error}"
    deadline = time.monotonic() + timeout
    output = b""
    while True:
        try:
            output, _stderr = process.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancelled is not None and cancelled():
                _stop_process(process)
                return False, CANCELLED_BATCH
            if time.monotonic() >= deadline:
                _stop_process(process, kill=True)
                return False, (
                    f"脚本在 {timeout:.0f} 秒内没有跑完（它可能自己弹了对话框），"
                    "已经结束它。"
                )
    text = decode_console(output or b"")
    if process.returncode != 0:
        return False, text or f"Praat 以退出码 {process.returncode} 结束。"
    return True, text


def _stop_process(process, *, kill: bool = False) -> None:
    """结束一个还开着的批处理进程（先温和、再强杀）。"""

    try:
        if kill:
            process.kill()
        else:
            process.terminate()
    except OSError:
        return
    try:
        process.wait(timeout=5)
    except Exception:   # noqa: BLE001 - 已经要结束了，尽力而为
        try:
            process.kill()
        except Exception:   # noqa: BLE001
            pass


def read_script(path: Path, *, maximum_bytes: int = 2_000_000) -> str:
    """读脚本（限定大小，别把一个大文件塞进内存）。"""

    if not path.is_file():
        raise ExternalScriptError(f"找不到脚本文件：{path}")
    if path.suffix.casefold() not in {".praat", ".praat.txt", ".txt"}:
        raise ExternalScriptError(
            f"{path.name} 看起来不是 Praat 脚本（.praat）。"
        )
    data = path.read_bytes()
    if len(data) > maximum_bytes:
        raise ExternalScriptError("脚本文件太大（超过 2 MB）。")
    return data.decode("utf-8", "replace")


def folder_snapshot(folder: Path) -> dict[str, float]:
    """记住文件夹里有哪些文件（用来报告脚本新写了什么）。"""

    snapshot: dict[str, float] = {}
    try:
        entries = list(folder.iterdir())
    except OSError:
        return snapshot
    for entry in entries:
        try:
            if entry.is_file():
                snapshot[entry.name] = entry.stat().st_mtime
        except OSError:
            continue
    return snapshot


def changed_files(before: dict[str, float], after: dict[str, float]) -> list[str]:
    """两次快照之间新增或改动的文件（按名字排序）。"""

    names = [
        name
        for name, stamp in after.items()
        if name not in before or before[name] != stamp
    ]
    return sorted(names)
