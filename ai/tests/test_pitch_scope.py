"""F0 quantities through the chat dispatcher and the native Praat engine."""
import json
import math
import re
from pathlib import Path
import tempfile
import unittest

import native_praat
from praat_ai import pitch_events, tools


class PitchScopeTests(unittest.TestCase):
    def execute(self, tool, arguments, formula, *, existing=False):
        from praat_ai import chat
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
                      + ('2\tPitch' if existing else '1\tSound')
                      + '\tfixture\t1\t0.4\t0.8\n')
            context = tools.ToolContext(tools.parse_object_context(source), root/'result.tsv', root/'state.txt')
            def run(script):
                prefix = 'Create Sound from formula: "fixture", 1, 0, 1.2, 16000, ~ ' + formula + '\n'
                if existing:
                    prefix += 'To Pitch (raw autocorrelation): 0.01, 60, 900, 15, "no", 0.03, 0.45, 0.01, 0.35, 0.14\n'
                native_praat.run(prefix + script)
                return True, context.result_path.read_text(encoding='utf-8-sig').splitlines(), ''
            step = chat._execute_action(dict(tool=tool, arguments=arguments), context, run, 1)
            self.assertTrue(step.ok, step.observation)
            self.assertIn('observations_path', step.acoustic, step.results)
            frames = json.loads(Path(step.acoustic['observations_path']).read_text(encoding='utf-8'))['frames']
            raw = context.result_path.read_text(encoding='utf-8-sig')
            for measure in step.acoustic['measures']:
                if measure['name'] == 'pitch.slope_octave_free' and measure['value'] is not None:
                    script_value = float(re.search(r'不含倍频跳变的基频斜率 = ([0-9.]+) 半音/s', raw)[1])
                    self.assertAlmostEqual(script_value, measure['value'], delta=.006)
            return step, frames

    def test_octave_free_slope_uses_only_the_selected_interval(self):
        # The middle is steady 200 Hz, while both unselected sides sweep.
        formula = ('0.3*sin(2*pi*(if x < 0.35 then 100*x+140*x*x '
                   'else if x < 0.85 then 200*x-17.85 else 200*x+100*(x-0.85)^2-17.85 fi fi))')
        step, frames = self.execute('measure', {'parameter': 'pitch_slope_octave_free'}, formula)
        self.assertEqual(len(step.acoustic['measures']), 1)
        measure = step.acoustic['measures'][0]
        self.assertEqual(measure['name'], 'pitch.slope_octave_free')
        self.assertEqual(measure['unit'], '半音/s')
        self.assertLess(measure['value'], .1)
        self.assertTrue(all(.4 <= f['time'] <= .8 for f in frames))
        self.assertTrue(any('不含倍频跳变的基频斜率' in row for row in step.results))
        outside, frames = self.execute('measure', {
            'parameter': 'pitch_slope_octave_free', 'from': .9, 'to': 1.1}, formula)
        self.assertGreater(outside.acoustic['measures'][0]['value'], 1)
        self.assertTrue(all(.9 <= f['time'] <= 1.1 for f in frames))

    def test_octave_free_slope_retains_native_span_across_a_gap(self):
        formula = ('if x < 0.52 then 0.3*sin(2*pi*180*x) else '
                   '(if x > 0.66 then 0.3*sin(2*pi*220*x) else 0 fi) fi')
        step, frames = self.execute('measure', {'parameter': 'pitch_slope_octave_free,pitch_slope'}, formula)
        accepted = [f for f in frames if f['status'] == 'screened']
        self.assertTrue(any(f['status'] == 'rejected' for f in frames))
        changes = [abs(12*math.log2(b['value']/a['value'])) % 12 for a,b in zip(accepted, accepted[1:])]
        expected = sum(min(c, 12-c) for c in changes) / (accepted[-1]['time']-accepted[0]['time'])
        measure = next(m for m in step.acoustic['measures'] if m['name'] == 'pitch.slope_octave_free')
        self.assertAlmostEqual(measure['value'], expected)
        from praat_ai.escape_policy import Evidence
        from praat_ai.report_guards import _quantities
        evidence = Evidence('measure', {}, step.results, '', acoustic=step.acoustic)
        roles = {q.role for q in _quantities('E1', evidence)}
        self.assertTrue({'pitch.slope_absolute', 'pitch.slope_octave_free'} <= roles)

    def test_octave_free_slope_reports_motion_magnitude_and_silence(self):
        for rate in (60, 0, -60, None):
            with self.subTest(rate=rate):
                formula = '0' if rate is None else f'0.3*sin(2*pi*(220*x+{rate}/2*x*x))'
                step, frames = self.execute('measure', {'parameter': 'pitch_slope_octave_free'}, formula)
                measure = step.acoustic['measures'][0]
                if rate is None:
                    self.assertIsNone(measure['value'])
                    self.assertTrue(measure['reason'])
                    self.assertFalse(any(f['status'] == 'screened' for f in frames))
                elif rate == 0:
                    self.assertLess(measure['value'], .1)
                else:
                    self.assertGreater(measure['value'], 1)
                    self.assertGreaterEqual(measure['value'], 0)

    def test_existing_pitch_does_not_invent_extraction_settings(self):
        step, _ = self.execute('pitch_statistics', {}, '0.3*sin(2*pi*220*x)', existing=True)
        method = step.acoustic['method']
        self.assertEqual(method['name'], 'existing_pitch')
        for name in ('floor_hz', 'ceiling_hz', 'silence_threshold', 'purpose'):
            self.assertIsNone(method[name], name)


class OctaveFreeDefinitionTests(unittest.TestCase):
    def test_native_octave_folding_and_elapsed_span(self):
        # Praat folds each absolute semitone jump into [0, 6], then divides
        # by the first-to-last voiced span, including intervening silence.
        for frequencies in ((100, 200, 100), (100, 400, 100), (100, 150, 100)):
            with self.subTest(frequencies=frequencies):
                rows = ['@aipraat-pitch-v1\t0\t0.1\t0.01\texisting_pitch\t--undefined--\t--undefined--\t未核验']
                rows += [f'@aipraat-frame-v1\t{t}\t{f}\t1\t1\t1'
                         for t, f in zip((.01, .02, .05), frequencies)]
                analysis, _ = pitch_events.consume(rows, {'parameter': 'pitch_slope_octave_free'}, {}, 'measure')
                change = abs(12 * math.log2(frequencies[1] / frequencies[0])) % 12
                expected = 2 * min(change, 12-change) / .04
                self.assertAlmostEqual(analysis.measures[0].value, expected)


if __name__ == '__main__':
    unittest.main()
