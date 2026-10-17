from __future__ import annotations

from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from praat_ai.models import AnalysisRequest, AnalysisResult, FeatureTrack, PhoneFeatureTracks, PhoneSpec
from praat_ai.normalization import (VERSION, INVENTORIES, MEASUREMENT_SETTINGS, audio_identity,
    fit_formant_calibration, transform_formants, apply_formant_normalization, import_formant_calibration)
from praat_ai.pronunciation_rules import PROFILES, evaluate_phone_rules
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.report import write_json, write_textgrid, write_overlay_png
from praat_ai.tutor import request_from_dict

PROFILE = 'ja-JP-standard-v1'


def phone(index=1, ipa='a', value=500.0):
    start = (index - 1) * 0.2
    times = [start + f * 0.2 for f in (.1, .4, .5, .6, .9)]
    return PhoneFeatureTracks(index, ipa, start, start + .2,
        {f: FeatureTrack(f, times, [value * multiplier] * 5, 'Hz')
         for f, multiplier in [('f1', 1), ('f2', 3), ('f3', 5)]},
        alignment_source='provided')


def calibration_phones():
    return [phone(i * 3 + j + 1, vowel, 200 + i * 100 + j * 10)
            for i, vowel in enumerate(INVENTORIES[PROFILE]) for j in range(3)]


def fit(phones=None, material_id='independent'):
    return fit_formant_calibration(calibration_phones() if phones is None else phones,
        speaker_label='speaker', language='ja', profile_id=PROFILE,
        material=dict(audio_identity=material_id))


def options(left=None, right=None):
    return dict(version=VERSION, formants=True, confirmed=True,
                reference_calibration=fit() if left is None else left,
                learner_calibration=fit(material_id='another') if right is None else right)


def apply(opts, references=None, learners=None, **kwargs):
    return apply_formant_normalization(opts, references if references is not None else [phone()],
        learners if learners is not None else [phone()], language='ja', profile_id=PROFILE, **kwargs)


def make_wav(path, frequency=220):
    rate = 16000
    with wave.open(str(path), 'wb') as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(rate)
        t = np.arange(rate) / rate
        f.writeframes((np.sin(2 * np.pi * frequency * t) * 16000).astype('<i2').tobytes())


