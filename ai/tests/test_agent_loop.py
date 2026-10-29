"""对话循环：结果回灌（A2）、多步操作（A3）、工具报错当观察（A4）。

用假客户端按剧本回应、假执行器记录脚本，所以不依赖模型也不依赖 Praat；
真机版本在 `ai/tests/verify_chat_planning.py`。
"""

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from praat_ai import chat, qwen, tools


CONTEXT_TSV = "id\tclass\tname\tselected\n1\tSound\tSound tone\t1\n"


def tool_call(name: str, arguments: dict, call_id: str = "call-1") -> dict:
    return {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {"name": name, "arguments": json.dumps(arguments)},
            }
        ],
    }


def answer(text: str) -> dict:
    return {"role": "assistant", "content": text}


class FakeClient:
    """按剧本回应的假客户端：``chat_message`` / ``chat`` 依次取下一段。"""

    def __init__(self, script: list[dict]) -> None:
        self.script = list(script)
        self.seen_messages: list[list[dict]] = []
        self.config = qwen.QwenConfig()
        self.wrap_up_text = "（收尾回答）"
        self.json_plans: list[dict] = []
        self.json_history: list[list[dict]] = []

    def chat_message(self, messages, **_kwargs) -> dict:
        self.seen_messages.append(copy.deepcopy(messages))
        return self.script.pop(0) if self.script else answer("")

    def chat(self, messages, **_kwargs) -> str:
        self.seen_messages.append(copy.deepcopy(messages))
        # 收尾那轮（wrap_up）只要文字：跳过剧本里剩下的「工具调用」条目。
        while self.script:
            content = str(self.script.pop(0).get("content", ""))
            if content:
                return content
        return self.wrap_up_text

    def plan_praat_command(self, user_text, object_context, history, **_kwargs) -> dict:
        self.json_history.append(copy.deepcopy(history))
        return self.json_plans.pop(0) if self.json_plans else {"reply": "没有了"}


class Recorder:
    """假执行器：记下每条脚本，按剧本返回（成功/结果行/失败说明）。"""

    def __init__(self, results: list[tuple[bool, list[str], str]] | None = None) -> None:
        self.scripts: list[str] = []
        self.plan = list(results or [])

    def __call__(self, script: str) -> tuple[bool, list[str], str]:
        self.scripts.append(script)
        if self.plan:
            return self.plan.pop(0)
        return True, ["已执行"], ""


def make_context() -> tools.ToolContext:
    return tools.ToolContext(
        objects=tools.parse_object_context(CONTEXT_TSV),
        result_path=Path("D:/ai/runtime/chat_result.tsv"),
        state_path=Path("D:/ai/runtime/chat_state.txt"),
    )


def run(client: FakeClient, execute, **kwargs) -> chat.TurnOutcome:
    return chat.run_turn(
        client,
        user_text=kwargs.pop("user_text", "查一下 0.5 秒处的基频"),
        context_text=CONTEXT_TSV,
        history=[],
        context=make_context(),
        execute=execute,
        **kwargs,
    )


class SecondRoundTests(unittest.TestCase):
    """A2：模型在第二轮已经看到了真实结果，回答里带得出数值。"""

    def test_second_round_sees_the_results(self) -> None:
        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.5}),
                answer("0.5 秒处基频 220 Hz。"),
            ]
        )
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])
        outcome = run(client, execute)

        self.assertEqual(outcome.reply, "## 分析结论\n\n0.5 秒处基频 220 Hz。")
        self.assertEqual(outcome.results, ["基频（0.500 秒处）= 220.000 Hz"])
        self.assertTrue(outcome.used_tools)
        # 第二轮请求里带着 tool 结果（role=tool + tool_call_id），模型才看得到数值。
        second = client.seen_messages[1]
        tool_messages = [item for item in second if item.get("role") == "tool"]
        self.assertEqual(len(tool_messages), 1)
        self.assertEqual(tool_messages[0]["tool_call_id"], "call-1")
        self.assertIn("基频（0.500 秒处）= 220.000 Hz", tool_messages[0]["content"])
        # 上一轮的 assistant 工具调用也要原样带回去，否则服务端对不上 id。
        assistants = [item for item in second if item.get("role") == "assistant"]
        self.assertTrue(assistants[0]["tool_calls"])

    def test_plain_answer_does_not_execute_anything(self) -> None:
        client = FakeClient([answer("你好，我可以帮你操作 Praat。")])
        execute = Recorder()
        outcome = run(client, execute)
        self.assertFalse(outcome.used_tools)
        self.assertEqual(outcome.reply, "你好，我可以帮你操作 Praat。")
        self.assertEqual(execute.scripts, [])

    def test_empty_reply_falls_back_to_the_results(self) -> None:
        client = FakeClient([tool_call("pitch", {"time": 0.5}), answer("")])
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])
        outcome = run(client, execute)
        self.assertIn("220.000 Hz", outcome.reply)


