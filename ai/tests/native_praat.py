"""Run generated production scripts with the native Praat command registry."""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest


def executable():
    return Path(os.environ.get('PRAAT_AI_PRAAT_EXECUTABLE') or
                Path(__file__).resolve().parents[2]/('Praat.exe' if os.name == 'nt' else 'praat'))


def run(objects_or_script, script=None, *, capture_output=False):
    binary=executable()
    if not binary.is_file():
        raise unittest.SkipTest('原生 Praat 脚本验收需要 PRAAT_AI_PRAAT_EXECUTABLE；内嵌旧 Praat 不替代此层验收')
    objects=[] if script is None else objects_or_script
    script=objects_or_script if script is None else script
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory)
        prefix=[]
        for index,obj in enumerate(objects):
            path=root/f'input-{index}.bin'
            obj.save_as_binary_file(str(path))
            prefix.append(f'Read from file: "{path.as_posix()}"')
        path=root/'run.praat'
        path.write_text('\n'.join(prefix)+ '\n'+script,encoding='utf-8')
        result=subprocess.run([str(binary),'--no-pref-files','--no-plugins','--FULL-TRUST','--run',str(path)],capture_output=True,timeout=30)
        def decode(data):
            if data.startswith((b'\xff\xfe',b'\xfe\xff')):
                return data.decode('utf-16')
            return data.decode('utf-16-le' if b'\x00' in data[:100] else 'utf-8','replace')
        if result.returncode:
            raise RuntimeError(decode(result.stderr))
        return ([],decode(result.stdout)) if capture_output else []
