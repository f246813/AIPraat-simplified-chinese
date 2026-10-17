"""Development-only pinned upstream execution; never substitute Python for R.

Use --rscript for an unpacked official R, --require-r for a hard gate, and
--evidence-dir/--output to preserve the executed R program and numeric outputs.
No packages are installed, and R runs with --vanilla (no user startup files).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

import numpy as np
import pandas as pd

from praat_ai._vendor.vowspace import fit_lobanov, transform_lobanov

PINNED = {
    'phonR.R': ('c6d419f187f25d2be315cc4cdc35b7ad2b8ce6ee',
                '016755a7823f8ce8e59d621ea1962b73d62d0449621b52e4980c618053b7cc29'),
    'vowspace-normalization.py': ('3143f636c56cb34e26cc83da0f425494bb3f9c94',
                                '9581297226017f1878ac79f1d876f3513970eecf24893e5831c46e300603a576'),
}


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_workspace():
    directory = Path(__file__).resolve().parents[2]/'scratch'/'normalization-acceptance'/'r-session-temp'
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def check_r(rscript, directory, root, frame, actual):
    """Execute the pinned source in real R and preserve the actual output bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    np.savetxt(directory/'input.csv', frame[['f1', 'f2', 'f3']].to_numpy(), delimiter=',')
    np.savetxt(directory/'python-output.csv', actual, delimiter=',')
    program = (f'source({json.dumps((root/"phonR.R").as_posix())})\n'
               f'x <- as.matrix(read.csv({json.dumps((directory/"input.csv").as_posix())}, header=FALSE))\n'
               'options(digits=17)\n'
               'z <- normLobanov(x, group=rep(c("reference", "learner"), each=15))\n'
               f'write.table(z, file={json.dumps((directory/"r-output.csv").as_posix())}, sep=",", row.names=FALSE, col.names=FALSE)\n'
               'cat("Executed pinned phonR normLobanov; rows=", nrow(z), "; columns=", ncol(z), "\\n", sep="")\n'
               'print(R.version.string)\nsessionInfo()\n')
    (directory/'check.R').write_text(program, encoding='utf-8')
    # Never let stale output from a previous successful run satisfy this run.
    (directory/'r-output.csv').unlink(missing_ok=True)
    command = [str(rscript), '--vanilla', str(directory/'check.R')]
    # MSYS may pass /tmp to a native Windows R. Set a native temp path only in
    # this child environment; never edit the user's environment or registry.
    environment = os.environ.copy()
    # Keep the native R scratch directory under this development workspace:
    # the unpacked R on this device cannot create R_TempDir under the user's
    # default C: Temp folder even though Python can. Evidence/output paths may
    # still be in a caller-supplied directory. No user environment is changed.
    with tempfile.TemporaryDirectory(prefix='r-temp-', dir=native_workspace()) as native_temp:
        # Windows R can consult TEMP/TMP as well as TMPDIR. Do not retain
        # MSYS '/tmp' values in any of the native child's temporary paths.
        for variable in ('TMPDIR', 'TMP', 'TEMP'):
            environment[variable] = Path(native_temp).resolve().as_posix()
        run = subprocess.run(command, timeout=60, capture_output=True, env=environment)
    (directory/'r-stdout.txt').write_bytes(run.stdout)
    (directory/'r-stderr.txt').write_bytes(run.stderr)
    version = subprocess.run([str(rscript), '--version'], timeout=15, capture_output=True)
    result = dict(status='failed', commit=PINNED['phonR.R'][0], command=command,
                  executable_sha256=sha256(rscript), version=version.stdout.decode('utf-8', 'replace').strip(),
                  temporary_directory=environment['TMPDIR'],
                  returncode=run.returncode, stdout=run.stdout.decode('utf-8', 'replace'),
                  stderr=run.stderr.decode('utf-8', 'replace'))
    dll = rscript.parent/'R.dll'
    if dll.is_file():
        result['r_dll_sha256'] = sha256(dll)
    result['artifacts'] = {name: sha256(directory/name) for name in
                           ('input.csv', 'python-output.csv', 'check.R', 'r-stdout.txt', 'r-stderr.txt')}
    if run.returncode:
        return result
    result['artifacts']['r-output.csv'] = sha256(directory/'r-output.csv')
    r_z = np.loadtxt(directory/'r-output.csv', delimiter=',')
    result['shape'] = list(r_z.shape)
    try:
        np.testing.assert_allclose(actual, r_z, atol=1e-12, rtol=1e-12)
    except AssertionError as exc:
        result['reason'] = str(exc)
        return result
    result.update(status='passed', max_absolute_error=float(np.max(np.abs(actual-r_z))),
                  tolerance=dict(atol=1e-12, rtol=1e-12))
    return result


