"""把声学测量参数表打包成一个 Praat 原生插件。

出处：chengafni/praat 的插件都是 ``plugin_*/setup.praat`` + 若干 ``.praat``
脚本，装在 Praat 的 preferences 目录里就会被 Praat 自己的启动流程扫到
（``sys/praat.cpp`` 里 ``Melder_preferencesFolder7()`` 下 ``plugin_*``）。
菜单命令用 ``Add menu command`` / ``Add action command`` 注册，**不用重新编译
Praat.exe**，所以这条分发路径连「没装我们这个 fork 的用户」也能用。

这里只生成一个文件：``praatAiMeasure.praat``（参数表里每个参数一段）。其余
（``setup.praat`` / 编辑器包装 / 对话窗口启动器模板）是手写的，直接复制过去。
参数表改了就跑一次这个脚本，``--check`` 用在测试里守着「生成物和表一致」。

用法::

    python ai/tools/build_plugin.py                 # 重新生成 plugin 里的脚本
    python ai/tools/build_plugin.py --check         # 只检查是否和表一致（不改文件）
    python ai/tools/build_plugin.py --install       # 装到 %APPDATA%\\Praat\\plugin_praat_ai
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praat_ai import measures   # noqa: E402


#: 仓库里的插件源码目录；安装时整个文件夹复制成 ``plugin_praat_ai``。
PLUGIN_SOURCE = Path(__file__).resolve().parents[1] / "plugin" / "praat_ai"
PLUGIN_NAME = "plugin_praat_ai"
GENERATED_SCRIPT = "praatAiMeasure.praat"
CHAT_TEMPLATE = "praatAiChat.praat.in"
CHAT_SCRIPT = "praatAiChat.praat"

#: 装到哪个目录（Praat 的 preferences 目录，和 ``Melder_preferencesFolder7()`` 对应）。
def preferences_folder() -> Path:
    appdata = os.getenv("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "Praat"
    home = os.getenv("USERPROFILE", "").strip()
    if home:
        return Path(home) / "AppData" / "Roaming" / "Praat"
    return Path.home() / "Praat"


def praat_literal(text: str) -> str:
    """Praat 字符串字面量：双引号写两遍、反斜杠换成斜杠（和 tools.quote 一致）。"""

    return '"' + text.replace("\\", "/").replace('"', '""') + '"'


def _expand(command: str, table: measures.Table) -> str:
    """把 ``{pitch_floor}`` 这类占位符换成表里的默认值（插件里不放可调设置）。"""

    text = command
    for key, setting in table.settings.items():
        value = setting.value if setting.is_integer else f"{float(setting.value):.6f}"
        text = text.replace("{" + key + "}", value)
    text = text.replace("{unit}", '"hertz"').replace("{unit_text}", "Hz")
    return text


#: 会话里的模板用 ``tmin``/``tmax`` 表示时间范围（``from``/``to``/``end`` 是 Praat
#: 保留字），插件脚本里这两段范围是对话框字段 ``Start (s)``/``End (s)``（Praat 会把
#: 标签变成 start/end 两个变量），所以生成插件时把名字换掉。
_RANGE_TOKENS = ((re.compile(r"\btmin\b"), "start"), (re.compile(r"\btmax\b"), "end"))


def _rename_range(text: str) -> str:
    for pattern, replacement in _RANGE_TOKENS:
        text = pattern.sub(replacement, text)
    return text


def _needed_derivations(table: measures.Table, entry: measures.Entry) -> list[str]:
    """这一段要用到的派生对象，按表里的依赖顺序。"""

    wanted = {
        key
        for key in measures.source_keys(entry.source)
        if key != "sound"
    }
    for key in list(wanted):
        source = table.derivations[key].source.strip()
        if source and source != "Sound":
            wanted.add(source)
    return [key for key in table.derivations if key in wanted]


def _block(table: measures.Table, entry: measures.Entry) -> list[str]:
    """一个参数一段：建中间对象 → 查询 → 写一行结果 → 清理。"""

    unit = _expand(entry.unit, table)
    lines = [
        f"# ---- {entry.parameter}：{entry.label} ----",
        f'if all = 1 or parameter$ = "{entry.parameter}"',
    ]
    variables = {"sound": "soundId"}
    for key in _needed_derivations(table, entry):
        lines.append("    selectObject: soundId")
        lines.append(
            f"    {key}Id = {_rename_range(_expand(table.derivations[key].command, table))}"
        )
        variables[key] = f"{key}Id"
    for index, key in enumerate(measures.source_keys(entry.source)):
        verb = "selectObject" if index == 0 else "plusObject"
        lines.append(f"    {verb}: {variables[key]}")
    statements = [
        piece.strip()
        for piece in _rename_range(_expand(entry.command, table)).split(";")
        if piece.strip()
    ]
    if not any("=" in statement for statement in statements):
        statements[0] = f"value = {statements[0]}"
    lines.extend(f"    {statement}" for statement in statements)
    lines.extend(
        [
            "    selectObject: tableId",
            "    Append row",
            "    row = Get number of rows",
            f'    Set string value: row, "参数", "{entry.parameter}"',
            f'    Set string value: row, "说明", "{entry.label}"',
            '    Set numeric value: row, "起点", start',
            '    Set numeric value: row, "终点", end',
            "    if value = undefined",
            f'        Set string value: row, "单位", "{unit}（这一段没法算）"',
            "    else",
            '        Set numeric value: row, "数值", value',
            f'        Set string value: row, "单位", "{unit}"',
            "    endif",
        ]
    )
    for key in reversed(_needed_derivations(table, entry)):
        lines.extend(
            [
                "    if keep_temp = 0",
                f"        selectObject: {key}Id",
                "        Remove",
                "    endif",
            ]
        )
    lines.append("endif")
    lines.append("")
    return lines


def render_measure_script(table: measures.Table | None = None) -> str:
    """生成 ``praatAiMeasure.praat``：参数表里每个参数一段。"""

    table = table or measures.load_table()
    lines = [
        "# " + "=" * 76,
        "# AI 声学测量（对象列表版）",
        "#",
        f"# 这个文件由 ai/tools/build_plugin.py 从 ai/praat_ai/measures.tsv 生成，",
        "# 不要手改；要加参数请改表再跑一次生成脚本。",
        "#",
        "# 用法：选中一个 Sound，从 Query（查询）菜单运行「AI 声学测量...」；",
        "#       在声音/TextGrid 编辑器里用「AI 声学测量（圈选段）...」也行。",
        f"# 结果：对象列表里多一个 Table「AI 测量结果」，每个参数一行（共 {len(table.queries())} 个参数）。",
        "# " + "=" * 76,
        "",
        'form: "AI 声学测量"',
        '    comment: "用 measures.tsv 的默认分析设置（基频 75-600 Hz、共振峰 5/5500 Hz 等）。"',
        '    comment: "Parameter 填 all，或者表里的参数名（例如 mean_pitch）。"',
        '    word: "Parameter", "all"',
        '    real: "Start (s)", "0"',
        '    real: "End (s)", "0"',
        '    comment: "Start 和 End 都填 0 就是整个对象。"',
        # 表单字段的标签就是变量名（空格变下划线）：这里要的是 keep_temp。
        '    boolean: "Keep temp", 0',
        "endform",
        "",
        "soundId = selected (\"Sound\")",
        "if soundId = 0",
        '    exitScript: "请先在对象列表里选中一个 Sound 对象。"',
        "endif",
        "selectObject: soundId",
        "duration = Get total duration",
        "if start <= 0 and end <= 0",
        "    start = 0",
        "    end = duration",
        "endif",
        "if start < 0",
        "    start = 0",
        "endif",
        "if end > duration",
        "    end = duration",
        "endif",
        "if end <= start",
        "    start = 0",
        "    end = duration",
        "endif",
        'if parameter$ = "all"',
        "    all = 1",
        "else",
        "    all = 0",
        "endif",
        "",
        'Create Table with column names: "AI 测量结果", 0, "参数 说明 起点 终点 数值 单位"',
        'tableId = selected ("Table")',
        "",
    ]
    for entry in table.queries():
        lines.extend(_block(table, entry))
    lines.extend(
        [
            "# 收尾：结果表留着一行行看，用户的 Sound 保持选中。",
            "selectObject: tableId",
            "rows = Get number of rows",
            "if rows = 0",
            "    Append row",
            '    Set string value: 1, "参数", "（没有匹配的参数）"',
            '    Set string value: 1, "说明", parameter$ + " 不在参数表里，请看 ai/praat_ai/measures.tsv。"',
            "endif",
            "selectObject: soundId",
            "plusObject: tableId",
            "",
        ]
    )
    return "\n".join(lines)


def render_chat_script(project_directory: Path, python_executable: str) -> str:
    """生成 ``praatAiChat.praat``：从 Praat 菜单启动本地 AI 对话窗口。"""

    launcher = project_directory / "ai" / "start_ai_chat.py"
    interpreter = _pythonw(preferred=python_executable)
    # 不带 cmd /c start：start_ai_chat.py 自己就是「起进程立刻返回」的启动器，
    # runSystem 只会等这个启动器（几十毫秒），不会等对话窗口关掉。
    command = f'"{interpreter}" "{launcher}"'
    template = (PLUGIN_SOURCE / CHAT_TEMPLATE).read_text(encoding="utf-8")
    return template.replace("@CHAT_COMMAND@", praat_literal(command)).replace(
        "@PROJECT_DIRECTORY@", praat_literal(str(project_directory))
    )


def _pythonw(preferred: str | None = None) -> str:
    """优先用 pythonw.exe 启动前端，免得弹出一个控制台窗口。"""

    candidate = Path(preferred) if preferred else Path(sys.executable)
    if candidate.name.casefold() == "python.exe":
        windowed = candidate.with_name("pythonw.exe")
        if windowed.is_file():
            return str(windowed)
    return str(candidate)


def measure_script_path(directory: Path | None = None) -> Path:
    return (directory or PLUGIN_SOURCE) / GENERATED_SCRIPT


def write_generated(directory: Path | None = None) -> Path:
    """把生成物写到插件目录，返回写好的文件路径。"""

    target = measure_script_path(directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_measure_script(), encoding="utf-8", newline="\n")
    return target


def check_generated(directory: Path | None = None) -> str:
    """检查生成物和表一致；不一致时返回一段中文说明（一致返回空串）。"""

    target = measure_script_path(directory)
    if not target.is_file():
        return f"{target} 不存在，请运行 python ai/tools/build_plugin.py"
    expected = render_measure_script()
    actual = target.read_text(encoding="utf-8")
    if actual == expected:
        return ""
    expected_lines = expected.splitlines()
    actual_lines = actual.splitlines()
    for number, (want, have) in enumerate(zip(expected_lines, actual_lines), 1):
        if want != have:
            return (
                f"{target.name} 第 {number} 行和参数表不一致：\n"
                f"  表里应该是：{want}\n"
                f"  文件里现在是：{have}"
            )
    return (
        f"{target.name} 比表里生成的多了或少了 "
        f"{abs(len(expected_lines) - len(actual_lines))} 行"
    )


def install(
    *,
    project_directory: Path | None = None,
    python_executable: str | None = None,
    destination: Path | None = None,
    quiet: bool = False,
) -> Path:
    """把插件装进 Praat 的 preferences 目录（``plugin_praat_ai``）。"""

    project = Path(project_directory or Path(__file__).resolve().parents[2])
    python = python_executable or sys.executable
    target = Path(destination) if destination else preferences_folder() / PLUGIN_NAME
    target.mkdir(parents=True, exist_ok=True)
    for source in sorted(PLUGIN_SOURCE.iterdir()):
        if not source.is_file() or source.name == CHAT_TEMPLATE:
            continue
        shutil.copyfile(source, target / source.name)
    (target / GENERATED_SCRIPT).write_text(
        render_measure_script(), encoding="utf-8", newline="\n"
    )
    (target / CHAT_SCRIPT).write_text(
        render_chat_script(project, python), encoding="utf-8", newline="\n"
    )
    if not quiet:
        print(f"已安装到 {target}")
        print(f"  对话窗口命令：\"{_pythonw(python)}\" \"{project / 'ai' / 'start_ai_chat.py'}\"")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="生成/安装 Praat 插件")
    parser.add_argument("--check", action="store_true", help="只检查生成物是否和参数表一致")
    parser.add_argument("--install", action="store_true", help="装到 Praat 的 preferences 目录")
    parser.add_argument("--project", type=Path, help="项目目录（对话窗口用，默认仓库根）")
    parser.add_argument("--python", dest="python_executable", help="Python 解释器路径")
    parser.add_argument("--destination", type=Path, help="装到哪个目录（测试用）")
    arguments = parser.parse_args(argv)
    if arguments.check:
        problem = check_generated()
        if problem:
            print(problem)
            return 1
        print(f"{GENERATED_SCRIPT} 和参数表一致")
        return 0
    if arguments.install:
        install(
            project_directory=arguments.project,
            python_executable=arguments.python_executable,
            destination=arguments.destination,
        )
        return 0
    target = write_generated()
    print(f"已生成 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
