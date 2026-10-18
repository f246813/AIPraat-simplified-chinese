"""P0（B 方案）报告审计：数值问题就地遮蔽，只有结构性错误才重试。

对应 ai/docs/backend-workflow-redesign.zh-CN.md 第 4 节。覆盖真机事故的直接成因：
`object_info` 没有 interval，导致「本次分析覆盖 0–0.892 秒」被判成编造数值。
"""
import unittest

from pydantic_ai import ModelRetry

from praat_ai.cloud_agent import AnalysisReport, Coverage
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.report_audit import audit_report
from praat_ai.report_guards import UNVERIFIED_NUMBER, validate_report

CONTEXT = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
           '1\tSound\tSound あなた\t1\t\t\n# praat-pid=13764\n')
GOAL = '分析录音语调'

INFO = Evidence('object_info', {'object': 1},
                ['Sound「Sound あなた」（id 1）：时长 0.892 秒，通道数 1，采样率 44100 Hz'], CONTEXT)
MEASURE = Evidence('measure', {'object': 1, 'from': 0, 'to': .892,
                               'parameter': 'mean_pitch,minimum_pitch,maximum_pitch,f1,mean_intensity'},
                   ['平均基频（0.000–0.892 秒）= 94.159 Hz',
                    '最低基频（0.000–0.892 秒）= 77.014 Hz',
                    '最高基频（0.000–0.892 秒）= 119.243 Hz',
                    '第 1 共振峰平均（0.000–0.892 秒）= 891.500 Hz',
                    '平均强度（0.000–0.892 秒）= 50.700 dB'], CONTEXT)
SEGMENT = Evidence('pitch_statistics', {'object': 1, 'from': .297, 'to': .594},
                   ['基频统计（0.297–0.594 秒）：平均 95.203 Hz，最低 77.014 Hz，'
                    '最高 119.243 Hz，最高点出现在 0.361 秒'], CONTEXT)


def state(*items, goal=GOAL):
    return AnalysisState(goal, CONTEXT, evidence=list(items))

def report(text, explanation='已按测量范围解释。'):
    # AnalysisReport.analysis 至少 30 字：补一句没有数字的常规尾句。
    tail = '\n报告依据已有记录，未测的特征仍需相应测量，缺口写在交付项状态里。'
    return AnalysisReport(analysis=text + tail,
                          coverage=[Coverage(item=GOAL, status='complete', explanation=explanation)])


class ReportAuditTests(unittest.TestCase):
    def audit(self, current, text, explanation='已按测量范围解释。'):
        return audit_report(current, report(text, explanation))

    def test_evidence_value_with_unit_is_not_flagged(self):
        """真机事故里的假阳性：整段 measure 的 F1=891.5 Hz 被当成编造数值。"""

        current = state(INFO, MEASURE)
        result = self.audit(current, '全段共振峰 F1=891.5 Hz，平均基频 94.159 Hz。[E2]')
        self.assertNotIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertTrue(current.metrics['audit']['verified'])
        self.assertEqual(current.metrics['audit']['masked_count'], 0)

    def test_range_endpoint_over_object_info_is_supported(self):
        """object_info 没有 interval，但它的总时长就是 0–duration。"""

        current = state(INFO, MEASURE)
        result = self.audit(current, '本次分析覆盖了音频全长 0–0.892 秒。[E1]')
        self.assertNotIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertTrue(current.metrics['audit']['verified'])

    def test_derived_difference_is_accepted(self):
        """透明推导：119.243 − 77.014 = 42.229 Hz 不该被判成编造。"""

        current = state(INFO, MEASURE)
        result = self.audit(current, '最高与最低基频相差 42.229 Hz。[E2]')
        self.assertNotIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertGreaterEqual(current.metrics.get('report_derived_accepted', 0), 1)

    def test_typical_range_without_citation_is_accepted(self):
        """「通常 85–180 Hz」是一般知识，不是本次测量。"""

        current = state(INFO, MEASURE)
        result = self.audit(current, '成年男性基频通常在 85–180 Hz 之间。')
        self.assertNotIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertGreaterEqual(current.metrics.get('report_general_value_assumed', 0), 1)

    def test_unknown_number_is_masked_instead_of_rewritten(self):
        current = state(INFO, MEASURE)
        result = self.audit(current, '平均基频为 233.700 Hz。[E2]')
        self.assertIn(UNVERIFIED_NUMBER, result.analysis)
        audit = current.metrics['audit']
        self.assertFalse(audit['verified'])
        self.assertEqual(audit['masked_count'], 1)
        self.assertEqual(current.metrics['guard_repairs']['report.number_masked'], 1)
        # 正文其余部分保留：遮蔽只发生在那个数上。
        self.assertIn('平均基频为', result.analysis)

    def test_unknown_number_in_coverage_explanation_is_masked(self):
        current = state(INFO, MEASURE)
        result = self.audit(current, '结论见数据板。', explanation='最高 233.700 Hz 出现在句中。')
        self.assertIn(UNVERIFIED_NUMBER, result.coverage[0].explanation)

    def test_structural_rule_still_raises_for_retry(self):
        current = state(INFO, MEASURE)
        with self.assertRaises(ModelRetry) as caught:
            self.audit(current, '对象 9 的平均基频为 94.159 Hz。')
        self.assertEqual(getattr(caught.exception, 'rule', ''), 'report.object_unknown')
        self.assertEqual(current.metrics['guard_blocks'], {'report.object_unknown': 1})

    def test_whole_scope_citation_of_segment_is_annotated_not_blocked(self):
        """「全段」却说成分段值：只追加真实范围，不否决整份报告。"""

        current = state(INFO, SEGMENT)
        result = self.audit(current, '全段平均基频为 95.203 Hz。[E2]')
        self.assertIn('局部分段测量', result.analysis)
        self.assertIn('0.297', result.analysis)
        self.assertEqual(current.metrics['guard_repairs']['report.range_whole_partial'], 1)

    def test_whole_scope_with_whole_evidence_is_not_annotated(self):
        current = state(INFO, MEASURE)
        result = self.audit(current, '全段平均基频为 94.159 Hz。[E2]')
        self.assertNotIn('局部分段测量', result.analysis)

    def test_validate_report_keeps_blocking_by_itself(self):
        """判定仍由 report_guards 负责；审计只是处置层，不改判定。"""

        current = state(INFO, MEASURE)
        with self.assertRaises(ModelRetry):
            validate_report(current, report('平均基频为 233.700 Hz。[E2]'))



    def test_markdown_list_numbering_is_not_masked(self):
        """列表序号（``**2.**``）不是量值，遮蔽它只会毁掉正文结构。"""

        current = state(INFO, MEASURE)
        result = self.audit(current, '**1. 平均基频** 94.159 Hz。[E2]\n'
                                     '**2. 最高基频** 119.243 Hz。[E2]')
        self.assertIn('**1.', result.analysis)
        self.assertIn('**2.', result.analysis)
        self.assertEqual(current.metrics['audit']['masked_count'], 0)

    def test_unknown_range_is_masked_as_one_span(self):
        """没有证据支持的范围整体遮蔽，不把两个端点拆成两个占位符。"""

        current = state(INFO, MEASURE)
        result = self.audit(current, '截取 0.100–0.300 秒这一段重新测量。')
        self.assertIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertEqual(current.metrics['audit']['masked_count'], 1)
        self.assertNotIn('0.100', result.analysis)

    def test_peak_time_matches_when_claim_wraps_max_in_a_sentence(self):
        """「最高基频出现在 0.361 秒」对得上证据里的 max_time 量。"""

        current = state(INFO, SEGMENT)
        result = self.audit(current, '最高基频出现在 0.361 秒处。[E2]')
        self.assertNotIn(UNVERIFIED_NUMBER, result.analysis)

    def test_number_from_a_sibling_measurement_is_accepted(self):
        """引证写错（引了 object_info），但数值确实来自同对象的另一条测量：不算编造。"""

        current = state(INFO, MEASURE, SEGMENT)
        result = self.audit(current, '分段测得平均基频为 95.203 Hz。[E1]')
        self.assertNotIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertGreaterEqual(current.metrics.get('report.sibling_citation', 0), 1)


