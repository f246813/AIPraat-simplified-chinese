"""Verify the actual Start frontend callback with identical Low/Medium EXEs.

Private preferences, synthetic audio and a Medium status.json reproduce the
publication bug without touching the user's runtime or sending model requests.
See docs/CURRENT-IMPLEMENTATION.zh-CN.md.
"""
import argparse
import ctypes
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
A = ctypes.WinDLL('advapi32', use_last_error=True)
K = ctypes.WinDLL('kernel32', use_last_error=True)
K.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
K.OpenProcess.restype = W.HANDLE
K.CloseHandle.argtypes = [W.HANDLE]
K.LocalFree.argtypes = [ctypes.c_void_p]
A.OpenProcessToken.argtypes = [W.HANDLE, W.DWORD, ctypes.POINTER(W.HANDLE)]
A.GetTokenInformation.argtypes = [W.HANDLE, ctypes.c_int, ctypes.c_void_p, W.DWORD, ctypes.POINTER(W.DWORD)]
A.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
A.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
A.GetSidSubAuthority.argtypes = [ctypes.c_void_p, W.DWORD]
A.GetSidSubAuthority.restype = ctypes.POINTER(W.DWORD)
A.GetNamedSecurityInfoW.argtypes = [W.LPCWSTR, ctypes.c_int, W.DWORD] + [ctypes.POINTER(ctypes.c_void_p)] * 5
A.GetAce.argtypes = [ctypes.c_void_p, W.DWORD, ctypes.POINTER(ctypes.c_void_p)]


def rid(sid):
    return A.GetSidSubAuthority(sid, A.GetSidSubAuthorityCount(sid)[0] - 1)[0]


