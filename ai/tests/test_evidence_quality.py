"""Accuracy regressions: candidate F0, screened observations and inference differ."""
import json
import threading
import unittest
from dataclasses import asdict

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import delivery_text, evidence_quality as quality
from praat_ai.cloud_agent import report_context, run_cloud_turn
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.report_guards import guard_delivered_text, report_context_data, INTERPRETATION_NOTE
from test_dialogue_stream import config

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\ttarget\t1\n2\tPitch\tderived\t0\n'
SCREENED = '基频质量：已筛查；参数：下限=50 Hz，上限=300 Hz；有效帧=30'


def item(rows, **args):
    return Evidence('measure', dict(object=1, **args), list(rows), CONTEXT)


def state(*items):
    return AnalysisState('根据已有声学结果分析发音和语调', CONTEXT, evidence=list(items))


class EvidenceQualityTests(unittest.TestCase):
    def test_finite_legacy_candidate_does_not_prove_quality(self):
        record = item(['平均基频 = 50.599 Hz'], pitch_floor=50, pitch_ceiling=300)
        self.assertEqual(quality.pitch_quality(record)['status'], 'unverified')
        self.assertFalse(quality.can_interpret_pitch(record))

    def test_screened_low_voice_is_not_rejected_by_absolute_frequency(self):
        record = item(['平均基频 = 55.000 Hz', '基频质量：已筛查；下限=35 Hz，上限=300 Hz'],
                      pitch_floor=35, pitch_ceiling=300)
        self.assertTrue(quality.can_interpret_pitch(record))
        text, status = guard_delivered_text(state(record), '平均基频 = 55.000 Hz。')
        self.assertIn('55.000 Hz', text)
        self.assertEqual(status, 'verified')

    def test_suspect_receipt_wins_and_is_not_a_quantity(self):
        record = item(['平均基频 = 50.599 Hz', SCREENED, '基频质量：待复核；近下限帧=18'],
                      pitch_floor=50, pitch_ceiling=300)
        self.assertEqual(quality.pitch_quality(record)['status'], 'suspect')
        refs = report_context_data(state(record)).get('quantity_refs', {})
        self.assertNotIn('E1', refs)
        self.assertFalse(quality.can_interpret_pitch(record))

    def test_quality_thresholds_and_counts_are_not_registered_as_f0(self):
        record = item(['平均基频 = 100.000 Hz', SCREENED])
        refs = report_context_data(state(record))['quantity_refs']['E1']
        self.assertEqual(refs, ['q1:pitch.mean'])

    def test_existing_pitch_does_not_inherit_sound_extraction_defaults(self):
        record = Evidence('pitch_statistics', {'object': 2, 'pitch_floor': 50}, ['平均 100 Hz'], CONTEXT)
        self.assertEqual(quality.pitch_configuration(record), (2, 'Pitch', None, None, None))
        self.assertFalse(quality.can_interpret_pitch(record))

    def test_mixed_bands_are_explicit_in_task_context(self):
        a = item(['平均基频 = 100 Hz'], pitch_floor=50, pitch_ceiling=300)
        b = item(['平均基频 = 107 Hz'], pitch_floor=75, pitch_ceiling=600)
        current = state(a, b)
        original = [asdict(e) for e in current.evidence]
        facts = report_context(current)
        self.assertEqual(facts['incompatible_pitch_settings'], [['E1', 'E2']])
        self.assertEqual([asdict(e) for e in current.evidence], original)
        json.dumps(facts, ensure_ascii=False)

    def test_average_absolute_slope_does_not_prove_flatness(self):
        record = item(['基频平均绝对斜率 = 334.57 Hz/s', SCREENED], parameter='pitch_slope')
        text, status = guard_delivered_text(state(record), '基频平均绝对斜率为 334.57 Hz/s，因此本段语调平淡。')
        self.assertIn('334.57 Hz/s', text)
        self.assertNotIn('本段语调平淡', text)
        self.assertIn(INTERPRETATION_NOTE, text)
        self.assertEqual(status, 'rejected_masked')

    def test_close_endpoints_cannot_prove_middle_is_flat(self):
        record = item(['起点基频（0–1 秒）= 100 Hz', '终点基频（0–1 秒）= 100 Hz', SCREENED],
                      parameter='pitch_start,pitch_end', **{'from': 0, 'to': 1})
        text, _ = guard_delivered_text(state(record), '本段语调平坦。[E1]')
        self.assertNotIn('本段语调平坦', text)

    def test_unverified_endpoints_do_not_prove_intonation(self):
        record = item(['起点基频（0–1 秒）= 120 Hz', '终点基频（0–1 秒）= 100 Hz'],
                      parameter='pitch_start,pitch_end', **{'from': 0, 'to': 1})
        text, _ = guard_delivered_text(state(record), '本段音高下降。[E1]')
        self.assertNotIn('本段音高下降', text)

    def test_screened_endpoints_only_support_matching_net_direction(self):
        record = item(['起点基频（0–1 秒）= 120 Hz', '终点基频（0–1 秒）= 100 Hz', SCREENED],
                      parameter='pitch_start,pitch_end', **{'from': 0, 'to': 1})
        good, _ = guard_delivered_text(state(record), '本段音高下降。[E1]')
        bad, _ = guard_delivered_text(state(record), '本段音高上升。[E1]')
        self.assertIn('本段音高下降', good)
        self.assertNotIn('本段音高上升', bad)

    def test_mixed_bands_cannot_combine_into_an_endpoint_pair(self):
        a = item(['起点基频（0–1 秒）= 120 Hz', SCREENED], parameter='pitch_start',
                 pitch_floor=50, pitch_ceiling=300, **{'from': 0, 'to': 1})
        b = item(['终点基频（0–1 秒）= 100 Hz', SCREENED], parameter='pitch_end',
                 pitch_floor=75, pitch_ceiling=600, **{'from': 0, 'to': 1})
        text, _ = guard_delivered_text(state(a, b), '本段音高下降。[E1][E2]')
        self.assertNotIn('本段音高下降', text)

    def test_local_and_rescue_outputs_remove_physiological_assertion(self):
        record = item(['平均基频 = 50.599 Hz'])
        for verify in (True, False):
            with self.subTest(verify=verify):
                text, status = guard_delivered_text(state(record),
                    '平均基频为 50.599 Hz，说明你的喉部肌肉过度紧张，气息支撑不足。', verify=verify)
                self.assertIn('50.599 Hz', text)
                self.assertNotIn('过度紧张', text)
                self.assertNotIn('支撑不足', text)
                self.assertIn(INTERPRETATION_NOTE, text)
                self.assertEqual(status, 'rejected_masked')

    def test_cloud_report_is_partial_not_falsely_verified_after_inference_removal(self):
        current = state(item(['平均基频 = 50.599 Hz']))
        current.continuation, current.continuation_mode = True, 'L2'
        def respond(*_):
            return ModelResponse([TextPart(json.dumps(dict(analysis=
                '平均基频为 50.599 Hz，说明你的喉部肌肉过度紧张。建议先核实周期跟踪，再讨论发音。',
                coverage=[]), ensure_ascii=False))])
        run_cloud_turn(config(), current, execute_action=lambda *_: self.fail('No tool dispatch'),
                       cancel=threading.Event(), model=FunctionModel(respond))
        self.assertNotIn('过度紧张', current.report)
        self.assertFalse(current.metrics.get('report_verified'))
        self.assertEqual(current.status, 'partial')
        self.assertNotIn('部分数值未能', current.report)
    def test_report_shape_from_the_accuracy_review_is_cleaned_end_to_end(self):
        """同一份报告结构：删掉无证据的定性/生理结论，保留实测数值与建议。"""
        record = item(['起点基频（0–1 秒）= 110.00 Hz', '终点基频（0–1 秒）= 95.00 Hz'],
                      parameter='pitch_start,pitch_end', **{'from': 0, 'to': 1})
        draft = '\n'.join([
            '**1. 语调平淡，缺乏起伏**',
            '标准日语中，通用说法是平板型或头高型。然而，本录音的整体基频走向非常平坦，几乎是一条直线。',
            '- **证据**：起点基频 110.00 Hz，终点基频 95.00 Hz（）。',
            '- **不足**：在「だ」处未出现预期的尾音下降。',
            '**2. 起始音高过低**',
            '录音前半段均值为 100.00 Hz（），远低于成年男性的常见舒适发声范围（一般知识）。',
            '- **不足**：这可能是由于喉部肌肉过度紧张或气息支撑不足造成的。',
            '- **改进**：练习时请放松下颌、自然发声。',
        ])
        text, status = guard_delivered_text(state(record), draft)
        # 展示层按真实管线再走一遍：引证与空括号在这里才被清掉。
        text = delivery_text.present_analysis(text)
        for gone in ('语调平淡', '几乎是一条直线', '尾音下降', '舒适发声范围',
                     '喉部肌肉过度紧张', '气息支撑不足'):
            with self.subTest(gone=gone):
                self.assertNotIn(gone, text)
        for kept in ('110.00 Hz', '95.00 Hz', '100.00 Hz', '放松下颌'):
            with self.subTest(kept=kept):
                self.assertIn(kept, text)
        self.assertNotIn('（）', text)
        self.assertIn(INTERPRETATION_NOTE, text)
        self.assertEqual(status, 'rejected_masked')



if __name__ == '__main__':
    unittest.main()
