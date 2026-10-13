"""Anki audio -> this Praat installation. No model service is required."""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile, ZIP_DEFLATED

from . import sendpraat

AI = Path(__file__).resolve().parents[1]
CONFIG = AI / 'anki_config.json'
DEFAULTS = dict(media_dir='', ffmpeg='', fallback=True, audio_field='',
                start_field='', end_field='', mode='button', magnifier_selector='.Search, .search a, .dict-link, #dict-link')


def settings():
    return {**DEFAULTS, **(json.loads(CONFIG.read_text(encoding='utf-8')) if CONFIG.is_file() else {})}


def save_settings(values):
    value = {**settings(), **{k: v for k, v in values.items() if k in DEFAULTS}}
    if value['mode'] not in ('button', 'magnifier', 'both'):
        raise ValueError('入口应为新按钮、放大镜或两者')
    CONFIG.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    return value


def command_lock():
    return sendpraat.command_lock()


def parse_request(url):
    parts = urlsplit(url.strip().strip('\u2066\u2067\u2068\u2069'))
    if parts.scheme.lower() != 'aipraat' or parts.hostname not in ('open', 'lookup'):
        raise ValueError('需要 aipraat://open 链接')
    values = parse_qs(parts.query)
    return {key: values[key][0] for key in ('audio', 'media_dir', 'start', 'end', 'word') if key in values}


def resolve_audio(audio, media_dir=''):
    if not audio:
        raise ValueError('链接只含单词，未指定卡片音频；请使用 Anki 插件的放大镜入口。')
    path = Path(audio)
    if not path.is_absolute():
        directory = media_dir or settings()['media_dir']
        if not directory:
            raise ValueError('请先在 Anki 连接中选择 collection.media 文件夹。')
        base = Path(directory).resolve()
        path = (base / path).resolve()
        if not path.is_relative_to(base):
            raise ValueError('音频文件必须位于指定媒体目录内。')
    path = path.resolve()
    if not path.is_file():
        raise FileNotFoundError(f'找不到卡片音频：{path}')
    return path


def time_range(start=0, end=0):
    start, end = float(start or 0), float(end or 0)
    if not all(math.isfinite(t) for t in (start, end)) or start < 0 or end < 0 or (end and end <= start):
        raise ValueError('时间范围必须为非负秒数，结束应大于开始。')
    return start, end


