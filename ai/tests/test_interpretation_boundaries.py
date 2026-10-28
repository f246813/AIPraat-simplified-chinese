"""报告越界守卫：编造阈值、人群/性别推断、无基线评价、数值重复。

2026-10-20 真机贴回来的那段日语纠音报告同时踩了四个洞：
``（107.35 Hz）= 107.35 Hz`` 的重复、``男性 85–180 Hz`` 的自带阈值、
``若说话者为女性则音调明显过低`` 的性别推断、``斜率极低`` 的无基线评价；
本机交付那条路还整段跳过了解释边界。这里逐条钉住。
"""

import tempfile
import unittest
from pathlib import Path

from praat_ai import escape_policy, interpretation_policy, report_guards


class InterpretationBoundaryTests(unittest.TestCase):
    def state(self):
        state = escape_policy.AnalysisState(goal="帮我纠音：这段日语发音哪不对", context_text="")
        state.evidence = [
            escape_policy.Evidence(
                tool="pitch_statistics",
                arguments={"from": 0.0, "to": 1.175},
                rows=["基频统计（0.000–1.175 秒）平均 = 107.354 Hz，最低 = 78.753 Hz，"
                      "最高 = 120.400 Hz，最高点 = 0.832 秒"],
                source="local",
            ),
            escape_policy.Evidence(
                tool="pitch_slope",
                arguments={"from": 0.0, "to": 1.175},
                rows=["基频走向（0.000–1.175 秒）：起点 106.65 Hz，终点 78.75 Hz，"
                      "平均绝对斜率 127.84 Hz/s"],
                source="local",
            ),
        ]
        return state

    def sanitize(self, text):
        return interpretation_policy.sanitize_interpretation(self.state(), text)

    def test_duplicate_value_pairs_collapse_to_one_value(self):
        state = self.state()
        text = "观测：平均基频为 (107.35 Hz) = 107.35 Hz，最高 (120.40 Hz) = 120.4 Hz。"
        collapsed = report_guards._collapse_duplicate_values(state, text)
        self.assertIn("平均基频为 107.35 Hz", collapsed)
        self.assertIn("最高 120.40 Hz", collapsed)
        self.assertNotIn("= 107.35 Hz", collapsed)
        self.assertNotIn("= 120.4 Hz", collapsed)

    def test_duplicate_pairs_that_disagree_are_left_for_audit(self):
        state = self.state()
        text = "(107.35 Hz) = 999.9 Hz"
        self.assertEqual(report_guards._collapse_duplicate_values(state, text), text)

    def test_general_knowledge_thresholds_with_numbers_are_dropped(self):
        body, issues = self.sanitize(
            "对比：一般知识指出，标准日语的斜率通常大于 200 Hz/s。")
        self.assertNotIn("200 Hz/s", body)
        self.assertEqual([item['rule'] for item in issues],
                         ['interpretation.general_threshold'])

    def test_gender_range_sentence_is_dropped_whole(self):
        body, issues = self.sanitize(
            "对比：一般知识指出，成年男性的标准日语基频通常在 85–180 Hz 之间。")
        self.assertNotIn("85–180", body)
        # 这句同时踩了人群范围和自带阈值；先命中的那条算，两条都可以。
        self.assertIn(issues[0]['rule'], {'interpretation.acoustic_to_body_norm',
                                          'interpretation.general_threshold'})

    def test_gender_inference_is_dropped_even_as_a_conditional(self):
        body, issues = self.sanitize("若说话者为女性或希望呈现标准语调，则音调明显过低。")
        self.assertNotIn("女性", body)
        self.assertIn('interpretation.acoustic_to_body_norm', [i['rule'] for i in issues])

    def test_evaluation_without_baseline_is_dropped_with_whole_sentence(self):
        body, issues = self.sanitize("结论：127.84 Hz/s 的斜率极低，几乎呈平板状。")
        self.assertNotIn("极低", body)
        self.assertNotIn("平板状", body)
        self.assertIn('interpretation.evaluative_without_baseline',
                      [i['rule'] for i in issues])

    def test_cited_threshold_is_kept(self):
        text = "对比：你提供的参考录音是 200 Hz，本段 107.35 Hz 低于该值。[E1]"
        body, _ = self.sanitize(text)
        self.assertIn("200 Hz", body)

    def test_qualitative_general_knowledge_without_numbers_is_kept(self):
        text = "一般知识：日语是高低重音语言，词内音高变化起区别意义的作用。"
        body, issues = self.sanitize(text)
        self.assertEqual(body, text)
        self.assertEqual(issues, [])

    def test_delivery_path_neutralizes_physiology_atomically(self):
        text = ("检查发声状态：当前基频偏低可能与发声位置靠后或气息支撑不足有关，"
                "建议练习腹式呼吸。")
        body, status = report_guards.guard_delivered_text(self.state(), text, verify=False)
        self.assertNotIn("发声位置", body)
        self.assertNotIn("气息支撑", body)
        self.assertIn(report_guards.INTERPRETATION_NOTE, body)
        self.assertEqual(status, 'rejected_masked')

    def test_real_report_loses_every_leak_but_keeps_measurements(self):
        text = Path(__file__).with_name("fixtures").joinpath(
            "real-correction-report.txt").read_text(encoding="utf-8")
        body, _ = report_guards.guard_delivered_text(self.state(), text, verify=False)
        for leak in ("85–180", "165–255", "200 Hz/s", "发声位置", "气息支撑",
                     "女性", "男性", "= 107.35 Hz"):
            with self.subTest(leak=leak):
                self.assertNotIn(leak, body)
        for measured in ("107.35 Hz", "78.75 Hz", "120.40 Hz", "127.84 Hz/s", "58.39 dB"):
            with self.subTest(measured=measured):
                self.assertIn(measured, body)


if __name__ == "__main__":
    unittest.main()