class CalibrationTests(unittest.TestCase):
    def test_sample_sd_and_equal_token_weight(self):
        phones = calibration_phones()
        result = fit(phones)
        values = [p.tracks['f1'].values[0] for p in phones]
        self.assertAlmostEqual(result['formants']['f1']['sigma_hz'], np.std(values, ddof=1))
        # Long/more frequent vowels do not add weight to the fit.
        phones += [phone(30 + i, 'a', 9000) for i in range(10)]
        self.assertEqual(fit(phones)['formants']['f1']['mu_hz'], result['formants']['f1']['mu_hz'])
        self.assertEqual(result['ddof'], 1)
        self.assertEqual(result['status'], 'applied')

    def test_middle_window_median_and_invalid_frames(self):
        phones = calibration_phones()
        for p in phones:
            p.tracks['f1'].values = [9000, 300, 400, math.nan, 9000]
        result = fit(phones)
        # All selected tokens have a middle-window median of 350, hence SD=0.
        self.assertEqual(result['formants']['f1']['status'], 'insufficient_evidence')
        self.assertEqual(result['formants']['f1']['selected_tokens']['a'][0]['value_hz'], 350)
        self.assertEqual(result['formants']['f2']['status'], 'applied')

    def test_affine_invariance_and_frozen_local_difference(self):
        original = fit()
        changed = calibration_phones()
        for p in changed:
            for track in p.tracks.values():
                track.values = [v * 1.7 + 170 for v in track.values]
        shifted = fit(changed, material_id='shifted')
        left, right = [phone(value=480)], [phone(value=480)]
        for track in right[0].tracks.values():
            track.values = [v * 1.7 + 170 for v in track.values]
        snapshot = deepcopy(shifted)
        transform_formants(left, original); transform_formants(right, shifted)
        for f in ('f1', 'f2', 'f3'):
            np.testing.assert_allclose(left[0].derived_tracks[f + '_lobanov_z'].values,
                                       right[0].derived_tracks[f + '_lobanov_z'].values, atol=1e-12)
        right[0].tracks['f1'].values = [v + 200 for v in right[0].tracks['f1'].values]
        transform_formants(right, shifted)
        self.assertGreater(right[0].derived_tracks['f1_lobanov_z'].values[0], left[0].derived_tracks['f1_lobanov_z'].values[0])
        self.assertEqual(snapshot, shifted)  # transformation never updates parameters
        self.assertEqual(left[0].tracks['f1'].values[0], 480)

    def test_masks_zero_negative_and_z_zero_are_not_missing(self):
        p = phone(value=fit()['formants']['f1']['mu_hz'])
        p.tracks['f1'].values[0:3] = [0, -1, math.nan]
        transform_formants([p], fit())
        values = p.derived_tracks['f1_lobanov_z'].values
        self.assertTrue(all(math.isnan(v) for v in values[:3]))
        self.assertEqual(values[3:], [0, 0])
        self.assertEqual(p.derived_tracks['f1_lobanov_z'].to_dict()['values'][:3], [None]*3)
        self.assertEqual(p.tracks['f1'].values[:2], [0, -1])

    def test_single_vowel_two_tokens_zero_variance_and_missing_f3(self):
        for phones in ([phone(1), phone(2)], [p for p in calibration_phones() if p.ipa == 'a']):
            self.assertEqual(fit(phones)['status'], 'insufficient_evidence')
        constant = calibration_phones()
        for p in constant:
            p.tracks['f1'].values = [500]*5
            p.tracks.pop('f3')
        result = fit(constant)
        self.assertIn('方差', result['formants']['f1']['reason'])
        self.assertEqual(result['formants']['f2']['status'], 'applied')
        self.assertEqual(result['formants']['f3']['status'], 'insufficient_evidence')
        no_f3 = calibration_phones()
        for p in no_f3:
            p.tracks.pop('f3')
        self.assertEqual(fit(no_f3)['status'], 'applied')

    def test_unknown_reading_has_no_guessed_inventory(self):
        result = fit_formant_calibration(calibration_phones(), speaker_label='s', language='en', profile_id='en-GB')
        self.assertEqual(result['formants']['f1']['status'], 'not_applicable')

    def test_settings_language_reading_protocol_confirmation_and_self_calibration(self):
        for key, value in [('measurement_settings', {}), ('language', 'en'), ('reading_id', 'other'),
                           ('protocol', 'other'), ('ddof', 0), ('speaker_label', '')]:
            left = fit(); left[key] = value
            with self.subTest(key=key):
                self.assertEqual(apply(options(left))['formants']['f1']['status'], 'insufficient_evidence')
        opts = options(); opts['confirmed'] = False
        self.assertIn('未确认', apply(opts)['formants']['f1']['reason'])
        self.assertIn('待测', apply(options(), target_ids=('independent',))['formants']['f1']['reason'])
        opts = options(); opts.pop('learner_calibration')
        left, right = [phone()], [phone()]
        self.assertIn('学习者', apply(opts, left, right)['formants']['f1']['reason'])
        self.assertEqual(left[0].derived_tracks, {})
        self.assertEqual(right[0].derived_tracks, {})

    def test_target_missing_frames_never_claims_applied(self):
        left, right = [phone()], [phone()]
        right[0].tracks.pop('f1')
        result = apply(options(), left, right)
        self.assertEqual(result['formants']['f1']['status'], 'insufficient_evidence')
        self.assertEqual(result['formants']['f2']['status'], 'applied')
        self.assertNotIn('f1_lobanov_z', left[0].derived_tracks)
        self.assertEqual(result['formants']['f1']['valid_target_frames']['learner'], 0)

    def test_disabled_absent_and_legacy_preserve_raw(self):
        self.assertIsNone(apply(None))
        opts = options(); opts['formants'] = False
        self.assertEqual(apply(opts)['formants']['f1']['status'], 'disabled')
        result = apply_formant_normalization(options(), [phone()], [phone()], language='ja', profile_id=None)
        self.assertEqual(result['formants']['f1']['status'], 'not_applicable')

    def test_only_matching_pair_derives_and_f3_is_independent(self):
        left, right = fit(), fit()
        right['formants']['f3'] = dict(status='insufficient_evidence', reason='no f3')
        refs, learners = [phone(1, 'ɹ')], [phone(1, 'ɹ', 450)]
        metadata = apply(options(left, right), refs, learners)
        self.assertEqual(set(refs[0].derived_tracks), {'f1_lobanov_z', 'f2_lobanov_z'})
        observations, assessments = evaluate_phone_rules(PROFILES[0], refs, learners, normalization=metadata)
        derived = [o for o in observations if o.feature.endswith('_lobanov_z')]
        self.assertEqual([o.feature for o in derived], ['f2_lobanov_z'])
        self.assertTrue(derived[0].evidence['experimental_approximant_extension'])


