"""Real Praat regression: automatic VOT must retain only original objects."""
from pathlib import Path
import subprocess
import tempfile
import unittest
from praat_ai import tools


class VotCleanupTests(unittest.TestCase):
    def test_automatic_vot_leaves_no_created_objects(self):
        praat = Path(__file__).resolve().parents[2] / 'Praat.exe'
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
