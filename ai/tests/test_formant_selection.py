from copy import deepcopy
import json
import math
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import wave

import numpy as np
import parselmouth
from scipy.signal import lfilter

from praat_ai.audio import read_wav
from praat_ai.bridge import SelectedObject
from praat_ai.formant_selection import (QUALITY, analyze_candidates, decide, evaluate_quality,
                                       public_analysis, select_candidate)
from praat_ai.normalization import MEASUREMENT_SETTINGS, audio_identity
from praat_ai.ui import TutorFormApplication
from test_normalization import PROFILE, calibration_phones


def material(root):
    rate, length = 16000, .4
    chunks, labels = [], []
    inventory = [('a', 700, 1200), ('i', 300, 2300), ('ɯ', 400, 1400), ('e', 500, 1900), ('o', 450, 850)]
    for vowel, f1, f2 in inventory:
        for repeat in range(3):
            excitation = np.zeros(round(rate*length))
            excitation[::round(rate/(110+repeat*10))] = 1
            samples = excitation
            for frequency, bandwidth in ((f1, 60), (f2, 90), (2800, 120), (3800, 180), (4700, 220)):
                pole = math.exp(-math.pi*bandwidth/rate)
                samples = lfilter([1.], [1., -2*pole*math.cos(2*math.pi*frequency/rate), pole*pole], samples)
            samples = samples/max(abs(samples))*.5
            chunks.append(samples)
            labels.append(vowel)
    wav = root/'independent.wav'
    with wave.open(str(wav), 'wb') as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(rate)
        handle.writeframes((np.concatenate(chunks)*32767).astype('<i2').tobytes())
    grid = root/'independent.TextGrid'
    lines = ['File type = "ooTextFile"', 'Object class = "TextGrid"', '', 'xmin = 0', f'xmax = {len(labels)*length}',
        'tiers? <exists>', 'size = 1', 'item []:', '    item [1]:', '        class = "IntervalTier"',
        '        name = "phones"', '        xmin = 0', f'        xmax = {len(labels)*length}', f'        intervals: size = {len(labels)}']
    for i, label in enumerate(labels):
        lines.extend([f'        intervals [{i+1}]:', f'            xmin = {i*length}',
                      f'            xmax = {(i+1)*length}', f'            text = "{label}"'])
    grid.write_text('\n'.join(lines), encoding='utf-8')
    return wav, grid


def analyze(wav, grid, **kwargs):
    return analyze_candidates(wav, grid, speaker_label='speaker', language='ja', profile_id=PROFILE, **kwargs)


def candidate(key, score, status='eligible'):
    return dict(id=key, parameters={**MEASUREMENT_SETTINGS, 'maximum_formant_hz': 5000.},
                quality=dict(status=status, score=score), calibration=dict(status='applied'))


class CandidateQualityTests(unittest.TestCase):
    def test_clear_ambiguous_and_unavailable_decisions(self):
        self.assertEqual(decide([candidate('a', .02), candidate('b', .5)])['recommended_id'], 'a')
        self.assertIsNone(decide([candidate('a', .02), candidate('b', .03)])['recommended_id'])
        self.assertEqual(decide([candidate('a', None, 'unavailable')])['status'], 'unavailable')
        self.assertEqual(decide([candidate('a', 0.)])['status'], 'suggested')
        self.assertEqual(decide([])['status'], 'unavailable')

    def test_zero_jitter_valid_f1_f2_and_missing_f3(self):
        phones = calibration_phones()
        for phone in phones:
            phone.tracks['f3'].values = [math.nan]*len(phone.tracks['f3'].values)
        quality = evaluate_quality(phones, ('a', 'i', 'ɯ', 'e', 'o'))
        self.assertEqual(quality['status'], 'eligible', quality)
        self.assertEqual(quality['score'], 0.)

    def test_silence_missing_negative_and_unstable_frames_do_not_qualify(self):
        for samples in ([0.]*5, [math.nan]*5, [-100.]*5, [100., 100., 1000., 100., 100.]):
            phones = calibration_phones()
            for phone in phones:
                phone.tracks['f1'].values = samples.copy()
            self.assertEqual(evaluate_quality(phones, ('a', 'i', 'ɯ', 'e', 'o'))['status'], 'uncertain')


class CandidateAcousticTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.wav, self.grid = material(self.root)

    def test_real_burg_candidates_keep_protocol_actual_settings_and_observations(self):
        result = analyze(self.wav, self.grid, bandwidth_hz=6000)
        self.assertEqual(len(result['candidates']), 5)
        self.assertEqual(result['bandwidth']['effective_cap_hz'], 6000)
        self.assertIsNotNone(result['engine']['praat'])
        self.assertEqual(result['candidates'][-1]['quality']['status'], 'unavailable')
        available = [c for c in result['candidates'] if c.get('calibration', {}).get('status') == 'applied']
        self.assertTrue(available, [(c['parameters']['maximum_formant_hz'], c['quality']) for c in result['candidates']])
        selected = select_candidate(result, available[0]['id'])
        self.assertEqual(selected['measurement_settings'], available[0]['parameters'])
        self.assertEqual(selected['formant_selection']['source'], 'manual_candidate_override')
        self.assertTrue(selected['formant_selection']['candidates'][0]['tracks'][0]['tracks']['f1']['analysis']['measurement_id'])
        public = public_analysis(result)
        self.assertNotIn('tracks', public['candidates'][0])
        json.dumps(result, allow_nan=False)
        json.dumps(selected, allow_nan=False)
        with self.assertRaises(ValueError):
            select_candidate(result, 'forged')

    def test_target_material_and_invalid_bandwidth_rejected(self):
        with self.assertRaisesRegex(ValueError, '待测录音'):
            analyze(self.wav, self.grid, target_ids=(audio_identity(read_wav(self.wav)),))
        for value in (0, -1, math.nan, math.inf, True, 9000):
            with self.subTest(value=value), self.assertRaises(ValueError):
                analyze(self.wav, self.grid, bandwidth_hz=value)

    def test_ambiguous_candidates_do_not_auto_apply_but_manual_override_is_traced(self):
        result = analyze(self.wav, self.grid)
        usable = [c for c in result['candidates'] if c.get('calibration', {}).get('status') == 'applied']
        self.assertTrue(usable)
        result['decision'] = dict(status='uncertain', recommended_id=None, reason='fixture ambiguity')
        with self.assertRaisesRegex(ValueError, '可靠区分'):
            select_candidate(result, usable[0]['id'], automatic=True)
        selected = select_candidate(result, usable[0]['id'])
        self.assertEqual(selected['formant_selection']['decision']['status'], 'uncertain')

    def test_real_form_rpc_selection_binding_and_frozen_request(self):
        targets = []
        for index in (4, 9):
            target = self.root/f'target-{index}.wav'
            parselmouth.Sound(np.sin(np.arange(16000)/16000*2*np.pi*(180+index))*.2, 16000).save(str(target), 'WAV')
            targets.append(SelectedObject(index, 'same-speaker-label', 'Sound', target))
        targets.append(SelectedObject(12, 'independent-calibration', 'Sound', self.wav))
        config = self.root/'config.json'
        config.write_text('{}', encoding='utf-8')
        app = TutorFormApplication(targets, config)
        app.window = Mock()
        self.addCleanup(app.close)
        app.window.create_file_dialog.side_effect = [(str(self.wav),), (str(self.grid),)]
        params = dict(role='reference', sound_id='4', language='ja', profile_id=PROFILE, reading_id=PROFILE,
                      reference_sound_id='4', learner_sound_id='9')
        with patch.dict('sys.modules', webview=SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1))):
            result = app.rpc('tutor.analyze_formant_candidates', params)
        chosen = next(c for c in result['candidates'] if c['can_apply'])
        with self.assertRaisesRegex(ValueError, '不匹配'):
            app.rpc('tutor.select_formant_candidate', {**params, 'role': 'learner', 'sound_id': '9', 'analysis_id': result['id'], 'candidate_id': chosen['id']})
        app.window.create_file_dialog.side_effect = [(str(self.wav),), (str(self.grid),)]
        with patch.dict('sys.modules', webview=SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1))):
            other = app.rpc('tutor.analyze_formant_candidates', {**params, 'role': 'learner', 'sound_id': '9'})
        self.assertEqual(result['id'], other['id'])
        self.assertEqual(len(app._formant_candidates), 2)
        receipt = app.rpc('tutor.select_formant_candidate', {**params, 'analysis_id': result['id'], 'candidate_id': chosen['id']})
        request = app.build_request(dict(reference='4', learner='9', language='ja', phonemes='a', profile_id=PROFILE,
            normalization=dict(version='normalization-v1', formants=False, confirmed=False,
                reference_calibration_id=receipt['id'], measurement_settings=dict(reference=receipt['measurement_settings']))))
        snapshot = deepcopy(request.normalization['formant_selections']['reference'])
        app._calibrations[receipt['id']][1]['formant_selection']['source'] = 'changed'
        self.assertEqual(request.normalization['formant_selections']['reference'], snapshot)
        self.assertEqual(snapshot['selected_candidate_id'], chosen['id'])
        self.assertEqual(request.normalization['measurement_settings']['reference'], chosen['parameters'])
        self.assertEqual(snapshot['material']['wav_name'], self.wav.name)
        self.assertEqual(snapshot['material']['textgrid_sha256'], result['material']['textgrid_sha256'])
        with self.assertRaisesRegex(ValueError, '待测录音相同'):
            app.build_request(dict(reference='4', learner='12', language='ja', phonemes='a', profile_id=PROFILE,
                normalization=dict(version='normalization-v1', formants=False, confirmed=False,
                    reference_calibration_id=receipt['id'], measurement_settings=dict(reference=receipt['measurement_settings']))))
        from praat_ai.models import PhoneSpec
        from praat_ai.pronunciation import analyze_pronunciation
        request.reference_object, request.learner_object = str(targets[0].file), str(targets[1].file)
        request.phonemes = [PhoneSpec('a', reference_start=.1, reference_end=.5, learner_start=.1, learner_end=.5,
                                    alignment_source='provided')]
        result_payload = analyze_pronunciation(request).to_dict()
        self.assertEqual(result_payload['normalization']['formant_selections']['reference'], snapshot)
        self.assertEqual(result_payload['reference_phones'][0]['tracks']['f1']['analysis']['parameters'], chosen['parameters'])
        json.dumps(result_payload, allow_nan=False)
        changed = {**chosen['parameters'], 'time_step_sec': .02}
        with self.assertRaisesRegex(ValueError, '参数已改变'):
            app.rpc('tutor.select_formant_candidate', {**params, 'analysis_id': result['id'], 'candidate_id': chosen['id'],
                'measurement_settings': dict(reference=changed)})