class MultiStepTests(unittest.TestCase):
    """A3：一句话里的多步操作，后一步可以用前一步的结果。"""

    def test_later_step_runs_after_the_first_result(self) -> None:
        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.5}),
                tool_call("extract_part", {"start": 0.2, "end": 0.5}, call_id="call-2"),
                answer("基频量好了，片段也截出来了。"),
            ]
        )
        execute = Recorder(
            [
                (True, ["基频（0.500 秒处）= 220.000 Hz"], ""),
                (True, ["已截取片段：片段，区间 0.200–0.500 秒"], ""),
            ]
        )
        # 用户明确说了「然后」→ 允许第二步动对象（截取片段）。
        outcome = run(client, execute, user_text="查一下 0.5 秒处的基频，然后把这段截出来")

        self.assertEqual(len(outcome.steps), 2)
        self.assertEqual([step.tool for step in outcome.steps], ["pitch", "extract_part"])
        self.assertEqual([step.round_index for step in outcome.steps], [1, 2])
        self.assertEqual(len(execute.scripts), 2)
        # 第二步的请求里，第一步的结果作为观察结果回灌了。
        third = client.seen_messages[2]
        observations = [item["content"] for item in third if item.get("role") == "tool"]
        self.assertIn("220.000 Hz", " ".join(observations))
        self.assertIn("片段", outcome.reply)

    def test_several_actions_in_one_round_are_each_executed(self) -> None:
        """模型一轮给两个调用（「0.25 和 0.75 秒的基频」），两个都要执行。"""

        client = FakeClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "pitch", "arguments": '{"time": 0.25}'},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "pitch", "arguments": '{"time": 0.75}'},
                        },
                    ],
                },
                answer("两个时刻都是 220 Hz。"),
            ]
        )
        execute = Recorder(
            [
                (True, ["基频（0.250 秒处）= 220.000 Hz"], ""),
                (True, ["基频（0.750 秒处）= 220.000 Hz"], ""),
            ]
        )
        outcome = run(client, execute)
        self.assertEqual(len(outcome.steps), 2)
        self.assertEqual(len(execute.scripts), 2)
        self.assertEqual(len(outcome.results), 2)


