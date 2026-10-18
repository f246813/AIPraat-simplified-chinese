"""Real Praat regression: automatic VOT must retain only original objects.

需要真实的 ``Praat.exe``：判定交给 :mod:`praat_ai.environment`（与前端「运行环境」
面板同一份口径：仓库根 → ``PRAAT_AI_PRAAT_EXECUTABLE`` → ``PATH`` → 常见安装位置）。
没有二进制时按「环境缺件」跳过，不再报成 FileNotFoundError。
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

from praat_ai import environment, tools


class VotCleanupTests(unittest.TestCase):
    def setUp(self):
        praat = environment.praat_executable()
        if not praat:
            self.skipTest('需要真实 Praat.exe：' + environment.PRAAT_HINT)
        self.praat = Path(praat)

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
