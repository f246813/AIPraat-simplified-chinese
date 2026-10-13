"""Standard-library-only desktop launch boundary; no model probes or requests."""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import uuid4


def runtime_directory():
    return Path(__file__).resolve().parents[1] / 'runtime'


def request_model_settings(runtime, pid):
    """Send a page request to the reused/new desktop, without changing settings."""
    from uuid import uuid4
    write_record(Path(runtime) / 'frontend-navigation.json',
                 dict(pid=pid, id=uuid4().hex, category='模型'))


def model_settings_request(previous_id='', *, runtime=None):
    try:
        record = json.loads((Path(runtime or runtime_directory()) / 'frontend-navigation.json').read_text(encoding='utf8'))
        if (record.get('pid') == os.getpid() and record.get('category') == '模型'
                and isinstance(record.get('id'), str) and record['id'] != previous_id):
            return dict(id=record['id'], category='模型')
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return None


def write_record(path, values):
    path = Path(path)
    content = json.dumps(values, ensure_ascii=False, indent=2)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'{path.name}.{os.getpid()}.{uuid4().hex}.tmp')
    created = False
    try:
        # One exclusive create, no tempfile retry loop on Windows ACL denial.
        # A unique suffix also avoids stale PID files and concurrent writers.
        try:
            with temporary.open('x', encoding='utf8') as handle:
                created = True
                handle.write(content)
            os.replace(temporary, path)
        except OSError:
            # An existing target may be writable even when creating a sibling
            # or replacing the target is denied. Try it once, then propagate.
            path.write_text(content, encoding='utf8')
    finally:
        if created:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Cleanup must not replace the actual write error.


def safe_launch_error(error, config_path=None):
    # This must also work when the chosen Python cannot import application deps.
    detail = str(error)
    path = Path(config_path or os.getenv('PRAAT_AI_CONFIG_PATH') or
                Path(__file__).resolve().parents[1] / 'ai_config.json')
    try:
        raw = json.loads(path.read_text(encoding='utf8'))
        for section in ('api', 'qwen'):
            secret = raw.get(section, {}).get('api_key')
            if isinstance(secret, str) and secret and secret != 'EMPTY':
                detail = detail.replace(secret, '[REDACTED]')
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    # Reuse the regular request-error sanitizer when available, but never depend on it.
    try:
        from .model_capabilities import safe_error
        detail = safe_error(RuntimeError(detail))
    except Exception:
        import re
        detail = re.sub(r'(?i)(bearer\s+|api[_-]?key[\s:=]+)\S+', r'\1[REDACTED]', detail)
    return detail[:1600]


def report_failure(error, *, runtime=None, config_path=None):
    runtime = Path(runtime or runtime_directory())
    detail = safe_launch_error(error, config_path)
    message = (f'新前端未启动：{detail}\n\nPython：{sys.executable}\n'
               '请在 Praat 的路径配置中选择已安装 pywebview/pythonnet 的 Python。'
               '\n不会自动改用旧界面或发送模型请求。')
    try:
        write_record(runtime / 'frontend-startup-error.json',
                     dict(pid=os.getpid(), python=sys.executable, error=detail))
    except OSError:
        pass
    if sys.stderr is not None:
        print(message, file=sys.stderr)
    if os.name == 'nt':
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'AIPraat 新前端启动失败', 0x10)
    return 1


def check_desktop_dependencies(assets=None):
    missing = [name for name in ('webview', 'clr', 'pydantic_ai', 'pydantic_graph')
               if importlib.util.find_spec(name) is None]
    if missing:
        raise RuntimeError('当前 Python 缺少依赖：' + ', '.join(missing))
    assets = Path(assets or Path(__file__).resolve().parents[1] / 'frontend' / 'dist')
    if not (assets / 'index.html').is_file():
        raise RuntimeError('缺少现代前端离线资产 ai/frontend/dist/index.html')


