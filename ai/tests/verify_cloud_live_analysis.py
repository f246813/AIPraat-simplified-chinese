"""真机验收（**会花一点点钱**）：真云端模型 + 真 Praat 批处理，跑一次整段语音分析。

该入口组合检查 API 请求、执行循环、对象上下文刷新与真实 Praat 多步脚本。
只有显式 --send 才发送请求；既有日志不代表当前配置已经通过。

它复现用户报故障的那句话（「分析整段语音，指出不足」），并逐项检查：

1. 每一步的脚本第一行是不是选中了正确的对象（不是拿 Harmonicity 当 Sound）；
2. 对 Harmonicity 之类的对象调用 ``To Pitch`` / ``To Intensity`` / ``To Formant`` 有没有
   在到达 Praat 之前就被挡住；
3. ``from``/``to``（整段范围）有没有真的进到脚本里，而不是被静默换成默认值；
4. 最终回答有没有区分观测/推导/一般知识，而不是一句「已完成」。

**用的是 Praat 自带的测试音频**（``test/fon/examples/sounds/aaaa02.wav``），不是你自己的录音，
所以不会把你的人声发到云端。音频也确实是被分析的对象（Read from file 读进隔离 Praat）。

用法（仓库根目录）::

    python ai/tests/verify_cloud_live_analysis.py           # 只打印计划，不发请求
    python ai/tests/verify_cloud_live_analysis.py --send    # 真发（会花掉极少量额度）

**它不会打印你的 API key。**
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PROJECT = TESTS_DIR.parents[1]
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

from praat_ai import chat, qwen, tools  # noqa: E402
from praat_ai.config import api_is_active, load_config  # noqa: E402

#: Praat 自带的 7.6 秒语音（不是用户录音）。
AUDIO = PROJECT / "test" / "fon" / "examples" / "sounds" / "aaaa02.wav"
PRAAT = PROJECT / "Praat-fixed.exe"

USER_TEXT = "分析整段语音，指出我的发音有哪些不足。"

#: 这些转换命令不能作用在 Harmonicity/Pitch/Intensity/Formant 上——出现就说明类型检查漏了。
FORBIDDEN_ON_DERIVED = (
    "To Pitch:",
    "To Intensity:",
    "To Formant (burg):",
    "To Harmonicity (cc):",
    "To Spectrum:",
)

#: 回答里应该有这些迹象（区分证据来源 / 真的给了结论）。
EVIDENCE_MARKERS = ("观测", "实测", "推导", "一般知识", "假设", "Hz", "dB", "建议")


def praat_object_list(context_path: Path) -> str:
    """取一次对象列表（模拟前端 ``chat.object_context()``）。"""

    return context_path.read_text(encoding="utf-8").strip()


def main() -> int:
    if "--send" not in sys.argv:
        print("这是真机验收，会向配置好的云端模型发若干次请求（每次约 6k token）。")
        print(f"音频：{AUDIO}（Praat 自带的测试音频，不是你的录音）")
        print("要真的发就加 --send：")
        print("  python ai/tests/verify_cloud_live_analysis.py --send")
        return 0

    if not PRAAT.is_file():
        print(f"没有找到 {PRAAT}")
        return 2
    if not AUDIO.is_file():
        print(f"没有找到测试音频 {AUDIO}")
        return 2

    config = load_config()
    if not api_is_active(config):
        print("当前不是 API 模式：先在「API 配置…」里启用云端模型。")
        return 2
    print(f"· 云端模型：{config.qwen.model}（provider={config.qwen.provider}）")
    print(f"· 知识模式：{getattr(config.qwen, 'knowledge_mode', '?')}")
    print(f"· 音频：{AUDIO.name}（Praat 自带测试音频）")

    failures = 0
    with tempfile.TemporaryDirectory(prefix="live-analysis-") as raw:
        work = Path(raw)
        context_path = work / "chat_context.tsv"
        result_path = work / "chat_result.tsv"
        state_path = work / "chat_state.txt"
        failure_path = work / "chat_failure.txt"

        # 1) 用真 Praat 把音频读进来，拿到真实的类名/时长/采样率。
        listing = work / "listing.txt"
        listing.write_text("", encoding="utf-8")
        probe = work / "load.praat"
        probe.write_text(
            f'Read from file: "{str(AUDIO).replace(chr(92), "/")}"\n'
            f'appendFileLine: "{str(listing).replace(chr(92), "/")}", "1\tSound\t" '
            '+ selected$() + "\t1"\n',
            encoding="utf-8",
            newline="\n",
        )
        subprocess.run(
            [str(PRAAT), "--no-pref-files", "--no-plugins", "--FULL-TRUST", "--run", str(probe)],
            capture_output=True,
            timeout=120,
        )
        row = listing.read_text(encoding="utf-8").strip().splitlines()
        # 必须是完整的 6 列（id/class/name/selected/sel_start/sel_end）：前端就是这么
        # 写、也是这么解析的，少一列会让 parse_object_context 把 sel_start 当缺失。
        fields = (row[0].split("\t") if row else ["1", "Sound", "Sound", "1"])[:4]
        while len(fields) < 4:
            fields.append("")
        context_path.write_text(
            "id\tclass\tname\tselected\tsel_start\tsel_end\n" + "\t".join(fields) + "\t\t\n",
            encoding="utf-8",
        )
        print(f"· 对象列表：{context_path.read_text(encoding='utf-8').strip()!r}")

        # 2) 假不了的真执行器：每个脚本都在真 Praat 批处理里跑，并且先把音频读进来。
        executions: list[dict[str, object]] = []

        def execute(script: str) -> tuple[bool, list[str], str]:
            result_path.unlink(missing_ok=True)
            state_path.unlink(missing_ok=True)
            failure_path.unlink(missing_ok=True)
            prelude = f'Read from file: "{str(AUDIO).replace(chr(92), "/")}"\n'
            target = work / f"step-{len(executions):02d}.praat"
            target.write_text(prelude + script, encoding="utf-8", newline="\n")
            started = time.monotonic()
            done = subprocess.run(
                [str(PRAAT), "--no-pref-files", "--no-plugins", "--FULL-TRUST", "--run", str(target)],
                capture_output=True,
                timeout=180,
            )
            elapsed = time.monotonic() - started
            console = (done.stdout + done.stderr).decode("utf-16-le", "replace").replace("\x00", "").strip()
            results = (
                [line.strip() for line in result_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                if result_path.is_file()
                else []
            )
            wrote_done = state_path.is_file() and state_path.read_text(encoding="utf-8").strip() == "done"
            error = console if (not wrote_done or done.returncode != 0) else ""
            executions.append(
                {
                    "script": prelude + script,
                    "results": results,
                    "returncode": done.returncode,
                    "done": wrote_done,
                    "console": console,
                    "seconds": round(elapsed, 2),
                }
            )
            if not wrote_done:
                return False, results, error or "脚本没有写出完成标记。"
            return True, results, ""

        context = tools.ToolContext(
            tools.parse_object_context(context_path.read_text(encoding="utf-8")),
            result_path,
            state_path,
        )
        client = qwen.QwenClient(config.qwen)

        print("\n===== 开始跑（真云端 + 真 Praat）=====\n")
        # 云端会超时、会断流，但这些都不该让「已经跑完的测量」消失——真机上
        # run_turn 已经会把收尾失败降级成实测摘要，这里再兜一层，保证轨迹一定打印出来。
        turn_error = ""
        try:
            outcome = chat.run_turn(
                client,
                user_text=USER_TEXT,
                context_text=context_path.read_text(encoding="utf-8"),
                history=[],
                context=context,
                execute=execute,
                refresh_context=lambda: praat_object_list(context_path),
                environment=tools.LocalEnvironment(
                    execute=execute,
                    praat_executable=str(PRAAT),
                    runtime_directory=work,
                ),
            )
        except Exception as error:  # noqa: BLE001 - 真机脚本要把失败也交代清楚
            turn_error = f"{type(error).__name__}: {error}"
            outcome = None

        # 3) 逐条打印执行轨迹。
        print("----- 执行轨迹 -----")
        for index, record in enumerate(executions):
            head = str(record["script"]).splitlines()[1] if len(str(record["script"]).splitlines()) > 1 else ""
            print(f"[{index}] {head}  ({record['seconds']}s, done={record['done']})")
            for line in record["results"]:
                print(f"      · {line}")
            if record["console"] and not record["done"]:
                print(f"      ! console: {str(record['console'])[:200]}")

        print("\n----- 模型最终回答 -----")
        if outcome is None:
            print(f"（这一轮抛异常了：{turn_error}）")
        else:
            print(outcome.reply)
            if outcome.notes:
                print("\n----- notes -----")
                for note in outcome.notes:
                    print(f"· {note}")

        # 4) 断言。
        print("\n----- 检查 -----")

        def check(label: str, ok: bool, detail: str = "") -> None:
            nonlocal failures
            failures += 0 if ok else 1
            print(f"{'OK  ' if ok else 'FAIL'} {label}{(' — ' + detail) if detail else ''}")

        scripts = [str(record["script"]) for record in executions]
        rows = tools.parse_object_context(context_path.read_text(encoding="utf-8"))
        class_of = {str(row.id): row.class_name for row in rows}

        # 4.1 派生转换只能作用在声音对象上。
        #
        # 关键：**不能按命令名判断**。`To Pitch:` / `To Intensity:` / `To Formant`
        # 出现在脚本里是正常的——模板对 Sound 就是先转换、统计、再 Remove 那个临时
        # 对象（`_build_pitch_statistics` 等）。真正要防的是「被转换的对象本身已经是
        # Harmonicity / Intensity / Pitch 这类派生对象」，也就是用户报的那个故障。
        # 所以这里看的是：紧跟在 selectObject 后面的转换命令，作用对象**是不是现场
        # 的声音对象**。头两次跑我按命令名判断，把 5 处正确脚本误报成了失败。
        converted_from = []  # (步骤, 对象id, 类名, 命令)
        for index, script in enumerate(scripts):
            lines = script.splitlines()
            for position, line in enumerate(lines):
                if not line.startswith("selectObject:"):
                    continue
                target = line.split(":", 1)[1].strip()
                if not target.isdigit():
                    continue  # 脚本自己的临时对象变量，不是现场对象
                following = lines[position + 1] if position + 1 < len(lines) else ""
                if any(command in following for command in FORBIDDEN_ON_DERIVED):
                    converted_from.append(
                        (index, target, class_of.get(target, "?"), following.strip())
                    )
        illegal = [item for item in converted_from if item[2] != "Sound"]
        check(
            "所有派生转换的对象都是 Sound（不是 Harmonicity 之类的派生对象）",
            bool(converted_from) and not illegal,
            f"{len(converted_from)} 处转换，非法：{illegal}",
        )

        # 4.2 所有选择都指向「现场存在的对象」或脚本自己新建的临时对象。
        selections = [
            line.split(":", 1)[1].strip()
            for script in scripts
            for line in script.splitlines()
            if line.startswith("selectObject:")
        ]
        unknown = [
            value
            for value in selections
            if not value.isdigit() and not value.endswith("Id") and not value.endswith("__")
        ]
        check(
            "所有 selectObject 都是现场对象 id 或脚本自己的临时对象变量",
            bool(selections) and not unknown,
            f"{len(selections)} 处选择，可疑：{unknown[:5]}",
        )

        # 4.2b 对现场 Sound 的测量必须真的选中了它（不是拿别的对象顶替）。
        sound_ids = sorted(
            object_id for object_id, kind in class_of.items() if kind == "Sound"
        )
        measured_scripts = [
            script for script in scripts
            if any(marker in script for marker in ("Get mean:", "Get value at time:", "Get maximum:"))
        ]
        check(
            "所有测量脚本都选中了现场的声音对象",
            bool(measured_scripts) and bool(sound_ids)
            and all(
                any(f"selectObject: {object_id}" in script for object_id in sound_ids)
                for script in measured_scripts
            ),
            f"{len(measured_scripts)} 个测量脚本，现场声音 id={sound_ids}",
        )

        # 4.2c 最高/最低基频必须走稳健取法（倍频误判防住了没有）。
        #
        # 注意区分：`maximum = Get maximum: tmin, tmax, "Parabolic"` 是**强度**的
        # 最大值（没有单位参数），完全合法——强度没有倍频问题。只有带 `"Hertz"`
        # 的那种是基频极值，才会被自相关的倍频误判劫持。
        pitch_extrema = (
            'maximum = Get maximum: tmin, tmax, "Hertz"',
            'minimum = Get minimum: tmin, tmax, "Hertz"',
            'maxtime = Get time of maximum: tmin, tmax, "Hertz"',
        )
        pitch_scripts = [script for script in scripts if "To Pitch:" in script]
        guarded = [script for script in pitch_scripts if "ceilingValue = median * 1.5" in script]
        check(
            "所有自建 Pitch 的测量脚本都带倍频误判限幅",
            bool(pitch_scripts) and len(guarded) == len(pitch_scripts),
            f"{len(guarded)}/{len(pitch_scripts)} 个含 To Pitch 的脚本带限幅",
        )
        leaky = [
            (index, pattern)
            for index, script in enumerate(scripts)
            for pattern in pitch_extrema
            if pattern in script
        ]
        check(
            "没有脚本把裸的基频极值当成结论输出",
            not leaky,
            f"{len(leaky)} 处：{leaky[:3]}",
        )

        # 4.3 整段请求不能静默用默认值：给了 from/to 就要真的进脚本。
        ranged = [script for script in scripts if "tmin =" in script or "t1 =" in script]
        check("至少做了一步带范围的测量", bool(ranged), f"{len(ranged)} 步带范围")

        # 4.4 没有执行失败（脚本都在真 Praat 里跑完了）。
        check(
            "所有脚本都在真 Praat 里跑完并写出完成标记",
            all(record["done"] for record in executions),
            f"{sum(1 for r in executions if r['done'])}/{len(executions)}",
        )

        # 4.5 确实拿到了实测结果。
        measured = [line for record in executions for line in record["results"]]
        check("拿到了实测结果行", bool(measured), f"{len(measured)} 行")

        # 4.6 最终回答不是空话，且区分了证据来源。
        reply = outcome.reply.strip() if outcome is not None else ""
        check(
            "最终回答不是「已完成」之类的空话",
            len(reply) >= 60 and reply not in {"已完成", "已完成。"},
            turn_error or f"长度 {len(reply)}",
        )
        hits = [marker for marker in EVIDENCE_MARKERS if marker in reply]
        check("回答里区分了证据来源/给了结论", len(hits) >= 2, "、".join(hits))

        trace = {
            "user_text": USER_TEXT,
            "object_classes": class_of,
            "turn_error": turn_error,
            "reply": reply,
            "executions": executions,
            "steps": (
                [
                    {
                        "tool": step.tool,
                        "ok": step.ok,
                        "arguments": step.arguments,
                        "observation": step.observation,
                    }
                    for step in outcome.steps
                ]
                if outcome is not None
                else []
            ),
            "failure": outcome.failure if outcome is not None else turn_error,
            "notes": outcome.notes if outcome is not None else [],
        }
        (work / "trace.json").write_text(
            json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        kept = PROJECT / "test-records" / "project" / "verify-cloud-live-analysis.json"
        kept.write_text((work / "trace.json").read_text(encoding="utf-8"), encoding="utf-8")
        print(f"\n· 完整轨迹写到 {kept}")
        print(f"· 失败项：{trace['failure'] or '（无）'}")

    total = 9
    print(f"\n{total - failures}/{total} 条通过")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
