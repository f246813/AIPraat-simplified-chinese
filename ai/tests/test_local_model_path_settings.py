import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from praat_ai.modern_app import ModernApplication
from praat_ai.modern_settings import SettingsService
from tests.test_modern_organization import NoExecution


class LocalModelPathSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.model = self.root / '本地模型.gguf'
        self.model.write_bytes(b'GGUF')
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({
            'api': {'enabled': True, 'base_url': 'https://example.invalid/v1', 'model': 'cloud-model', 'api_key': 'keep-key'},
            'qwen': {'model': 'old-local', 'plan_temperature': .7},
            'server': {'llama_server': 'keep-server', 'mmproj_path': 'keep-mmproj', 'custom': 'keep',
                       'presets': [{'id': 'known', 'model_path': str(self.model)}]},
        }), encoding='utf8')
        self.service = SettingsService(self.config)

    def test_save_and_candidate_update_local_model_without_changing_cloud(self):
        incoming = {'server': {'model_path': str(self.model), 'llama_server': 'ignored'}}
        candidate = self.service.candidate(incoming)
        self.assertEqual(candidate.server.model_path, str(self.model))
        self.assertEqual(candidate.local_qwen.model, self.model.name)
        self.assertEqual(candidate.qwen.model, 'cloud-model')
        saved = self.service.save(incoming)
        self.assertEqual(saved['server']['model_path'], str(self.model))
        raw = json.loads(self.config.read_text(encoding='utf8'))
        self.assertEqual(raw['server']['active_preset'], 'known')
        self.assertEqual(raw['server']['llama_server'], 'keep-server')
        self.assertEqual(raw['server']['mmproj_path'], 'keep-mmproj')
        self.assertEqual(raw['server']['custom'], 'keep')
        self.assertEqual(raw['qwen']['model'], self.model.name)
        self.assertEqual(raw['qwen']['plan_temperature'], .7)
        self.assertEqual(raw['api']['api_key'], 'keep-key')
        self.assertTrue(raw['api']['enabled'])
        self.assertEqual(SettingsService(self.config).get()['server']['model_path'], str(self.model))

    def test_invalid_model_is_rejected_without_writing_config(self):
        before = self.config.read_bytes()
        for value in [str(self.root / 'missing.gguf'), str(self.config), None]:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.service.save({'server': {'model_path': value}})
                self.assertEqual(self.config.read_bytes(), before)

    def test_unchanged_missing_model_does_not_block_other_settings(self):
        raw = json.loads(self.config.read_text(encoding='utf8'))
        raw['server']['model_path'] = str(self.root / 'unavailable.gguf')
        self.config.write_text(json.dumps(raw), encoding='utf8')
        settings = self.service.get()
        settings['preferences']['theme'] = 'dark'
        self.assertEqual(self.service.save(settings)['preferences']['theme'], 'dark')

    def test_native_picker_selection_and_cancel_do_not_save_or_load(self):
        app = ModernApplication(self.root / 'modern', self.config, executor=NoExecution())
        try:
            app.window = Mock()
            before = self.config.read_bytes()
            for paths, expected in [([str(self.model)], str(self.model)), (None, None)]:
                app.window.create_file_dialog.return_value = paths
                self.assertEqual(app.rpc('settings.choose_model', {})['path'], expected)
                self.assertFalse(app.window.create_file_dialog.call_args.kwargs['allow_multiple'])
                self.assertIn('*.gguf', app.window.create_file_dialog.call_args.kwargs['file_types'][0])
                self.assertEqual(self.config.read_bytes(), before)
                self.assertIsNone(app.local.manager)
        finally:
            app.close()
