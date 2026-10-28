"""Candidates must be validated before deriving or explaining acoustic measures."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from praat_ai import acoustic_events as ae


def candidates(**updates):
    result = dict(scope_start=.1, scope_end=.55, source_start=0, source_end=.7,
                  burst=.3, pre_start=.14, pre_end=.23, pre_f0=220, post_start=.328,
                  vowel=-1, truncated=0, separated=1, burst_rise=12, high_band=1,
                  closure_start=-1)
    result.update(updates)
    return result


class AcousticEventTests(unittest.TestCase):
    def test_interrupted_voicing_with_confirmed_closure_keeps_negative_vot(self):
        analysis = ae.analyse_vot(candidates(), {'closure_start': .12}, {'object_id': 1})
        self.assertAlmostEqual(analysis.measures[0].value, -160)
        self.assertEqual(analysis.measures[0].inputs, ['pre_start', 'burst'])
        self.assertTrue(any(x.kind == 'stop_closure' for x in analysis.intervals))

    def test_prior_voice_without_closure_is_a_hypothesis_not_a_validated_vot(self):
        analysis = ae.analyse_vot(candidates(), {}, {})
        self.assertLess(analysis.measures[0].value, 0)
        self.assertEqual(analysis.measures[0].status, 'provisional')
        self.assertIn('尚未确认', analysis.events[1].reason)
        self.assertAlmostEqual(analysis.measures[1].value, 28)

    def test_invalid_release_and_truncated_onset_never_generate_signed_vot(self):
        for updates in ({'burst': .9}, {'truncated': 1}, {'high_band': 0}, {'burst_rise': 2}):
            with self.subTest(updates=updates):
                analysis = ae.analyse_vot(candidates(**updates), {}, {})
                self.assertIsNone(analysis.measures[0].value)

    def test_manual_signed_measurements_and_vowel_interval_are_independent(self):
        for onset in (.2, .3, .4):
            values = candidates(pre_start=onset if onset < .3 else -1,
                                post_start=onset if onset >= .3 else -1, vowel=.413,
                                separated=0)
            analysis = ae.analyse_vot(values, {'burst': .3, 'voicing': onset, 'vowel': .413}, {})
            self.assertAlmostEqual(analysis.measures[0].value, (onset - .3) * 1000)
            self.assertAlmostEqual(analysis.measures[2].value, 113)

    def test_scope_is_bound_to_real_sound_domain(self):
        with self.assertRaisesRegex(ValueError, '时间域'):
            ae.analyse_vot(candidates(source_end=.2), {}, {})

    def test_report_separates_conclusion_events_and_calculation(self):
        analysis = ae.analyse_vot(candidates(separated=0), {}, {})
        body = ae.render_report(analysis.to_dict())
        for heading in ('## 测量结论', '## 事件与区间', '## 计算依据', '## 限制'):
            self.assertIn(heading + '\n\n', body)
        conclusion = body.split('## 事件与区间')[0]
        self.assertNotIn('×', conclusion)
        self.assertNotIn('vot_ms', body)
        self.assertIn('× 1000', body)

    def test_negative_vot_and_positive_thirteen_ms_are_distinct_measures(self):
        values=candidates(scope_start=3.7,scope_end=4.3,source_start=3.7,source_end=4.3,
            burst=4.017,pre_start=3.874,pre_end=4.0,post_start=4.030,vowel=4.030)
        analysis=ae.analyse_vot(values,{'vowel':4.030},{'kind':'synthetic_landmarks'})
        self.assertAlmostEqual(analysis.measures[0].value,-143)
        self.assertAlmostEqual(analysis.measures[1].value,13)
        self.assertAlmostEqual(analysis.measures[2].value,13)
        self.assertNotEqual(analysis.measures[0].inputs,analysis.measures[2].inputs)

    def test_coincident_zero_vot_landmarks_export_one_point_with_both_roles(self):
        analysis=ae.analyse_vot(candidates(pre_start=-1,post_start=.3),{'burst':.3,'voicing':.3},{})
        grid=ae.to_textgrid(analysis)
        self.assertAlmostEqual(analysis.measures[0].value,0)
        self.assertIn('release_burst [validated]; post_release_periodicity [validated]',grid)


PRAAT = Path(os.environ.get('PRAAT_AI_PRAAT_EXECUTABLE') or
             Path(__file__).resolve().parents[2] / 'Praat.exe')


@unittest.skipUnless(PRAAT.is_file(), '需要真实 Praat')
class AcousticPipelineTests(unittest.TestCase):
    def test_missing_vot_preserves_other_measurements_as_partial_evidence(self):
        from praat_ai import chat, tools, execution_core, workflow_policy
        from native_praat import run
        from test_vot_prevoicing import VotAcousticTests
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            source='id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tfixture\t1\t0.2\t0.55\n'
            context=tools.ToolContext(tools.parse_object_context(source),root/'result.tsv',root/'state.txt')
            def execute(script):
                run('Create Sound from formula: "fixture", 1, 0, 0.7, 44100, ~ '+
                    VotAcousticTests.signal(.05)+'\n'+script)
                return True,context.result_path.read_text(encoding='utf-8-sig').splitlines(),''
            step=chat._execute_action({'tool':'vot','arguments':{'from':.2,'to':.55}},context,execute,1)
            self.assertFalse(step.ok)
            self.assertIsNone(step.acoustic['measures'][0]['value'])
            self.assertIsNotNone(step.acoustic['measures'][1]['value'])
            self.assertTrue(execution_core.valid_evidence(step,mutating=False))
            state=execution_core.evidence_from_steps(source,[step],is_mutating=tools.is_mutating)
            self.assertEqual(len(state.evidence),1)
            state.goal='计算VOT'
            state.attempts=[execution_core.attempt_record(step)]
            self.assertFalse(workflow_policy.measurement_complete(state))
            from praat_ai.report_guards import _quantities, validate_report
            from praat_ai.cloud_agent import AnalysisReport
            quantities=_quantities('E1',state.evidence[0])
            self.assertFalse(any(q.role=='vot.value' for q in quantities))
            value=step.acoustic['measures'][1]['value']
            from pydantic_ai import ModelRetry
            with self.assertRaises(ModelRetry):
                validate_report(state,AnalysisReport(analysis=f'VOT = {value:.3f} 毫秒。[E1]\n该值用于本次测量的结论，仍需结合地标核对。',coverage=[]))

    def test_actual_chat_executes_candidates_validates_derives_and_exports_tiers(self):
        from praat_ai import chat, tools, execution_core, workflow_policy
        from praat_ai.escape_policy import AnalysisState
        from test_vot_prevoicing import VotAcousticTests
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tfixture\t1\t0.1\t0.55\n'
            context = tools.ToolContext(tools.parse_object_context(source), root/'result.tsv', root/'state.txt')

            def execute(script):
                path = root/'measure.praat'
                path.write_text('seed = random_initializeWithSeedUnsafelyButPredictably (5489)\n'
                    'Create Sound from formula: "fixture", 1, 0, 0.7, 44100, ~ ' +
                    VotAcousticTests.signal(.2) + '\n' + script, encoding='utf-8')
                proc = subprocess.run([str(PRAAT), '--no-pref-files', '--no-plugins', '--FULL-TRUST',
                                       '--run', str(path)], capture_output=True, timeout=30)
                return proc.returncode == 0, context.result_path.read_text(encoding='utf-8-sig').splitlines(), proc.stderr.decode('utf-8', 'replace')

            step = chat._execute_action({'tool': 'vot', 'arguments': {'from': .1, 'to': .55}}, context, execute, 1)
            self.assertTrue(step.ok, step.observation)
            self.assertLess(step.acoustic['measures'][0]['value'], -50)
            self.assertFalse(any(ae.RECEIPT in row for row in step.results))
            state = AnalysisState('计算当前选区的VOT', source)
            state.task_id = 'fixture'
            state.attempts = [execution_core.attempt_record(step)]
            state.evidence = [execution_core.evidence_row(step, source)]
            workflow_policy.publish_results(state)
            workflow_policy.finish_locally(state)
            self.assertEqual(state.status, 'complete')
            self.assertIn('## 计算依据', state.report)
            self.assertTrue(state.results[0]['acoustic']['checks'])
            restored = json.loads(json.dumps(execution_core.evidence_dicts(state)))
            self.assertEqual(restored[0]['acoustic']['events'], step.acoustic['events'])
            annotation = Path(step.acoustic['annotation_path'])
            verify = root/'verify.praat'
            check = root/'tiers.txt'
            verify.write_text(f'Read from file: {tools.quote(annotation)}\n'
                'first = Is interval tier: 1\nsecond = Is interval tier: 2\n'
                f'appendFileLine: {tools.quote(check)}, first\n'
                f'appendFileLine: {tools.quote(check)}, second\n', encoding='utf-8')
            proc = subprocess.run([str(PRAAT), '--no-pref-files', '--no-plugins', '--FULL-TRUST',
                                   '--run', str(verify)], capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf-8', 'replace'))
            self.assertEqual(check.read_text(encoding='utf-8-sig').splitlines(), ['0', '1'])


if __name__ == '__main__':
    unittest.main()