class ObservationTests(unittest.TestCase):
    """A4：工具报错（或执行失败）当成观察结果回灌，模型可以改参数重试。"""

    def test_bad_argument_goes_back_to_the_model(self) -> None:
        client = FakeClient(
            [
                tool_call("formant_frequency", {"formant": 99, "time": 0.5}),
                tool_call("formant_frequency", {"formant": 1, "time": 0.5}, call_id="c2"),
                answer("F1 是 190 Hz。"),
            ]
        )
        execute = Recorder([(True, ["第 1 共振峰（0.500 秒处）：频率 190 Hz"], "")])
        outcome = run(client, execute)

        # 第一次没有执行（参数被工具拒绝），第二次换成合法参数才执行。
        self.assertEqual(len(execute.scripts), 1)
        self.assertEqual([step.ok for step in outcome.steps], [False, True])
        second = client.seen_messages[1]
        observation = [item["content"] for item in second if item.get("role") == "tool"][0]
        self.assertIn("没有执行", observation)
        self.assertIn("共振峰编号", observation)
        self.assertIn("190 Hz", outcome.reply)

    def test_execution_failure_goes_back_to_the_model(self) -> None:
        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.5}),
                answer("Praat 那边没执行成功，请看看是不是有对话框挡着。"),
            ]
        )
        execute = Recorder([(False, [], "Praat 在 25 秒内没有执行这个脚本。")])
        outcome = run(client, execute)

        self.assertIn("没有执行这个脚本", outcome.failure)
        second = client.seen_messages[1]
        observation = [item["content"] for item in second if item.get("role") == "tool"][0]
        self.assertIn("执行失败", observation)
        self.assertIn("对话框", outcome.reply)

    def test_script_error_still_shows_its_result_line(self) -> None:
        """脚本在 Praat 里报错时，Praat 写回来的那行错误说明要显示给用户。"""

        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.5}),
                answer("这条脚本在 Praat 里报错了。"),
            ]
        )
        execute = Recorder(
            [
                (
                    False,
                    ["脚本没跑完（Praat 报错，后面的消息不会被它挡住）：Unknown function"],
                    'Unknown function «nosuchcommand» in 公式.',
                )
            ]
        )
        outcome = run(client, execute)

        self.assertIn("nosuchcommand", outcome.failure)
        self.assertEqual(
            outcome.results,
            ["脚本没跑完（Praat 报错，后面的消息不会被它挡住）：Unknown function"],
        )
        self.assertEqual([step.ok for step in outcome.steps], [False])

    def test_render_error_uses_the_model_script_and_notes_it(self) -> None:
        """工具拒绝、但模型同时给了脚本时，用脚本兜底并把这件事说给用户。"""

        client = FakeClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {
                                "name": "formant_frequency",
                                "arguments": '{"formant": 99}',
                            },
                        }
                    ],
                },
                answer("已按脚本执行。"),
            ]
        )
        execute = Recorder([(True, ["兜底脚本的结果"], "")])
        with patch.object(
            chat,
            "render_action",
            return_value=(
                'appendFileLine: "D:/ai/runtime/chat_result.tsv", "兜底"',
                "工具 formant_frequency 无法执行（编号越界），改用模型给出的脚本。",
            ),
        ):
            outcome = run(client, execute)
        self.assertTrue(outcome.notes)
        self.assertIn("改用模型给出的脚本", outcome.notes[0])
        self.assertEqual(len(execute.scripts), 1)

    def test_round_cap_asks_for_a_text_answer(self) -> None:
        """模型一直要工具：到上限就强制要一次纯文本回答，不会无限循环。"""

        # 每轮换一个参数（否则会被「重复调用」那道防线拦下，测不到轮数上限）。
        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.1 * (index + 1)}, call_id=f"c{index}")
                for index in range(chat.MAX_AGENT_ROUNDS)
            ]
            + [answer("够了，就这些。")]
        )
        execute = Recorder()
        outcome = run(client, execute)
        self.assertEqual(len(execute.scripts), chat.MAX_AGENT_ROUNDS)
        self.assertEqual(outcome.reply, "够了，就这些。")
        # 最后那次收尾调用不带 tools（不让它再调工具）。
        self.assertNotIn("tools", client.seen_messages[-1][0])

    def test_repeated_identical_action_runs_once(self) -> None:
        """同一个动作在一轮里出现多次（小模型会这样）只执行一次。"""

        client = FakeClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "c1",
                            "type": "function",
                            "function": {"name": "pitch", "arguments": '{"time": 0.5}'},
                        },
                        {
                            "id": "c2",
                            "type": "function",
                            "function": {"name": "pitch", "arguments": '{"time": 0.5}'},
                        },
                    ],
                },
                answer("0.5 秒处是 220 Hz。"),
            ]
        )
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])
        outcome = run(client, execute)

        self.assertEqual(len(execute.scripts), 1)
        self.assertEqual(len(outcome.results), 1)
        self.assertTrue(any("重复调用" in note for note in outcome.notes))
        # 重复的那次也回灌了一条「已经做过」，模型才知道不用再等结果。
        second = client.seen_messages[1]
        observations = [item["content"] for item in second if item.get("role") == "tool"]
        self.assertEqual(len(observations), 2)
        self.assertIn("已经执行过", observations[1])

    def test_total_step_cap_stops_a_runaway_model(self) -> None:
        """模型每轮都换着参数调工具：总步数到上限就停，别把用户的 Praat 改花。"""

        rounds = []
        for index in range(chat.MAX_AGENT_ROUNDS):
            calls = []
            for offset in range(3):
                time_value = index * 3 + offset
                calls.append(
                    {
                        "id": f"c{index}-{offset}",
                        "type": "function",
                        "function": {
                            "name": "pitch",
                            "arguments": json.dumps({"time": time_value}),
                        },
                    }
                )
            rounds.append({"role": "assistant", "content": "", "tool_calls": calls})
        client = FakeClient(rounds + [answer("够了。")])
        outcome = run(client, execute=Recorder())
        self.assertEqual(len(outcome.steps), chat.MAX_AGENT_STEPS)
        # 步数到上限时直接收尾（要一次纯文本回答），不再让模型继续调工具。
        self.assertEqual(outcome.reply, "够了。")

    def test_second_round_cannot_mutate_without_being_asked(self) -> None:
        """用户只说了一件事时，第二轮想改动对象的动作会被拦下（0.8B 预设实测会这样）。"""

        client = FakeClient(
            [
                tool_call("view_edit", {}),
                tool_call("spectrogram", {"name": "多余"}, call_id="c2"),
                answer("编辑器已经打开了。"),
            ]
        )
        execute = Recorder([(True, ["已打开编辑器：Sound tone"], "")])
        outcome = run(client, execute, user_text="打开当前声音的编辑器")

        # 只执行了第一轮那一个动作，频谱图没做。
        self.assertEqual(len(execute.scripts), 1)
        self.assertEqual([step.tool for step in outcome.steps], ["view_edit", "spectrogram"])
        self.assertFalse(outcome.steps[1].script)
        self.assertTrue(any("拦下" in note for note in outcome.notes))
        self.assertEqual(outcome.reply, "编辑器已经打开了。")

    def test_second_round_may_read_even_without_being_asked(self) -> None:
        """只读查询不受这条护栏限制（多量一个数不会改坏任何东西）。"""

        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.5}),
                tool_call("intensity", {"time": 0.5}, call_id="c2"),
                answer("基频 220 Hz，强度 70 dB。"),
            ]
        )
        execute = Recorder(
            [
                (True, ["基频（0.500 秒处）= 220.000 Hz"], ""),
                (True, ["强度（0.500 秒处）= 70.0 dB"], ""),
            ]
        )
        outcome = run(client, execute, user_text="查一下 0.5 秒处的基频")
        self.assertEqual(len(execute.scripts), 2)
        self.assertEqual(len(outcome.results), 2)


