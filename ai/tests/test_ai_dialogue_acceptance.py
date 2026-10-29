"""`verify_ai_dialogue_acceptance.py` 的离线回归：不发任何云端请求。

覆盖这几件事（都是验收脚本能不能信自己的前提）：

* dry-run 不发请求、不写账本、不写报告；
* ``--max-requests`` 超过 50 直接拒绝，默认 50；
* 账本额度用尽时在**发出去之前**就抛错，失败尝试也计数；
* 账本重复运行不重置（同一路径累计，额度只会收紧不会放宽）；
* 发送钩子在调用原 ``cloud_metrics.observe_request`` **之前**已把账本落盘，
  并且退出后两个指标钩子都恢复原样；
* parselmouth 执行器独立 smoke：``chat._execute_action`` → 真 Praat 脚本 →
  真结果文件 / 完成标记，测出来的 220 Hz、1 秒时长能对上；
* 没有对象时工具被拒、不会真跑 Praat；
* 泄漏检查与频率窗口检查本身不糊弄（占位符要判红，正常句子不判红）。

需要 praat-parselmouth 的那条在没有它的解释器上如实报 skipped。
"""

import asyncio
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import verify_ai_dialogue_acceptance as acceptance  # noqa: E402

from praat_ai import chat, cloud_metrics, tools  # noqa: E402

try:  # 只有执行器 smoke 需要它。
    import parselmouth
except ImportError:  # pragma: no cover - 3.13+ 的解释器按 requirements 不装
    parselmouth = None

try:
    import numpy  # noqa: F401
except ImportError:  # pragma: no cover
    numpy = None

HAS_PRAAT = parselmouth is not None and numpy is not None

TESTS_DIR = Path(__file__).resolve().parent
SCRIPT = TESTS_DIR / "verify_ai_dialogue_acceptance.py"


