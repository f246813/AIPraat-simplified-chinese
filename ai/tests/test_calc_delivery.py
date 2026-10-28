"""交付正文里的推导占位式：能算的算出来，算不出来的整小句删掉。

2026-10-20 真机：`所选区间占时长的 [推导未核对]（约 74%）`——`verify=False`
这条出口以前完全不渲染 `{{calc:…}}`，占位式被换成内部标记交到用户面前。
"""

import unittest

from praat_ai import escape_policy, report_guards

TEMPLATE = ("该词总时长为 1.175 秒，所选区间占时长的 {calc}（约 74%），"
            "覆盖了目标词的大部分发音时段。")


def state():
    current = escape_policy.AnalysisState(goal="帮我纠音：这段日语发音哪不对", context_text="")
    current.evidence = [
        escape_policy.Evidence(
            tool="object_info", arguments={},
            rows=["圈选区间 = 0.151091–1.025063 秒", "总时长 = 1.175 秒"],
            source="local",
        ),
    ]
    return current


class CalcTemplateDeliveryTests(unittest.TestCase):
    def test_renderable_calc_is_computed_and_duplicate_approx_dropped(self):
        text = TEMPLATE.format(calc="{{calc:(E1.q2-E1.q1)/E1.q3*100|%}}")
        for verify in (True, False):
            with self.subTest(verify=verify):
                body, _status = report_guards.guard_delivered_text(state(), text, verify=verify)
                self.assertIn("74.38", body)
                self.assertNotIn("{{calc:", body)
                self.assertNotIn("[推导未核对]", body)
                self.assertNotIn("约 74%", body)

    def test_unrenderable_calc_drops_the_clause_instead_of_showing_a_marker(self):
        text = TEMPLATE.format(calc="{{calc:E9.q1/E9.q2*100|%}}")
        for verify in (True, False):
            with self.subTest(verify=verify):
                body, _status = report_guards.guard_delivered_text(state(), text, verify=verify)
                self.assertNotIn("[推导未核对]", body)
                self.assertNotIn("{{calc:", body)
                self.assertNotIn("约 74%", body)
                # 同一行里别的实测句仍然保留。
                self.assertIn("1.175 秒", body)
                self.assertIn("覆盖了目标词的大部分发音时段", body)

    def test_malformed_placeholder_is_removed_too(self):
        text = TEMPLATE.format(calc="{{calc:E1.q1/")
        body, _status = report_guards.guard_delivered_text(state(), text, verify=False)
        self.assertNotIn("{{calc:", body)
        self.assertNotIn("推导未核对", body)

    def test_interpretation_note_no_longer_repeats_the_candidate_wording(self):
        self.assertNotIn("测量候选值不等于发音或生理诊断", report_guards.INTERPRETATION_NOTE)
        self.assertIn("相关断言已省略", report_guards.INTERPRETATION_NOTE)


if __name__ == "__main__":
    unittest.main()
