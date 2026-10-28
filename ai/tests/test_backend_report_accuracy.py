"""Keep analysis settings distinct from F0 and preserve measured statistics."""
import unittest

from praat_ai.cloud_agent import AnalysisReport
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai import answer_contract, execution_core, pitch_events, report_guards


class BackendReportAccuracyTests(unittest.TestCase):
    def state(self, value=220):
        rows = ['@aipraat-pitch-v1\t0.2\t0.8\t0.01\tfiltered_autocorrelation\t75\t600\t已筛查']
        rows += [f'@aipraat-frame-v1\t{time}\t{value}\t1\t1\t1' for time in (.2, .21, .22)]
        analysis, _ = pitch_events.consume(rows, {}, {}, 'pitch_statistics')
        source = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tfixture\t1\t\t\n'
        return AnalysisState('测量平均基频', source, evidence=[Evidence(
            'pitch_statistics', {'object': 1}, pitch_events.result_rows(analysis, [], 'pitch_statistics'),
            source, acoustic=analysis.to_dict())])

    def verify(self, text, current=None):
        return report_guards.guard_delivered_text(current or self.state(), text)

    def test_actual_analysis_settings_and_frame_counts_are_reportable(self):
        for text in ('有效帧数为 3 帧。[E1]', '测量基于 3 个有效帧。[E1]', '基频提取范围为 75.0 Hz 至 600.0 Hz。[E1]',
                     '基频分析下限为 75 Hz。[E1]', '基频提取上限为 600 Hz。[E1]'):
            with self.subTest(text=text):
                body, status = self.verify(text)
                self.assertEqual(status, 'verified', body)

    def test_parameters_cannot_become_f0_measurements(self):
        for text in ('平均基频为 75 Hz。[E1]', '最高基频为 600 Hz。[E1]',
                     '平均基频为 3 Hz。[E1]', '有效帧数为 4 帧。[E1]',
                     '基频参数下限为 600 Hz。[E1]',
                     '基频提取范围为 80 Hz 至 600 Hz。[E1]'):
            with self.subTest(text=text):
                body, status = self.verify(text)
                self.assertEqual(status, 'rejected_masked', body)

    def test_registered_settings_have_separate_roles_and_count_dimension(self):
        refs = report_guards.report_context_data(self.state())['quantity_refs']['E1']
        self.assertIn('q8:analysis.pitch_floor', refs)
        self.assertIn('q9:analysis.pitch_ceiling', refs)
        self.assertIn('q10:analysis.accepted_frames', refs)
        self.assertNotEqual(report_guards._unit('frames')[2], report_guards._unit('Hz')[2])

    def test_measured_mean_is_referenced_without_recomputation(self):
        state = self.state()
        quantity = next(q for q in report_guards._quantities('E1', state.evidence[0]) if q.role=='pitch.mean')
        body, status = self.verify('平均基频为 {{q:'+quantity.ref+'}}。[E1]', state)
        self.assertEqual(status, 'verified', body)
        self.assertIn('220 Hz', body)
        instructions = report_guards.report_guard_instructions(state)
        self.assertIn('工具已返回的均值、斜率', instructions)

    def test_unmatched_range_bound_with_inherited_unit_is_masked(self):
        body, status = self.verify('测得基频范围为 110.0–330.0 Hz。[E1]')
        self.assertEqual(status, 'rejected_masked')
        self.assertNotIn('110.0', body)
        self.assertNotIn('330.0', body)
        self.assertIn(report_guards.UNVERIFIED_NUMBER, body)

    def test_valid_statistic_survives_an_unmatched_range(self):
        body, status = self.verify('平均基频为 220.0 Hz。[E1]\n基频范围为 110.0–330.0 Hz。[E1]')
        self.assertEqual(status, 'rejected_masked')
        self.assertIn('220.0', body)
        self.assertNotIn('330.0', body)

    def test_japanese_u_does_not_treat_devoicing_as_a_lip_rule(self):
        goal = '日语 /ɯ/ 和 /u/ 怎么纠音'
        for text in ('浊境/词首时双唇微圆。', '清化环境下唇形趋于扁平。',
                     '在清辅音后让唇部自然放松变平。'):
            self.assertTrue(answer_contract.knowledge_issues(goal, text), text)
        for text in ('清化与圆唇程度是不同维度。', '不能把词首微圆当成规则。',
                     '清辅音之间的 /u/ 可能清化。', '日语 /u/ 可有轻微唇压缩。'):
            self.assertFalse(answer_contract.knowledge_issues(goal, text), text)
        self.assertFalse(answer_contract.knowledge_issues('英语元音怎么练', '词首时双唇微圆。'))

    def test_silent_native_result_keeps_missingness_scope_and_reason(self):
        import test_pitch_scope
        step, _ = test_pitch_scope.PitchScopeTests().execute(
            'pitch_statistics', {}, '0')
        self.assertTrue(execution_core.valid_evidence(step, mutating=False, rows_valid=False))
        self.assertTrue(all(m['value'] is None and m['reason'] for m in step.acoustic['measures']))
        current = self.state()
        current.evidence = [execution_core.evidence_row(step, current.context_text)]
        body, status = self.verify('分析范围为 0.4–0.8 秒，没有足够有效基频帧，无法计算平均基频。', current)
        self.assertEqual(status, 'verified', body)

    def test_failed_execution_does_not_create_missingness_evidence(self):
        from types import SimpleNamespace
        step = SimpleNamespace(ok=False, results=['无法计算'], acoustic={
            'schema': 'acoustic-analysis-v1', 'kind': 'pitch',
            'measures': [{'value': None, 'reason': '没有有效帧'}]})
        self.assertFalse(execution_core.valid_evidence(step, mutating=False, rows_valid=False))


if __name__ == '__main__':
    unittest.main()
