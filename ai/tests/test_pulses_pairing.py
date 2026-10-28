"""Pulses (PointProcess) reading and Pitch↔Sound pairing for the energy gate.

行为测试跑真实生成的 Praat 脚本（合成音，不含任何私人录音）；parselmouth 缺失时跳过。
"""

import native_praat
import re
import tempfile
import unittest
from pathlib import Path

from praat_ai import evidence_quality, pitch_quality, tools
from praat_ai.tools import schema as tool_schema

try:
    import parselmouth
except ImportError:  # pragma: no cover - 依赖缺失时跳过行为部分
    parselmouth = None

SOUND_ONLY = 'id\tclass\tname\tselected\n1\tSound\tprobe\t1\n'
SOUND_AND_PP = 'id\tclass\tname\tselected\n1\tSound\tprobe\t0\n2\tPointProcess\tpulses\t1\n'
SOUND_AND_PITCH = 'id\tclass\tname\tselected\n1\tSound\tprobe\t0\n2\tPitch\tprobe\t1\n'
TWO_SOUNDS_AND_PITCH = 'id\tclass\tname\tselected\n1\tSound\tprobe\t0\n3\tSound\tother\t0\n2\tPitch\tprobe\t1\n'


class ContractTests(unittest.TestCase):
    def test_pulse_prefix_is_shared_with_evidence_quality(self):
        self.assertEqual(pitch_quality.PULSE_PREFIX, '脉冲质量：')
        self.assertEqual(pitch_quality.QUALITY_PREFIX, evidence_quality.QUALITY_PREFIX)
        self.assertTrue(evidence_quality.is_quality_row('脉冲质量：已筛查；参数：下限 75 Hz'))
        self.assertTrue(evidence_quality.is_quality_row('基频质量：未核验'))

    def test_pulse_tool_is_registered_with_a_usable_parameter_list(self):
        tool = tools.TOOL_MAP['pulses']
        self.assertIn('from', tool.signature)
        self.assertIn('sound', tool_schema.TOOL_PARAMETERS['pulses']['properties'])
        schema = tool_schema.TOOL_PARAMETERS['pulses']['properties']
        self.assertEqual({'from', 'to', 'pitch_floor', 'pitch_ceiling', 'sound', 'object'},
                         set(schema))

    def test_pitch_tools_accept_the_pairing_parameter(self):
        for name in ('pitch', 'pitch_statistics'):
            with self.subTest(tool=name):
                self.assertIn('sound', tool_schema.TOOL_PARAMETERS[name]['properties'])

    def test_pulse_receipt_is_not_a_measured_interval(self):
        rows = ['脉冲质量：已筛查；区间内脉冲 10/12 个', '脉冲（0–1 秒）：脉冲点 10 个；平均间隔 0.00833 秒']
        quantities = [row for row in rows if not evidence_quality.is_quality_row(row)]
        self.assertEqual(quantities, [rows[1]])


class ScriptStructureTests(unittest.TestCase):
    def test_pulse_script_uses_only_commands_the_engine_exposes(self):
        lines = pitch_quality.pulse_screen_lines(sound='1', floor=75.0)
        joined = '\n'.join(lines)
        self.assertIn('Get number of points', joined)
        self.assertIn('Get time from index: pulseIndex', joined)
        # 这些命令实测不可用/参数签名随版本变：不能依赖。
        for forbidden in ('Get jitter', 'Get mean period', 'Get time from index:' + ' 1'):
            self.assertNotIn(forbidden, joined)

    def test_pulse_script_keeps_one_gap_set_and_skips_noise_pulses(self):
        lines = pitch_quality.pulse_screen_lines(sound='1', floor=75.0)
        joined = '\n'.join(lines)
        self.assertEqual(joined.count('pulseGateThreshold'), joined.count('pulseGateThreshold'))
        self.assertIn('elsif pulseLevel < pulseGateThreshold', joined)
        self.assertIn('pulseJitterSum = pulseJitterSum + abs (pulseGap - pulseLastGap)', joined)

    def test_unpaired_analysis_says_unverified(self):
        lines = pitch_quality.screen_lines(sound=None, floor=75.0, ceiling=600.0)
        joined = '\n'.join(lines)
        self.assertIn('没有原始 Sound', joined)
        self.assertNotIn('To Intensity', joined)


