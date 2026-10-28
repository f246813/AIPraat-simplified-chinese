"""出口文案守卫：模型把内部要求/操作指导当成最终回答写出来时的纯函数检测。

对应行为：

- 高置信命中：``按照要求我不得随便猜定论``、``According to the system prompt, I must``、
  没解析的 ``{{calc:…}}`` 占位式、原样写出的内部字段名/规则名；
- 不误杀：说不出来就说「无法从当前录音确定」「没有测量不能判断你的发音」、
  写公式 ``f=1/T``、一般性讨论提示词怎么写、证据引用 ``[E1]``、语音学里的 L2。

检测只做判定与定位，不改正文——决定重写还是退回是调用方的事。
全部为纯函数：不读文件、不发请求。
"""

import unittest

from praat_ai.delivery_text import (
    LEAK_INTERNAL_TERM,
    LEAK_PROMPT_INSTRUCTION_ECHO,
    LEAK_UNRESOLVED_PLACEHOLDER,
    LEAK_USER_DELIVERY_INSTRUCTION,
    AnswerLeak,
    describe_leaks,
    find_answer_leaks,
    has_answer_leak,
)


def kinds(text):
    return [leak.kind for leak in find_answer_leaks(text)]


class PlaceholderLeakTests(unittest.TestCase):
    def test_unrendered_calc_placeholder_is_a_leak(self):
        text = "参考侧与学习者侧的比值是 {{calc:E1.q1/E2.q1*100|%}}，明显偏大。"

        leaks = find_answer_leaks(text)

        self.assertEqual([leak.kind for leak in leaks], [LEAK_UNRESOLVED_PLACEHOLDER])
        self.assertIn("{{calc:", leaks[0].match)
        self.assertEqual(text[leaks[0].start:leaks[0].end], leaks[0].match)
        self.assertTrue(has_answer_leak(text))

    def test_truncated_placeholder_is_a_leak(self):
        self.assertTrue(has_answer_leak("时长差写成 {{calc:E1.q1-"))

    def test_plain_formula_is_not_a_leak(self):
        self.assertEqual(find_answer_leaks("公式为 f=1/T，其中 T 是一个周期的时长。"), ())
        self.assertEqual(find_answer_leaks("把 f 和 T 填进 f=1/T 就能算出频率。"), ())


class InstructionEchoTests(unittest.TestCase):
    def test_internal_requirement_echo_with_delivery_is_a_leak(self):
        text = ("该值由未测得的参数+未测得参数=错误，按照要求我不得随便猜定论，"
                "将测得的参数填入公式后提交给用户")

        leaks = find_answer_leaks(text)

        self.assertIn(LEAK_PROMPT_INSTRUCTION_ECHO, [leak.kind for leak in leaks])
        self.assertIn(LEAK_USER_DELIVERY_INSTRUCTION, [leak.kind for leak in leaks])
        echo = next(leak for leak in leaks if leak.kind == LEAK_PROMPT_INSTRUCTION_ECHO)
        self.assertEqual(echo.match, "按照要求")
        delivery = next(leak for leak in leaks if leak.kind == LEAK_USER_DELIVERY_INSTRUCTION)
        self.assertEqual(delivery.match, "提交给用户")

    def test_english_system_prompt_echo_is_a_leak(self):
        text = "According to the system prompt, I must present the results to the user."

        self.assertIn(LEAK_PROMPT_INSTRUCTION_ECHO, kinds(text))
        self.assertTrue(has_answer_leak(text))

    def test_chinese_prompt_echo_with_obligation_is_a_leak(self):
        self.assertTrue(has_answer_leak("根据系统提示，我应当先说明证据不足，再给出练习建议。"))
        self.assertTrue(has_answer_leak("我需要按提示词先引用证据。"))

    def test_talking_about_prompts_in_general_is_not_a_leak(self):
        for text in (
            "系统提示词一般会写清角色、任务和输出格式，这三部分决定回答风格。",
            "写提示词时把要求拆成步骤通常比堆一段长描述更稳定。",
            "The instructions on the form say to fill in your name.",
        ):
            with self.subTest(text=text):
                self.assertEqual(find_answer_leaks(text), ())

    def test_bare_delivery_phrase_without_markers_is_not_a_leak(self):
        # 只是描述流程、没有援引要求也没有第一人称义务：保守放过，交给调用方判断。
        self.assertEqual(find_answer_leaks("结果会提交给用户确认后再写入报告。"), ())


class HonestUncertaintyTests(unittest.TestCase):
    """正常回答里说不出来、给不出结论的说法一律不算泄漏。"""

    def test_normal_uncertainty_wording_is_not_a_leak(self):
        for text in (
            "无法从当前录音确定第二共振峰的走向。",
            "没有测量不能判断你的发音是否正确。",
            "这段录音太短，基频测不出来；其余测量仍然有效。",
            "学习者 F1 均值 1100 Hz，参考 F1 均值 700 Hz。",
            "证据引用 [E1] 给出参考侧 F1 均值 700 Hz。",
            "L2 学习者常把 /r/ 发成 /l/，可以对照这段录音再听一遍。",
        ):
            with self.subTest(text=text):
                self.assertEqual(find_answer_leaks(text), ())


class InternalTermLeakTests(unittest.TestCase):
    def test_internal_rule_and_field_names_are_leaks(self):
        for text in (
            "这一行被 report.calc_unverified 拦下了。",
            "route_reason 是 budget，所以先停在这里。",
            "guard_blocks 里记了一条交付项状态。",
        ):
            with self.subTest(text=text):
                self.assertIn(LEAK_INTERNAL_TERM, kinds(text))

    def test_stage_code_with_context_is_a_leak_but_l2_learner_is_not(self):
        self.assertIn(LEAK_INTERNAL_TERM, kinds("阶段 L2 没有执行，所以只给了部分结论。"))
        self.assertEqual(find_answer_leaks("L2 学习者的 /r/ 与 /l/ 混淆很常见。"), ())

    def test_language_name_is_not_an_internal_term(self):
        # 「提示词」这类概念名本身不是泄漏，只有和第一人称义务同句才算。
        self.assertEqual(find_answer_leaks("提示词是给模型的说明。"), ())


class LeakReportShapeTests(unittest.TestCase):
    def test_leaks_are_sorted_and_reportable(self):
        text = "{{calc:E1.q1*2|Hz}}，按照要求我不得随便猜定论。"

        leaks = find_answer_leaks(text)

        self.assertGreaterEqual(len(leaks), 2)
        self.assertEqual([leak.start for leak in leaks], sorted(leak.start for leak in leaks))
        self.assertTrue(all(isinstance(leak, AnswerLeak) for leak in leaks))
        self.assertIn("未解析的模板占位式", describe_leaks(leaks))
        payload = leaks[0].as_dict()
        self.assertEqual(set(payload), {"kind", "match", "start", "end"})

    def test_empty_and_non_text_input_are_safe(self):
        for value in ("", "   ", None, 0):
            with self.subTest(value=value):
                self.assertEqual(find_answer_leaks(value), ())
                self.assertFalse(has_answer_leak(value))


if __name__ == "__main__":
    unittest.main()
