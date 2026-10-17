"""Exercise actual native runner/launch functions through a linked test driver.

Usage: python verify_frontend_timeout.py /absolute/path/to/native_frontend_timeout.exe
No GUI, model request, user configuration, or user runtime record is used.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time


def main():
    driver = Path(sys.argv[1]).resolve()
    checks = []
    with tempfile.TemporaryDirectory(prefix='frontend-timeout-') as directory:
        root = Path(directory)
        environment = dict(os.environ, PYTHONIOENCODING='utf-8', PRAAT_AI_PROJECT_DIR=str(root))

        def run_case(name, source, timeout, expected):
            case = root / name
            case.mkdir()
            script = case / 'probe.py'
            script.write_text(source, encoding='utf8')
            completed = subprocess.run([str(driver), sys.executable, str(script), str(case), str(timeout)],
                capture_output=True, text=True, encoding='utf8', env=environment, timeout=8)
            assert completed.returncode == expected, (name, completed.stdout, completed.stderr)
            elapsed = int(completed.stdout.rsplit('elapsed_ms=', 1)[1].strip())
            if expected:
                assert f'{timeout} 毫秒内未完成' in completed.stderr, completed.stderr
                diagnostic_path = case / 'runtime' / 'frontend-startup-error.json'
                assert diagnostic_path.exists(), (name, completed.stdout, completed.stderr)
                diagnostic = json.loads(diagnostic_path.read_text())
                assert diagnostic['timeout_ms'] == timeout
                assert timeout - 20 <= elapsed < timeout + 1500, elapsed
            else:
                assert elapsed < 2000, elapsed
            checks.append(dict(name=name, elapsed_ms=elapsed, exit_code=completed.returncode))
            return completed, case

        completed, _ = run_case('normal-output', "print('前端输出')\n", 2000, 0)
        assert '前端输出' in completed.stdout
        run_case('silent-hang', 'import time\ntime.sleep(30)\n', 200, 1)
        run_case('continuous-output', "import os\nwhile True: os.write(1, b'busy\\n')\n", 200, 1)
        _, case = run_case('venv-child-terminated', "import time\nfrom pathlib import Path\ntime.sleep(1)\nPath('still-running.txt').write_text('bad')\ntime.sleep(30)\n", 200, 1)
        time.sleep(1.1)
        assert not (case / 'still-running.txt').exists(), 'venv interpreter survived native timeout'
        run_case('inherited-pipe', "import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(1)'], close_fds=False)\nprint('root finished')\n", 2000, 0)
        run_case('ordinary-script-without-menu-deadline', "import time\ntime.sleep(.3)\nprint('finished')\n", 0, 0)

        # A driver built inside this checkout inherits its Low integrity label.
        # That reproduces the reported denial against the ordinary TEMP folder.
        low_driver = Path(__file__).resolve().parents[2] / 'test-records/diagnostics/native_frontend_timeout.exe'
        if low_driver.exists() and low_driver != driver:
            script = root / 'denied_record.py'
            script.write_text("from pathlib import Path\nfrom praat_ai.desktop_launch import write_record\nwrite_record(Path('denied.json'), {'success': True})\n", encoding='utf8')
            denied_environment = dict(environment, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
            completed = subprocess.run([str(low_driver), sys.executable, str(script), str(root), '2000'],
                capture_output=True, text=True, encoding='utf8', env=denied_environment, timeout=5)
            assert completed.returncode == 1, (completed.stdout, completed.stderr)
            assert 'PermissionError' in completed.stdout, (completed.stdout, completed.stderr)
            elapsed = int(completed.stdout.rsplit('elapsed_ms=', 1)[1].strip())
            assert elapsed < 1000, elapsed
            checks.append(dict(name='actual-low-integrity-write-denial-exits-immediately', elapsed_ms=elapsed))

        (root / 'runtime').mkdir()
        (root / 'run_ai_control.py').write_text("import json\nfrom pathlib import Path\nPath('runtime/status.json').write_text(json.dumps({'success': True}))\n", encoding='utf8')
        (root / 'run_frontend_status.py').write_text('pass\n', encoding='utf8')
        (root / 'start_ai_chat.py').write_text("import os, time\nfrom pathlib import Path\nPath('launcher-parent.txt').write_text(os.environ['PRAAT_AI_PRAAT_PID'])\ntime.sleep(2)\nPath('launcher-complete.txt').write_text('done')\n", encoding='utf8')
        started = time.monotonic()
        completed = subprocess.run([str(driver), sys.executable, 'start', str(root)],
            capture_output=True, text=True, encoding='utf8', env=environment, timeout=8)
        elapsed = time.monotonic() - started
        assert completed.returncode == 0, (completed.stdout, completed.stderr)
        assert elapsed < 1.5, elapsed
        assert not (root / 'launcher-complete.txt').exists(), 'GUI caller waited for detached launcher'
        deadline = time.monotonic() + 5
        while not (root / 'launcher-complete.txt').exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert (root / 'launcher-complete.txt').exists()
        assert int((root / 'launcher-parent.txt').read_text()) > 0
        checks.append(dict(name='detached-launch-returns-before-child', elapsed_ms=round(elapsed * 1000)))
    print(json.dumps(dict(checks=checks), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