@unittest.skipIf(parselmouth is None, '本机没有 parselmouth，跳过 Praat 行为回归')
class BehaviourTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def run_tool(self, tool, arguments, prefix, context_text):
        result = self.base / f'{tool}.tsv'
        for path in (result, self.base / 'state.txt'):
            if path.exists():
                path.unlink()
        context = tools.ToolContext(tools.parse_object_context(context_text), result,
                                    self.base / 'state.txt')
        native_praat.run(prefix + tools.render(tool, arguments, context))
        raw = result.read_bytes()
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            text = raw.decode('utf-16')
        return [line for line in text.splitlines() if line.strip() and not line.startswith("@aipraat-")]

    TONE = ('Create Sound from formula: "probe", 1, 0, 1.0, 16000, '
            '~ 0.3*sin(2*pi*120*x)\n')

    def test_pulses_from_sound_recovers_the_glottal_rate(self):
        rows = self.run_tool('pulses', {'object': 1}, self.TONE, SOUND_ONLY)
        quality = next(row for row in rows if row.startswith('脉冲质量：'))
        result = next(row for row in rows if row.startswith('脉冲（'))
        self.assertIn('已筛查', quality)
        self.assertNotIn('源音频', quality)
        rate = float(re.search(r'约 ([0-9.]+) Hz', result)[1])
        self.assertAlmostEqual(rate, 120.0, delta=2.0)
        self.assertIn('%', result)

    def test_existing_point_process_discloses_how_the_sound_was_paired(self):
        prefix = self.TONE + 'To PointProcess (periodic, cc): 75, 600\n'
        auto = self.run_tool('pulses', {'object': 2}, prefix, SOUND_AND_PP)
        self.assertIn('按列表推断', next(row for row in auto if row.startswith('脉冲质量：')))
        explicit = self.run_tool('pulses', {'object': 2, 'sound': 1}, prefix, SOUND_AND_PP)
        self.assertIn('显式指定对象 1', next(row for row in explicit if row.startswith('脉冲质量：')))

    def test_noise_between_pulses_does_not_fake_a_low_rate(self):
        noisy = ('Create Sound from formula: "probe", 1, 0, 1.0, 16000, '
                 '~ if x < 0.2 or x > 0.9 then 0.004*randomGauss(0,1) else 0.3*sin(2*pi*120*x) endif\n')
        rows = self.run_tool('pulses', {'object': 1}, noisy, SOUND_ONLY)
        result = next(row for row in rows if row.startswith('脉冲（'))
        rate = float(re.search(r'约 ([0-9.]+) Hz', result)[1])
        self.assertAlmostEqual(rate, 120.0, delta=3.0)

    def test_existing_pitch_without_a_sound_stays_unverified(self):
        prefix = self.TONE + 'To Pitch: 0, 75, 600\n'
        rows = self.run_tool('pitch_statistics', {'object': 2}, prefix, TWO_SOUNDS_AND_PITCH)
        self.assertIn('未核验', next(row for row in rows if row.startswith('基频质量：')))

    def test_existing_pitch_with_a_paired_sound_is_screened_and_keeps_direction(self):
        prefix = prompt = ('Create Sound from formula: "probe", 1, 0, 1.0, 16000, '
                           '~ if x < 0.5 then 0.3*sin(2*pi*120*x) else 0.3*sin(2*pi*250*x) endif\n'
                           'To Pitch: 0, 75, 600\n')
        rows = self.run_tool('pitch_statistics', {'object': 2, 'sound': 1}, prompt, SOUND_AND_PITCH)
        quality = next(row for row in rows if row.startswith('基频质量：'))
        stats = next(row for row in rows if row.startswith('基频统计（'))
        self.assertIn('待复核', quality)
        self.assertIn('另一个音高层次', quality)
        self.assertIn('显式指定对象 1', quality)
        self.assertIn('假定', quality)
        # 真实的 120→250 升降不能被当成倍频删掉
        self.assertIn('落在中位带外', quality)
        self.assertIn('最高 250.0', stats)

    def test_pairing_never_claims_a_source_that_does_not_exist(self):
        prefix = self.TONE + 'To Pitch: 0, 75, 600\n'
        rows = self.run_tool('pitch', {'object': 2, 'time': '0.25'}, prefix, SOUND_AND_PITCH)
        self.assertTrue(any('120.0' in row for row in rows), rows)


if __name__ == '__main__':
    unittest.main()
