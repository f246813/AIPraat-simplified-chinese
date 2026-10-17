from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from praat_ai.audio import SoundSamples
from praat_ai.models import FeatureTrack, PhoneFeatureTracks
from praat_ai.normalization import VERSION
from praat_ai.normalization_prosody import (PITCH_DEFAULTS, TARGET_LUFS, apply_pitch, apply_loudness,
    fit_pitch_baseline, reliable_pitch, evaluate_prosody_observations, apply_prosody_normalization)


def track(values):
    return FeatureTrack('pitch', [.05 + i*.01 for i in range(len(values))], list(values), 'Hz')


def phone(end=1.0, gain=1.0):
    times = [.05 + i*.01 for i in range(max(2, int((end-.1)*100)))]
    return PhoneFeatureTracks(7, 'a', 0, end, dict(
        rms=FeatureTrack('rms', times, [gain*.1/math.sqrt(2)]*len(times), 'amplitude'),
        intensity=FeatureTrack('intensity', times, [70+20*math.log10(gain)]*len(times), 'dB'),
        pitch=FeatureTrack('pitch', times, [100]*len(times), 'Hz')), alignment_source='provided')


def sound(duration=1, gain=1):
    sr = 48000
    return SoundSamples(np.sin(np.arange(int(sr*duration))/sr*2*np.pi*1000)*.1*gain, sr)


def opts(**kw):
    return dict(version=VERSION, pitch=True, pitch_confirmed=True, loudness=True,
                pitch_baseline_mode='utterance', **kw)


class PitchTests(unittest.TestCase):
    def test_vendored_hz_to_midi_known_values(self):
        from praat_ai._vendor.librosa import hz_to_midi
        np.testing.assert_allclose(hz_to_midi([110, 220, 440, 880]), [45, 57, 69, 81], atol=1e-12)

    def apply(self, lvals, rvals, options=None):
        left, right = [phone()], [phone()]
        original = deepcopy(left[0].to_dict())
        with patch('praat_ai.normalization_prosody.utterance_pitch', side_effect=[track(lvals), track(rvals)]):
            result = apply_pitch(options or opts(), left, right, [sound(), sound(gain=.5)], ['left', 'right'], language='ja', reading_id='ja')
        self.assertEqual(left[0].tracks['pitch'].to_dict(), original['tracks']['pitch'])
        return result, left, right

    def test_scale_invariance_and_no_range_standardization(self):
        values = np.array([100, 105, 110, 120, 130, 140])
        result, left, right = self.apply(values, values*2)
        self.assertEqual(result['status'], 'applied')
        np.testing.assert_allclose(left[0].derived_tracks['pitch_relative_st'].values, right[0].derived_tracks['pitch_relative_st'].values, atol=1e-12)
        widened = 110 * (values/110)**2
        result, left, right = self.apply(values, widened)
        self.assertGreater(np.ptp(right[0].derived_tracks['pitch_relative_st'].values), np.ptp(left[0].derived_tracks['pitch_relative_st'].values)*1.9)
        obs = evaluate_prosody_observations(dict(pitch=result, loudness={'status':'disabled'}), left, right)
        self.assertTrue(obs)
        self.assertGreater(obs[0].evidence['normalized_distance'], .1)
        self.assertFalse(obs[0].evidence['diagnostic'])
        self.assertEqual(obs[0].phone_index, 7)
        self.assertEqual(result['source'], 'utterance')

    def test_log_median_not_linear_median(self):
        result = fit_pitch_baseline(track([100, 100, 400, 400]), settings=dict(floor_hz=50, ceiling_hz=500))
        # The 24-st jump is not the explicitly conservative +/-12-st suspect detector.
        self.assertAlmostEqual(result['baseline_hz'], 200)
        self.assertEqual(result['raw_median_hz'], 250)

    def test_gaps_octave_suspicions_and_no_cross_gap_dtw(self):
        values = [100]*4+[math.nan]*3+[100]*4+[200]*4
        reliable, quality = reliable_pitch(track(values), PITCH_DEFAULTS)
        self.assertTrue(math.isnan(reliable[4]))
        self.assertEqual(quality['octave_jump_frames'], [10, 11])
        result, left, right = self.apply(values, values)
        relative = left[0].derived_tracks['pitch_relative_st'].values
        self.assertTrue(math.isnan(relative[4]))
        obs = evaluate_prosody_observations(dict(pitch=result, loudness={'status':'disabled'}), left, right)
        obs = [o for o in obs if o.feature == 'pitch_relative_st']
        self.assertEqual(len(obs), 3)
        self.assertTrue(all(o.evidence['reference_interval'][1] < .1 or o.evidence['reference_interval'][0] > .1 for o in obs))
        self.assertTrue(all(o.evidence['method'] == 'dtw_contiguous_run' for o in obs))

    def test_no_baseline_no_confirmation_and_unmatched_gaps(self):
        result, left, right = self.apply([math.nan]*10, [100]*10)
        self.assertEqual(result['status'], 'insufficient_evidence')
        result, _, _ = self.apply([100]*10, [100]*10, {**opts(), 'pitch_confirmed':False})
        self.assertEqual(result['status'], 'insufficient_evidence')
        result, left, right = self.apply([100]*4+[math.nan]+[100]*4, [100]*9)
        self.assertEqual(evaluate_prosody_observations(dict(pitch=result, loudness={'status':'disabled'}), left, right), [])

    def test_independent_baselines_no_mixed_source_or_settings(self):
        options = {**opts(), 'pitch_baseline_mode':'independent'}
        result, _, _ = self.apply([100]*10, [100]*10, options)
        self.assertEqual(result['status'], 'insufficient_evidence')
        for role in ('reference', 'learner'):
            options[role+'_pitch_baseline'] = fit_pitch_baseline(track([110]*10), settings=PITCH_DEFAULTS,
                language='ja', reading_id='ja', material={'audio_identity':role})
        result, left, right = self.apply([100]*10, [110]*10, options)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['source'], 'independent')
        self.assertAlmostEqual(left[0].derived_tracks['pitch_relative_st'].values[0], 12*math.log2(100/110))
        options['reference_pitch_baseline']['measurement_settings'] = {}
        result, _, _ = self.apply([100]*10, [110]*10, options)
        self.assertEqual(result['status'], 'insufficient_evidence')


