import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from praat_ai.audio_decoder import resolve_ffmpeg
from praat_ai.config import load_config


class AudioDecoderTests(unittest.TestCase):
    def test_shared_path_and_empty_value_override_legacy_setting(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            config = root/'ai_config.json'
            (root/'anki_config.json').write_text(json.dumps({'ffmpeg': 'legacy.exe'}))
            with patch.dict(os.environ, {'PRAAT_AI_CONFIG_PATH': str(config), 'PRAAT_AI_FFMPEG': '', 'LOCALAPPDATA': raw}), patch('shutil.which', return_value='path.exe'):
                self.assertEqual(resolve_ffmpeg(), 'legacy.exe')
                self.assertEqual(load_config(config).to_dict()['audio']['ffmpeg'], 'legacy.exe')
                config.write_text(json.dumps({'audio': {'ffmpeg': 'shared.exe'}}))
                self.assertEqual(resolve_ffmpeg(), 'shared.exe')
                self.assertEqual(load_config(config).to_dict()['audio']['ffmpeg'], 'shared.exe')
                config.write_text(json.dumps({'audio': {'ffmpeg': ''}}))
                self.assertEqual(resolve_ffmpeg(), 'path.exe')
                self.assertEqual(load_config(config).audio.ffmpeg, '')

    def test_explicit_override_is_not_replaced_by_auto_discovery(self):
        with patch.dict(os.environ, {'PRAAT_AI_FFMPEG': 'chosen-ffmpeg.exe'}):
            self.assertEqual(resolve_ffmpeg(), 'chosen-ffmpeg.exe')


if __name__ == '__main__':
    unittest.main()
