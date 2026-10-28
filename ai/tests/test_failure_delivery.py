"""Failure replies must not invent completed measurements or expose SDK errors."""
import unittest

from praat_ai.delivery_text import classify, user_reason
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.workflow_policy import finish_locally


class FailureDeliveryTests(unittest.TestCase):
    def test_english_connection_errors_are_transient(self):
        for error in ('Connection error.', 'Request timed out.', 'SSL handshake failed', 'TLS EOF'):
            with self.subTest(error=error):
                self.assertEqual(classify(error), 'transient')

    def test_empty_evidence_never_claims_retained_measurements(self):
        for reason in ('Connection error.', '请求预算耗尽', '缺少分析组件', '用户取消', '无效对象'):
            with self.subTest(reason=reason):
                state = AnalysisState('测量当前声音的平均基频', '', reason=reason)
                finish_locally(state)
                self.assertEqual(state.status, 'partial')
                self.assertNotIn('已保留测得', state.report)
                self.assertNotIn('测量仍然有效', state.report)
                self.assertIn('尚未取得测量结果', state.report)

    def test_real_measurements_are_retained_and_acknowledged(self):
        state = AnalysisState('分析基频', '', reason='Connection error.',
                              evidence=[Evidence('pitch', {}, ['基频 = 220 Hz'], 'fixture')],
                              measurement_content='基频 = 220 Hz')
        finish_locally(state)
        self.assertIn('220 Hz', state.report)
        self.assertIn('已保留测得的数值', state.report)
        self.assertEqual(state.metrics['reason_class'], 'transient')

    def test_operation_confirmation_is_not_a_measurement(self):
        state = AnalysisState('测量基频', '', reason='Connection error.',
                              evidence=[Evidence('duplicate_object', {}, ['已完成复制'], 'fixture', kind='operation')])
        finish_locally(state)
        self.assertIn('尚未取得测量结果', state.report)
        self.assertNotIn('已保留测得', state.report)

    def test_fallback_keeps_internal_error_only_in_diagnostics(self):
        state = AnalysisState('测量基频', '', reason='ModelRetry report.number_unmatched Connection error.')
        text = state.fallback_report()
        self.assertIn('连接没成功', text)
        self.assertIn('尚未取得测量结果', text)
        self.assertNotIn('ModelRetry', text)
        self.assertNotIn('report.number_unmatched', text)
        self.assertEqual(state.metrics['reason_detail'], state.reason)

    def test_explicit_measurement_flag_controls_guarantee(self):
        self.assertIn('已保留测得', user_reason('连接失败', has_measurements=True))
        self.assertNotIn('已保留测得', user_reason('连接失败', has_measurements=False))


if __name__ == '__main__':
    unittest.main()
