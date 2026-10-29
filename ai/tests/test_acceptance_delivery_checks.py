"""Acceptance checks must reject incomplete arithmetic, not just spot keywords."""
import unittest

import verify_ai_dialogue_acceptance as acceptance
from praat_ai.escape_policy import AnalysisState


class AcceptanceDeliveryChecksTests(unittest.TestCase):
    def checks(self, case, status, sends, report):
        return {item['id']: item['ok'] for item in acceptance.delivery_checks(
            acceptance.CASES[case], status, sends, report)}

    def test_masked_difference_is_not_a_delivered_result(self):
        checks = self.checks('derived', 'partial', 4, '两者差值为 [推导未核对]。')
        self.assertFalse(checks['status'])
        self.assertFalse(checks['difference_stated'])

    def test_inaccurate_difference_does_not_pass_a_wide_band(self):
        checks = self.checks('derived', 'complete', 3, '两者的差值为 28 Hz。')
        self.assertFalse(checks['difference_stated'])

    def test_real_and_reasonably_rounded_difference_passes(self):
        for value in ('19.999', '20.00'):
            self.assertTrue(all(self.checks('derived', 'complete', 3,
                                           f'两者差值为 {value} Hz。').values()))

    def test_no_data_can_finish_locally_without_http(self):
        self.assertTrue(all(self.checks('no_data', 'partial', 0,
                                       '请先加载或选择声音，尚未取得测量结果。').values()))

    def test_ordinary_cases_cannot_pass_as_partial_or_without_http(self):
        checks = self.checks('pitch', 'partial', 0, '未完成')
        self.assertFalse(checks['status'])
        self.assertFalse(checks['http_observed'])

    def test_metrics_keep_cache_observations_and_missing_values(self):
        state = AnalysisState('test', '')
        state.metrics = {'request_timings': [dict(phase='report', input_tokens_total=100,
                          cache_read_tokens=40, cache_write_tokens=None)],
                         'phases': {'report': dict(requests=1, retries=0, input_tokens_total=100,
                          cache_read_tokens=40, cache_write_tokens=None, elapsed_seconds=2)}}
        metrics = acceptance._metrics_projection(state, 1)
        self.assertEqual(metrics['request_timings'][0]['cache_read_tokens'], 40)
        self.assertIsNone(metrics['request_timings'][0]['cache_write_tokens'])
        self.assertEqual(metrics['phases']['report']['elapsed_seconds'], 2)
        self.assertIsNone(metrics['phases']['report']['cache_write_tokens'])


if __name__ == '__main__':
    unittest.main()
