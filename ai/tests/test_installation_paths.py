import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from praat_ai.audio_decoder import resolve_ffmpeg
from praat_ai.paths import dependency_environment, installation_root
from praat_ai.server import log_dir
from mfa_alignment_probe import mfa_root_directory


class InstallationPathsTests(unittest.TestCase):
    def test_default_root_is_application_location_independent_of_cwd_and_config(self):
        with patch.dict(os.environ, {'PRAAT_AI_INSTALL_ROOT': '', 'PRAAT_AI_CONFIG_PATH': 'C:/other/config.json'}):
            self.assertEqual(installation_root(), Path(__file__).resolve().parents[2])

    def test_installed_copy_uses_its_own_dependency_locations_and_logs(self):
        with tempfile.TemporaryDirectory(prefix='AIPraat 中文 ') as folder:
            root = Path(folder)
            config = root / 'ai' / 'ai_config.json'
            config.parent.mkdir()
            config.write_text('{"audio":{"ffmpeg":""}}')
            ffmpeg = root / 'tools' / 'ffmpeg' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
            ffmpeg.parent.mkdir(parents=True)
            ffmpeg.touch()
            with patch.dict(os.environ, {'PRAAT_AI_INSTALL_ROOT': folder, 'PRAAT_AI_CONFIG_PATH': str(config),
                                        'PRAAT_AI_FFMPEG': '', 'HF_HOME': '', 'TORCH_HOME': '',
                                        'MFA_ROOT_DIR': '', 'NUMBA_CACHE_DIR': ''}):
                self.assertEqual(resolve_ffmpeg(), str(ffmpeg))
                self.assertEqual(log_dir(), root / 'logs')
                environment = dependency_environment()
                for name in ('HF_HOME', 'TORCH_HOME', 'NUMBA_CACHE_DIR'):
                    self.assertTrue(Path(environment[name]).is_relative_to(root))
                    self.assertTrue(Path(environment[name]).is_dir())
                self.assertEqual(Path(environment['MFA_ROOT_DIR']), mfa_root_directory(root))
                if os.name == 'nt':
                    self.assertTrue(environment['MFA_ROOT_DIR'].isascii())
                external = root / 'custom-cache'
                with patch.dict(os.environ, {'HF_HOME': str(external)}):
                    self.assertEqual(dependency_environment()['HF_HOME'], str(external))
