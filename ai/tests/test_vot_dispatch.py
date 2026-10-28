"""Regression: a VOT measurement must never finish with object metadata."""

import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from praat_ai import chat, qwen, tools
from test_agent_loop import FakeClient, Recorder, answer, tool_call


CONTEXT = (
    "id\tclass\tname\tselected\tsel_start\tsel_end\n"
    "7\tSound\ttone\t1\t0.25\t0.5\n"
)
RESULT = "VOT 估计值 = 0.0320 秒（32.0 毫秒）：爆破 0.300 秒 → 浊音起始 0.332 秒"


def run(text, client=None, execute=None, **kwargs):
    client = client or FakeClient([tool_call("object_info", {"object": 7}), answer("根据您的需求，我需要使用")])
    execute = execute or Recorder([(True, [RESULT], "")])
    context_text = kwargs.pop("context_text", CONTEXT)
    outcome = chat.run_turn(
        client, user_text=text, context_text=context_text, history=[],
        context=tools.ToolContext(tools.parse_object_context(context_text), Path("result.tsv"), Path("state.txt")),
        execute=execute, **kwargs,
    )
    return outcome, execute


class VotDispatchTests(unittest.TestCase):
    def test_measurement_uses_real_vot_and_editor_selection(self):
        for text in ("测量vot", "请测一下选区的 VOT", "提取这段语音的vot", "帮我计算VOT", "VOT测量"):
            with self.subTest(text=text):
                outcome, execute = run(text)
                self.assertEqual([step.tool for step in outcome.steps], ["vot"])
                self.assertIn("tmin = 0.250000", execute.scripts[0])
                self.assertIn("tmax = 0.500000", execute.scripts[0])
                self.assertIn("voicingTime - burstTime", execute.scripts[0])
                self.assertIn("32.0 毫秒", outcome.reply)
                self.assertIn("计算公式", outcome.reply)
                self.assertEqual(outcome.failure, "")

    def test_explicit_range_is_search_window_not_two_landmarks(self):
        for text in ("在0.28到0.45秒之间找一下VOT", "测量VOT，范围280到450毫秒"):
            with self.subTest(text=text):
                outcome, execute = run(text)
                self.assertEqual(outcome.steps[0].arguments, {"from": 0.28, "to": 0.45})
                self.assertIn("voicingTime - burstTime", execute.scripts[0])

    def test_explicit_landmarks_and_object_are_preserved(self):
        outcome, execute = run("爆破是300毫秒，浊音起始是420毫秒，帮我算7号对象的VOT")
        self.assertEqual(outcome.steps[0].arguments, {"object": 7, "burst": 0.3, "voicing": 0.42})
        self.assertIn("t1 = 0.300000", execute.scripts[0])
        self.assertIn("t2 = 0.420000", execute.scripts[0])

    def test_negative_and_zero_vot_are_dispatched_without_reordering(self):
        for onset, value in ((.28, "-0.0200"), (.30, "0.0000")):
            execute = Recorder([(True, [f"VOT = {value} 秒"], "")])
            outcome, execute = run(f"爆破是0.3秒，浊音起始是{onset}秒，测量VOT", execute=execute)
            self.assertFalse(outcome.failure)
            self.assertIn(f"t2 = {onset:.6f}", execute.scripts[0])
            self.assertIn(value, outcome.reply)

    def test_post_release_interval_alone_is_not_successful_vot(self):
        result = "爆破后稳定起声候选间隔 = 13.0 毫秒；预浊归属待确认"
        outcome, _ = run("测量VOT", execute=Recorder([(True, [result], "")]))
        self.assertTrue(outcome.failure)
        self.assertIn("13.0 毫秒", outcome.reply)

    def test_explicit_vowel_landmark_is_preserved(self):
        outcome, execute = run("爆破是0.3秒，浊音起始是0.2秒，清晰元音起始是0.33秒，测量VOT")
        self.assertFalse(outcome.failure)
        self.assertEqual(outcome.steps[0].arguments["vowel"], .33)
        self.assertIn("Burst-to-clear-vowel interval", execute.scripts[0])

    def test_failure_or_metadata_cannot_count_as_vot_measurement(self):
        for ok, results, failure in (
            (True, ["Sound：时长 1.175 秒"], ""),
            (True, ["自动检测失败：范围起点已经是浊音，请把 from 提前。"], ""),
            (True, [], ""),
            (False, [], "脚本执行超时"),
        ):
            with self.subTest(results=results, failure=failure):
                outcome, _ = run("测量vot", execute=Recorder([(ok, results, failure)]))
                self.assertTrue(outcome.failure)
                self.assertIn("未完成", outcome.reply)
                self.assertNotIn("计算公式", outcome.reply)

    def test_questions_negation_and_multi_step_requests_still_use_planner(self):
        for text in ("VOT是什么", "不要测量VOT", "怎么测量VOT？", "测量VOT然后另存声音", "测量VOT，基频下限100Hz", "测量上一段的VOT"):
            with self.subTest(text=text):
                outcome, execute = run(text, client=FakeClient([answer("正常规划回答")]))
                self.assertEqual(execute.scripts, [])
                self.assertEqual(outcome.reply, "正常规划回答")

    def test_cancelled_request_does_not_execute(self):
        cancel = threading.Event()
        cancel.set()
        outcome, execute = run("测量vot", cancel=cancel)
        self.assertEqual(execute.scripts, [])
        self.assertIn("取消", outcome.reply)

    def test_missing_landmark_does_not_silently_use_auto_detection(self):
        outcome, execute = run("爆破是0.3秒，测量VOT")
        self.assertEqual(execute.scripts, [])
        self.assertTrue(outcome.failure)
        self.assertIn("voicing", outcome.reply)

    def test_json_planner_setting_also_uses_vot(self):
        outcome, _ = run("测量vot", native=False)
        self.assertEqual([step.tool for step in outcome.steps], ["vot"])

    def test_invalid_range_does_not_measure_the_whole_sound(self):
        outcome, execute = run("在0.5到0.25秒之间测量VOT")
        self.assertEqual(execute.scripts, [])
        self.assertIn("范围", outcome.failure)

    def test_explicit_zero_landmarks_never_become_auto_detection(self):
        execute=Recorder([(True,['VOT = 0.0000 秒'],'')])
        outcome,execute=run('爆破是0秒，浊音起始是0秒，测量VOT',execute=execute)
        self.assertTrue(outcome.steps[0].ok)
        self.assertIn('vot = t2 - t1',execute.scripts[0])
        self.assertNotIn('Filter (pass Hann band)',execute.scripts[0])

    def test_explicit_selection_requires_a_selection(self):
        outcome, execute = run("测量选区的VOT", context_text="id\tclass\tname\tselected\n7\tSound\ttone\t1\n")
        self.assertEqual(execute.scripts, [])
        self.assertIn("选区", outcome.failure)

    def test_short_editor_selection_is_not_replaced_by_whole_recording(self):
        for text in ("测量VOT", "测量选区的VOT"):
            with self.subTest(text=text):
                outcome, execute = run(text, context_text=CONTEXT.replace("0.5\n", "0.251\n"))
                self.assertEqual(execute.scripts, [])
                self.assertIn("选区", outcome.failure)

    def test_explicit_object_is_never_replaced_by_another_sound(self):
        context = "id\tclass\tname\tselected\n7\tTextGrid\tlabels\t1\n8\tSound\tunrelated\t0\n"
        for text in ("测量7号对象的VOT", "测量999号对象的VOT"):
            with self.subTest(text=text):
                outcome, execute = run(text, context_text=context)
                self.assertEqual(execute.scripts, [])
                self.assertTrue(outcome.failure)

    def test_model_chosen_vot_also_checks_result_and_adds_formula(self):
        client = FakeClient([tool_call("vot", {}), answer("解释结果")])
        outcome, _ = run("测量VOT并解释结果", client=client)
        self.assertIn("计算公式", "\n".join(outcome.results))
        client = FakeClient([tool_call("vot", {}), answer("未找到爆破")])
        outcome, _ = run("测量VOT并解释结果", client=client, execute=Recorder([(True, ["自动检测失败"], "")]))
        self.assertFalse(outcome.steps[0].ok)
        self.assertIn("未完成", outcome.failure)