class NumericRetryTests(unittest.TestCase):
    """云端报告阶段：对不上的数值先用掉一次 output 重试额度，再退回遮蔽。"""

    def test_first_mismatch_asks_the_model_to_fix_those_sentences(self):
        current = state(INFO, MEASURE)
        with self.assertRaises(ModelRetry) as caught:
            audit_report(current, report('平均基频为 233.700 Hz。[E2]'), allow_numeric_retry=True)
        message = str(caught.exception)
        self.assertIn('233.700', message)
        self.assertIn('{{calc:', message)
        self.assertIn('一行只说一个选区的一个量', message)
        self.assertEqual(current.metrics['audit_numeric_retry'], 1)
        self.assertEqual(current.metrics['guard_repairs']['report.number_retried'], 1)
        # 还没遮蔽：这一版根本不交付，改完再判。
        self.assertEqual(current.metrics['audit']['masked_count'], 0)

    def test_the_retry_budget_is_spent_once_per_report(self):
        current = state(INFO, MEASURE)
        with self.assertRaises(ModelRetry):
            audit_report(current, report('平均基频为 233.700 Hz。[E2]'), allow_numeric_retry=True)
        # 改完还是不对：不再退回去，按老规矩遮蔽 + 标未核对。
        result = audit_report(current, report('平均基频为 233.700 Hz。[E2]'), allow_numeric_retry=True)
        self.assertIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertFalse(current.metrics['audit']['verified'])
        self.assertEqual(current.metrics['audit']['masked_count'], 1)
        self.assertEqual(current.metrics['audit_numeric_retry'], 1)

    def test_callers_without_the_budget_keep_masking_immediately(self):
        """默认不开重试：本机交付与恢复稿仍然一次遮蔽（不浪费请求）。"""

        current = state(INFO, MEASURE)
        result = audit_report(current, report('平均基频为 233.700 Hz。[E2]'))
        self.assertIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertNotIn('audit_numeric_retry', current.metrics)

    def test_exhausted_audit_counts_masking_not_a_delivered_block(self):
        """审计放弃时把「拦截」转成「遮蔽」记账：门禁才好区分误伤与模型写错。"""

        current = state(INFO, MEASURE)
        # 轮数用尽（改不动）时正文保留、数值已遮蔽：这一轮不该再记一条「拦截」。
        result = audit_report(current, report('平均基频为 233.700 Hz。[E2]'), max_rounds=1)
        self.assertIn(UNVERIFIED_NUMBER, result.analysis)
        audit = current.metrics['audit']
        self.assertTrue(audit['exhausted'])
        self.assertFalse(audit['verified'])
        # 遮蔽照旧记账
        self.assertGreaterEqual(current.metrics['guard_repairs'].get('report.number_masked'), 1)
        # 但不再同时记成一条「拦在交付之外」的阻断
        self.assertNotIn('report.number_unmatched', current.metrics.get('guard_blocks') or {})

if __name__ == '__main__':
    unittest.main()
