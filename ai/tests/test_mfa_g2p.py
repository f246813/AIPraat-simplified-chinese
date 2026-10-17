import copy
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from praat_ai.mfa_g2p import (english_words, generate, choose_readings, check_launcher,
                              launcher_command, run_command)
from praat_ai.phone_labels import map_sequence, map_phone
from praat_ai.mfa_task import task_alignment, file_state, explanation_payload
from praat_ai.models import AnalysisRequest, PhoneSpec
from praat_ai.config import AlignmentConfig
from praat_ai.forced_alignment import build_aligner, AlignmentError, parse_mfa_textgrid
from praat_ai.tutor import request_from_dict


class G2pTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='测试 G2P ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.paths = {kind: str(self.root / (kind + '.zip')) for kind in ('g2p', 'dictionary', 'acoustic')}
        for value in self.paths.values():
            Path(value).write_text('placeholder', encoding='utf-8')
        Path(self.paths['dictionary']).write_text('hello HH AH0 L OW1\nhello HH EH1 L OW0\n', encoding='utf-8')
        self.snapshot = dict(preset=dict(id='en-US', language='en', rule_id='en-US-general-v1',
                                        phone_set='arpabet', phone_map={}, status=dict(can_generate=True)),
                             paths=self.paths, launcher=dict(executable=sys.executable), fingerprint='v1')

    def fake_g2p(self, command, *args):
        self.assertEqual(command[-2:], ['--num_pronunciations', '3'])
        self.assertEqual(Path(command[2]).read_text(encoding='utf-8'), 'hello\n')
        Path(command[4]).write_text('hello HH AH0 L OW1\n', encoding='utf-8')
        return ''

    def generated(self, text='Hello, hello!'):
        with patch('praat_ai.mfa_g2p.check_launcher', return_value=dict(help_order='input-first')), \
             patch('praat_ai.mfa_g2p.launcher_command', return_value=['mfa']), \
             patch('praat_ai.mfa_g2p.run_command', side_effect=self.fake_g2p):
            return generate(self.snapshot, text, threading.Event())

    def test_generation_calls_g2p_even_for_dictionary_words_and_restores_repeats(self):
        result = self.generated()
        self.assertEqual(result['sequence'], ['hello', 'hello'])
        self.assertEqual(len(result['words']), 1)
        self.assertEqual(len(result['words'][0]['candidates']), 2)
        self.assertEqual(result['words'][0]['candidates'][0]['source'], 'MFA G2P')
        readings, ipa, raw = choose_readings(result, {'hello': 0})
        self.assertEqual(ipa, ['h', 'ə', 'l', 'oʊ'] * 2)
        self.assertEqual(raw, ['HH', 'AH0', 'L', 'OW1'] * 2)
        self.assertEqual(readings[0]['stress'], [None, 0, None, 1])

    def test_candidates_require_valid_selection(self):
        result = self.generated()
        for choices in ({}, {'hello': -1}, {'hello': True}, {'hello': 5}, {'hello': 0, 'other': 0}):
            with self.assertRaises(ValueError):
                choose_readings(result, choices)

    def test_missing_output_and_partial_output_fail(self):
        for output in (None, 'other HH AH0\n'):
            def fake(command, *args):
                if output is not None:
                    Path(command[4]).write_text(output, encoding='utf-8')
                return ''
            with patch('praat_ai.mfa_g2p.check_launcher', return_value=dict(help_order='input-first')), \
                 patch('praat_ai.mfa_g2p.launcher_command', return_value=['mfa']), \
                 patch('praat_ai.mfa_g2p.run_command', side_effect=fake), self.assertRaises(ValueError):
                generate(self.snapshot, 'hello', threading.Event())

    def test_word_normalization_does_not_erase_unsupported_characters(self):
        self.assertEqual(english_words("Hello—world".replace('—', '-')), ['hello', 'world'])
        self.assertEqual(english_words("Don't, don’t!"), ["don't", "don't"])
        self.assertEqual(english_words('I am a.'), ['i', 'am', 'a'])
        for text in ('', 'hello 123', '你好', 'U.S.A.', 'hi @bob', 'a' * 2001):
            with self.assertRaises(ValueError):
                english_words(text)

    def test_explicit_unicode_and_arpabet_mapping(self):
        self.assertEqual(map_phone('AH0', 'arpabet'), 'ə')
        self.assertEqual(map_phone('AH1', 'arpabet'), 'ʌ')
        self.assertEqual(map_phone('ER0', 'arpabet'), 'ɚ')
        self.assertEqual(map_phone('CH', 'arpabet'), 'tʃ')
        self.assertEqual(map_phone('ɯ̥', 'ipa'), 'ɯ̥')
        self.assertEqual(map_phone('aː', 'ipa'), 'aː')
        self.assertEqual(map_phone('X', 'custom', {'X': 't͡ʃ'}), 't͡ʃ')
        for label, system, mapping in [('BAD', 'arpabet', {}), ('HH', 'ipa', {}), ('X', 'custom', {'X': 'a b'})]:
            with self.assertRaises(ValueError):
                map_phone(label, system, mapping)

    def test_help_adapts_advertised_order_and_rejects_unknown_layout(self):
        for help_text, expected in [('Usage: mfa g2p INPUT_PATH G2P_MODEL_PATH OUTPUT_PATH --num_pronunciations', 'input-first'),
                                    ('Usage: mfa g2p G2P_MODEL_PATH INPUT_PATH OUTPUT_PATH --num_pronunciations', 'model-first')]:
            with patch('praat_ai.mfa_g2p.launcher_command', return_value=['mfa']), \
                 patch('praat_ai.mfa_g2p.run_command', side_effect=['3.3.0', help_text]):
                self.assertEqual(check_launcher({})['help_order'], expected)
        with patch('praat_ai.mfa_g2p.launcher_command', return_value=['mfa']), \
             patch('praat_ai.mfa_g2p.run_command', side_effect=['old', 'unrecognized']), self.assertRaises(ValueError):
            check_launcher({})

    def test_launcher_and_subprocess_cancel_timeout_failure(self):
        self.assertEqual(launcher_command(dict(executable=sys.executable)), [sys.executable])
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(ValueError, '取消'):
            run_command([sys.executable, '-c', 'print(1)'], cancel)
        with self.assertRaisesRegex(ValueError, '超时'):
            run_command([sys.executable, '-c', 'import time; time.sleep(10)'], timeout=.2)
        with self.assertRaisesRegex(ValueError, '取消'):
            cancel.clear()
            timer = threading.Timer(.2, cancel.set)
            timer.start()
            try:
                run_command([sys.executable, '-c', 'import time; time.sleep(10)'], cancel, timeout=5)
            finally:
                timer.join()
        with self.assertRaisesRegex(ValueError, 'failure'):
            run_command([sys.executable, '-c', 'import sys; sys.stderr.write("failure"); sys.exit(1)'])
        self.assertIn('测试', run_command([sys.executable, '-c', 'print("测试")']))

    def request(self):
        result = self.generated('hello')
        readings, ipa, raw = choose_readings(result, {'hello': 0})
        snapshot = copy.deepcopy(self.snapshot)
        snapshot['file_state'] = file_state(snapshot['paths'])
        return AnalysisRequest(1, 2, 'en', [PhoneSpec(p, label=r) for p, r in zip(ipa, raw)],
                               transcript='hello', mfa_snapshot=snapshot, selected_readings=readings,
                               generation_source=result['source'])

    def test_task_uses_frozen_model_and_selected_temporary_dictionary(self):
        req = self.request()
        cfg = AlignmentConfig()
        cfg.mfa.acoustic_model = 'old-global-model'
        with task_alignment(req, cfg) as task:
            self.assertEqual(task.mfa.acoustic_model, self.paths['acoustic'])
            self.assertEqual(task.backend, 'mfa-strict')
            temp_path = Path(task.mfa.dictionary_path)
            self.assertEqual(temp_path.read_text(encoding='utf-8'), 'hello\tHH AH0 L OW1\n')
            self.assertTrue(build_aligner(task).strict)
        self.assertFalse(temp_path.exists())
        self.assertEqual(cfg.mfa.acoustic_model, 'old-global-model')
        self.assertIn('EH1', Path(self.paths['dictionary']).read_text(encoding='utf-8'))

    def test_task_snapshot_rejects_changed_files_and_mismatched_ipa(self):
        req = self.request()
        req.phonemes[0].ipa = 'x'
        with self.assertRaises(ValueError):
            with task_alignment(req, AlignmentConfig()):
                pass
        req = self.request()
        Path(self.paths['acoustic']).write_text('changed model', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, '改变'):
            with task_alignment(req, AlignmentConfig()):
                pass

    def test_json_roundtrip_and_local_metadata_not_sent_to_explanation(self):
        req = self.request()
        restored = request_from_dict(req.to_dict())
        self.assertEqual(restored.selected_readings, req.selected_readings)
        self.assertEqual(restored.mfa_snapshot, req.mfa_snapshot)
        payload = explanation_payload(req.to_dict())
        self.assertNotIn('mfa_snapshot', payload)
        self.assertNotIn('selected_readings', payload)
        self.assertNotIn('generation_source', payload)
        self.assertEqual(payload['phonemes'][0]['ipa'], 'h')
        self.assertIsNone(request_from_dict(dict(reference_object=1, learner_object=2, phonemes=[])).mfa_snapshot)

    def test_uk_cannot_use_us_rules(self):
        req = self.request()
        req.mfa_snapshot['preset']['rule_id'] = None
        req.profile_id = 'en-US-general-v1'
        with self.assertRaisesRegex(ValueError, '判据'):
            with task_alignment(req, AlignmentConfig()):
                pass

    def test_strict_mfa_cannot_fallback(self):
        cfg = AlignmentConfig(backend='mfa-strict')
        with self.assertRaisesRegex(AlignmentError, '未执行比例回退'):
            build_aligner(cfg).align('unused.wav', [PhoneSpec('a', label='AA')], 'en', 'a')

    def test_textgrid_raw_labels_return_ipa_but_reject_extra_or_different_tokens(self):
        grid = self.root / 'test.TextGrid'
        def make(labels):
            grid.write_text('name = "phones"\n' + ''.join(
                f'xmin = {i/10}\nxmax = {(i+1)/10}\ntext = "{label}"\n' for i, label in enumerate(labels)), encoding='utf-8')
        phones = [PhoneSpec('h', label='HH'), PhoneSpec('ə', label='AH0')]
        make(['HH', 'AH0'])
        result = parse_mfa_textgrid(grid, phones, raw_labels=True)
        self.assertEqual([p.ipa for p in result.phones], ['h', 'ə'])
        for labels in (['HH', 'AH1'], ['HH', 'AH0', 'L']):
            make(labels)
            with self.assertRaises(AlignmentError):
                parse_mfa_textgrid(grid, phones, raw_labels=True)

    def test_clusters_are_not_single_phone_units(self):
        for cluster in ('ks', 'ps', 'kt'):
            with self.subTest(cluster=cluster), self.assertRaises(ValueError):
                map_phone('X', 'custom', {'X': cluster})
        self.assertEqual(map_phone('CH', 'arpabet'), 'tʃ')

    def test_relative_launcher_is_absolute_inside_temporary_working_directory(self):
        relative = os.path.relpath(sys.executable, Path.cwd())
        command = launcher_command({'executable': relative})
        self.assertTrue(Path(command[0]).is_absolute())
        self.assertIn('ready', run_command([*command, '-c', 'print("ready")'], cwd=self.root))

    def test_exited_parent_live_child_is_killed_on_cancellation(self):
        from praat_ai.mfa_g2p import _terminate
        pid_file = self.root / 'child.pid'
        child = f'import os,time; from pathlib import Path; Path({str(pid_file)!r}).write_text(str(os.getpid())); time.sleep(20)'
        parent = f'import subprocess,sys; subprocess.Popen([sys.executable,"-c",{child!r}])'
        cancel = threading.Event()
        timer = threading.Timer(.7, cancel.set)
        timer.start()
        try:
            with patch('praat_ai.mfa_g2p._terminate', wraps=_terminate) as terminate, self.assertRaisesRegex(ValueError, '取消'):
                run_command([sys.executable, '-c', parent], cancel, timeout=4)
            self.assertEqual(terminate.call_args.args[0].returncode, 0)
        finally:
            timer.join()
        self.assertTrue(pid_file.is_file())
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            api = ctypes.WinDLL('kernel32', use_last_error=True)
            api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            api.OpenProcess.restype = wintypes.HANDLE
            api.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
            api.CloseHandle.argtypes = [wintypes.HANDLE]
            handle = api.OpenProcess(0x100000, False, int(pid_file.read_text()))
            if handle:
                try:
                    self.assertEqual(api.WaitForSingleObject(handle, 1000), 0)
                finally:
                    api.CloseHandle(handle)


if __name__ == '__main__':
    unittest.main()
