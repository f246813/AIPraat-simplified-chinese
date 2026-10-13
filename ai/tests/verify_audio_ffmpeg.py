"""Exercise the compiled Praat reader with real files and an isolated profile."""
import argparse
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile


def run(executable, ffmpeg, sources):
    executable, ffmpeg = executable.resolve(), ffmpeg.resolve()
    sources = [source.resolve() for source in sources]
    # Workspace directories are writable by the desktop executor's test processes.
    with tempfile.TemporaryDirectory(prefix='aipraat-reader-test-', dir=executable.parent) as raw:
        root = Path(raw)
        for source in sources:
            before = hashlib.sha256(source.read_bytes()).hexdigest()
            output = root / (source.stem + '.wav')
            script = root / 'read.praat'
            quote = lambda p: str(p).replace('\\', '/').replace('"', '""')
            script.write_text(f'Read from file: "{quote(source)}"\nSave as WAV file: "{quote(output)}"\nprintline OK\n', encoding='utf-8')
            result = subprocess.run([str(executable), f'--pref-dir={root}', '--no-plugins', '--FULL-TRUST', '--run', str(script)],
                                    env={**os.environ, 'PRAAT_AI_FFMPEG': str(ffmpeg), 'TEMP': str(root), 'TMP': str(root)}, capture_output=True, timeout=30)
            assert result.returncode == 0, result.stderr.decode('utf-16-le' if b'\0' in result.stderr[:20] else 'utf-8', errors='replace')
            assert output.read_bytes()[:4] == b'RIFF'
            assert hashlib.sha256(source.read_bytes()).hexdigest() == before
            assert not list(root.glob('aipraat-audio-*')), 'Temporary decoder files were not cleaned'
            print('PASS ordinary Read from file + WAV export + source unchanged:', source.name)
        # A native OGG read must succeed even if the decoder executable is
        # missing. AAC, which this build cannot read natively, must then fail.
        for source, succeeds in ((sources[0], True), (sources[-1], False)):
            script.write_text(f'Read from file: "{quote(source)}"\n', encoding='utf-8')
            result = subprocess.run([str(executable), f'--pref-dir={root}', '--no-plugins', '--FULL-TRUST', '--run', str(script)],
                                    env={**os.environ, 'PRAAT_AI_FFMPEG': str(root/'missing.exe'), 'TEMP': str(root), 'TMP': str(root)}, capture_output=True, timeout=30)
            assert (result.returncode == 0) == succeeds
            assert not list(root.glob('aipraat-audio-*')), 'Failed decoder left temporary files'
        print('PASS native read needs no FFmpeg; failed fallback cleans temporary files')
        # Exercise the saved configuration used by the path dialog, without a
        # test-only executable override.
        config = root/'ai_config.json'
        import json
        config.write_text(json.dumps({'audio': {'ffmpeg': str(ffmpeg)}}), encoding='utf-8')
        script.write_text(f'Read from file: "{quote(sources[-1])}"\n', encoding='utf-8')
        env = {**os.environ, 'PRAAT_AI_CONFIG_PATH': str(config), 'TEMP': str(root), 'TMP': str(root)}
        env.pop('PRAAT_AI_FFMPEG', None)
        result = subprocess.run([str(executable), f'--pref-dir={root}', '--no-plugins', '--FULL-TRUST', '--run', str(script)], env=env, capture_output=True, timeout=30)
        assert result.returncode == 0, result.stderr
        assert not list(root.glob('aipraat-audio-*'))
        print('PASS shared audio.ffmpeg config is used by the native reader')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--exe', type=Path, required=True)
    parser.add_argument('--ffmpeg', type=Path, required=True)
    parser.add_argument('sources', type=Path, nargs='+')
    args = parser.parse_args()
    run(args.exe, args.ffmpeg, args.sources)
