"""S6：遥测聚合 + 黄金集门禁（离线部分）。"""
import unittest
from types import SimpleNamespace

import golden_backend
from praat_ai import telemetry


def row(**fields):
    base = {'index': 1, 'status': 'complete', 'requests': 5, 'wall_seconds': 20.0,
            'report_verified': True, 'guard_blocks': {}, 'content': '平均基频 94 Hz，说明发声放松。'}
    base.update(fields)
    return base


class TelemetryTests(unittest.TestCase):
    def test_collect_keeps_only_aggregatable_numbers(self):
        metrics = {'requests': 3, 'total_seconds': 12.3456, 'report_verified': True,
                   'guard_blocks': {'report.number_unmatched': 2},
                   'guard_repairs': {'report.number_masked': 1},
                   'audit': {'rounds': 2, 'masked_count': 1},
                   'router': {'source': 'model', 'intent': 'analyze', 'confidence': 0.93},
                   'finish_state': 'partial_with_text', 'reason_class': 'budget'}
        copy = telemetry.collect(metrics)
        self.assertEqual(copy['requests'], 3)
        self.assertEqual(copy['wall_seconds'], 12.346)
        self.assertEqual(copy['masked'], 1)
        self.assertEqual(copy['blocked'], 2)
        self.assertEqual(copy['repaired'], 1)
        self.assertEqual(copy['router_source'], 'model')
        self.assertEqual(copy['reason_class'], 'budget')
        # 原文一个都不带
        self.assertNotIn('rejected_report', copy)

    def test_counters_flatten_blocks_and_repairs(self):
        rows = telemetry.counters({'guard_blocks': {'a': 2}, 'guard_repairs': {'b': 1},
                                   'report_derived_accepted': 3})
        self.assertEqual({(item['rule'], item['kind'], item['count']) for item in rows},
                         {('a', 'block', 2), ('b', 'repair', 1), ('report_derived_accepted', 'repair', 3)})

    def test_slim_drops_drafts_and_caps_timings(self):
        metrics = {'rejected_report': 'x' * 4000, 'rejected_coverage': [1, 2, 3],
                   'request_timings': [{'n': index} for index in range(40)],
                   'audit': {'masked': ['a'] * 200}}
        slim = telemetry.slim(metrics)
        self.assertNotIn('rejected_report', slim)
        self.assertEqual(slim['rejected_report_dropped'], 4000)
        self.assertEqual(slim['rejected_coverage_dropped'], 3)
        self.assertEqual(len(slim['request_timings']), telemetry.TIMING_LIMIT)
        self.assertEqual(slim['request_timings_dropped'], 40 - telemetry.TIMING_LIMIT)
        self.assertLessEqual(len(str(slim)), 4096)

    def test_slim_never_exceeds_the_budget(self):
        metrics = {f'k{index}': 'y' * 400 for index in range(30)}
        slim = telemetry.slim(metrics)
        self.assertLessEqual(telemetry._size(slim), telemetry.DEBUG_LIMIT)

    def test_summarize_aggregates_runs_and_rules(self):
        rows = [telemetry.payload({'requests': 4, 'report_verified': True,
                                   'guard_blocks': {'x': 1}}, status='complete'),
                telemetry.payload({'requests': 6, 'report_verified': False}, status='partial')]
        summary = telemetry.summarize(rows)
        self.assertEqual(summary['runs'], 2)
        self.assertEqual(summary['verified'], 1)
        self.assertEqual(summary['requests_avg'], 5.0)
        self.assertEqual(summary['blocks'], {'x': 1})
        self.assertEqual(telemetry.summarize([]), {'runs': 0})


class GoldenGateTests(unittest.TestCase):
    def test_clean_rows_pass(self):
        self.assertEqual(golden_backend.evaluate([row(), row(index=2)]), [])

    def test_internal_terms_fail_the_gate(self):
        failures = golden_backend.evaluate([row(content='尚未完成：L2 阶段时间上限已用完')])
        self.assertTrue(any('内部术语' in item for item in failures), failures)

    def test_blocks_and_request_budget_fail_the_gate(self):
        failures = golden_backend.evaluate(
            [row(guard_blocks={'report.number_unmatched': 1}), row(index=2, requests=20)])
        self.assertTrue(any('阻断' in item for item in failures), failures)
        self.assertTrue(any('平均请求' in item for item in failures), failures)

    def test_low_verified_rate_fails_the_gate(self):
        rows = [row(report_verified=False), row(index=2, report_verified=False)]
        failures = golden_backend.evaluate(rows)
        self.assertTrue(any('核对通过率' in item for item in failures), failures)

    def test_error_rows_fail_the_gate(self):
        failures = golden_backend.evaluate([row(error='RuntimeError: boom')])
        self.assertTrue(any('异常' in item for item in failures), failures)

    def test_empty_input_is_a_failure(self):
        self.assertEqual(golden_backend.evaluate([]), ['没有任何一轮结果'])

    def test_mock_gate_round_trip_is_reachable(self):
        """离线骨架：mock 模型跑 1 轮，门禁机制本身要能用（不校验模型质量）。"""

        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as root:
            code = golden_backend.main([
                '--model', 'mock', '--runs', '1', '--min-verified', '0',
                '--max-blocks', '5', '--max-requests', '30',
                '--out', str(Path(root) / 'golden.json'),
                '--ai-root', str(Path(__file__).resolve().parents[1])])
        self.assertEqual(code, 0)


if __name__ == '__main__':
    unittest.main()
