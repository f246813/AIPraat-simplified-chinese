"""Open an isolated real WebView2 desktop; check its shown-event activation.

No shared chat records, settings, conversations or model requests are changed.
The fixture closes its own window after inspecting the built frontend.
"""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import tempfile
import time
from ctypes import wintypes

import webview
from praat_ai.modern_app import ModernApplication
from praat_ai.modern_host import create_window


def main():
    user32 = ctypes.WinDLL('user32')
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    def foreground():
        hwnd = user32.GetForegroundWindow()
        pid = wintypes.DWORD()
        title = ctypes.create_unicode_buffer(256)
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        user32.GetWindowTextW(hwnd, title, len(title))
        return dict(pid=pid.value, title=title.value)

    result = dict(before=foreground(), desktop_pid=os.getpid())
    with tempfile.TemporaryDirectory(prefix='praat-desktop-focus-') as directory:
        root = Path(directory)
        application = ModernApplication(root / 'data', root / 'config.json')
        window, server = create_window(application)
        window.events.shown += lambda: result.update(shown=foreground())

        def verify():
            try:
                window.events.loaded.wait(15)
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    header = window.evaluate_js("Boolean(document.querySelector('.chat-header'))")
                    if header:
                        break
                    time.sleep(.1)
                result['loaded'] = foreground()
                result['header_visible'] = bool(header)
                result['model_header_button'] = window.evaluate_js(
                    "Boolean(document.querySelector('.chat-header .model-button'))")
                result['gear_visible'] = window.evaluate_js(
                    "Boolean(document.querySelector('.sidebar-bottom .settings-entry'))")
            except Exception as error:
                result['error'] = str(error)
            finally:
                window.destroy()

        try:
            webview.start(verify, gui='edgechromium', private_mode=True,
                          storage_path=str(root / 'profile'))
        finally:
            application.close()
            server.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    assert result['shown']['pid'] == result['desktop_pid'], result
    assert result['loaded']['pid'] == result['desktop_pid'], result
    assert result['header_visible'] and result['gear_visible'], result
    assert result['model_header_button'] is False, result


if __name__ == '__main__':
    main()
