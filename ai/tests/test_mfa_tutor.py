import copy
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from praat_ai.bridge import SelectedObject
from praat_ai.ui import TutorFormApplication
from praat_ai.mfa_task import task_alignment
from praat_ai.config import AlignmentConfig


class MfaTutorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        sounds = [SelectedObject(1, 'standard', 'Sound', self.root / 'ref.wav'),
                  SelectedObject(2, 'learner', 'Sound', self.root / 'learner.wav')]
        self.app = TutorFormApplication(sounds, self.root / 'config.json')
        self.app.window = Mock()
        paths = {}
        for kind in ('g2p', 'dictionary', 'acoustic'):
            path = self.root / kind
            path.write_text('resource', encoding='utf-8')
            paths[kind] = str(path)
        self.snapshot = dict(preset=dict(id='en-US', language='en', rule_id='en-US-general-v1',
                                        phone_set='arpabet', phone_map={}, status=dict(can_generate=True)),
                             paths=paths, launcher=dict(executable='mfa'), fingerprint='original')
        self.app.resources = Mock()
        self.app.resources.resolve.side_effect = lambda preset: copy.deepcopy(self.snapshot)
        self.generation = dict(preset_id='en-US', transcript='hello', sequence=['hello'], source='MFA G2P · test',
                               words=[dict(word='hello', candidates=[dict(raw=['HH', 'AH0', 'L', 'OW1'],
                                                                            ipa=['h', 'ə', 'l', 'oʊ'], source='MFA G2P')])])
        self.params = dict(reference='1', learner='2', language='en', preset_id='en-US',
                           transcript='hello', phonemes='h ə l oʊ', choices={'hello': 0},
                           threshold=1.25, explain=False, vision=False)

    def run_generation(self):
        with patch('praat_ai.ui.generate', return_value=copy.deepcopy(self.generation)), patch('praat_ai.ui.read_wav') as read:
            result = self.app.rpc('tutor.generate_phonemes', dict(preset_id='en-US', transcript='hello'))
            read.assert_not_called()
        self.app.window.destroy.assert_not_called()
        self.assertIsNone(self.app.result.request)
        self.params['generation_id'] = result['id']
        return result

    def test_generation_then_submit_derives_raw_labels_and_freezes_resources(self):
        self.run_generation()
        with patch('praat_ai.ui.read_wav', return_value=SimpleNamespace(duration=1)):
            self.app.rpc('tutor.submit', self.params)
        req = self.app.result.request
        self.assertEqual([p.label for p in req.phonemes], ['HH', 'AH0', 'L', 'OW1'])
        self.assertEqual(req.mfa_snapshot['paths'], self.snapshot['paths'])
        self.assertEqual(req.generation_source, self.generation['source'])
        with task_alignment(req, AlignmentConfig()) as cfg:
            self.assertEqual(cfg.backend, 'mfa-strict')
        self.app.window.destroy.assert_called_once()

    def test_modified_transcript_ipa_candidate_or_resources_are_rejected_without_audio(self):
        self.run_generation()
        bad = [dict(transcript='world'), dict(phonemes='x'), dict(choices={'hello': 99}),
               dict(profile_id='ja-JP-standard-v1'), dict(language='ja'), dict(generation_id='fake')]
        with patch('praat_ai.ui.read_wav') as read:
            for fields in bad:
                with self.subTest(fields=fields), self.assertRaises(ValueError):
                    self.app.rpc('tutor.submit', {**self.params, **fields})
            self.snapshot['fingerprint'] = 'changed'
            with self.assertRaisesRegex(ValueError, '改变'):
                self.app.rpc('tutor.submit', self.params)
            read.assert_not_called()
        self.app.window.destroy.assert_not_called()

    def test_cancel_generation_rejects_previously_stored_candidates(self):
        self.run_generation()
        self.app.rpc('tutor.cancel_generation', {})
        with self.assertRaisesRegex(ValueError, '先自动生成'):
            self.app.rpc('tutor.submit', self.params)
        self.app.window.destroy.assert_not_called()

    def test_cancel_remains_responsive_while_generation_is_running(self):
        started, errors = threading.Event(), []
        def generate(snapshot, transcript, cancel):
            started.set()
            if not cancel.wait(3):
                raise AssertionError('取消被锁阻塞')
            return copy.deepcopy(self.generation)
        def worker():
            try:
                self.app.rpc('tutor.generate_phonemes', dict(preset_id='en-US', transcript='hello'))
            except ValueError as error:
                errors.append(str(error))
        with patch('praat_ai.ui.generate', side_effect=generate):
            thread = threading.Thread(target=worker)
            thread.start()
            self.assertTrue(started.wait(2))
            self.app.rpc('tutor.cancel', {})
            thread.join(3)
            self.assertFalse(thread.is_alive())
        self.assertTrue(errors)
        self.assertFalse(self.app._generations)
        self.assertIsNone(self.app.result.request)

    def test_window_close_cancels_and_submit_while_generating_is_rejected(self):
        event = threading.Event()
        self.app._generation_cancel = event
        with self.assertRaisesRegex(ValueError, '等待'):
            self.app.rpc('tutor.submit', self.params)
        self.app.close()
        self.assertTrue(event.is_set())
        with self.assertRaisesRegex(ValueError, '已关闭'):
            self.app.rpc('tutor.bootstrap', {})

    def test_resource_rpc_does_not_close_or_read_audio(self):
        self.app.resources.rpc.return_value = {'presets': []}
        with patch('praat_ai.ui.read_wav') as read:
            self.assertEqual(self.app.rpc('mfa.list', {}), {'presets': []})
            read.assert_not_called()
        self.app.resources.rpc.assert_called_once_with('mfa.list', {}, self.app.window)
        self.app.window.destroy.assert_not_called()


if __name__ == '__main__':
    unittest.main()