class TruncatedResponseTests(unittest.TestCase):
    def test_truncated_response_is_rejected_even_with_partial_tool_call(self):
        client = qwen.QwenClient(qwen.QwenConfig())
        for message in (answer("根据您的需求，我需要使用"), tool_call("vot", {"burst": 0.3})):
            with self.subTest(message=message), patch.object(client, "_post", return_value={
                "choices": [{"finish_reason": "length", "message": message}]
            }):
                with self.assertRaisesRegex(qwen.QwenError, "截断"):
                    client.chat_message([{"role": "user", "content": "测量"}])

    def test_reasoning_without_final_answer_is_not_shown_as_answer(self):
        self.assertEqual(qwen.message_text({"content": "", "reasoning_content": "我需要先查一下，然后"}), "")

    def test_truncated_explanation_preserves_completed_tool_results(self):
        client = qwen.QwenClient(qwen.QwenConfig())
        responses = [
            {"choices": [{"finish_reason": "tool_calls", "message": tool_call("pitch", {"object": 7, "time": 0.3})}]},
            {"choices": [{"finish_reason": "length", "message": answer("根据您的需求，我需要使用")}]},
        ]
        with patch.object(client, "_post", side_effect=responses):
            outcome, _ = run("测基频", client=client, execute=Recorder([(True, ["基频 = 220 Hz"], "")]))
        self.assertEqual(outcome.results, ["基频 = 220 Hz"])
        self.assertIn("截断", outcome.failure)
        self.assertNotIn("根据您的需求", outcome.reply)

    def test_truncated_first_response_executes_nothing(self):
        client = qwen.QwenClient(qwen.QwenConfig())
        with patch.object(client, "_post", return_value={"choices": [{
            "finish_reason": "length", "message": tool_call("pitch", {"time": 0.3})
        }]}):
            outcome, execute = run("测基频", client=client)
        self.assertEqual(execute.scripts, [])
        self.assertIn("截断", outcome.reply)

    def test_truncated_connection_probe_still_confirms_connectivity(self):
        with patch.object(qwen.QwenClient, "_post", return_value={"choices": [{
            "finish_reason": "length", "message": answer("接口已收到")
        }]}):
            ok, detail = qwen.probe_api(base_url="https://example.invalid/v1", api_key="", model="test")
        self.assertTrue(ok)
        self.assertIn("截断", detail)


if __name__ == "__main__":
    unittest.main()