class NormalizationIntegrationTests(unittest.TestCase):
    def test_new_json_is_frozen_old_json_unchanged(self):
        payload = dict(reference_object='ref', learner_object='learner', language='ja', phonemes=[], normalization=options())
        req = request_from_dict(payload)
        payload['normalization']['reference_calibration']['speaker_label'] = 'changed'
        self.assertEqual(req.normalization['reference_calibration']['speaker_label'], 'speaker')
        payload.pop('normalization')
        self.assertNotIn('normalization', request_from_dict(payload).to_dict())

    def test_analysis_rules_json_textgrid_png_same_feature_and_position(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); ref = root/'ref.wav'; learner = root/'learner.wav'
            make_wav(ref); make_wav(learner, 230)
            request = AnalysisRequest(str(ref), str(learner), 'ja',
                [PhoneSpec('a', reference_start=0, reference_end=.2, learner_start=0, learner_end=.2,
                           phone_index=7, alignment_source='provided')],
                profile_id=PROFILE, normalization=options(), qwen_explain=False)
            refs, learns = [phone(1)], [phone(1, value=550)]
            refs[0].phone_index = learns[0].phone_index = 7
            with patch('praat_ai.pronunciation.extract_phone_tracks', side_effect=[refs, learns]):
                result = analyze_pronunciation(request)
            observations = {o.feature: o for o in result.observations}
            self.assertIn('f1', observations)
            observation = observations['f1_lobanov_z']
            self.assertEqual(observation.unit, 'z')
            self.assertEqual(observation.phone_index, 7)
            self.assertEqual(observation.start, learns[0].start)
            self.assertAlmostEqual(observation.reference_value, refs[0].derived_tracks[observation.feature].values[0])
            self.assertEqual(observation.score, 0)
            self.assertEqual(result.errors, [])
            self.assertEqual(observation.evidence['normalization_version'], VERSION)
            report = root/'report.json'; write_json(result, report)
            parsed = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(parsed['reference_phones'][0]['tracks']['f1']['unit'], 'Hz')
            self.assertEqual(parsed['reference_phones'][0]['derived_tracks']['f1_lobanov_z']['unit'], 'z')
            self.assertEqual(parsed['normalization']['reference_calibration'], request.normalization['reference_calibration'])
            grid = root/'result.TextGrid'; write_textgrid(result, grid)
            self.assertIn(observation.message, grid.read_text(encoding='utf-8'))
            import parselmouth
            self.assertEqual(parselmouth.read(str(grid)).class_name, 'TextGrid')
            # Capture plot labels: the derived curve is explicitly in z, not Hz.
            from PIL import ImageDraw
            original = ImageDraw.ImageDraw.text
            labels = []
            def record(draw, xy, text, *args, **kwargs):
                labels.append(text); return original(draw, xy, text, *args, **kwargs)
            with patch.object(ImageDraw.ImageDraw, 'text', record):
                self.assertTrue(write_overlay_png(result, root/'overlay.png'))
            self.assertTrue(any('f1_lobanov_z [z]' in label for label in labels))
            self.assertGreater((root/'overlay.png').stat().st_size, 1000)

    def test_import_real_textgrid_and_material_identity(self):
        import parselmouth
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); wav = root/'cal.wav'; make_wav(wav)
            grid = root/'cal.TextGrid'
            # Use a real Praat-generated grid so both long and short formats are parsed.
            from parselmouth.praat import call
            tg = call('Create TextGrid...', 0, 1, 'phones', '')
            for index, format in enumerate(('text', 'short_text')):
                if format == 'text':
                    tg.save(str(grid), parselmouth.Data.FileFormat.TEXT)
                else:
                    tg.save(str(grid), parselmouth.Data.FileFormat.SHORT_TEXT)
                with patch('praat_ai.audio.extract_phone_tracks', return_value=calibration_phones()):
                    result = import_formant_calibration(wav, grid, speaker_label='s', language='ja', profile_id=PROFILE)
                self.assertEqual(result['status'], 'applied')
                self.assertEqual(len(result['material']['audio_identity']), 64)
                metadata = apply(options(result), target_ids=(result['material']['audio_identity'],))
                self.assertIn('待测', metadata['formants']['f1']['reason'])

    def test_real_extraction_smoke_uses_recorded_burg_settings(self):
        import parselmouth
        from parselmouth.praat import call
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); wav = root/'cal.wav'; make_wav(wav)
            grid = call('Create TextGrid...', 0, 1, 'phones', '')
            call(grid, 'Set interval text', 1, 1, 'a')
            grid.save(str(root/'cal.TextGrid'))
            result = import_formant_calibration(wav, root/'cal.TextGrid', speaker_label='s', language='ja', profile_id=PROFILE)
            self.assertEqual(result['measurement_settings'], MEASUREMENT_SETTINGS)
            self.assertEqual(result['formants']['f1']['coverage']['a'], 1)
            self.assertEqual(result['status'], 'insufficient_evidence')  # not a multi-vowel calibration

    def test_reading_mismatch_skips_without_inventing_target_calibration(self):
        result = apply(options(), reading_id='different-preset')
        self.assertIn('读音', result['formants']['f1']['reason'])


if __name__ == '__main__':
    unittest.main()
