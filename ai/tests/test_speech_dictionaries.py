import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from praat_ai.config import load_config
from praat_ai.speech_dictionaries import DictionaryService, DictionaryApplication


class SpeechDictionaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / 'config.json'
        self.first = self.root / '中文词典.dict'
        self.second = self.root / 'second.txt'
        for file in (self.first, self.second):
            file.write_text('hello HH AH L OW\n', encoding='utf-8')
        self.raw = {'alignment': {'backend': 'mfa', 'mfa': {'dictionary_path': str(self.first), 'beam': 23}},
                    'api': {'api_key': 'fixture-only'}, 'custom': {'keep': 17}}
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        self.service = DictionaryService(self.path)

    def test_legacy_path_is_listed_without_rewriting_configuration(self):
        before = self.path.read_bytes()
        data = self.service.list()
        self.assertEqual(data['dictionaries'][0]['name'], '中文词典.dict')
        self.assertTrue(data['dictionaries'][0]['active'])
        self.assertEqual(before, self.path.read_bytes())

    def test_add_deduplicates_and_preserves_latest_unrelated_settings(self):
        data = self.service.add([str(self.first), str(self.second), str(self.second)])
        self.assertEqual(len(data['dictionaries']), 2)
        updated = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(updated['api'], self.raw['api'])
        self.assertEqual(updated['custom'], self.raw['custom'])
        self.assertEqual(updated['alignment']['mfa']['beam'], 23)
        self.assertEqual(load_config(self.path).alignment.mfa.dictionary_paths, [str(self.first), str(self.second)])
        updated['custom']['keep'] = 29
        self.path.write_text(json.dumps(updated), encoding='utf-8')
        self.service.remove(str(self.second))
        self.assertEqual(json.loads(self.path.read_text(encoding='utf-8'))['custom']['keep'], 29)

    def test_remove_active_then_last_path_never_deletes_files(self):
        self.service.add([str(self.second)])
        data = self.service.remove(str(self.first))
        self.assertEqual([item['path'] for item in data['dictionaries']], [str(self.second)])
        self.assertTrue(data['dictionaries'][0]['active'])
        self.assertEqual(self.service.remove(str(self.second))['dictionaries'], [])
        self.assertEqual(load_config(self.path).alignment.mfa.dictionary_path, '')
        self.assertTrue(self.first.is_file() and self.second.is_file())

    def test_missing_configured_path_can_be_removed(self):
        self.first.unlink()
        self.assertFalse(self.service.list()['dictionaries'][0]['exists'])
        self.assertEqual(self.service.remove(str(self.first))['dictionaries'], [])

    def test_invalid_add_and_cancel_do_not_modify_config(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.service.add([str(self.second), str(self.root / 'missing.dict')])
        self.service.add([])
        self.assertEqual(before, self.path.read_bytes())

    def test_first_added_path_is_used_by_existing_aligner(self):
        self.path.unlink()
        self.service.add([str(self.second)])
        self.assertEqual(load_config(self.path).alignment.mfa.dictionary_path, str(self.second))

    def test_native_file_picker_adds_paths_and_cancel_is_lossless(self):
        app = DictionaryApplication(self.path)
        app.window = Mock()
        app.window.create_file_dialog.return_value = None
        before = self.path.read_bytes()
        with patch.dict('sys.modules', webview=Mock()):
            app.rpc('dictionaries.choose', {})
            self.assertEqual(before, self.path.read_bytes())
            app.window.create_file_dialog.return_value = [str(self.second)]
            self.assertEqual(len(app.rpc('dictionaries.choose', {})['dictionaries']), 2)
        self.assertTrue(app.window.create_file_dialog.call_args.kwargs['allow_multiple'])
        with self.assertRaises(ValueError):
            app.rpc('settings.save', {})

    def test_menu_launcher_detaches_the_window(self):
        import start_speech_dictionaries as launcher
        with patch.object(launcher.subprocess, 'Popen') as start:
            self.assertEqual(launcher.main(), 0)
        args, kwargs = start.call_args
        self.assertTrue(args[0][1].endswith('run_speech_dictionaries.py'))
        self.assertEqual(kwargs['stdout'], launcher.subprocess.DEVNULL)

    def test_select_updates_only_current_dictionary_and_persists(self):
        self.service.add([str(self.second)])
        app = DictionaryApplication(self.path)
        data = app.rpc('dictionaries.select', {'path': str(self.second)})
        self.assertEqual([item['path'] for item in data['dictionaries'] if item['active']], [str(self.second)])
        config = load_config(self.path)
        self.assertEqual(config.alignment.mfa.dictionary_path, str(self.second))
        self.assertEqual(config.alignment.backend, 'mfa')
        self.assertEqual(config.alignment.mfa.beam, 23)
        self.assertEqual(len(config.alignment.mfa.dictionary_paths), 2)
        self.assertEqual(DictionaryService(self.path).list(), data)
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['api'], self.raw['api'])
        self.assertEqual(saved['custom'], self.raw['custom'])

    def test_select_rejects_missing_and_unconfigured_paths_without_saving(self):
        before = self.path.read_bytes()
        with self.assertRaises(ValueError):
            self.service.select(str(self.second))
        self.first.unlink()
        with self.assertRaises(ValueError):
            self.service.select(str(self.first))
        self.assertEqual(self.path.read_bytes(), before)

    def test_mfa_alignment_command_uses_selected_dictionary(self):
        from praat_ai.forced_alignment import MfaAligner
        from praat_ai.models import PhoneSpec
        self.service.add([str(self.second)])
        self.service.select(str(self.second))
        aligner = MfaAligner(load_config(self.path).alignment.mfa)

        def execute(command, cancel=None, timeout=None, cwd=None):
            self.assertEqual(command[3], str(self.second))
            (Path(command[5]) / 'learner.TextGrid').write_text('', encoding='utf-8')
            return 'ok\n'

        with patch.object(aligner, 'available', return_value=True), \
             patch('praat_ai.forced_alignment._copy_as_wav'), \
             patch('praat_ai.forced_alignment.run_mfa_command', side_effect=execute), \
             patch('praat_ai.forced_alignment.parse_mfa_textgrid', return_value='aligned'):
            self.assertEqual(aligner.align('fixture.wav', [PhoneSpec('a')], 'en', transcript='hello'), 'aligned')

    def test_acoustic_model_picker_select_and_delete_preserve_dictionary(self):
        first, second = self.root / '中文声学.zip', self.root / '英语声学.zip'
        for file in (first, second):
            file.write_bytes(b'model-fixture')
        app = DictionaryApplication(self.path)
        app.window = Mock()
        app.window.create_file_dialog.return_value = [str(first), str(second), str(second)]
        with patch.dict('sys.modules', webview=Mock()):
            try:
                data = app.rpc('acoustic_models.choose', {})
            except ValueError as error:
                self.fail('声学模型管理尚未实现：' + str(error))
        self.assertEqual(len(data['models']), 2)
        self.assertTrue(data['models'][0]['active'])
        self.assertIn('*.zip', app.window.create_file_dialog.call_args.kwargs['file_types'][0])
        selected = app.rpc('acoustic_models.select', {'path': str(second)})
        self.assertEqual([item['path'] for item in selected['models'] if item['active']], [str(second)])
        loaded = load_config(self.path).alignment.mfa
        self.assertEqual(loaded.acoustic_model, str(second))
        self.assertEqual(loaded.acoustic_models, [str(first), str(second)])
        self.assertEqual(loaded.dictionary_path, str(self.first))
        from praat_ai.forced_alignment import MfaAligner
        command = MfaAligner(loaded).build_command(self.root, self.first, self.root)
        self.assertEqual(command[4], str(second))
        app.rpc('acoustic_models.remove', {'path': str(second)})
        self.assertEqual(load_config(self.path).alignment.mfa.acoustic_model, str(first))
        self.assertEqual(app.rpc('acoustic_models.remove', {'path': str(first)})['models'], [])
        self.assertTrue(first.is_file() and second.is_file())
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['alignment']['mfa']['dictionary_path'], str(self.first))
        self.assertEqual(saved['api'], self.raw['api'])
        self.assertEqual(saved['custom'], self.raw['custom'])

    def test_legacy_model_is_listed_and_picker_cancel_is_lossless(self):
        model = self.root / 'existing.zip'
        model.write_bytes(b'fixture')
        self.raw['alignment']['mfa']['acoustic_model'] = str(model)
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        app = DictionaryApplication(self.path)
        app.window = Mock()
        app.window.create_file_dialog.return_value = None
        before = self.path.read_bytes()
        try:
            data = app.rpc('acoustic_models.list', {})
        except ValueError as error:
            self.fail('声学模型管理尚未实现：' + str(error))
        self.assertEqual(data['models'][0]['path'], str(model))
        self.assertTrue(data['models'][0]['exists'] and data['models'][0]['active'])
        with patch.dict('sys.modules', webview=Mock()):
            self.assertEqual(app.rpc('acoustic_models.choose', {}), data)
        self.assertEqual(self.path.read_bytes(), before)
        model.unlink()
        self.assertFalse(app.rpc('acoustic_models.list', {})['models'][0]['exists'])
        with self.assertRaises(ValueError):
            app.rpc('acoustic_models.select', {'path': str(model)})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(app.rpc('acoustic_models.remove', {'path': str(model)})['models'], [])

    def test_dictionary_changes_keep_model_selection_and_list(self):
        self.raw['alignment']['mfa'].update(acoustic_model='legacy-model', acoustic_models=['legacy-model'])
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        self.service.add([str(self.second)])
        self.service.select(str(self.second))
        self.service.remove(str(self.first))
        mfa = json.loads(self.path.read_text(encoding='utf-8'))['alignment']['mfa']
        self.assertEqual(mfa['acoustic_model'], 'legacy-model')
        self.assertEqual(mfa['acoustic_models'], ['legacy-model'])

    def test_wav2vec2_directory_picker_select_remove_and_parameter_preservation(self):
        first, second = self.root / '中文Wav2Vec2', self.root / '英语Wav2Vec2'
        first.mkdir(); second.mkdir()
        (first / 'config.json').write_text('{}', encoding='utf-8')
        self.raw['alignment']['wav2vec2'] = {'model': str(first), 'enabled': True,
            'device': 'cpu', 'blank_token_id': 3, 'token_map': {'a': 'A'}}
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        app = DictionaryApplication(self.path)
        app.window = Mock()
        before = self.path.read_bytes()
        self.assertEqual(app.rpc('wav2vec2_models.list', {})['models'][0]['path'], str(first))
        app.window.create_file_dialog.return_value = None
        webview = Mock()
        with patch.dict('sys.modules', webview=webview):
            app.rpc('wav2vec2_models.choose', {})
            self.assertEqual(self.path.read_bytes(), before)
            app.window.create_file_dialog.return_value = [str(second)]
            self.assertEqual(len(app.rpc('wav2vec2_models.choose', {})['models']), 2)
        app.window.create_file_dialog.assert_called_with(webview.FileDialog.FOLDER)
        selected = app.rpc('wav2vec2_models.select', {'path': str(second)})
        self.assertEqual([item['path'] for item in selected['models'] if item['active']], [str(second)])
        loaded = load_config(self.path).alignment.wav2vec2
        self.assertEqual(loaded.model, str(second))
        self.assertEqual(loaded.models, [str(first), str(second)])
        self.assertEqual(loaded.device, 'cpu')
        app.rpc('wav2vec2_models.remove', {'path': str(second)})
        self.assertEqual(load_config(self.path).alignment.wav2vec2.model, str(first))
        self.assertEqual(app.rpc('wav2vec2_models.remove', {'path': str(first)})['models'], [])
        self.assertTrue((first / 'config.json').is_file() and second.is_dir())
        saved = json.loads(self.path.read_text(encoding='utf-8'))
        self.assertEqual(saved['alignment']['mfa'], self.raw['alignment']['mfa'])
        for key in ('enabled', 'device', 'blank_token_id', 'token_map'):
            self.assertEqual(saved['alignment']['wav2vec2'][key], self.raw['alignment']['wav2vec2'][key])
        self.assertEqual(saved['api'], self.raw['api'])
        self.assertEqual(saved['custom'], self.raw['custom'])

    def test_wav2vec2_rejects_files_and_missing_directories_without_saving(self):
        app = DictionaryApplication(self.path)
        app.window = Mock()
        app.window.create_file_dialog.return_value = [str(self.first)]
        before = self.path.read_bytes()
        with patch.dict('sys.modules', webview=Mock()), self.assertRaises(ValueError):
            app.rpc('wav2vec2_models.choose', {})
        self.assertEqual(self.path.read_bytes(), before)
        self.raw['alignment']['wav2vec2'] = {'model': str(self.root / 'missing')}
        self.path.write_text(json.dumps(self.raw), encoding='utf-8')
        self.assertFalse(app.rpc('wav2vec2_models.list', {})['models'][0]['exists'])
        with self.assertRaises(ValueError):
            app.rpc('wav2vec2_models.select', {'path': str(self.root / 'missing')})
        self.assertEqual(app.rpc('wav2vec2_models.remove', {'path': str(self.root / 'missing')})['models'], [])

    def test_legacy_items_show_shared_preset_references_without_list_recursion(self):
        from praat_ai.mfa_resources import MfaResourceService
        resources = MfaResourceService(self.path)
        data = resources.list()
        preset = data['presets'][0]
        preset['dictionary'] = data['resources']['dictionary'][0]['id']
        resources.save(preset, data['launcher'])
        before = self.path.read_bytes()
        with patch.object(MfaResourceService, 'list', side_effect=AssertionError('recursive list')):
            self.assertEqual(self.service.list()['dictionaries'][0]['used_by'], ['en-GB'])
        self.assertEqual(before, self.path.read_bytes())

    def test_mfa_rpc_routes_outside_dictionary_window_guard(self):
        app = DictionaryApplication(self.path)
        app.window = Mock()
        with app._guard, patch.object(app._mfa_resources, 'rpc', return_value={'status': 'fixture'}) as rpc:
            self.assertEqual(app.rpc('mfa.check_launcher', {'launcher': {}}), {'status': 'fixture'})
        rpc.assert_called_once_with('mfa.check_launcher', {'launcher': {}}, window=app.window)

    def test_shared_mfa_import_does_not_switch_legacy_active_dictionary(self):
        app = DictionaryApplication(self.path)
        app.window = Mock()
        app.window.create_file_dialog.return_value = [str(self.second)]
        with patch.dict('sys.modules', webview=Mock()):
            data = app.rpc('mfa.choose', {'kind': 'dictionary'})
        self.assertEqual(len(data['resources']['dictionary']), 2)
        self.assertEqual(load_config(self.path).alignment.mfa.dictionary_path, str(self.first))
        self.assertEqual(load_config(self.path).alignment.mfa.dictionary_paths, [])



if __name__ == '__main__':
    unittest.main()