def verify(args):
    root = Path(__file__).resolve().parents[1] / 'third_party' / 'normalization-reference'
    sources = {}
    for name, (commit, expected_hash) in PINNED.items():
        digest = sha256(root/name)
        if digest != expected_hash:
            raise ValueError(f'Pinned source hash mismatch: {name}: {digest}')
        sources[name] = dict(commit=commit, sha256=digest)
    spec = importlib.util.spec_from_file_location('upstream_vowspace', root/'vowspace-normalization.py')
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    rng = np.random.default_rng(20261016)
    matrices = [rng.normal(size=(15, 3)) * [120, 310, 250] + [480, 1550, 2900],
                rng.normal(size=(15, 3)) * [150, 360, 270] + [600, 1900, 3300]]
    frame = pd.DataFrame(np.vstack(matrices), columns=['f1', 'f2', 'f3'])
    frame['speaker'] = ['reference'] * 15 + ['learner'] * 15
    expected = upstream.lobanov_normalization(frame.copy(), ['f1', 'f2', 'f3'])
    actual = np.vstack([np.column_stack([transform_lobanov(matrix[:, j], *fit_lobanov(matrix[:, j]))
                                        for j in range(3)]) for matrix in matrices])
    upstream_z = expected[['zsc_f1', 'zsc_f2', 'zsc_f3']].to_numpy()
    np.testing.assert_allclose(actual, upstream_z, atol=1e-12, rtol=1e-12)
    result = dict(executed_at_utc=datetime.now(timezone.utc).isoformat(),
                  scope='Synthetic numeric equivalence only; not real speech accuracy.', sources=sources,
                  vowspace=dict(status='passed', commit=PINNED['vowspace-normalization.py'][0],
                      numpy=np.__version__, pandas=pd.__version__,
                      max_absolute_error=float(np.max(np.abs(actual-upstream_z)))),
                  phonR=dict(status='not_run', reason='Rscript not found; no Python substitute for actual R.'))
    # An explicit invalid path is an error, not permission to fall back silently.
    executable = str(args.rscript) if args.rscript else shutil.which('Rscript')
    if executable:
        rscript = Path(executable).resolve(strict=True)
        if args.evidence_dir:
            result['phonR'] = check_r(rscript, args.evidence_dir.resolve(), root, frame, actual)
        else:
            with tempfile.TemporaryDirectory(prefix='r-evidence-', dir=native_workspace()) as directory:
                result['phonR'] = check_r(rscript, Path(directory), root, frame, actual)
    return result


def main():
    import sys
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-r', action='store_true')
    parser.add_argument('--rscript', type=Path)
    parser.add_argument('--evidence-dir', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    try:
        result = verify(args)
    except (OSError, ValueError, AssertionError, subprocess.SubprocessError) as exc:
        result = dict(status='failed', reason=f'{type(exc).__name__}: {exc}')
    payload = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding='utf-8')
    print(payload, end='')
    status = result.get('phonR', {}).get('status')
    return int(result.get('status') == 'failed' or status == 'failed'
               or (args.require_r and status != 'passed'))


if __name__ == '__main__':
    raise SystemExit(main())