class JsonPlannerTests(unittest.TestCase):
    """``PRAAT_AI_PLANNER=json`` 的兜底路也要能回灌观察结果。"""

    def test_json_plan_is_executed_and_observed(self) -> None:
        client = FakeClient([])
        client.json_plans = [
            {"reply": "查询基频。", "tool": "pitch", "arguments": {"time": 0.5}},
            {"reply": "0.5 秒处是 220 Hz。", "tool": "", "arguments": {}},
        ]
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])
        outcome = run(client, execute, native=False)

        self.assertEqual(outcome.reply, "## 分析结论\n\n0.5 秒处是 220 Hz。")
        self.assertEqual(len(execute.scripts), 1)
        # 第二轮把「上一步执行结果」写进了历史。
        self.assertTrue(len(client.json_history) >= 2)
        second_history = client.json_history[1]
        self.assertIn("上一步的执行结果", " ".join(item["content"] for item in second_history))


class TextToolCallTests(unittest.TestCase):
    """模型把工具调用写成正文里的 ``<tool_call>`` 文本时也要能执行，别显示给用户。"""

    TEXT_CALL = (
        "<tool_call>\n"
        "<function=pitch>\n"
        "<parameter=time>\n"
        "0.5\n"
        "</parameter>\n"
        "</function>\n"
        "</tool_call>"
    )

    def test_full_text_call_is_parsed(self) -> None:
        actions, leftover = qwen.parse_text_tool_calls(self.TEXT_CALL)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["tool"], "pitch")
        self.assertEqual(actions[0]["arguments"], {"time": "0.5"})
        self.assertEqual(leftover, "")

    def test_truncated_text_call_is_parsed_and_hidden(self) -> None:
        """被 max_tokens 截断（没有 </tool_call>）也要能执行，半截 XML 不能给用户看。"""

        truncated = self.TEXT_CALL.split("</tool_call>")[0]
        actions, leftover = qwen.parse_text_tool_calls(truncated)
        self.assertEqual(len(actions), 1)
        self.assertEqual(leftover, "")
        self.assertNotIn("<", leftover)

    def test_prose_outside_the_call_is_kept(self) -> None:
        actions, leftover = qwen.parse_text_tool_calls("我来看一下这个声音。\n" + self.TEXT_CALL)
        self.assertEqual(len(actions), 1)
        self.assertEqual(leftover, "我来看一下这个声音。")

    def test_plain_text_is_untouched(self) -> None:
        actions, leftover = qwen.parse_text_tool_calls("这个我做不到。")
        self.assertEqual(actions, [])
        self.assertEqual(leftover, "这个我做不到。")

    def test_loop_executes_text_calls_and_shows_no_xml(self) -> None:
        client = FakeClient(
            [
                {"role": "assistant", "content": self.TEXT_CALL},
                answer("0.5 秒处是 220 Hz。"),
            ]
        )
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])
        outcome = run(client, execute)

        self.assertEqual(len(execute.scripts), 1)
        self.assertEqual(outcome.reply, "## 分析结论\n\n0.5 秒处是 220 Hz。")
        self.assertNotIn("<tool_call>", outcome.reply)
        # 回灌给模型的 assistant 消息里带上了这次调用（id 是补的），tool 结果才对得上。
        second = client.seen_messages[1]
        self.assertTrue([item for item in second if item.get("role") == "assistant"][0]["tool_calls"])
        self.assertTrue([item for item in second if item.get("role") == "tool"])


