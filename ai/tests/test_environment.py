"""运行环境体检：必需项决定能不能用，可选项只降级。"""
import unittest
from pathlib import Path
from unittest.mock import patch

from praat_ai import capabilities, environment


class EnvironmentReportTests(unittest.TestCase):
    def missing(self, **patches):
        with patch.object(environment, 'praat_executable', return_value=patches.get('praat', '/app/Praat.exe')), \
             patch.object(environment, 'ffmpeg_executable', return_value=patches.get('ffmpeg', '/bin/ffmpeg')):
            return environment.report()

    def test_everything_present_is_ready_and_quiet(self):
        report = self.missing()
        self.assertTrue(report['ready'])
        self.assertFalse(report['degraded'])
        self.assertEqual(report['message'], '')
        self.assertEqual([item['id'] for item in report['items']], ['praat', 'ffmpeg'])
        for item in report['items']:
            self.assertTrue(item['ready'])
            self.assertEqual(item['hint'], '')

    def test_missing_praat_blocks_the_whole_chain(self):
        report = self.missing(praat='')
        self.assertFalse(report['ready'])
        self.assertEqual(report['missing'], ['Praat 可执行文件'])
        self.assertIn('缺少必需运行件', report['message'])
        praat = report['items'][0]
        self.assertFalse(praat['ready'])
        self.assertEqual(praat['detail'], '未找到')
        self.assertIn('Praat.exe', praat['hint'])
        # 可选项此时不该被当成阻塞原因
        self.assertEqual(report['degraded'], False)

    def test_missing_ffmpeg_only_degrades(self):
        report = self.missing(ffmpeg='')
        self.assertTrue(report['ready'])
        self.assertTrue(report['degraded'])
        self.assertEqual(report['missing'], [])
        self.assertIn('可选项缺失', report['message'])
        ffmpeg = report['items'][1]
        self.assertFalse(ffmpeg['required'])
        self.assertIn('Install-FFmpeg', ffmpeg['hint'])

    def test_wav2vec2_row_follows_the_configured_directory(self):
        with self.subTest('目录存在'):
            report = environment.report({'alignment': {'wav2vec2': {'models': [str(Path(__file__).parent)]}}})
            row = next(item for item in report['items'] if item['id'] == 'wav2vec2')
            self.assertTrue(row['ready'])
            self.assertFalse(row['required'])
        with self.subTest('目录不存在'):
            report = environment.report({'alignment': {'wav2vec2': {'models': ['Z:/nope/model']}}})
            row = next(item for item in report['items'] if item['id'] == 'wav2vec2')
            self.assertFalse(row['ready'])
            self.assertIn('模型目录不存在', row['detail'])

    def test_no_config_means_no_wav2vec2_row(self):
        report = environment.report()
        self.assertNotIn('wav2vec2', [item['id'] for item in report['items']])

    def test_capabilities_payload_carries_the_environment_block(self):
        """前端只读一个 RPC：``tutor.capabilities`` 里就要能看到运行环境。"""

        payload = capabilities.analysis_capabilities()
        self.assertIn('environment', payload)
        self.assertIn('praat', [item['id'] for item in payload['environment']['items']])
        # 解释器依赖与运行环境是两件事，缺哪一类都要在 message 里说出来
        self.assertEqual(payload['ready'], payload['environment']['ready'] and not any(
            not feature['ready'] for feature in payload['features'].values()))

    def test_refresh_clears_the_praat_probe_cache(self):
        from praat_ai import praat_app

        praat_app._cached, praat_app._cache_valid = Path('/x/Praat.exe'), True
        environment.refresh()
        self.assertFalse(praat_app._cache_valid)


if __name__ == '__main__':
    unittest.main()
