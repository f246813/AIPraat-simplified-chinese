"""手动回归：把每个对话工具模板放进真实 Praat 批处理里跑一遍。

用法（仓库根目录，需要本机已经构建好 ``Praat.exe``）：

    python ai/tests/verify_chat_templates.py

它不依赖 unittest，也不会被 ``unittest discover`` 收集。每个用例都会先用
``prefix`` 造出对象，再渲染模板，最后检查脚本是否写出 ``chat_state.txt``、
结果文件里是否出现期望文字。改 ``ai/praat_ai/tools.py`` 的模板后应该跑一遍，
因为「脚本语法对不对」只有真正的 Praat 才能回答。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praat_ai import measures, tools   # noqa: E402


PROJECT = Path(__file__).resolve().parents[2]
PRAAT = PROJECT / "Praat.exe"

SOUND = 'Create Sound from formula: "tone", 1, 0, 1, 44100, ~ 0.5 * sin (2*pi*220*x)'
SILENT = 'Create Sound from formula: "silence", 1, 0, 1, 44100, ~ 0'
# 合成一个 VOT ≈ 30 ms 的音：0–0.30 闭音、0.30–0.33 爆破噪声、0.33 起浊音。
VOT_SOUND = (
    'Create Sound from formula: "vot", 1, 0, 1, 44100, '
    '~ if x < 0.3 then 0 else (if x < 0.33 then 0.3 * randomGauss (0, 1) '
    'else 0.5 * sin (2*pi*220*x) fi) fi'
)
HALF_SILENT = (
    'Create Sound from formula: "half", 1, 0, 1, 44100, '
    '~ if x < 0.4 then 0 else 0.5 * sin (2*pi*220*x) fi'
)
# 两个音素：0–0.30 闭音、0.30–0.33 爆破、0.33–0.60 浊音、0.60–0.75 静音、0.75 起又浊音。
TWO_PHONEME_SOUND = (
    'Create Sound from formula: "vot2", 1, 0, 1, 44100, '
    '~ if x < 0.3 then 0 else (if x < 0.33 then 0.3 * randomGauss (0, 1) '
    'else (if x < 0.6 then 0.5 * sin (2*pi*220*x) else (if x < 0.75 then 0 '
    'else 0.5 * sin (2*pi*220*x) fi) fi) fi) fi'
)

ONE_SOUND = "id\tclass\tname\tselected\n1\tSound\tSound tone\t1\n"
# 编辑器开着、并在波形上拖选了 0.25–0.5 秒（Praat 会多写两列）。
ONE_SOUND_SELECTED = (
    "id\tclass\tname\tselected\tsel_start\tsel_end\n"
    "1\tSound\tSound tone\t1\t0.250000\t0.500000\n"
)
TEXTGRID = 'Create TextGrid: 0, 1, "words", ""'
# 已经标好一个区间、并且 0.5 秒处已经有边界，用来验证重复插入会被跳过。
TEXTGRID_ANNOTATED = "\n".join(
    [
        TEXTGRID,
        "selectObject: 1",
        "Insert boundary: 1, 0.2",
        "Insert boundary: 1, 0.5",
        'Set interval text: 1, 2, "a"',
    ]
)
ONE_TEXTGRID = "id\tclass\tname\tselected\n1\tTextGrid\tTextGrid grid\t1\n"
# TextGrid 编辑器开着并拖选了 0.25–0.5 秒。
ONE_TEXTGRID_SELECTED = (
    "id\tclass\tname\tselected\tsel_start\tsel_end\n"
    "1\tTextGrid\tTextGrid grid\t1\t0.250000\t0.500000\n"
)
TWO_SOUNDS = (
    "id\tclass\tname\tselected\n"
    "1\tSound\tSound tone\t1\n"
    "2\tSound\tSound tone2\t0\n"
)
TWO_SOUND_PREFIX = SOUND + "\n" + SOUND.replace('"tone"', '"tone2"')


@dataclass
class Case:
    name: str
    arguments: dict[str, object]
    prefix: str
    context: str
    expect: str = ""
    custom_script: str = ""
    tool: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    #: 这个用例**必须失败**（脚本中途 exitScript）。此时不该写出 chat_state.txt，
    #: ``expect`` 改成在 Praat 的标准输出里找。见 run_case()。
    expect_error: bool = False


def _measure_cases() -> tuple[Case, ...]:
    """按 ``measures.tsv`` 生成用例：表里每个参数都要在真 Praat 里跑一遍。

    表里加一行参数，这里就自动多一个用例（``ai/tests/test_measures.py`` 守着
    「表里的参数都在这里出现过」）。交给专用工具算的那几行也走 ``measure``，
    顺手验了「委托给专用工具」这条路。
    """

    table = measures.load_table()
    cases: list[Case] = []
    for entry in table.entries:
        expect = entry.label
        if entry.kind == "dedicated":
            # 结果行的措辞由那个专用工具决定；这里只要求脚本跑到结尾（写出
            # chat_state.txt），也就是委托那条路没走错。
            expect = ""
        elif entry.kind == "editor":
            expect = "编辑器"
        cases.append(
            Case(
                f"measure-{entry.parameter}",
                {"parameter": entry.parameter},
                SOUND,
                ONE_SOUND,
                expect,
                tool="measure",
                tags=("measure",),
            )
        )
    # 一次测全部参数：共享派生对象、多条查询、临时对象清理全在一份脚本里。
    every = ",".join(entry.parameter for entry in table.queries())
    cases.append(
        Case(
            "measure-many",
            {"parameter": every},
            SOUND,
            ONE_SOUND,
            "平均基频",
            tool="measure",
            tags=("measure",),
        )
    )
    return tuple(cases)


CASES: tuple[Case, ...] = (
    Case("duration", {}, SOUND, ONE_SOUND, "总时长"),
    Case("object_info", {}, SOUND, ONE_SOUND, "采样率"),
    Case("formant_bandwidth", {"formant": 2}, SOUND, ONE_SOUND, "共振峰带宽"),
    Case(
        "formant_frequency",
        {"formant": "1,2", "time": 0.4},
        SOUND,
        ONE_SOUND,
        "第 2 共振峰（",
    ),
    Case("pitch", {"time": 0.5}, SOUND, ONE_SOUND, "基频"),
    Case(
        "pitch-multi-times",
        {"time": "0.25,0.75"},
        SOUND,
        ONE_SOUND,
        "0.750 秒处",
        tool="pitch",
    ),
    Case("pitch_statistics", {}, SOUND, ONE_SOUND, "基频统计"),
    # 逐帧的稳健取最高/最低值（防倍频误判）也要在真 Praat 里跑一遍：合成纯音上
    # 中位数就是 220 Hz，上限 330 Hz，不会触发「剔除」那句话，但循环本身必须能跑。
    Case(
        "pitch_statistics-robust-extremes",
        {"from": 0, "to": 1},
        SOUND,
        ONE_SOUND,
        "基频统计",
        tool="pitch_statistics",
    ),
    Case("intensity", {}, SOUND, ONE_SOUND, "强度"),
    Case("intensity_statistics", {}, SOUND, ONE_SOUND, "强度统计"),
    Case(
        "formant_statistics",
        {"formant": "1,2"},
        SOUND,
        ONE_SOUND,
        "第 2 共振峰平均",
    ),
    Case("harmonicity_statistics", {}, SOUND, ONE_SOUND, "谐噪比 HNR"),
    Case("spectrogram", {}, SOUND, ONE_SOUND, "已生成频谱图"),
    Case("textgrid_info", {}, TEXTGRID_ANNOTATED, ONE_TEXTGRID, "标签「a」"),
    Case(
        "textgrid_set_interval",
        {"start": 0.3, "end": 0.7, "label": "b"},
        TEXTGRID,
        ONE_TEXTGRID,
        "标成「b」",
    ),
    # from/to 是 start/end 的别名，云端 Prompt 就教模型用 from/to：必须和
    # start/end 走同一条路（2026-09-29 回归，见 ai/tests/test_range_aliases.py）。
    Case(
        "textgrid_set_interval-from-to",
        {"from": 0.3, "to": 0.7, "label": "b"},
        TEXTGRID,
        ONE_TEXTGRID,
        "标成「b」",
        tool="textgrid_set_interval",
    ),
    Case(
        "extract_part-from-to",
        {"from": 0.3, "to": 0.7},
        SOUND,
        ONE_SOUND,
        "已截取片段",
        tool="extract_part",
    ),
    Case(
        "textgrid_insert_boundary",
        {"time": 0.6},
        TEXTGRID,
        ONE_TEXTGRID,
        "插入边界",
    ),
    Case(
        "textgrid-insert-existing-boundary",
        {"time": 0.5},
        TEXTGRID_ANNOTATED,
        ONE_TEXTGRID,
        "已经有边界",
        tool="textgrid_insert_boundary",
    ),
    Case(
        "vot",
        {"burst": 0.3, "voicing": 0.42},
        TEXTGRID,
        ONE_TEXTGRID,
        "VOT = 0.1200 秒（120.0 毫秒）",
    ),
    Case(
        "vot-sound",
        {"burst": 0.3, "voicing": 0.42},
        SOUND,
        ONE_SOUND,
        "VOT = 0.1200 秒（120.0 毫秒）",
        tool="vot",
    ),
    Case(
        "vot-auto",
        {"from": 0.25, "to": 0.5},
        VOT_SOUND,
        ONE_SOUND,
        "VOT 估计值 = 0.03",
        tool="vot",
    ),
    Case(
        "vot-auto-no-range",
        {},
        VOT_SOUND,
        ONE_SOUND,
        "未指定范围",
        tool="vot",
    ),
    Case(
        "vot-auto-no-burst",
        {"from": 0.4, "to": 0.9},
        VOT_SOUND,
        ONE_SOUND,
        "已经是浊音",
        tool="vot",
    ),
    Case(
        "vot-auto-two-phonemes",
        {"from": 0.25, "to": 1.0},
        TWO_PHONEME_SOUND,
        ONE_SOUND,
        "第 2 段浊音",
        tool="vot",
    ),
    Case(
        "vot-auto-editor-selection",
        {},
        VOT_SOUND,
        ONE_SOUND_SELECTED,
        "按编辑器圈选 0.250–0.500 秒",
        tool="vot",
    ),
    # 选区不只 VOT 用：区间统计和截取片段取的是同一个选区，回话里都得注明出处。
    Case(
        "pitch_statistics-editor-selection",
        {},
        SOUND,
        ONE_SOUND_SELECTED,
        "按编辑器圈选 0.250–0.500 秒",
        tool="pitch_statistics",
    ),
    Case(
        "formant_statistics-editor-selection",
        {"formant": "1,2"},
        SOUND,
        ONE_SOUND_SELECTED,
        "按编辑器圈选 0.250–0.500 秒",
        tool="formant_statistics",
    ),
    Case(
        "extract_part-editor-selection",
        {},
        SOUND,
        ONE_SOUND_SELECTED,
        "按编辑器圈选 0.250–0.500 秒",
        tool="extract_part",
    ),
    Case(
        "pitch-editor-selection",
        {},
        SOUND,
        ONE_SOUND_SELECTED,
        "0.375 秒处",
        tool="pitch",
    ),
    Case(
        "textgrid_set_interval-editor-selection",
        {"label": "b"},
        TEXTGRID,
        ONE_TEXTGRID_SELECTED,
        "按编辑器圈选 0.250–0.500 秒",
        tool="textgrid_set_interval",
    ),
    Case("select_object", {}, SOUND, ONE_SOUND, "已选中"),
    # 借用 Chen Gafni 的现成测量脚本算出来的量（公式与出处见 tools.py 的 docstring）。
    Case("spectral_emphasis", {}, SOUND, ONE_SOUND, "谱强调"),
    Case("hl_ratio", {}, SOUND, ONE_SOUND, "H/L ="),
    Case("hammarberg_index", {}, SOUND, ONE_SOUND, "Hammarberg 指数"),
    Case("pitch_peak_latency", {}, SOUND, ONE_SOUND, "基频峰值延迟"),
    Case(
        "pitch_peak_latency-range",
        {"from": 0.2, "to": 0.8},
        SOUND,
        ONE_SOUND,
        "峰值",
        tool="pitch_peak_latency",
    ),
    Case("peak_to_average_ratio", {}, SOUND, ONE_SOUND, "峰值/有效值"),
    Case("intensity_slope", {}, SOUND, ONE_SOUND, "强度局部斜率"),
    Case(
        "intensity_slope-global",
        {"method": "global"},
        SOUND,
        ONE_SOUND,
        "强度整体斜率",
        tool="intensity_slope",
    ),
    Case(
        "intensity_slope-range",
        {"from": 0.2, "to": 0.8},
        SOUND,
        ONE_SOUND,
        "强度局部斜率",
        tool="intensity_slope",
    ),
    Case("rename_object", {"new_name": "改名测试"}, SOUND, ONE_SOUND, "已重命名"),
    Case("create_sound", {"duration": 0.5, "frequency": 220}, SOUND, ONE_SOUND, "已创建"),
    Case("duplicate_object", {}, SOUND, ONE_SOUND, "已复制为"),
    Case("resample_sound", {"rate": 16000}, SOUND, ONE_SOUND, "采样率"),
    Case("extract_part", {"start": 0.2, "end": 0.5}, SOUND, ONE_SOUND, "已截取片段"),
    Case("save_sound", {"path": "@WAV@"}, SOUND, ONE_SOUND, "已保存 WAV"),
    # 「读取文件」以前只能靠模型自编脚本，模型会把「读取 D:/in/a.wav」理解成
    # 「另存为」（方向反了）。现在读入有独立工具，这里验它在真 Praat 里能跑通。
    Case(
        "read_file",
        {"path": "@WAV@"},
        SOUND + f'\nSave as WAV file: "@WAV@"',
        ONE_SOUND,
        "已读入文件",
    ),
    # 文件不存在时给中文说明，而不是 Praat 那句英文错误。脚本在 fileReadable 判断
    # 失败时用 exitScript 直接结束（2026-09-29 起不再「写一条结果、写 done、当成功」），
    # 所以这里断言的是「脚本失败 + 中文错误出现在输出里 + 没写出 chat_state.txt」。
    Case(
        "read_file-missing",
        {"path": "@WAV@"},
        SOUND,
        ONE_SOUND,
        "找不到要读取的文件",
        tool="read_file",
        expect_error=True,
    ),
    Case(
        "concatenate_sounds",
        {"object": 1, "object2": 2},
        TWO_SOUND_PREFIX,
        TWO_SOUNDS,
        "拼成",
    ),
    Case(
        "remove_object",
        {"object": 2},
        TWO_SOUND_PREFIX,
        TWO_SOUNDS,
        "已删除",
    ),
    Case(
        "custom_script",
        {},
        SOUND,
        ONE_SOUND,
        "自定义改名完成",
        custom_script=(
            'selectObject: 1\nRename: "自定义改名"\n'
            'appendFileLine: "@RESULT@", "自定义改名完成"'
        ),
    ),
    # 模型在自定义脚本里写 writeInfoLine 会弹出 Praat Info 窗口，前端会把它改写成
    # appendFileLine；这里验改写后的脚本在真 Praat 里语法正确、内容也回来了。
    Case(
        "custom_script-info-rewrite",
        {},
        SOUND,
        ONE_SOUND,
        "Info 改写成功",
        custom_script=(
            'selectObject: 1\nwriteInfoLine: "Info 改写成功"\n'
            'appendInfo: "改写成 appendFileLine", "也照样回来"\n'
        ),
        tool="custom_script",
    ),
    # printline / print / echo 在 Praat 里写的是**字面文字**（见 sys/praat_script.cpp），
    # 前端整段抄成一个字符串参数；这里验改写后语法正确、内容也真的回来了。
    Case(
        "custom_script-literal-output",
        {},
        SOUND,
        ONE_SOUND,
        "printline 的字面输出",
        custom_script=(
            "selectObject: 1\n"
            "printline printline 的字面输出\n"
            "echo echo 也要留着\n"
        ),
        tool="custom_script",
    ),
    # 越界时间：应该被截断到对象末尾，而不是报错或者回一个 --undefined--。
    Case(
        "pitch-out-of-range",
        {"time": 5.0},
        SOUND,
        ONE_SOUND,
        "已按对象时长截断",
        tool="pitch",
    ),
    Case(
        "formant-out-of-range",
        {"formant": 2, "time": 5.0},
        SOUND,
        ONE_SOUND,
        "已按对象时长截断",
        tool="formant_bandwidth",
    ),
    Case(
        "intensity-out-of-range",
        {"time": 5.0},
        SOUND,
        ONE_SOUND,
        "已按对象时长截断",
        tool="intensity",
    ),
    # 静音段：Praat 返回 --undefined--，模板要换成中文说明。
    Case(
        "pitch-unvoiced",
        {"time": 0.2},
        HALF_SILENT,
        ONE_SOUND,
        "没有周期性声源",
        tool="pitch",
    ),
    Case(
        "pitch-statistics-silence",
        {},
        SILENT,
        ONE_SOUND,
        "没有周期性声源",
        tool="pitch_statistics",
    ),
) + _measure_cases()


def run_case(case: Case, directory: Path) -> tuple[bool, str]:
    result_path = directory / f"{case.name}-result.tsv"
    state_path = directory / f"{case.name}-state.txt"
    wav_path = directory / f"{case.name}.wav"
    arguments = {
        key: (
            str(value).replace("@WAV@", str(wav_path).replace("\\", "/"))
            if isinstance(value, str)
            else value
        )
        for key, value in case.arguments.items()
    }
    context = tools.ToolContext(
        tools.parse_object_context(case.context),
        result_path,
        state_path,
    )
    try:
        tool_name = case.tool or case.name
        if tool_name == tools.CUSTOM_SCRIPT_TOOL:
            rendered = tools.render(
                tool_name,
                arguments,
                context,
                custom_script=case.custom_script.replace(
                    "@RESULT@", str(result_path).replace("\\", "/")
                ),
            )
        else:
            rendered = tools.render(tool_name, arguments, context)
    except tools.ToolError as error:
        return False, f"渲染失败：{error}"
    # 前缀里也会用 @WAV@（例如先存一个 wav 再读进来）：和参数一样替换成临时路径。
    prefix = case.prefix.replace("@WAV@", str(wav_path).replace("\\", "/"))
    script = prefix + "\n" + rendered
    script_path = directory / f"{case.name}.praat"
    script_path.write_text(script, encoding="utf-8")
    completed = subprocess.run(
        [str(PRAAT), "--FULL-TRUST", "--run", str(script_path)],
        capture_output=True,
        timeout=120,
    )
    output = (completed.stdout + completed.stderr).decode("utf-16-le", "replace")
    output = output.replace("\x00", "").strip().replace("\n", " | ")
    if case.expect_error:
        # 预期中途失败：脚本不该写出完成标记，错误说明要在输出里。
        if state_path.is_file():
            return False, f"本来应该中途失败，却写出了 chat_state.txt：{output[:300]}"
        if case.expect and case.expect not in output:
            return False, f"输出里没有出现「{case.expect}」：{output[:300]}"
        return True, output[:200]
    if not state_path.is_file():
        return False, output[:400] or "脚本没有写完 chat_state.txt"
    text = (
        result_path.read_text(encoding="utf-8").strip()
        if result_path.is_file()
        else "(没有结果行)"
    )
    if case.expect and case.expect not in text:
        return False, f"结果里没有出现「{case.expect}」：{text}"
    return True, text.replace("\n", " | ")


def main() -> int:
    if not PRAAT.is_file():
        print(f"没有找到 {PRAAT}，请先构建 Praat.exe。")
        return 2
    failures = 0
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        for case in CASES:
            ok, detail = run_case(case, directory)
            print(f"{'OK  ' if ok else 'FAIL'} {case.name}: {detail}")
            if not ok:
                failures += 1
    print(f"\n{len(CASES) - failures}/{len(CASES)} 个模板用例通过")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
