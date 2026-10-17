import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from praat_ai.bridge import SelectedObject
from praat_ai.ui import TutorFormApplication
from praat_ai.models import AnalysisRequest
from praat_ai.tutor import run_tutor
from test_normalization import fit, PROFILE, VERSION


class NormalizationHostTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'config.json'
        self.path.write_text(json.dumps(dict(api=dict(api_key='keep-secret'), pronunciation=dict(other='keep'))), encoding='utf-8')
        sounds = [SelectedObject(4, 'ref', 'Sound', Path('ref.wav')), SelectedObject(9, 'learner', 'Sound', Path('learner.wav'))]
        self.app = TutorFormApplication(sounds, self.path)
        self.app.window = Mock()
        self.params = dict(reference='4', learner='9', language='ja', phonemes='a', profile_id=PROFILE,
            normalization=dict(version=VERSION, formants=True, confirmed=True))

    def _import(self, role):
        self.app.window.create_file_dialog.side_effect = [('independent.wav',), ('independent.TextGrid',)]
        webview = SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1))
        with patch.dict('sys.modules', webview=webview), patch('praat_ai.ui.import_formant_calibration', return_value=fit()):
            return self.app.rpc('tutor.import_calibration', dict(role=role, sound_id='4' if role == 'reference' else '9',
                language='ja', profile_id=PROFILE, reading_id=PROFILE))

    def test_two_slots_native_pick_snapshot_and_preference_preserve_config(self):
        reference, learner = self._import('reference'), self._import('learner')
        self.params['normalization'].update(reference_calibration_id=reference['id'], learner_calibration_id=learner['id'])
        with patch('praat_ai.ui.read_wav', return_value=SimpleNamespace(duration=1)):
            self.app.rpc('tutor.submit', self.params)
        request = self.app.result.request
        self.assertEqual(request.normalization['reference_calibration']['method'], fit()['method'])
        self.assertEqual(request.normalization['learner_calibration']['id'], fit()['id'])
        # Editing the window's cache cannot mutate the request snapshot.
        self.app._calibrations[reference['id']][1]['speaker_label'] = 'changed'
        self.assertEqual(request.normalization['reference_calibration']['speaker_label'], 'speaker')
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(raw['api']['api_key'], 'keep-secret')
        self.assertEqual(raw['pronunciation']['other'], 'keep')
        self.assertTrue(self.app.bootstrap()['normalization']['formants'])
        self.app.window.destroy.assert_called_once()

    def test_task_freezes_before_progress_callbacks_and_model_startup(self):
        request = AnalysisRequest(4, 9, 'ja', [], profile_id=PROFILE, qwen_explain=False,
                                  normalization=dict(version=VERSION, formants=True, reference_calibration=fit()))
        def progress(fraction, message):
            request.normalization['reference_calibration']['speaker_label'] = 'changed-after-start'
        bridge = Mock()
        bridge.resolve_sound.side_effect = self.app.sounds
        with patch('praat_ai.tutor.PraatBridge.from_praat', return_value=bridge), \
             patch('praat_ai.tutor.detect_gpu', return_value=None), \
             patch('praat_ai.tutor.analyze_pronunciation', side_effect=ValueError('test-stop')) as analyze, \
             self.assertRaisesRegex(ValueError, 'test-stop'):
            run_tutor(config_path=self.path, request=request, progress=progress)
        frozen = analyze.call_args.args[0]
        self.assertEqual(frozen.normalization['reference_calibration']['speaker_label'], 'speaker')
        self.assertEqual(request.reference_object, 4)  # resolving did not mutate the caller

    def test_import_cancellation_has_no_receipt_or_request(self):
        self.app.window.create_file_dialog.return_value = None
        with patch.dict('sys.modules', webview=SimpleNamespace(FileDialog=SimpleNamespace(OPEN=1))):
            value = self.app.rpc('tutor.import_calibration', dict(role='reference', sound_id='4', language='ja', profile_id=PROFILE))
        self.assertIsNone(value)
        self.assertEqual(self.app._calibrations, {})
        self.assertFalse(self.app._calibration_busy)

    def test_reject_untrusted_receipt_swapped_speaker_and_reading(self):
        reference = self._import('reference')
        self.params['normalization']['reference_calibration_id'] = reference['id']
        for changed in (dict(reference='9'), dict(preset_id='not-real')):
            with self.subTest(changed=changed), patch('praat_ai.ui.read_wav', return_value=SimpleNamespace(duration=1)), self.assertRaises(ValueError):
                self.app.build_request({**self.params, **changed})
        self.params['normalization']['reference_calibration_id'] = 'fabricated'
        with patch('praat_ai.ui.read_wav', return_value=SimpleNamespace(duration=1)), self.assertRaisesRegex(ValueError, '校准'):
            self.app.build_request(self.params)

    def test_missing_calibration_does_not_block_submission(self):
        with patch('praat_ai.ui.read_wav', return_value=SimpleNamespace(duration=1)):
            request = self.app.build_request(self.params)
        self.assertTrue(request.normalization['formants'])
        self.assertNotIn('reference_calibration', request.normalization)

    def test_analysis_cannot_submit_during_import_and_close_releases_receipts(self):
        self._import('reference')
        self.app._calibration_busy = True
        with self.assertRaisesRegex(ValueError, '校准导入'):
            self.app.rpc('tutor.submit', self.params)
        self.app.close()
        self.assertEqual(self.app._calibrations, {})
        self.assertIsNone(self.app.result.request)


if __name__ == '__main__':
    unittest.main()