class LoudnessTests(unittest.TestCase):
    def test_actual_meter_known_signal_gain_invariance_and_raw_unchanged(self):
        left, right = [phone()], [phone(gain=.1)]
        sounds = [sound(), sound(gain=.1)]
        original = [s.samples.copy() for s in sounds]
        raw = [deepcopy(p[0].to_dict()) for p in (left, right)]
        result = apply_loudness(opts(), left, right, sounds)
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['method'], 'integrated_lufs_constant_gain')
        self.assertAlmostEqual(result['reference_baseline']['integrated_lufs'], -23.04504759460712, places=7)
        self.assertEqual(result['target_lufs'], TARGET_LUFS)
        for feature in result['feature_ids']:
            np.testing.assert_allclose(left[0].derived_tracks[feature].values, right[0].derived_tracks[feature].values, atol=1e-10)
        for i in range(2):
            np.testing.assert_array_equal(sounds[i].samples, original[i])
        for p, before in zip((left, right), raw):
            self.assertEqual(p[0].to_dict()['tracks'], before['tracks'])
        gain = result['quality'][0]['gain_linear']
        import pyloudnorm
        self.assertAlmostEqual(pyloudnorm.Meter(48000).integrated_loudness(sounds[0].samples*gain), -23, places=8)

    def test_common_target_changes_do_not_change_paired_db_difference(self):
        deltas = []
        for target in (-23, -19):
            left, right = [phone()], [phone(gain=.3)]
            sounds = [sound(), sound(gain=.5)]
            with patch('praat_ai.normalization_prosody.TARGET_LUFS', target):
                result = apply_loudness(opts(), left, right, sounds)
            self.assertEqual(result['status'], 'applied')
            deltas.append(right[0].derived_tracks['intensity_lufs_adjusted_db'].values[0] - left[0].derived_tracks['intensity_lufs_adjusted_db'].values[0])
        self.assertAlmostEqual(deltas[0], deltas[1])

    def test_unaccepted_package_version_is_not_applied(self):
        versions = {'pyloudnorm':'0.2.0', 'scipy':'1.15.0', 'numpy':'2.2.6'}
        left, right = [phone()], [phone()]
        with patch('importlib.metadata.version', side_effect=lambda name: versions[name]):
            result = apply_loudness(opts(), left, right, [sound(), sound()])
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertIn('版本不匹配', result['dependency_error'])
        # 报告里的原因要直接说明缺什么/版本怎么不对（能力门控与运行路径同一份结论）。
        self.assertIn('响度归一化未应用', result['reason'])
        self.assertIn('scipy>=1.14.1,<1.15', result['reason'])
        self.assertEqual(left[0].derived_tracks, {})

    def test_same_process_stale_version_and_bad_reference_probe_are_rejected(self):
        versions = {'pyloudnorm':'0.2.0', 'scipy':'1.14.1', 'numpy':'2.2.5'}
        with patch('importlib.metadata.version', side_effect=lambda name: versions[name]):
            result = apply_loudness(opts(), [phone()], [phone()], [sound(), sound()])
        self.assertIn('仍载入旧版本', result['dependency_error'])
        with patch('pyloudnorm.Meter') as meter:
            meter.return_value.integrated_loudness.return_value = -20
            result = apply_loudness(opts(), [phone()], [phone()], [sound(), sound()])
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertIn('参考探针失败', result['dependency_error'])

    def test_local_level_contrast_survives_constant_gain(self):
        left, right = [phone()], [phone()]
        r = right[0].tracks['intensity']; midpoint = len(r.values)//2
        r.values[midpoint:] = [v+6 for v in r.values[midpoint:]]
        rsound = sound(); rsound.samples[len(rsound.samples)//2:] *= 2
        result = apply_loudness(opts(), left, right, [sound(), rsound])
        self.assertEqual(result['status'], 'applied')
        values = right[0].derived_tracks['intensity_lufs_adjusted_db'].values
        self.assertAlmostEqual(values[-1]-values[0], 6)
        observations = evaluate_prosody_observations(dict(loudness=result, pitch={'status':'disabled'}), left, right)
        self.assertGreater(observations[0].evidence['normalized_distance'], .1)

    def test_any_short_recording_forces_both_to_active_rms(self):
        left, right = [phone(.2)], [phone(.2, gain=2)]
        with patch.dict('sys.modules', {'pyloudnorm':None}):
            result = apply_loudness(opts(), left, right, [sound(.2), sound(1,gain=2)])
        self.assertEqual(result['status'], 'applied')
        self.assertEqual(result['method'], 'utterance_active_rms')
        self.assertEqual(result['reference_baseline']['intervals'], [[0,.2]])
        self.assertNotIn('integrated_lufs', result['learner_baseline'])
        np.testing.assert_allclose(left[0].derived_tracks['rms_active_relative_db'].values, right[0].derived_tracks['rms_active_relative_db'].values)
        self.assertNotIn('rms_lufs_adjusted', right[0].derived_tracks)

    def test_silence_unreliable_intervals_nan_and_missing_dependency(self):
        silent = SoundSamples(np.zeros(48000), 48000)
        with np.errstate(all='ignore'):
            result = apply_loudness(opts(), [phone()], [phone()], [silent, silent])
        self.assertEqual(result['status'], 'insufficient_evidence')
        self.assertIsNone(result['reference_baseline']['integrated_lufs'])
        json.dumps(result, allow_nan=False)
        p = phone(.2); p.alignment_source='proportional'
        result = apply_loudness(opts(), [p], [phone(.2)], [sound(.2), sound(.2)])
        self.assertEqual(result['status'], 'insufficient_evidence')
        with patch.dict('sys.modules', {'pyloudnorm':None}):
            result = apply_loudness(opts(), [phone()], [phone()], [sound(), sound()])
        self.assertEqual(result['repair_action'], 'path_settings.configure_runtime')
        bad = sound(); bad.samples[0] = np.nan
        bad_result = apply_loudness(opts(), [phone()], [phone()], [bad, sound()])
        self.assertEqual(bad_result['status'], 'insufficient_evidence')
        json.dumps(bad_result, allow_nan=False)

    def test_clipping_quality_is_not_repaired_and_disabled_legacy_do_not_load(self):
        clipped = sound(); clipped.samples[0] = 1
        result = apply_loudness(opts(), [phone()], [phone()], [clipped, sound()])
        self.assertTrue(result['quality'][0]['possible_existing_clipping'])
        self.assertEqual(clipped.samples[0], 1)
        self.assertEqual(apply_loudness({}, [], [], [])['status'], 'disabled')
        self.assertEqual(apply_prosody_normalization(None, [], [], [], [], language='ja', reading_id='ja', applicable=True), {})
        result = apply_prosody_normalization(opts(), [], [], [], [], language='ja', reading_id='ja', applicable=False)
        self.assertEqual(result['pitch']['status'], 'not_applicable')
        self.assertEqual(result['loudness']['status'], 'not_applicable')


if __name__ == '__main__':
    unittest.main()