def menu_control_main(arguments=None):
    """Compat with the shipped native menu's start -> status -> detached launch.

    Only its env-command/quiet handshake changes. Explicit service CLI and
    other legacy control operations retain their original behavior.
    Startup preparation and the connected desktop are separate phases.
    """
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if (not arguments and os.getenv('PRAAT_AI_CONTROL_COMMAND') == 'start'
            and os.getenv('PRAAT_AI_CONTROL_QUIET') == '1'):
        runtime = runtime_directory()
        try:
            check_desktop_dependencies()
            from .frontend_status import collect_menu_status
            summary = collect_menu_status(runtime=runtime)
            write_record(runtime / 'status.json', dict(
                summary,
                frontend_start_phase='ready-to-launch',
                frontend_model_source='desktop', model_service_started=False, error=''))
            return 0
        except Exception as error:
            try:
                write_record(runtime / 'status.json', dict(success=False, frontend_running=False,
                    frontend_status='desktop launch failed', error=safe_launch_error(error)))
            except OSError:
                pass  # A failed status write cannot depend on another status write.
            # The native Python callback displays stderr; avoid a second modal dialog here.
            if sys.stderr is not None:
                print('新前端准备失败：' + safe_launch_error(error), file=sys.stderr)
            return 1
    from .control import main
    result = main(arguments)
    if (result == 0 and not arguments and os.getenv('PRAAT_AI_CONTROL_COMMAND') == 'stop'
            and os.getenv('PRAAT_AI_CONTROL_QUIET') == '1'):
        close_desktop()
    return result


def close_desktop():
    """Ask the recorded desktop to close; never target any Praat editor."""
    if os.name != 'nt':
        return
    try:
        record = json.loads((runtime_directory() / 'chat-process.json').read_text(encoding='utf8'))
        pid = int(record['pid'])
        from .process import identities_match, process_identity
        if not identities_match(record.get('identity') or {}, process_identity(pid)):
            return
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.WinDLL('user32', use_last_error=True)
        user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
        callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        @callback
        def close(hwnd, _):
            owner = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value == pid:
                user32.PostMessageW(hwnd, 0x10, 0, 0) # WM_CLOSE: normal window cleanup
            return True
        user32.EnumWindows(close, 0)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return


def praat_snapshot():
    """Read the native menu's snapshot, without dispatching any Praat script."""
    try:
        pid = int(os.getenv('PRAAT_AI_PRAAT_PID', '0'))
        if pid <= 0:
            return None
        from .process import process_identity
        identity = process_identity(pid)
        executable = os.getenv('PRAAT_AI_PRAAT_EXECUTABLE', '')
        if not identity or not executable or os.path.normcase(os.path.realpath(executable)) != os.path.normcase(os.path.realpath(identity['executable'])):
            return None
        text = (runtime_directory() / 'chat_context.tsv').read_text(encoding='utf8')
        if f'# praat-pid={pid}' not in text.splitlines():
            return None
        objects = []
        for line in text.splitlines()[1:]:
            if not line or line.startswith('#'):
                continue
            fields = line.split('\t')
            if len(fields) >= 4:
                objects.append(dict(id=int(fields[0]), className=fields[1], name=fields[2],
                                    selected=fields[3] == '1'))
        return dict(pid=pid, objects=objects)
    except (OSError, ValueError, KeyError):
        return None


def connected_record(executor='ModernExecutor'):
    from .process import process_identity
    snapshot = praat_snapshot()
    write_record(runtime_directory() / 'frontend-ready.json', dict(
        pid=os.getpid(), identity=process_identity(os.getpid()), python=sys.executable,
        phase='react-connected', executor=executor,
        praatPid=snapshot['pid'] if snapshot else None))


def clear_own_records():
    for name in ('chat.pid', 'chat-process.json', 'frontend-ready.json', 'frontend-navigation.json'):
        path = runtime_directory() / name
        try:
            raw = path.read_text(encoding='utf8')
            pid = int(raw) if name == 'chat.pid' else json.loads(raw).get('pid')
            if pid == os.getpid():
                path.unlink(missing_ok=True)
        except (OSError, ValueError, AttributeError):
            pass