class EmptyAnswerTests(unittest.TestCase):
    """模型没给正文时的两条规矩（2026-09-21 用户报的「直接回复了已完成」）：

    ① 先再问一次要一句正文；② 实在没有就直说没内容，绝不用「已完成」糊过去。
    """

    def test_empty_answer_is_asked_again(self) -> None:
        client = FakeClient(
            [answer(""), answer("我是 deepseek-chat，我被设置成 Praat 的前端。")]
        )
        outcome = run(client, Recorder())
        self.assertEqual(
            outcome.reply, "我是 deepseek-chat，我被设置成 Praat 的前端。"
        )
        self.assertEqual(len(client.seen_messages), 2)

    def test_bare_done_is_never_shown_to_the_user(self) -> None:
        client = FakeClient([answer(""), answer("")])
        outcome = run(client, Recorder())
        self.assertNotIn("已完成", outcome.reply)
        self.assertIn("没有返回任何内容", outcome.reply)

    def test_results_are_kept_when_the_model_stays_silent(self) -> None:
        client = FakeClient([tool_call("pitch", {"time": 0.5}), answer("")])
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])
        outcome = run(client, execute)
        self.assertIn("220.000 Hz", outcome.reply)
        self.assertIn("没有给出说明", outcome.reply)

    def test_reasoning_only_message_is_used_as_the_answer(self) -> None:
        """思考型模型把答案只放在 reasoning_content 里时也要显示出来。"""

        client = FakeClient(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "想想看。\nFinal Answer: 对比结果：这次 F0 偏高。",
                }
            ]
        )
        outcome = run(client, Recorder())
        self.assertIn("对比结果", outcome.reply)


if __name__ == "__main__":
    unittest.main()
