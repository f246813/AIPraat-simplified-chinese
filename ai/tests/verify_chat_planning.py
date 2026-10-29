"""手动回归：真机模型规划一批口语化请求，并把脚本放进真 Praat 跑一遍。

用法（仓库根目录，需要本地模型服务已在运行）：

    python ai/tests/verify_chat_planning.py

它衡量的是「用户这么说，前端选对工具、参数填对、脚本真能跑通」这件事，
所以既依赖模型也依赖 ``Praat.exe``。判定规则：

- FAIL：模型选了工具、脚本渲染成功，但 Praat 跑失败（这才是真问题）；
- WARN：模型自编脚本被安全检查拦下，或这条请求本来就不在工具集里；
- CHAT：模型判断不需要执行脚本（例如「你好」）。

加新工具或改提示词之后跑一遍，就能看出「准确率」是升了还是降了。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praat_ai import chat, tools   # noqa: E402
from praat_ai.config import load_config   # noqa: E402
from praat_ai.qwen import QwenClient   # noqa: E402


PROJECT = Path(__file__).resolve().parents[2]
PRAAT = PROJECT / "Praat.exe"

SOUND = 'Create Sound from formula: "tone", 1, 0, 1, 44100, ~ 0.5 * sin (2*pi*220*x)'
GRID = (
    'Create TextGrid: 0, 1, "words", "tone"\n'
    'Create Sound from formula: "tone", 1, 0, 1, 44100, ~ 0.5 * sin (2*pi*220*x)'
)
# 1 号是 TextGrid、2 号是选中的 Sound：模型必须先看清「当前选中」再动手。
CONTEXT = (
    "id\tclass\tname\tselected\n"
    "1\tTextGrid\tTextGrid grid\t0\n"
    "2\tSound\tSound tone\t1\n"
)

REQUESTS: tuple[str, ...] = (
    "这个声音的时长和采样率是多少",
    "把当前声音的前 0.3 秒截出来",
    "查询 0.25 秒和 0.75 秒处的基频",
    "这个声音的共振峰平均值是多少",
    "把当前声音复制成 参考音",
    "把当前声音另存到 @WAV@",
    "把当前声音降到 8000 Hz",
    "新建一个 2 秒 440 Hz 的正弦音",
    "把这个声音做成频谱图",
    "读取 @INPUT@",
    "看看当前声音的谐噪比",
    "查询 0.5 秒处的第一共振峰频率和带宽",
    "看看这个 TextGrid 有几个区间",
    "把这个 TextGrid 的第一层 0.3 到 0.6 秒标成 b",
    "在 TextGrid 的 0.7 秒处加一个边界",
    "提取这段语音的 vot",
    "爆破是 0.30 秒，浊音起始是 0.42 秒，帮我算 VOT",
    "在 0.25 到 0.5 秒之间找一下 VOT",
    # 多步（A3）：先截出一段新对象，再改名——第二步要作用在第一步刚建出来的对象上。
    "把当前声音的前 0.3 秒截出来，然后把它改名为 短音",
    # 借用的声学测量工具，看模型能不能挑对（真机跑一遍由 verify_chat_templates 守）。
    "这段声音的谱强调是多少",
    "算一下这个声音的高低频能量比",
    "这个声音的强度斜率是多少",
    # 工具拒绝后的重试（A4）：没说标哪一段，模型得自己补时间或者反问。
    "把当前这个 TextGrid 标成 a",
    "删除 1 号对象",
)

# 这些请求只能走 vot 工具或直接回答：v1 曾把「提取 vot」静默换成「第 1 共振峰带宽」。
NO_SUBSTITUTION = {
    "提取这段语音的 vot": {"vot", tools.CUSTOM_SCRIPT_TOOL},
}


def requests_for(directory: Path) -> tuple[str, ...]:
    return tuple(
        text.replace("@WAV@", (directory / "out" / "saved.wav").as_posix()).replace(
            "@INPUT@", (directory / "input.wav").as_posix()
        )
        for text in REQUESTS
    )


def run_script(script: str, directory: Path, name: str, prefix: str) -> tuple[bool, str]:
    state = directory / f"{name}.state"
    result = directory / f"{name}.tsv"
    body = script.replace("@STATE@", state.as_posix()).replace(
        "@RESULT@", result.as_posix()
    )
    if "@STATE@" not in script:
        body += f'\nappendFileLine: "{state.as_posix()}", "done"\n'
    script_path = directory / f"{name}.praat"
    script_path.write_text(prefix + "\n" + body, encoding="utf-8")
    completed = subprocess.run(
        [str(PRAAT), "--FULL-TRUST", "--run", str(script_path)],
        capture_output=True,
        timeout=120,
    )
    output = (completed.stdout + completed.stderr).decode("utf-16-le", "replace")
    output = output.replace("\x00", "").strip().replace("\n", " | ")
    ok = state.is_file()
    text = (
        result.read_text(encoding="utf-8").strip().replace("\n", " | ")
        if result.is_file()
        else ""
    )
    return ok, (text or output[:200])


def run_case_script(
    script: str, directory: Path, name: str, prefix: str
) -> tuple[bool, list[str], str]:
    """把脚本交给批处理 Praat 跑一遍，返回 ``(成功, 结果行, 失败说明)``。

    对话窗口里的执行是「投给正在运行的 Praat」，这里换成 `--run` 批处理：
    两者跑的是同一份脚本，命令行输出的差别在 §8.4 里记过（批处理看不到 live 对象）。
    """

    state = directory / f"{name}.state"
    result = directory / f"{name}.tsv"
    script_path = directory / f"{name}.praat"
    script_path.write_text(prefix + "\n" + script, encoding="utf-8")
    completed = subprocess.run(
        [str(PRAAT), "--FULL-TRUST", "--run", str(script_path)],
        capture_output=True,
        timeout=120,
    )
    output = (completed.stdout + completed.stderr).decode("utf-16-le", "replace")
    output = output.replace("\x00", "").strip().replace("\n", " | ")
    if not state.is_file():
        return False, [], output[:200] or "脚本没有写完 chat_state.txt"
    lines: list[str] = []
    if result.is_file():
        lines = [
            line.strip()
            for line in result.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    return True, lines, ""


def main() -> int:
    if not PRAAT.is_file():
        print(f"没有找到 {PRAAT}，请先构建 Praat.exe。")
        return 2
    config = load_config()
    client = QwenClient(config.qwen)
    print(f"模型：{config.qwen.model}")
    failures = 0
    warnings = 0
    with tempfile.TemporaryDirectory() as raw:
        directory = Path(raw)
        # 造一个真 wav 供「读取 wav」用例使用。
        run_script(
            f'Save as WAV file: "{(directory / "input.wav").as_posix()}"',
            directory,
            "make-input",
            SOUND,
        )
        requests = requests_for(directory)
        for index, text in enumerate(requests):
            name = f"case{index}"
            started = time.monotonic()
            context = tools.ToolContext(
                tools.parse_object_context(CONTEXT),
                directory / f"{name}.tsv",
                directory / f"{name}.state",
            )
            # 每一步都在一个新的批处理进程里跑，所以每次把**前面所有步骤**重放一遍
            # （前缀 + 已执行的脚本），这样后一步仍然能看到前一步留下的对象。
            replay: list[str] = []
            seen_lines = 0

            def execute(script: str) -> tuple[bool, list[str], str]:
                nonlocal seen_lines
                replay.append(script)
                for path in (context.result_path, context.state_path):
                    try:
                        path.unlink()
                    except FileNotFoundError:
                        pass
                ok, lines, failure = run_case_script(
                    "\n".join(replay), directory, name, GRID
                )
                # 重放会让前面几步的结果行再出现一次：只把**这一步新增的**回灌给模型。
                fresh = lines[seen_lines:]
                seen_lines = len(lines)
                return ok, fresh, failure

            try:
                outcome = chat.run_turn(
                    client,
                    user_text=text,
                    context_text=CONTEXT,
                    history=[],
                    context=context,
                    execute=execute,
                )
            except Exception as error:   # noqa: BLE001 - 回归脚本只关心结论
                print(f"FAIL 这一轮没跑完：{text} -> {error}")
                failures += 1
                continue
            tool_names = "/".join(step.tool for step in outcome.steps)
            tool_name = outcome.steps[0].tool if outcome.steps else ""
            if not outcome.used_tools:
                if text in NO_SUBSTITUTION:
                    print(f"CHAT {text} -> 没有对应工具时的回答：{outcome.reply}")
                else:
                    print(f"CHAT {text} -> 不执行脚本：{outcome.reply}")
                continue
            allowed = NO_SUBSTITUTION.get(text)
            if allowed is not None and tool_name not in allowed:
                print(
                    f"FAIL {text} -> 拿别的工具顶替了：{tool_name}"
                    f"（允许：{'/'.join(sorted(allowed))} 或直接回答）"
                )
                failures += 1
                continue
            for step in outcome.steps:
                if not step.ok:
                    print(f"     ! {step.observation}")
            ok = bool(outcome.results)
            detail = " | ".join(outcome.results) or outcome.failure
            elapsed = time.monotonic() - started
            tag = "OK  " if ok else "FAIL"
            kind = "自编脚本" if tool_name == tools.CUSTOM_SCRIPT_TOOL else tool_names
            print(f"{tag} {text}")
            print(
                f"     {kind}（{len(outcome.steps)} 步）"
                f"{json.dumps([step.arguments for step in outcome.steps], ensure_ascii=False)} "
                f"({elapsed:.1f}s) -> {detail[:170]}"
            )
            if outcome.reply:
                print(f"     回答：{outcome.reply[:120]}")
            if not ok:
                failures += 1
    print(f"\n失败 {failures}，警告 {warnings}，共 {len(REQUESTS)} 条请求")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
