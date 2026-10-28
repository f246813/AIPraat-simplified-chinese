"""Tone-related claims through native chat measurements and report validation."""
import unittest

from praat_ai import pitch_events
from praat_ai.cloud_agent import AnalysisReport, Coverage
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.report_guards import _quantities, validate_report, sanitize_prosody_claims
import test_pitch_scope


SOURCE = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
          '1\tSound\tfixture\t1\t0.4\t0.8\n')


def state_for(step):
    return AnalysisState('分析选区的声调', SOURCE, evidence=[Evidence(
        'measure', {'parameter':'pitch_start,pitch_end'}, step.results, SOURCE,
        acoustic=step.acoustic)])


def validate(state, text):
    return validate_report(state, AnalysisReport(analysis=text+'\n观察只针对当前对象和已记录范围，其他范围需要相应测量。',
        coverage=[Coverage(item='声调观察', status='partial', explanation='基频观察与声调类别分别核对。')]))


class ToneEvidenceTests(unittest.TestCase):
    def execute(self, formula):
        return test_pitch_scope.PitchScopeTests().execute(
            'measure', {'parameter':'pitch_start,pitch_end'}, formula)

    def test_rising_falling_level_and_unavailable_endpoints(self):
        for rate in (60, -60, 0, None):
            with self.subTest(rate=rate):
                formula = '0' if rate is None else f'0.3*sin(2*pi*(220*x+{rate}/2*x*x))'
                step, frames = self.execute(formula)
                by_name = {m['name']:m for m in step.acoustic['measures']}
                events = {e['id']:e for e in step.acoustic['events']}
                accepted = [f for f in frames if f['status']=='screened']
                if rate is None:
                    for measure in by_name.values():
                        self.assertIsNone(measure['value'])
                        self.assertTrue(measure['reason'])
                        self.assertIsNone(events[measure['inputs'][0]]['time'])
                    for direction in ('上升', '下降'):
                        self.assertIn('缺少同目标', validate(state_for(step), f'本段基频{direction}。[E1]').analysis)
                    continue
                for role, frame in (('start', accepted[0]), ('end', accepted[-1])):
                    measure = by_name['pitch.'+role]
                    event = events[measure['inputs'][0]]
                    self.assertEqual(event['time'], frame['time'])
                    self.assertEqual(measure['value'], frame['value'])
                    self.assertTrue(any(f"{event['time']:.6f} 秒" in row for row in step.results))
                change = by_name['pitch.end']['value'] - by_name['pitch.start']['value']
                self.assertAlmostEqual(change, rate*(accepted[-1]['time']-accepted[0]['time']), delta=.3)
                if rate:
                    direction, opposite = ('上升','下降') if rate>0 else ('下降','上升')
                    state = state_for(step)
                    self.assertNotIn('缺少同目标', validate(state, f'本段基频{direction}。[E1]').analysis)
                    self.assertIn('缺少同目标', validate(state, f'本段基频{opposite}。[E1]').analysis)
                roles = {q.role for q in _quantities('E1', state_for(step).evidence[0])}
                self.assertTrue({'pitch.start','pitch.end','pitch.start_time','pitch.end_time'} <= roles)

    def test_internal_voicing_does_not_cover_silent_selection_edges(self):
        step, frames = self.execute('if x > 0.52 and x < 0.68 then '
                                   '0.3*sin(2*pi*(220*x+30*x*x)) else 0 fi')
        accepted = [f for f in frames if f['status']=='screened']
        self.assertTrue(accepted)
        self.assertGreater(accepted[0]['time'], .5)
        self.assertLess(accepted[-1]['time'], .7)
        result = validate(state_for(step), '本段基频上升。[E1]')
        self.assertIn('缺少同目标', result.analysis)

    def test_observation_is_not_a_lexical_tone_classification(self):
        step, _ = self.execute('0.3*sin(2*pi*(220*x+30*x*x))')
        for category in ('第一声', '二声', '第三声', '四声', '阴平', '阳平', '上声', '去声', '轻声'):
            with self.subTest(category=category):
                text = f'本段是{category}。[E1]'
                state = state_for(step)
                self.assertIn('缺少同目标', validate(state, text).analysis)
                clean, removed = sanitize_prosody_claims(state, text)
                self.assertTrue(removed)
                self.assertNotIn(category, clean)
                self.assertNotIn('缺少同目标', validate(state, f'无法判断本段是否为{category}。').analysis)
        self.assertNotIn('缺少同目标', validate(state_for(step), '使用同一声音对象作比较。').analysis)
        self.assertNotIn('缺少同目标', validate(state_for(step), '一般知识：第四声通常称为去声。').analysis)

    def test_exact_zero_endpoint_change_does_not_prove_rising_or_falling(self):
        rows = ['@aipraat-pitch-v1\t0.4\t0.8\t0.01\tfiltered_autocorrelation\t75\t600\t已筛查']
        rows += [f'@aipraat-frame-v1\t{t}\t220\t1\t1\t1' for t in (.4, .6, .8)]
        analysis, _ = pitch_events.consume(rows, {'parameter':'pitch_start,pitch_end'}, {}, 'measure')
        from types import SimpleNamespace
        step = SimpleNamespace(acoustic=analysis.to_dict(), results=pitch_events.result_rows(analysis, [], 'measure'))
        for direction in ('上升', '下降'):
            self.assertIn('缺少同目标', validate(state_for(step), f'本段基频{direction}。[E1]').analysis)


if __name__ == '__main__':
    unittest.main()
