import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from praat_ai import frontend_status as status
from praat_ai.vram import GpuMemory


class FrontendMenuStatusTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.model = self.root / '模型.gguf'; self.model.touch()
        self.server = self.root / 'llama-server.exe'; self.server.touch()
        self.env = patch.dict(os.environ)
        self.env.start(); self.addCleanup(self.env.stop)
        for key in list(os.environ):
            if key.startswith('PRAAT_AI_'): os.environ.pop(key)
        self.raw = dict(server=dict(model_path=str(self.model), llama_server=str(self.server),
            active_preset='local', presets=[dict(id='local', label='中文 "预设" 🦜', model_path=str(self.model))]))

    def collect(self):
        self.config.write_text(json.dumps(self.raw), encoding='utf8')
        return status.collect_menu_status(self.config, runtime=self.root)

    def test_thresholds_use_unrounded_free_memory(self):
        for free, expected in [(None,'unknown'),(-1,'red'),(0,'red'),(511,'red'),
                               (512,'yellow'),(513,'yellow'),(1535,'yellow'),(1536,'green'),(1537,'green')]:
            with self.subTest(free=free): self.assertEqual(status.vram_warning(free), expected)

    def test_local_shows_saved_preset_label_without_a_running_service(self):
        result = self.collect()
        self.assertEqual(result['frontend_model'], '中文 "预设" 🦜')
        self.assertEqual(result['frontend_status'], '已停止')
        self.assertTrue(result['frontend_local'])

    def test_cloud_label_and_incomplete_cloud_configuration(self):
        self.raw['api'] = dict(enabled=True, base_url='https://example.invalid/v1', model='cloud')
        self.assertEqual(self.collect()['frontend_model'], '云端API模式')
        self.assertEqual(self.collect()['frontend_status'], '已停止')
        self.raw['api']['model'] = ''
        self.assertEqual(self.collect()['frontend_status'], '未配置')

    def test_missing_local_files_and_no_configuration(self):
        self.model.unlink()
        self.assertEqual(self.collect()['frontend_status'], '未配置')
        self.raw = {}
        self.assertEqual(self.collect()['frontend_model'], '未配置')

    def test_cloud_never_reports_local_gpu_warning_and_local_switch_restores_it(self):
        self.raw['api'] = dict(enabled=True, base_url='https://example.invalid/v1', model='cloud')
        for free in (0, 511, 512, 1535, 1536, 8192):
            with self.subTest(free=free):
                self.config.write_text(json.dumps(self.raw), encoding='utf8')
                result = status.collect_menu_status(self.config, runtime=self.root,
                    gpu=GpuMemory('fixture', 8192, free))
                self.assertEqual(result['vram_warning'], 'unknown')
                self.assertFalse(result['frontend_local'])
                self.assertIsNone(result['vram_free_gb'])
        self.raw['api']['enabled'] = False
        self.config.write_text(json.dumps(self.raw), encoding='utf8')
        result = status.collect_menu_status(self.config, runtime=self.root,
            gpu=GpuMemory('fixture', 8192, 1536))
        self.assertEqual(result['vram_warning'], 'green')
        self.assertEqual(result['vram_free_gb'], 1.5)

    def test_only_connected_identity_matched_desktop_counts_as_running(self):
        identity = dict(executable='python.exe', started='same')
        (self.root/'frontend-ready.json').write_text(json.dumps(dict(pid=123, identity=identity, phase='react-connected')))
        with patch.object(status, 'process_identity', return_value=identity):
            self.assertEqual(self.collect()['frontend_status'], '运行中')
        with patch.object(status, 'process_identity', return_value=None):
            self.assertEqual(self.collect()['frontend_status'], '已停止')
        with patch.object(status, 'process_identity', return_value=dict(identity, started='reused')):
            self.assertEqual(self.collect()['frontend_status'], '已停止')
        (self.root/'frontend-ready.json').write_text('{invalid')
        self.assertEqual(self.collect()['frontend_status'], '已停止')

    def test_zero_memory_is_known_red_and_absent_gpu_is_unknown(self):
        self.config.write_text(json.dumps(self.raw))
        result = status.collect_menu_status(self.config, runtime=self.root, gpu=GpuMemory('fixture',8192,0))
        self.assertEqual((result['vram_warning'], result['vram_free_gb']), ('red',0))
        self.assertEqual(self.collect()['vram_warning'], 'unknown')

    def test_changes_are_read_fresh_without_rewriting_dictionary_configuration(self):
        self.raw['alignment'] = dict(mfa=dict(dictionary_path='selected.dict', dictionary_paths=['selected.dict']))
        self.collect()
        original = self.config.read_bytes()
        status.collect_menu_status(self.config, runtime=self.root)
        self.assertEqual(self.config.read_bytes(), original)
        self.raw['server']['presets'][0]['label'] = '已切换'
        self.assertEqual(self.collect()['frontend_model'], '已切换')

    def test_failed_probe_writes_diagnostic_and_failed_snapshot(self):
        output = self.root / 'frontend-menu-123.json'
        for error in (FileNotFoundError('No usable temporary directory'), RuntimeError('probe failed')):
            with self.subTest(error=error), patch.object(status, 'detect_gpu', return_value=None), \
                    patch.object(status, 'collect_menu_status', side_effect=error):
                self.assertEqual(status.main(['--output', str(output)]), 1)
            self.assertFalse(json.loads(output.read_text(encoding='utf8'))['success'])
            self.assertIn(type(error).__name__, (self.root / 'frontend-menu-error.txt').read_text(encoding='utf8'))
            self.assertIn(str(error), (self.root / 'frontend-menu-error.txt').read_text(encoding='utf8'))

    def test_record_write_failure_is_logged_and_propagated(self):
        output = self.root / 'frontend-menu-123.json'
        with patch.object(status, 'detect_gpu', return_value=None), \
                patch.object(status, 'collect_menu_status', return_value={'success': True}), \
                patch.object(status, 'write_record', side_effect=PermissionError('record denied')):
            with self.assertRaisesRegex(PermissionError, 'record denied'):
                status.main(['--output', str(output)])
        self.assertIn('PermissionError: record denied', (self.root / 'frontend-menu-error.txt').read_text())

    def test_diagnostic_write_denial_does_not_mask_original_failure(self):
        with patch.object(status, 'detect_gpu', return_value=None), \
                patch.object(status, 'collect_menu_status', return_value={'success': True}), \
                patch.object(status, 'write_record', side_effect=FileNotFoundError('original error')), \
                patch.object(Path, 'write_text', side_effect=PermissionError('diagnostic denied')):
            with self.assertRaisesRegex(FileNotFoundError, 'original error'):
                status.main(['--output', str(self.root / 'menu.json')])


if __name__ == '__main__': unittest.main()