class DryRunTests(unittest.TestCase):
    def test_dry_run_never_sends_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "ledger.json"
            output = Path(raw) / "report.json"
            with patch("praat_ai.cloud_agent.create_model", side_effect=AssertionError("dry-run 不该建模型")):
                code = acceptance.main([
                    "--ledger", str(ledger), "--output", str(output),
                ])
            self.assertEqual(code, 0)
            self.assertFalse(ledger.exists(), "dry-run 不该创建账本")
            self.assertFalse(output.exists(), "dry-run 不该写报告")

    def test_dry_run_accepts_repeated_case_selection(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "ledger.json"
            code = acceptance.main([
                "--case", "pitch", "--case", "knowledge", "--ledger", str(ledger),
            ])
            self.assertEqual(code, 0)
            names = [spec.name for spec in acceptance.selected_cases(["pitch", "knowledge"])]
            # 顺序固定按 CASE_ORDER，与命令行给的先后无关。
            self.assertEqual(names, ["knowledge", "pitch"])

    def test_dry_run_reads_existing_ledger_without_resetting(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = Path(raw) / "ledger.json"
            ledger.write_text(json.dumps({"attempts_total": 7, "limit": 20}), encoding="utf-8")
            summary = acceptance.read_ledger_summary(ledger, 50)
            self.assertEqual(summary["attempts_total"], 7)
            self.assertEqual(summary["limit"], 20)          # 更紧的额度照旧生效
            self.assertEqual(summary["remaining"], 13)
            acceptance.main(["--ledger", str(ledger)])
            self.assertEqual(json.loads(ledger.read_text(encoding="utf-8"))["attempts_total"], 7)


class MaxRequestsTests(unittest.TestCase):
    def test_cap_is_fifty(self):
        self.assertEqual(acceptance.DEFAULT_MAX_REQUESTS, 50)
        self.assertEqual(acceptance.MAX_REQUESTS_CAP, 50)

    def test_above_fifty_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            acceptance.main(["--max-requests", "51"])
        self.assertNotEqual(caught.exception.code, 0)

    def test_ledger_refuses_limits_above_cap(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(ValueError):
                acceptance.SendLedger(Path(raw) / "ledger.json", 51)


class LedgerTests(unittest.TestCase):
    def test_limit_blocks_before_the_next_send(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            ledger = acceptance.SendLedger(path, 2)
            ledger.reserve("pitch")
            ledger.reserve("pitch")
            with self.assertRaises(acceptance.LedgerLimitExceeded):
                ledger.reserve("pitch")
            self.assertEqual(ledger.attempts_total, 2)
            self.assertEqual(ledger.remaining, 0)

    def test_counters_persist_across_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            first = acceptance.SendLedger(path, 5)
            first.reserve("pitch")
            first.reserve("derived")
            first.record_run({"cases": ["pitch", "derived"], "sends": 2})
            second = acceptance.SendLedger(path, 5)
            self.assertEqual(second.attempts_total, 2)
            self.assertEqual(second.data["case_counts"], {"pitch": 1, "derived": 1})
            self.assertEqual(len(second.data["runs"]), 1)
            second.reserve("pitch")
            self.assertEqual(acceptance.SendLedger(path, 5).attempts_total, 3)

    def test_ledger_limit_only_tightens(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            acceptance.SendLedger(path, 4).reserve("pitch")
            # 后来的 --max-requests 更大也不会把已有额度放宽。
            self.assertEqual(acceptance.SendLedger(path, 50).limit, 4)

    def test_failed_attempts_are_counted_conservatively(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            ledger = acceptance.SendLedger(path, 5)
            ledger.reserve("pitch")
            ledger.note_failure(1)       # 发出去但 HTTP 500：照样计一次尝试
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(stored["attempts_total"], 1)
            self.assertEqual(stored["failed_attempts"], 1)


class SendGuardTests(unittest.TestCase):
    def test_ledger_is_written_before_the_real_hook_runs(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            ledger = acceptance.SendLedger(path, 3)
            counters = acceptance.new_counters()
            seen = {}

            async def fake_original(request):
                # 原指标钩子被调用时，账本必须已经落盘（发前记账）。
                seen["attempts"] = json.loads(path.read_text(encoding="utf-8"))["attempts_total"]
                seen["called"] = True

            with patch.object(cloud_metrics, "observe_request", fake_original):
                with acceptance.install_send_guard(ledger, counters, "pitch"):
                    guarded = cloud_metrics.observe_request
                    self.assertIsNot(guarded, fake_original)
                    asyncio.run(guarded(object()))
                    self.assertEqual(seen["attempts"], 1)
                    self.assertTrue(seen["called"])
            self.assertEqual(counters["sends"], 1)
            self.assertIn(threading.current_thread().name, counters["threads"])

    def test_guard_restores_both_metric_hooks(self):
        before_request = cloud_metrics.observe_request
        before_response = cloud_metrics.observe_response
        with tempfile.TemporaryDirectory() as raw:
            ledger = acceptance.SendLedger(Path(raw) / "ledger.json", 3)
            with acceptance.install_send_guard(ledger, acceptance.new_counters(), "pitch"):
                self.assertIsNot(cloud_metrics.observe_request, before_request)
        self.assertIs(cloud_metrics.observe_request, before_request)
        self.assertIs(cloud_metrics.observe_response, before_response)

    def test_guard_blocks_when_ledger_is_exhausted(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            ledger = acceptance.SendLedger(path, 1)
            ledger.reserve("pitch")
            counters = acceptance.new_counters()
            called = []
            real = cloud_metrics.observe_request

            async def fake_original(request):
                called.append(request)

            with patch.object(cloud_metrics, "observe_request", fake_original):
                with acceptance.install_send_guard(ledger, counters, "pitch"):
                    with self.assertRaises(acceptance.LedgerLimitExceeded):
                        asyncio.run(cloud_metrics.observe_request(object()))
            self.assertEqual(called, [], "额度用尽时原钩子不该被调用（等于没发）")
            self.assertTrue(counters["limit_hit"])
            self.assertEqual(counters["sends"], 0)
            self.assertIs(cloud_metrics.observe_request, real)

    def test_guard_rejects_concurrent_sends(self):
        with tempfile.TemporaryDirectory() as raw:
            ledger = acceptance.SendLedger(Path(raw) / "ledger.json", 5)
            counters = acceptance.new_counters()
            counters["in_flight"] = 1          # 假装已经有一路在发

            async def fake_original(request):
                raise AssertionError("并发发送不该走到真发送")

            with patch.object(cloud_metrics, "observe_request", fake_original):
                with acceptance.install_send_guard(ledger, counters, "pitch"):
                    with self.assertRaises(acceptance.SendGuardViolation):
                        asyncio.run(cloud_metrics.observe_request(object()))
            self.assertEqual(counters["violation"], "检测到并发的云端发送")
            self.assertEqual(counters["sends"], 0)
            self.assertEqual(counters["in_flight"], 1)

    def test_create_model_uses_the_patched_hook_with_zero_sdk_retries(self):
        """真正的发送钩子就是被替换的那个函数：不是装饰性的 patch。"""

        from praat_ai.config import AppConfig

        config = AppConfig()
        config.api.enabled = True
        config.api.base_url = "http://127.0.0.1:9/v1"
        config.api.model = "acceptance-model"
        config.api.api_key = "sk-not-a-real-key"
        config.api.request_timeout_sec = 5

        async def fake_hook(request):
            return None

        with patch.object(cloud_metrics, "observe_request", fake_hook):
            model = acceptance.model_factory(config)
        hooks = model.client._client.event_hooks
        self.assertIs(hooks["request"][0], fake_hook)
        self.assertEqual(model.client.max_retries, 0)
        self.assertTrue(hooks["response"], "响应侧的指标钩子不能丢")


    def test_reused_client_hook_counts_the_live_case_not_the_install_case(self):
        """CloudRuntime 会缓存首个 case 的 client（连同它的 request hook）。

        第二、第三个 case 复用同一个 client 时，被缓存的那个 hook 函数对象必须按
        **当前** case 记账，不能一直写第一个 case 的名字。旧实现把 case 关在安装时
        的闭包里（``ledger.reserve(case)``），这里的第二次调用会被算到 "first" 上。
        """

        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "ledger.json"
            ledger = acceptance.SendLedger(path, 5)
            counters = acceptance.new_counters()

            async def original_hook(request):
                # 只当替身：不会发出任何 HTTP。
                return None

            with patch.object(cloud_metrics, "observe_request", original_hook):
                with acceptance.install_send_guard(ledger, counters, "first"):
                    cached_hook = cloud_metrics.observe_request
                    asyncio.run(cached_hook(object()))
                with acceptance.install_send_guard(ledger, counters, "second"):
                    # 第二轮安装不会改到缓存里的那个 hook 对象。
                    self.assertIsNot(cloud_metrics.observe_request, cached_hook)
                # 复用的 client 之后才真正发送：仍走第一轮捕获的 hook。
                asyncio.run(cached_hook(object()))
                asyncio.run(cached_hook(object()))

            stored = json.loads(path.read_text(encoding="utf-8"))
            counts = stored["case_counts"]
            self.assertEqual(counts.get("second"), 2, f"复用的 hook 应按当前 case 记账：{counts}")
            self.assertEqual(counts.get("first"), 1, f"第二个 case 不该再记到第一个 case 上：{counts}")
            self.assertEqual(stored["attempts_total"], 3)
            self.assertEqual(counters["sends"], 3)

class CheckHelperTests(unittest.TestCase):
    def test_hz_values_and_band(self):
        rows = ["基频统计（0.100–0.800 秒）：平均 220.000 Hz，最低 219.900 Hz，最高 220.400 Hz"]
        self.assertTrue(acceptance.values_in_band(rows, acceptance.PITCH_BAND))
        wrong = ["平均基频 = 440.00 Hz"]
        self.assertEqual(acceptance.values_in_band(wrong, acceptance.PITCH_BAND), [])
        self.assertEqual(acceptance.parse_hz_values(wrong), [440.0])

    def test_seconds_values(self):
        rows = ["tone220 总时长 = 1.000000 秒"]
        self.assertIn(1.0, acceptance.parse_seconds_values(rows))

    def test_leak_detection_flags_placeholder_and_script_traces(self):
        self.assertTrue(acceptance.find_leaks("基频 = {{calc:220*2}} Hz"))
        self.assertTrue(acceptance.find_leaks("我先跑 selectObject: 1"))
        self.assertTrue(acceptance.find_leaks("your api key is sk-live-1234", secrets=["sk-live-1234"]))

    def test_leak_detection_accepts_a_normal_sentence(self):
        self.assertEqual(acceptance.find_leaks("这段声音的平均基频是 220 Hz，属于典型的成人音高范围。"), [])

    def test_secrets_and_temp_paths_are_scrubbed(self):
        workdir = Path(tempfile.gettempdir()) / "acceptance-x"
        text = f"结果写在 {workdir}/chat_result.tsv，key=sk-test-9999"
        scrubbed = acceptance._scrub_text(text, workdir, ["sk-test-9999"])
        self.assertNotIn("sk-test-9999", scrubbed)
        self.assertNotIn(str(workdir), scrubbed)
        self.assertIn("<tmp>", scrubbed)

    def test_object_context_is_six_columns(self):
        text = acceptance.build_object_context(selection=(0.1, 0.5))
        rows = tools.parse_object_context(text)
        self.assertEqual([row.id for row in rows], [1, 2])
        self.assertEqual(rows[0].class_name, "Sound")
        self.assertTrue(rows[0].selected)
        self.assertEqual(rows[0].selection, (0.1, 0.5))
        self.assertIsNone(rows[1].selection)

    def test_whole_request_drops_stale_editor_selection(self):
        goal = acceptance.CASES["whole"].goal
        text = acceptance.build_object_context(selection=(0.1, 0.5))
        stripped = chat._request_context(text, goal)
        self.assertIsNone(tools.parse_object_context(stripped)[0].selection)
        # 其它案例的目标不会顺手清掉选区（这里只是确认清理是有条件的）。
        kept = chat._request_context(text, acceptance.CASES["pitch"].goal)
        self.assertEqual(tools.parse_object_context(kept)[0].selection, (0.1, 0.5))

    def test_case_registry(self):
        self.assertEqual(set(acceptance.CASES), set(acceptance.CASE_ORDER))
        self.assertEqual(set(acceptance.CASE_ESTIMATE), set(acceptance.CASE_ORDER))
        self.assertFalse(acceptance.CASES["knowledge"].allow_tools)
        self.assertFalse(acceptance.CASES["concept"].allow_tools)
        self.assertIsNotNone(acceptance.CASES["whole"].selection)
        self.assertTrue(acceptance.CASES["derived"].expects_two_objects)


class RoutingClassificationTests(unittest.TestCase):
    """案例目标必须落到它自己那条路上，否则案例等于没验。"""

    def test_knowledge_goals_are_dialogue(self):
        from praat_ai.dialogue_policy import dialogue_kind

        self.assertEqual(dialogue_kind(acceptance.CASES["knowledge"].goal), "concept")
        self.assertEqual(dialogue_kind(acceptance.CASES["concept"].goal), "concept")

    def test_measurement_goals_enter_the_tool_staircase(self):
        from praat_ai.dialogue_policy import dialogue_kind

        for name in ("pitch", "mixed", "whole", "derived", "no_data"):
            kind = dialogue_kind(acceptance.CASES[name].goal)
            self.assertIsNone(kind, f"{name} 不该被当成对话")
            self.assertEqual(acceptance.CASES[name].route, "tools")


@unittest.skipUnless(HAS_PRAAT, "需要 praat-parselmouth 与 numpy 才能真算")
class PraatExecutorSmokeTests(unittest.TestCase):
    """执行器独立 smoke：真脚本、真结果文件、真完成标记。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.workdir = Path(self.directory.name)
        self.executor = acceptance.PraatExecutor(self.workdir, acceptance.load_sounds())
        self.context = tools.ToolContext(
            tools.parse_object_context(acceptance.build_object_context()),
            self.workdir / "chat_result.tsv",
            self.workdir / "chat_state.txt",
        )
        self.attempts = []
        self.execute_action = acceptance.make_execute_action(
            self.executor, self.context, self.attempts)

    def test_mean_pitch_of_the_temporary_tone(self):
        step = self.execute_action("measure", {"object": 1, "parameters": ["mean_pitch"]}, 1)
        self.assertTrue(step.ok, step.observation)
        results = [row for execution in self.executor.executions for row in execution["results"]]
        self.assertTrue(results, "没有读到真实结果行")
        self.assertTrue(self.executor.executions[-1]["done"], "没有写出完成标记")
        self.assertTrue(
            acceptance.values_in_band(results, acceptance.PITCH_BAND),
            f"实测没落在 220 Hz 附近：{results}",
        )

    def test_pitch_statistics_honour_the_requested_range(self):
        step = self.execute_action(
            "pitch_statistics", {"object": 1, "from": 0.1, "to": 0.8}, 1)
        self.assertTrue(step.ok, step.observation)
        joined = "\n".join(step.results)
        self.assertIn("0.100", joined)
        self.assertIn("0.800", joined)
        self.assertNotIn("按编辑器圈选", joined)
        self.assertTrue(acceptance.values_in_band(step.results, acceptance.PITCH_BAND))

    def test_duration_of_the_temporary_tone(self):
        step = self.execute_action("duration", {"object": 1}, 1)
        self.assertTrue(step.ok, step.observation)
        durations = [value for value in acceptance.parse_seconds_values(step.results)
                     if abs(value - acceptance.EXPECTED_DURATION) <= acceptance.DURATION_TOLERANCE]
        self.assertTrue(durations, f"时长不对：{step.results}")

    def test_second_object_is_the_reference_tone(self):
        step = self.execute_action("pitch_statistics", {"object": 2}, 1)
        self.assertTrue(step.ok, step.observation)
        self.assertTrue(acceptance.values_in_band(step.results, acceptance.REFERENCE_BAND),
                        f"对象 2 不是 200 Hz：{step.results}")

    def test_results_file_is_read_back_not_faked(self):
        """结果来自实际文件，读取层接受 Praat 使用的 UTF-8 或带 BOM 的 UTF-16。"""
        self.execute_action("duration", {"object": 1}, 1)
        raw = (self.workdir / "chat_result.tsv").read_bytes()
        self.assertTrue(raw, "Praat 没有写入结果文件")
        text = acceptance.read_praat_text(self.workdir / "chat_result.tsv")
        self.assertIn("总时长", text)

    def test_tool_safety_still_applies(self):
        """不存在的对象/超范围的参数要在工具层被拒，不能真跑 Praat。"""
        before = len(self.executor.executions)
        step = self.execute_action("duration", {"object": 99}, 1)
        self.assertFalse(step.ok)
        self.assertIn("没有执行", step.observation)
        self.assertEqual(len(self.executor.executions), before, "被拒的工具不该真的执行")

    def test_empty_object_list_refuses_tools(self):
        workdir = Path(self.directory.name) / "empty"
        workdir.mkdir()
        executor = acceptance.PraatExecutor(workdir, [])
        context = tools.ToolContext(
            tools.parse_object_context(acceptance.build_object_context(empty=True)),
            workdir / "chat_result.tsv",
            workdir / "chat_state.txt",
        )
        attempts = []
        execute_action = acceptance.make_execute_action(executor, context, attempts)
        step = execute_action("pitch_statistics", {}, 1)
        self.assertFalse(step.ok)
        self.assertIn("没有执行", step.observation)
        self.assertEqual(executor.executions, [], "没有对象时绝不能真跑 Praat")


class ScriptCliTests(unittest.TestCase):
    """脚本作为命令行工具也要能跑（--help 与非法额度）。"""

    def run_script(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            capture_output=True, text=True, encoding="utf-8", timeout=180,
        )

    def test_help_lists_the_documented_flags(self):
        result = self.run_script("--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for flag in ("--send", "--output", "--ledger", "--max-requests", "--case"):
            self.assertIn(flag, result.stdout)

    def test_over_cap_exits_non_zero(self):
        result = self.run_script("--max-requests", "80")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("50", result.stderr)


if __name__ == "__main__":
    unittest.main()
