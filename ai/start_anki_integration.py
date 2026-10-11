"""Launch the Anki window without blocking Praat's menu callback."""
import os
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    directory = Path(__file__).resolve().parent
    subprocess.Popen([sys.executable, str(directory / 'run_anki_integration.py')],
                     cwd=directory, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True,
                     creationflags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0)