def decode_wav(source, target, executable=''):
    from .audio_decoder import resolve_ffmpeg
    ffmpeg = executable or resolve_ffmpeg()
    if not ffmpeg:
        raise ValueError('未找到 FFmpeg。请在路径配置中检测或一键安装 FFmpeg。')
    result = subprocess.run([str(ffmpeg), '-nostdin', '-v', 'error', '-i', str(source),
                             '-map', '0:a:0', '-vn', '-c:a', 'pcm_s24le', '-y', str(target)],
                            capture_output=True, timeout=120,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('备用音频解码失败：' + result.stderr.decode('utf-8', errors='replace').strip())
    if not target.is_file():
        raise ValueError('备用解码没有生成 WAV。')
    return target


def _target(executable):
    windows = sendpraat.praat_windows()
    if os.name != 'nt':
        raise ValueError('当前 Anki 连接的窗口定位支持 Windows。')
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    matches = []
    for pid in {w.process_id for w in windows}:
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            continue
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            size = wintypes.DWORD(len(buffer))
            if kernel.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)) and os.path.normcase(buffer.value) == os.path.normcase(str(executable)):
                matches.append(pid)
        finally:
            kernel.CloseHandle(handle)
    if matches:
        return max(matches)
    if windows:
        raise ValueError('正在运行的是另一份 Praat，请先关闭它，再打开当前 AIPraat。')
    process = subprocess.Popen([str(executable)], cwd=executable.parent,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    until = time.monotonic() + 20
    while time.monotonic() < until:
        if sendpraat.choose_window(sendpraat.praat_windows(), process.pid):
            return process.pid
        if process.poll() is not None:
            raise ValueError('Praat 启动失败。')
        time.sleep(.1)
    raise TimeoutError('等待 Praat 窗口超时。')


def _open_native(audio, original, start, end, pid, directory):
    result = directory / 'result.txt'
    script = directory / 'anki_open.praat'
    quote = lambda value: '"' + str(value).replace('\\', '/').replace('"', '""').replace('\n', '').replace('\r', '') + '"'
    script.write_text(f'Open Anki audio: {quote(audio)}, {start:.9f}, {end:.9f}, {quote(result)}, {quote(original)}\n', encoding='utf-8')
    result.unlink(missing_ok=True)
    sent, note = sendpraat.deliver(AI, script, process_id=pid)
    if not sent:
        raise ValueError(note)
    until = time.monotonic() + 45
    while time.monotonic() < until:
        if result.is_file():
            status = result.read_text(encoding='utf-8-sig')
            if status.startswith('ok\n'):
                return ''
            if status.startswith('error\n'):
                return status[6:].strip()
        time.sleep(.05)
    sendpraat.cancel_pending(AI)
    raise TimeoutError('Praat 未确认打开结果；未重复投递或转换音频，请检查是否有对话框挡住窗口。')


def open_audio(audio, media_dir='', start=0, end=0, **_):
    source = resolve_audio(audio, media_dir)
    start, end = time_range(start, end)
    executable = AI.parent / 'Praat.exe'
    if not executable.is_file():
        raise FileNotFoundError('当前安装目录缺少 Praat.exe。')
    with command_lock():
        pid = _target(executable)
        with tempfile.TemporaryDirectory(prefix='aipraat-anki-') as temporary:
            directory = Path(temporary)
            error = _open_native(source, source, start, end, pid, directory)
            if error:
                raise ValueError(error)
            # The shared native reader owns the retry and temporary WAV lifetime.
            result_file = directory / 'result.txt'
            converted = result_file.is_file() and 'converted' in result_file.read_text(encoding='utf-8-sig').splitlines()[1:]
    return dict(ok=True, converted=converted, audio=str(source))


def export_addon(target, portable=False):
    config = settings()
    if portable:
        config.update(bridge_script='', python_executable='')
    else:
        config.update(bridge_script=str(AI / 'run_anki_bridge.py'), python_executable=sys.executable)
    assets = AI / 'anki_addon'
    with ZipFile(target, 'w', ZIP_DEFLATED) as archive:
        archive.write(assets / '__init__.py', '__init__.py')
        archive.writestr('entry.js', entry_code())
        archive.writestr('config.json', json.dumps(config, ensure_ascii=False, indent=2))
        archive.writestr('manifest.json', json.dumps(dict(package='aipraat_audio_bridge', name='AIPraat · 查看语图')))
        archive.write(assets / 'README.md', 'README.md')
    return str(target)


def entry_code():
    text = (AI / 'anki_addon' / 'entry.js').read_text(encoding='utf-8')
    cfg = settings()
    return text.replace('const AIPRAAT_ENTRY = "button";', 'const AIPRAAT_ENTRY = ' + json.dumps(cfg['mode']) + ';').replace(
        'const AIPRAAT_MAGNIFIER = ".Search, .search a, .dict-link, #dict-link";',
        'const AIPRAAT_MAGNIFIER = ' + json.dumps(cfg['magnifier_selector'], ensure_ascii=False) + ';')


def main(argv=None):
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser()
    parser.add_argument('--request')
    parser.add_argument('--url')
    args = parser.parse_args(argv)
    try:
        request = json.loads(args.request) if args.request else parse_request(args.url or '')
        print(json.dumps(open_audio(**request), ensure_ascii=False))
        return 0
    except Exception as error:
        print(json.dumps(dict(ok=False, error=str(error)), ensure_ascii=False))
        return 1
