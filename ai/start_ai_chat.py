from __future__ import annotations

import os
import json
import subprocess
import sys
from pathlib import Path

from praat_ai.desktop_window import focus_existing_window


try:   # 正常情况下来自包内实现（os.kill 在本机不可用，见 praat_ai/process.py）
    from praat_ai.process import process_alive
except ImportError:   # 独立运行时的兜底
    def process_alive(process_id: int) -> bool:
        if process_id <= 0:
            return False
        try:
            os.kill(process_id, 0)
            return True
        except OSError:
            return False


def start_new_chat_window(directory: Path, pid_path: Path) -> int:
    chat_script = directory / "run_ai_chat.py"
    creation_flags = 0
    if os.name == "nt":
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    process = subprocess.Popen(
        [sys.executable, str(chat_script)],
        cwd=directory,
        creationflags=creation_flags,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    pid_path.write_text(str(process.pid), encoding="utf-8")
    from praat_ai.desktop_launch import write_record
    from praat_ai.process import process_identity
    write_record(pid_path.with_name('chat-process.json'),
                 dict(pid=process.pid, identity=process_identity(process.pid)))
    return 0


def main(runtime: Path | None = None, *, model_settings=False) -> int:
    """Native menu entry: quick detached spawn or identity-checked window reuse."""
    from praat_ai.desktop_launch import check_desktop_dependencies, report_failure, request_model_settings
    from praat_ai.process import identities_match, process_identity
    from praat_ai.service_lock import service_transition_lock
    directory = Path(__file__).resolve().parent
    runtime = runtime or (directory / 'runtime')
    try:
        runtime.mkdir(parents=True, exist_ok=True)
        check_desktop_dependencies()
        with service_transition_lock(runtime / 'chat-launch.lock', timeout=2):
            pid_path = runtime / 'chat.pid'
            # Windows venv python.exe is a shim; the connected desktop owns
            # its window in a different PID. Use its verified ready record.
            try:
                ready = json.loads((runtime / 'frontend-ready.json').read_text(encoding='utf8'))
                desktop_pid = int(ready['pid'])
                if (identities_match(ready.get('identity') or {}, process_identity(desktop_pid))
                        and focus_existing_window(desktop_pid)):
                    if model_settings:
                        request_model_settings(runtime, desktop_pid)
                    return 0
            except (OSError, ValueError, KeyError, TypeError, AttributeError):
                pass
            if pid_path.is_file():
                try:
                    process_id = int(pid_path.read_text(encoding='utf8').strip())
                except ValueError:
                    process_id = 0
                if process_id and process_alive(process_id):
                    # Focus the actual desktop, not an arbitrary window for a reused PID.
                    if focus_existing_window(process_id):
                        if model_settings:
                            request_model_settings(runtime, process_id)
                        return 0
                    try:
                        record = json.loads((runtime / 'chat-process.json').read_text(encoding='utf8'))
                        if record.get('pid') == process_id and identities_match(record.get('identity') or {}, process_identity(process_id)):
                            if model_settings:
                                request_model_settings(runtime, process_id)
                            return 0  # Same process is still initializing; do not duplicate it.
                    except (OSError, ValueError, AttributeError):
                        pass
            result = start_new_chat_window(directory, pid_path)
            if result == 0 and model_settings:
                request_model_settings(runtime, int(pid_path.read_text(encoding='utf8')))
            return result
    except Exception as error:
        return report_failure(error, runtime=runtime)


if __name__ == "__main__":
    raise SystemExit(main())
