import contextlib
import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from zipfile import ZipFile

from praat_ai import anki_bridge as bridge


class BridgeTests(unittest.TestCase):
    @contextlib.contextmanager
    def install_layout(self):
        """造一个假的安装布局（``ai/`` 与同级 ``Praat.exe``）。

        ``open_audio`` 第一步就检查安装目录里有没有 ``Praat.exe``；这三个用例把
        ``command_lock`` / ``_target`` / ``_open_native`` 全 mock 了，测的是共享读取器
        的投递语义，跟真二进制无关。所以这里给一个空文件当占位，测试因此不再依赖
        「本机装没装 Praat」，在任何机器（含 CI）上都能跑。
        """

        with tempfile.TemporaryDirectory() as raw:
            ai = Path(raw) / 'ai'
            ai.mkdir()
            (Path(raw) / 'Praat.exe').write_bytes(b'')
            with patch.object(bridge, 'AI', ai):
                yield
    def test_media_path_and_unicode_url(self):
        with tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / '接する 音声.M4A'
            source.touch()
            self.assertEqual(bridge.resolve_audio(source.name, raw), source.resolve())
            with self.assertRaises(ValueError):
                bridge.resolve_audio('../outside.wav', raw)
        request = bridge.parse_request('aipraat://open?audio=%E6%8E%A5%E3%81%99%E3%82%8B.m4a&start=0.2\u2069')
        self.assertEqual(request['audio'], '接する.m4a')
        self.assertEqual(bridge.time_range(request['start'], '1.2'), (.2, 1.2))
        with self.assertRaises(ValueError):
            bridge.resolve_audio('')

    def test_invalid_ranges(self):
        for start, end in [(float('nan'), 1), (0, float('inf')), (-1, 2), (2, 1), (1, 1)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                bridge.time_range(start, end)

    def test_shared_reader_reports_conversion_without_python_retry(self):
        with self.install_layout(), tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / '接する.m4a'; source.touch()
            calls = []
            def native(audio, original, start, end, pid, directory):
                calls.append((audio, original, start, end))
                (directory/'result.txt').write_text('ok\nconverted\n',encoding='utf-8')
                return ''
            def decode(audio, target):
                target.touch(); return target
            with patch.object(bridge, 'command_lock', contextlib.nullcontext), patch.object(bridge, '_target', return_value=123), patch.object(bridge, '_open_native', side_effect=native), patch.object(bridge, 'decode_wav', side_effect=decode), patch.object(bridge, 'settings', return_value=bridge.DEFAULTS):
                result = bridge.open_audio(str(source), start=.1, end=.8)
            self.assertTrue(result['converted'])
            self.assertEqual(calls[0][:2], (source, source))
            self.assertEqual(len(calls), 1)
            self.assertTrue(source.exists())

    def test_unconfirmed_delivery_does_not_retry(self):
        with self.install_layout(), tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / 'clip.m4a'; source.touch()
            with patch.object(bridge, 'command_lock', contextlib.nullcontext), patch.object(bridge, '_target', return_value=1), patch.object(bridge, '_open_native', side_effect=TimeoutError('no acknowledgement')) as native, patch.object(bridge, 'decode_wav') as decode:
                with self.assertRaises(TimeoutError): bridge.open_audio(str(source))
                native.assert_called_once(); decode.assert_not_called()

    def test_shared_reader_failure_is_not_retried_in_python(self):
        with self.install_layout(), tempfile.TemporaryDirectory() as raw:
            source = Path(raw) / 'clip.m4a'; source.touch()
            with patch.object(bridge, 'command_lock', contextlib.nullcontext), patch.object(bridge, '_target', return_value=1), patch.object(bridge, '_open_native', return_value='bad codec'), patch.object(bridge, 'settings', return_value={**bridge.DEFAULTS, 'fallback': False}), patch.object(bridge, 'decode_wav') as decode:
                with self.assertRaisesRegex(ValueError, 'bad codec'): bridge.open_audio(str(source))
                decode.assert_not_called()

    def test_export_contains_editable_entry_and_current_paths(self):
        with tempfile.TemporaryDirectory() as raw, patch.object(bridge, 'settings', return_value={**bridge.DEFAULTS, 'mode': 'magnifier', 'magnifier_selector': '.Search'}):
            target = Path(raw) / 'AIPraat.ankiaddon'; bridge.export_addon(target)
            with ZipFile(target) as archive:
                self.assertEqual(set(archive.namelist()), {'__init__.py','entry.js','config.json','manifest.json','README.md'})
                self.assertIn('AIPRAAT_ENTRY = "magnifier"', archive.read('entry.js').decode())
                cfg = json.loads(archive.read('config.json'))
                self.assertTrue(Path(cfg['bridge_script']).is_file())
                self.assertTrue(Path(cfg['python_executable']).is_file())

    def test_ffmpeg_retains_channels_and_rate(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / 'decoded.wav'
            def run(args, **kwargs):
                self.assertNotIn('-ar', args); self.assertNotIn('-ac', args)
                self.assertIn('pcm_s24le', args); target.touch()
                return types.SimpleNamespace(returncode=0)
            with patch.object(bridge.subprocess, 'run', side_effect=run):
                self.assertEqual(bridge.decode_wav(Path(raw)/'input.m4a', target, 'ffmpeg.exe'), target)


class AddonTests(unittest.TestCase):
    def setUp(self):
        tag = type('SoundOrVideoTag', (), {'__init__': lambda self, filename: setattr(self, 'filename', filename)})
        self.mw = Mock()
        self.mw.addonManager.getConfig.return_value = {}
        modules = {'anki': types.ModuleType('anki'), 'anki.sound': types.SimpleNamespace(SoundOrVideoTag=tag),
                   'aqt': types.SimpleNamespace(mw=self.mw, gui_hooks=types.SimpleNamespace(card_will_show=[],webview_did_receive_js_message=[])),
                   'aqt.qt': types.SimpleNamespace(QAction=Mock(),QInputDialog=Mock()),
                   'aqt.utils': types.SimpleNamespace(showWarning=Mock(),tooltip=Mock())}
        spec=importlib.util.spec_from_file_location('test_aipraat_addon', bridge.AI/'anki_addon'/'__init__.py')
        self.addon=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', modules): spec.loader.exec_module(self.addon)
        self.card=Mock()
        self.card.question_av_tags.return_value=[tag('単語.m4a'),tag('単語.m4a')]
        self.card.answer_av_tags.return_value=[tag('例文.mp3'),tag('movie.mp4')]

    def test_audio_selection_deduplicates_both_sides(self):
        self.assertEqual(self.addon.audio_files(self.card), ['単語.m4a','例文.mp3'])

    def test_editing_entry_changes_mode_without_restart(self):
        with patch.object(self.addon, 'config', return_value={'mode':'magnifier','magnifier_selector':'.Search'}):
            rendered=self.addon.render('<a class="Search">🔍</a>',self.card,'reviewQuestion')
            self.assertIn('aipraatAttachMagnifier(".Search")', rendered)
            self.assertNotIn('<button onclick=',rendered)
        with patch.object(self.addon, 'config', return_value={'mode':'both'}):
            self.assertIn('查看语图 · AIPraat',self.addon.render('',self.card,'reviewAnswer'))

    def test_other_messages_pass_through(self):
        self.assertEqual(self.addon.receive((False,None),'play:q:0',None),(False,None))


if __name__ == '__main__': unittest.main()
