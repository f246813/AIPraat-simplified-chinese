"""手动回归：把 Praat 插件放进真 Praat 里跑一遍。

用法（仓库根目录，需要本机已经构建好 ``Praat.exe``）::

    python ai/tests/verify_plugin.py

验证三件事：

1. ``setup.praat`` 里的菜单注册语法在真 Praat 里能过（菜单名、脚本名都不报错）；
2. ``praatAiMeasure.praat``（由参数表生成）在真 Praat 里能对一个声音算出全部
   参数，并把结果写进 Table「AI 测量结果」——数值和对话前端那条路一致；
3. 只测一个参数、只测一段（start/end）时也对。

编辑器那一条（``praatAiMeasureEditor.praat``）读的是编辑器圈选范围，批量模式
没有编辑器，所以它只能在真机上手点验证（见 ai/plugin/README.zh-CN.md）。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import build_plugin   # noqa: E402


PROJECT = Path(__file__).resolve().parents[2]
PRAAT = PROJECT / "Praat.exe"
PLUGIN = build_plugin.PLUGIN_SOURCE

TONE = 'Create Sound from formula: "tone", 1, 0, 1, 44100, ~ 0.5 * sin (2*pi*220*x)'
SILENCE = 'Create Sound from formula: "silence", 1, 0, 1, 44100, ~ 0'


def run_praat(script: str, directory: Path, name: str) -> tuple[int, str]:
    path = directory / f"{name}.praat"
    path.write_text(script, encoding="utf-8")
    return run_praat_file(path)


def run_praat_file(path: Path) -> tuple[int, str]:
    """跑一个真文件（菜单注册要看脚本所在目录，不能复制到临时目录再跑）。"""

    completed = subprocess.run(
        [str(PRAAT), "--FULL-TRUST", "--run", str(path)],
        capture_output=True,
        timeout=180,
    )
    output = (completed.stdout + completed.stderr).decode("utf-16-le", "replace")
    return completed.returncode, output.replace("\x00", "").strip()


def measure_script(out: Path, *, parameter: str, start: str, end: str) -> str:
    target = str(PLUGIN / "praatAiMeasure.praat").replace("\\", "/")
    out_text = str(out).replace("\\", "/")
    return "\n".join(
        [
            TONE,
            "selectObject: 1",
            f'runScript: "{target}", "{parameter}", {start}, {end}, 0',
            # 插件只会多建一个对象（结果表），所以它就是 2 号。
            "selectObject: 2",
            f'Save as tab-separated file: "{out_text}"',
        ]
    )


def read_table(path: Path) -> dict[str, str]:
    """把 save 出来的 TSV 读成 ``参数 -> 数值``（只取有两行的表）。"""

    rows: dict[str, str] = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 2:
        return rows
    header = lines[0].split("\t")
    index = {name: position for position, name in enumerate(header)}
    for line in lines[1:]:
        cells = line.split("\t")
        if len(cells) < len(header):
            continue
        rows[cells[index["参数"]]] = cells[index["数值"]]
    return rows


def main() -> int:
    if not PRAAT.is_file():
        print(f"没有找到 {PRAAT}，请先构建 Praat.exe。")
        return 2
    failures = 0
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)

        # 1) 菜单注册本身：批处理里跑一遍，语法或菜单名错了就在这里报出来。
        # Praat 会把插件目录当成脚本目录，菜单里的相对脚本名按这个目录解析。
        code, output = run_praat_file(PLUGIN / "setup.praat")
        ok = code == 0
        print(f"{'OK  ' if ok else 'FAIL'} setup.praat: {output[:200] or '菜单注册通过'}")
        failures += 0 if ok else 1

        # 2) 全部参数：一行一个，数值要和大语言模型那条路对得上。
        table_path = directory / "all.tsv"
        code, output = run_praat(
            measure_script(table_path, parameter="all", start="0", end="0"),
            directory,
            "measure-all",
        )
        rows = read_table(table_path) if table_path.is_file() else {}
        expected = {entry.parameter for entry in build_plugin.measures.load_table().queries()}
        missing = expected - set(rows)
        ok = code == 0 and not missing
        detail = f"{len(rows)}/{len(expected)} 个参数"
        if missing:
            detail += f"；缺：{'、'.join(sorted(missing))}"
        elif code != 0:
            detail += f"；{output[:200]}"
        else:
            anchors = {
                "mean_pitch": 220.0,
                "rms": 0.3536,
                "f1": 191.9,
            }
            wrong = []
            for name, wanted in anchors.items():
                try:
                    got = float(rows[name])
                except (KeyError, ValueError):
                    wrong.append(f"{name}=?")
                    continue
                if abs(got - wanted) > 0.05:
                    wrong.append(f"{name}={got}（期望 {wanted}）")
            if wrong:
                ok = False
                detail += "；锚点不符：" + "、".join(wrong)
            else:
                detail += "；锚点 220 Hz / 0.3536 Pa / 191.9 Hz 都对"
        print(f"{'OK  ' if ok else 'FAIL'} measure-all: {detail}")
        failures += 0 if ok else 1

        # 3) 单个参数 + 指定时间段。
        one_path = directory / "one.tsv"
        code, output = run_praat(
            measure_script(one_path, parameter="cpps", start="0.25", end="0.75"),
            directory,
            "measure-one",
        )
        rows = read_table(one_path) if one_path.is_file() else {}
        ok = code == 0 and list(rows) == ["cpps"] and abs(float(rows["cpps"]) - 20.62) < 0.05
        detail = f"cpps={rows.get('cpps', '?')}" if ok else f"{rows or output[:200]}"
        print(f"{'OK  ' if ok else 'FAIL'} measure-one: {detail}")
        failures += 0 if ok else 1

        # 4) 没声的段：数值留空、单位里写清楚，而不是报错或者写个 0 骗人。
        silent_path = directory / "silent.tsv"
        code, output = run_praat(
            "\n".join(
                [
                    SILENCE,
                    "selectObject: 1",
                    'runScript: "'
                    + str(PLUGIN / "praatAiMeasure.praat").replace("\\", "/")
                    + '", "mean_pitch", 0, 0, 0',
                    "selectObject: 2",
                    f'Save as tab-separated file: "{str(silent_path).replace(chr(92), "/")}"',
                ]
            ),
            directory,
            "measure-silent",
        )
        text = silent_path.read_text(encoding="utf-8") if silent_path.is_file() else ""
        ok = code == 0 and "这一段没法算" in text
        print(f"{'OK  ' if ok else 'FAIL'} measure-silent: {text.strip()[:160] or output[:160]}")
        failures += 0 if ok else 1

    total = 4
    print(f"\n{total - failures}/{total} 个插件用例通过")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