def process_level(pid):
    handle = K.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    token = W.HANDLE()
    try:
        if not A.OpenProcessToken(handle, 8, ctypes.byref(token)):
            raise ctypes.WinError(ctypes.get_last_error())
        needed = W.DWORD()
        A.GetTokenInformation(token, 25, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not A.GetTokenInformation(token, 25, buffer, len(buffer), ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        return rid(ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0])
    finally:
        if token:
            K.CloseHandle(token)
        K.CloseHandle(handle)


def file_level(path):
    owner, group, dacl, sacl, descriptor = [ctypes.c_void_p() for _ in range(5)]
    error = A.GetNamedSecurityInfoW(str(path), 1, 0x10, *map(ctypes.byref, (owner, group, dacl, sacl, descriptor)))
    if error:
        raise ctypes.WinError(error)
    try:
        if sacl.value:
            count = ctypes.c_ushort.from_address(sacl.value + 4).value
            for index in range(count):
                ace = ctypes.c_void_p()
                if not A.GetAce(sacl, index, ctypes.byref(ace)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if ctypes.c_ubyte.from_address(ace.value).value == 0x11:
                    return rid(ace.value + 8)
        return 8192  # Windows treats an absent mandatory label as Medium.
    finally:
        K.LocalFree(descriptor)


CONTROL_PROBE = '''
import contextlib, io, json, os
from pathlib import Path
from integrity_probe import process_level
from praat_ai.desktop_launch import menu_control_main
error = io.StringIO()
with contextlib.redirect_stderr(error):
    code = menu_control_main()
result = dict(code=code, stderr=error.getvalue(), pid=os.getpid(),
              integrity=process_level(os.getpid()),
              command=os.environ.get('PRAAT_AI_CONTROL_COMMAND'))
Path(os.environ['INTEGRITY_PROBE_ROOT'], 'control-result.json').write_text(json.dumps(result), encoding='utf8')
if error.getvalue(): print(error.getvalue(), end='', file=__import__('sys').stderr)
raise SystemExit(code)
'''
CHAT_PROBE = '''
import json, os
from pathlib import Path
from integrity_probe import process_level
Path(os.environ['INTEGRITY_PROBE_ROOT'], 'chat-result.json').write_text(
    json.dumps(dict(pid=os.getpid(), integrity=process_level(os.getpid()))), encoding='utf8')
'''


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--low-exe', type=Path, required=True)
    parser.add_argument('--medium-exe', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    from verify_python_bridge_hello import U, owned_windows, wait
    from verify_path_settings_live import command, children, text
    paths = [args.low_exe.resolve(), args.medium_exe.resolve()]
    hashes = [hashlib.sha256(path.read_bytes()).hexdigest() for path in paths]
    assert hashes[0] == hashes[1], 'Only file metadata may differ'
    assert [file_level(path) for path in paths] == [4096, 8192]
    assert process_level(os.getpid()) == 8192
    report = dict(passed=False, executableSHA256=hashes[0], realCloud=False, cases=[])
    python = ROOT.parent / 'venv-ai-modern/Scripts/python.exe'
    low = Path(os.environ['USERPROFILE']) / 'AppData/LocalLow'
    medium = Path(os.environ['LOCALAPPDATA']) / 'Temp'
    assert file_level(medium) == 8192
    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        for executable, expected_level in zip(paths, (4096, 8192)):
            with tempfile.TemporaryDirectory(prefix='praat-integrity-', dir=low) as directory:
                root = Path(directory)
                ai = root / 'ai'
                shutil.copytree(ROOT / 'ai/praat_ai', ai / 'praat_ai', ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
                for script in (ROOT / 'ai').glob('*.py'):
                    shutil.copy2(script, ai / script.name)
                shutil.copy2(__file__, ai / 'integrity_probe.py')
                (ai / 'run_ai_control.py').write_text(CONTROL_PROBE, encoding='utf8')
                (ai / 'start_ai_chat.py').write_text(CHAT_PROBE, encoding='utf8')
                assets = ai / 'frontend/dist'
                assets.mkdir(parents=True)
                shutil.copy2(ROOT / 'ai/frontend/dist/index.html', assets / 'index.html')
                config = ai / 'ai_config.json'
                config.write_text(json.dumps(dict(api=dict(enabled=False), server=dict(auto_start=False))), encoding='utf8')
                runtime = ai / 'runtime'
                status = runtime / 'status.json'
                # Both runtime and its file are Medium in the faulting project.
                # A Low directory would permit atomic replacement of a Medium
                # file and hide the bug. Rename the entire Medium fixture here.
                seed = Path(tempfile.mkdtemp(prefix='praat-status-', dir=medium))
                try:
                    assert file_level(seed) == 8192
                    (seed / 'status.json').write_text('{"fixture":true}', encoding='utf8')
                    assert file_level(seed / 'status.json') == 8192
                    assert seed.drive.lower() == runtime.drive.lower()
                    os.replace(seed, runtime)
                finally:
                    if seed.exists():
                        (seed / 'status.json').unlink(missing_ok=True)
                        seed.rmdir()
                assert file_level(runtime) == 8192
                assert file_level(status) == 8192
                profile = root / 'preferences'
                profile.mkdir()
                script = root / 'prepare.praat'
                script.write_text('Create Sound from formula: "integrity-test", 1, 0, 0.1, 16000, ~0\nView & Edit\n', encoding='utf8')
                env = {key: value for key, value in os.environ.items() if not key.startswith('PRAAT_')}
                env.update(PRAAT_AI_PROJECT_DIR=str(ai), PRAAT_AI_CONFIG_PATH=str(config),
                           PRAAT_PYTHON_EXECUTABLE=str(python), INTEGRITY_PROBE_ROOT=str(root),
                           PYTHONDONTWRITEBYTECODE='1', TEMP=str(root), TMP=str(root))
                process = subprocess.Popen([str(executable), '--new-send', '--no-plugins', '--no-pref-files',
                    '--FULL-TRUST', '--pref-dir=' + str(profile), str(script)], env=env, cwd=root)
                case = dict(executable=str(executable), fileIntegrity=expected_level,
                            runtimeIntegrity=file_level(runtime), statusIntegrityBefore=file_level(status))
                report['cases'].append(case)
                try:
                    editor = wait(lambda: next((w for w in owned_windows(process.pid) if 'integrity-test' in w[1]), None), 'isolated editor')
                    case['praatIntegrity'] = process_level(process.pid)
                    assert case['praatIntegrity'] == expected_level
                    number = command(editor[0], ('启动前端', 'Start frontend'))
                    assert U.PostMessageW(editor[0], 0x111, number, 0)
                    result = wait(lambda: json.loads((root / 'control-result.json').read_text(encoding='utf8'))
                                  if (root / 'control-result.json').exists() else None, 'frontend preparation')
                    case.update(pythonPid=result['pid'], pythonIntegrity=result['integrity'], code=result['code'], stderr=result['stderr'])
                    assert result['command'] == 'start'
                    assert result['integrity'] == expected_level
                    case['status'] = json.loads(status.read_text(encoding='utf8'))
                    if expected_level == 4096:
                        assert result['code'] == 1 and 'Permission denied' in result['stderr'] and 'status.json' in result['stderr']
                        assert case['status'] == dict(fixture=True)
                        message = wait(lambda: next((w for w in owned_windows(process.pid) if w[1] == 'Message'), None), 'native failure dialog')
                        case['nativeDiagnostic'] = '\n'.join(text(child) for child in children(message[0]))
                        assert 'exit code 1' in case['nativeDiagnostic']
                        assert not (root / 'chat-result.json').exists()
                    else:
                        assert result['code'] == 0 and result['stderr'] == ''
                        assert case['status']['frontend_start_phase'] == 'ready-to-launch'
                        chat = wait(lambda: json.loads((root / 'chat-result.json').read_text(encoding='utf8'))
                                    if (root / 'chat-result.json').exists() else None, 'detached frontend launch')
                        case['chatIntegrity'] = chat['integrity']
                        assert chat['integrity'] == 8192
                    case['statusIntegrityAfter'] = file_level(status)
                    assert case['statusIntegrityAfter'] == 8192
                    case['passed'] = True
                finally:
                    for hwnd, unused in owned_windows(process.pid):
                        U.PostMessageW(hwnd, 0x10, 0, 0)
                    try:
                        process.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        process.terminate()
                        process.wait(timeout=5)
        report['passed'] = True
    except Exception as error:
        report['error'] = repr(error)
        raise
    finally:
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8', newline='\n')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
