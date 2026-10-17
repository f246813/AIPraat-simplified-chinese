"""Evidence harness tests; mock processes are never represented as real R checks."""
from __future__ import annotations

from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import numpy as np
try:
    import pandas as pd
except ImportError:
    pd = None  # Development-only dependency, deliberately absent from application requirements.

PATH = Path(__file__).with_name('verify_normalization_upstream.py')
verification = None
if pd is not None:
    spec = importlib.util.spec_from_file_location('upstream_evidence', PATH)
    verification = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verification)
ROOT = PATH.parents[1]/'third_party'/'normalization-reference'


@unittest.skipIf(pd is None, 'Development-only upstream reference requires pandas, not an application dependency')
class UpstreamEvidenceTests(unittest.TestCase):
    def test_pinned_sources_match_frozen_hashes(self):
        for filename, (_, expected) in verification.PINNED.items():
            self.assertEqual(verification.sha256(ROOT/filename), expected)

    def test_missing_r_is_not_python_substitution(self):
        with patch.object(verification.shutil, 'which', return_value=None):
            result = verification.verify(SimpleNamespace(rscript=None, evidence_dir=None))
        self.assertEqual(result['vowspace']['status'], 'passed')
        self.assertEqual(result['phonR']['status'], 'not_run')
        self.assertIn('no Python substitute', result['phonR']['reason'])

    def test_explicit_missing_r_never_falls_back(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(verification.shutil, 'which') as which:
                with self.assertRaises(FileNotFoundError):
                    verification.verify(SimpleNamespace(rscript=Path(directory)/'absent.exe', evidence_dir=None))
            which.assert_not_called()

    def test_require_r_fails_and_preserves_structured_output(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)/'out.json'
            with patch('sys.argv', [str(PATH), '--require-r', '--output', str(output)]), \
                 patch.object(verification.shutil, 'which', return_value=None), redirect_stdout(io.StringIO()):
                self.assertEqual(verification.main(), 1)
            self.assertEqual(json.loads(output.read_text())['phonR']['status'], 'not_run')

    def test_r_process_failure_cannot_reuse_old_output(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            fake = directory/'NOT-REAL-R.exe'
            fake.write_bytes(b'UNIT TEST ONLY')
            (directory/'r-output.csv').write_text('0,0,0\n')
            calls = [subprocess.CompletedProcess([], 17, stdout=b'', stderr=b'process failure'),
                     subprocess.CompletedProcess([], 0, stdout=b'mock version only', stderr=b'')]
            with patch.object(verification.subprocess, 'run', side_effect=calls) as run:
                result = verification.check_r(fake, directory, ROOT,
                    pd.DataFrame([[1, 2, 3]], columns=['f1', 'f2', 'f3']), np.zeros((1, 3)))
            self.assertEqual(result['status'], 'failed')
            self.assertEqual(result['returncode'], 17)
            self.assertFalse((directory/'r-output.csv').exists())
            self.assertEqual((directory/'r-stderr.txt').read_bytes(), b'process failure')
            self.assertEqual(run.call_args_list[0].args[0][1], '--vanilla')
            self.assertIn('TMPDIR', run.call_args_list[0].kwargs['env'])

    @unittest.skipUnless(os.getenv('NORMALIZATION_RSCRIPT'), 'set NORMALIZATION_RSCRIPT for actual R smoke test')
    def test_actual_r_executes_pinned_source(self):
        # Native R on this device is denied writes under the user's C: Temp.
        # Use the same workspace-local temporary policy as the verifier.
        with tempfile.TemporaryDirectory(dir=verification.native_workspace()) as directory:
            result = verification.verify(SimpleNamespace(rscript=Path(os.environ['NORMALIZATION_RSCRIPT']),
                                                         evidence_dir=Path(directory)))
            self.assertEqual(result['phonR']['status'], 'passed')
            self.assertEqual(result['phonR']['shape'], [30, 3])
            self.assertIn('R version', result['phonR']['stdout'])
            self.assertLess(result['phonR']['max_absolute_error'], 1e-12)
            self.assertEqual(result['phonR']['artifacts']['r-output.csv'],
                             verification.sha256(Path(directory)/'r-output.csv'))


if __name__ == '__main__':
    unittest.main()
