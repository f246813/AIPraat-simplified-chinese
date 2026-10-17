"""Exercise prompts at the outgoing HTTP boundary without a cloud connection."""

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from praat_ai import chat, qwen, tools


CONTEXT = "id\tclass\tname\tselected\n7\tSound\tSound learner\t1\n8\tTextGrid\tTextGrid words\t0\n"


def answer(content="", calls=None):
    message = {"role": "assistant", "content": content}
    if calls:
        message["tool_calls"] = calls
    return {"choices": [{"message": message, "finish_reason": "stop"}]}


def call(name, arguments, identifier="measure-1"):
    return {
        "id": identifier,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


class HttpCapture:
    """Replace only network I/O; run the real client and planner code."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []

    def __call__(self, request, **_kwargs):
        self.payloads.append(json.loads(request.data.decode("utf-8")))
        return io.BytesIO(json.dumps(self.responses.pop(0)).encode("utf-8"))


class CloudPromptTests(unittest.TestCase):
    def client(self, knowledge="open", provider="api"):
        return qwen.QwenClient(qwen.QwenConfig(
            provider=provider,
            knowledge_mode=knowledge,
            model="configured-cloud-model" if provider == "api" else "local-model",
            base_url="https://offline.example.invalid/v1",
            api_key="EMPTY",
            max_context_tokens=32768 if provider == "api" else 8192,
        ))

    def system(self, payload):
        return "\n".join(
            item["content"] for item in payload["messages"]
            if item["role"] == "system"
        )

    def assert_cloud_workflow(self, payload):
        prompt = self.system(payload)
        for restriction in (
            "不要输出 Markdown", "一个都不要自己编造或推算",
            "只做用户要求的那件事", "不要重复调用同一个工具",
            "两三个工具", "必须**照下面这个格式",
        ):
            self.assertNotIn(restriction, prompt)
        for rule in ("多步", "观测", "透明推导", "假设", "Markdown", "对象类型", "最新", "from=0", "duration"):
            self.assertIn(rule, prompt)
        self.assertIn("configured-cloud-model", prompt)
        self.assertIn("不得伪造", prompt)
        self.assertIn("听感只能来自", prompt)
        self.assertIn("旧选区", prompt)
        self.assertIn("不同", prompt)
        self.assertIn("Praat 英文脚本", prompt)

    def plan(self, client, **kwargs):
        return client.plan_praat_command(
            "完整分析整段录音，关联多项测量并解释练习方法", CONTEXT, [],
            tool_catalog=tools.catalog_text(), result_path="D:/offline/result.tsv",
            state_path="D:/offline/state.txt", **kwargs,
        )

    def test_direct_native_planner_sends_cloud_policy_and_keeps_all_actions(self):
        calls = [
            call("duration", {"object": 7}),
            call("pitch_statistics", {"object": 7, "from": 0, "to": 2.5}, "measure-2"),
            call("intensity_statistics", {"object": 7, "from": 0, "to": 2.5}, "measure-3"),
            call("formant_frequency", {"object": 7, "time": 0.5, "formant": "1,2"}, "measure-4"),
        ]
        network = HttpCapture([answer(calls=calls)])
        with patch.dict(os.environ, {qwen.PLANNER_MODE_ENV: "tools"}), patch("urllib.request.urlopen", network):
            plan = self.plan(self.client(), tool_schemas=tools.tool_schemas())
        self.assertEqual([item["tool"] for item in plan["actions"]], ["duration", "pitch_statistics", "intensity_statistics", "formant_frequency"])
        self.assertEqual(plan["actions"][1]["arguments"], {"object": 7, "from": 0, "to": 2.5})
        self.assert_cloud_workflow(network.payloads[0])
        self.assertIn("语言学", self.system(network.payloads[0]))
        self.assertEqual(network.payloads[0]["tool_choice"], "auto")

    def test_native_followup_and_wrap_up_keep_policy_and_observations(self):
        network = HttpCapture([
            answer(calls=[call("pitch_statistics", {"object": 7, "from": 0, "to": 2.5})]),
            answer("**观测**：平均基频为 180 Hz。"),
            answer("**透明推导**：以已测 180 Hz 和 200 Hz 为输入，差为 20 Hz；这里只是两次测量的差。"),
        ])
        planner = chat._NativePlanner(
            self.client(), user_text="分析整段录音", context_text=CONTEXT, history=[],
            result_path="D:/offline/result.tsv", state_path="D:/offline/state.txt",
        )
        with patch("urllib.request.urlopen", network):
            actions, _ = planner.next()
            planner.observe(actions[0], "成功；对象 7；整段 0–2.5 秒；平均基频 180 Hz")
            _, reply = planner.next()
            final = planner.wrap_up()
        self.assertIn("180 Hz", reply)
        self.assertIn("20 Hz", final)
        for payload in network.payloads:
            self.assert_cloud_workflow(payload)
        observation = network.payloads[-1]["messages"][-1]
        self.assertEqual(observation["role"], "tool")
        self.assertEqual(observation["tool_call_id"], "measure-1")
        self.assertIn("整段 0–2.5 秒", observation["content"])
        self.assertNotIn("tools", network.payloads[-1])

    def test_json_fallback_sends_same_cloud_policy_and_preserves_multi_action_plan(self):
        document = {
            "reply": "准备关联整段基频与强度测量。", "tool": "", "arguments": {}, "script": "",
            "actions": [
                {"tool": "pitch_statistics", "arguments": {"object": 7, "from": 0, "to": 2.5}},
                {"tool": "intensity_statistics", "arguments": {"object": 7, "from": 0, "to": 2.5}},
            ],
        }
        network = HttpCapture([answer(), answer(json.dumps(document, ensure_ascii=False))])
        with patch.dict(os.environ, {qwen.PLANNER_MODE_ENV: "auto"}), patch("urllib.request.urlopen", network):
            plan = self.plan(self.client(), tool_schemas=tools.tool_schemas())
        self.assertEqual(plan, document)
        self.assertEqual(len(tools.plan_actions(plan)), 2)
        self.assertEqual(len(network.payloads), 2)
        for payload in network.payloads:
            self.assert_cloud_workflow(payload)
        self.assertEqual(network.payloads[-1]["response_format"], {"type": "json_object"})
        self.assertIn("actions", self.system(network.payloads[-1]))
        self.assertIn('"from": 0, "to": 2.5', self.system(network.payloads[-1]))

    def test_api_strict_limits_knowledge_without_restoring_local_workflow(self):
        network = HttpCapture([answer("只据实测结果作说明。")])
        with patch.dict(os.environ, {qwen.PLANNER_MODE_ENV: "tools"}), patch("urllib.request.urlopen", network):
            self.plan(self.client("strict"), tool_schemas=tools.tool_schemas())
        self.assert_cloud_workflow(network.payloads[0])
        prompt = self.system(network.payloads[0])
        self.assertIn("禁止无依据", prompt)
        self.assertIn("一般知识", prompt)
        self.assertNotIn("可以使用自己的语言学", prompt)

    def test_local_native_keeps_conservative_measurement_rules(self):
        network = HttpCapture([answer("结果里没有。")])
        with patch.dict(os.environ, {qwen.PLANNER_MODE_ENV: "tools"}), patch("urllib.request.urlopen", network):
            self.plan(self.client("strict", "llama.cpp"), tool_schemas=tools.tool_schemas())
        prompt = self.system(network.payloads[0])
        self.assertIn("一个都不要自己编造或推算", prompt)
        self.assertIn("不要输出 Markdown", prompt)
        self.assertNotIn("透明推导", prompt)

    def test_error_explanation_uses_cloud_evidence_and_knowledge_policy(self):
        network = HttpCapture([answer("**观测**：偏差来自给出的测量；建议分段练习。")])
        with patch("urllib.request.urlopen", network):
            reply = self.client().explain_errors({"language": "cmn"}, {"score": 80, "errors": []})
        self.assertIn("分段练习", reply)
        prompt = self.system(network.payloads[0])
        for rule in ("观测", "透明推导", "一般知识", "假设", "听感只能来自", "不得伪造", "configured-cloud-model"):
            self.assertIn(rule, prompt)
        self.assertNotIn("300 字以内", prompt)

    def test_vision_explanation_keeps_image_evidence_distinct_from_audio(self):
        network = HttpCapture([answer("图中曲线可见，但没有音频听感证据。")])
        with tempfile.TemporaryDirectory() as folder:
            image = Path(folder) / "plot.png"
            image.write_bytes(b"offline image fixture")
            with patch("urllib.request.urlopen", network):
                reply = self.client("strict").vision_explain(image, {}, {"errors": []})
        self.assertIn("没有音频", reply)
        prompt = self.system(network.payloads[0])
        for rule in ("观测", "透明推导", "假设", "听感只能来自", "禁止无依据", "图中"):
            self.assertIn(rule, prompt)
        self.assertTrue(network.payloads[0]["messages"][1]["content"][0]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_damaged_native_arguments_are_marked_instead_of_becoming_a_default_action(self):
        # A nested valid object must not rescue a damaged outer argument document.
        for raw in ('{"object": broken, "nested": {"object": 8}}', "not json", "[]", "null", '"7"', ""):
            with self.subTest(raw=raw):
                damaged = call("remove_object", {})
                damaged["function"]["arguments"] = raw
                network = HttpCapture([answer(calls=[damaged])])
                with patch.dict(os.environ, {qwen.PLANNER_MODE_ENV: "tools"}), patch("urllib.request.urlopen", network):
                    plan = self.plan(self.client(), tool_schemas=tools.tool_schemas())
                action = plan["actions"][0]
                self.assertEqual(action["id"], "measure-1")
                self.assertEqual(action["tool"], "remove_object")
                self.assertEqual(action["arguments"], {})
                self.assertIn("参数", action.get("invalid_arguments", ""))

    def test_empty_json_argument_object_remains_valid(self):
        network = HttpCapture([answer(calls=[call("duration", {})])])
        with patch.dict(os.environ, {qwen.PLANNER_MODE_ENV: "tools"}), patch("urllib.request.urlopen", network):
            plan = self.plan(self.client(), tool_schemas=tools.tool_schemas())
        self.assertEqual(plan["actions"][0]["arguments"], {})
        self.assertNotIn("invalid_arguments", plan["actions"][0])

    def test_direct_native_planner_uses_explicit_final_reasoning_answer(self):
        response = answer()
        response["choices"][0]["message"]["reasoning_content"] = (
            "分析中……\nFinal Answer: **一般知识**：可先分段练习，再测量比较。"
        )
        network = HttpCapture([response])
        with patch.dict(os.environ, {qwen.PLANNER_MODE_ENV: "tools"}), patch("urllib.request.urlopen", network):
            plan = self.plan(self.client(), tool_schemas=tools.tool_schemas())
        self.assertEqual(plan["reply"], "**一般知识**：可先分段练习，再测量比较。")
        self.assertEqual(plan["actions"], [])
        self.assertNotIn("分析中", plan["reply"])


if __name__ == "__main__":
    unittest.main()
