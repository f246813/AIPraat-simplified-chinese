"""Real Praat regression: automatic VOT must retain only original objects.

需要真实的 ``Praat.exe``：放在仓库根、或用 ``PRAAT_AI_PRAAT`` 指到已安装的那份。
没有二进制时按「环境缺件」跳过（不算失败），不再报成 FileNotFoundError。
"""
from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

from praat_ai import tools

REPO = Path(__file__).resolve().parents[2]


def praat_executable():
    """按「仓库根 → PRAAT_AI_PRAAT → PATH」找真二进制；都没有返回 None。"""

    candidates = [REPO / 'Praat.exe']
    configured = os.environ.get('PRAAT_AI_PRAAT')
    if configured:
        candidates.append(Path(configured))
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which('Praat.exe') or shutil.which('praat')
    return Path(found) if found else None


class VotCleanupTests(unittest.TestCase):
    def setUp(self):
        praat = praat_executable()
        if praat is None:
            self.skipTest('需要真实 Praat.exe：放到仓库根，或设 PRAAT_AI_PRAAT 指向已安装的那份')
        self.praat = praat

    def test_automatic_vot_leaves_no_created_objects(self):
        praat = self.praat
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            context = tools.ToolContext((tools.ObjectRow(1, 'Sound', 'fixture', True),),
                                        folder / 'result.tsv', folder / 'state.txt')
            source = folder / 'vot.praat'
            count = folder / 'count.txt'
            source.write_text(
                'Create Sound from formula: "fixture", 1, 0, 1, 16000, "0.2 * sin(2*pi*220*x)"\n' +
                tools.render('vot', {'from': .1, 'to': .4}, context) +
                '\nselect all\nappendFileLine: ' + tools.quote(count) + ', numberOfSelected ()\n', encoding='utf8')
            proc = subprocess.run([str(praat), '--no-pref-files', '--no-plugins', '--FULL-TRUST', '--run', str(source)],
                                  capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode('utf8', 'replace'))
            self.assertEqual(context.state_path.read_text(encoding='utf-8-sig').strip(), 'done')
            self.assertEqual(count.read_text(encoding='utf-8-sig').strip(), '1')
