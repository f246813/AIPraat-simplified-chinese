"""Windows IME regression: inspect the actual native font in an isolated UI process.

Each process owns its keyboard layout and Tk interpreter; no user's window is touched.
"""
import os
import subprocess
import sys
import unittest
from pathlib import Path


PROBE = r'''
import ctypes as c
import time
from ctypes import wintypes as w
import tkinter as tk
from tkinter import ttk
from praat_ai.chat import ChatWindow

class LOGFONT(c.Structure):
    _fields_ = [('height', w.LONG), ('width', w.LONG), ('escapement', w.LONG),
                ('orientation', w.LONG), ('weight', w.LONG), ('italic', w.BYTE),
                ('underline', w.BYTE), ('strike', w.BYTE), ('charset', w.BYTE),
                ('out', w.BYTE), ('clip', w.BYTE), ('quality', w.BYTE),
                ('pitch', w.BYTE), ('face', w.WCHAR * 32)]
user = c.WinDLL('user32')
imm = c.WinDLL('imm32')
user.GetThreadDpiAwarenessContext.restype = w.HANDLE
user.GetAwarenessFromDpiAwarenessContext.argtypes = [w.HANDLE]
user.GetKeyboardLayoutList.argtypes = [c.c_int, c.POINTER(w.HANDLE)]
user.ActivateKeyboardLayout.argtypes = [w.HANDLE, w.UINT]
user.ActivateKeyboardLayout.restype = w.HANDLE
imm.ImmGetContext.argtypes = [w.HWND]
imm.ImmGetContext.restype = w.HANDLE
imm.ImmReleaseContext.argtypes = [w.HWND, w.HANDLE]
imm.ImmGetCompositionFontW.argtypes = [w.HANDLE, c.POINTER(LOGFONT)]
window = ChatWindow()
window.root.withdraw()
try:
    if MODE == 'geometry':
        window.root.update_idletasks()
        scale = max(1, min(2, window.root.winfo_fpixels('1i') / 96))
        assert window.root.winfo_width() == round(900 * scale), f'Window width {window.root.winfo_width()} at {scale:g} scale'
        assert window.root.winfo_height() == round(680 * scale), f'Window height {window.root.winfo_height()} at {scale:g} scale'
    elif MODE == 'dpi':
        awareness = user.GetAwarenessFromDpiAwarenessContext(user.GetThreadDpiAwarenessContext())
        assert awareness in (1, 2), f'Frontend is DPI unaware: {awareness}'
    else:
        layouts = (w.HANDLE * 32)()
        count = user.GetKeyboardLayoutList(32, layouts)
        matching = [layouts[i] for i in range(count) if layouts[i] & 0xffff == LANGUAGE]
        if not matching:
            print(f'Keyboard layout unavailable: {LANGUAGE:x}')
            raise SystemExit(77)
        previous = user.ActivateKeyboardLayout(matching[0], 0)
        window.root.update()  # Process WM_INPUTLANGCHANGE before inspecting the new HIMC.
        try:
            dialog = tk.Toplevel(window.root)
            dialog.attributes('-alpha', 0)
            dialog.geometry('300x200')
            fields = [window.entry, ttk.Entry(dialog, font=('Microsoft YaHei UI', 10)),
                      tk.Entry(dialog, font=('Microsoft YaHei UI', 10)),
                      ttk.Combobox(dialog, font=('Microsoft YaHei UI', 10))]
            # Tk can discard FocusIn events for never-mapped children. Map an
            # invisible test window so the real class bindings are exercised.
            window.root.attributes('-alpha', 0)
            for field in fields[1:]:
                field.pack()
            window.root.deiconify()
            window.root.update()
            for dpi in (96, 120, 144, 192):
                window.root.tk.call('tk', 'scaling', dpi / 72)
                for field in fields:
                    for size, weight in ((10, 'normal'), (12, 'bold'), (-18, 'normal')):
                        field.configure(font=('Microsoft YaHei UI', size, weight))
                        window.root.update_idletasks()
                        expected_pixels = abs(size) if size < 0 else round(size * float(window.root.tk.call('tk', 'scaling')))
                        # IME 的组合字体是共享状态：别的窗口（负载下同时跑的 UI 测试）可能把它抢回去，
                        # 读回来的高度就会变成 IME 自己的默认值。让 app 重新施加一次（<FocusIn> 正是它
                        # 的真实入口），在有限次数内等它稳定；一直不对仍按失败报，不放过真实回归。
                        native = LOGFONT()
                        for _attempt in range(10):
                            field.focus_set()
                            field.event_generate('<FocusIn>')
                            window.root.update()
                            context = imm.ImmGetContext(field.winfo_id())
                            assert context, 'Missing IME input context'
                            try:
                                assert imm.ImmGetCompositionFontW(context, c.byref(native))
                            finally:
                                imm.ImmReleaseContext(field.winfo_id(), context)
                            if native.height == -expected_pixels and native.face == 'Microsoft YaHei UI':
                                break
                            time.sleep(0.02)
                        assert native.face == 'Microsoft YaHei UI', f'{field.winfo_class()} DPI {dpi} size {size} IME family: {native.face}'
                        assert native.height == -expected_pixels, f'{field.winfo_class()} DPI {dpi}: IME height {native.height}, expected {-expected_pixels}'
                        assert native.weight == (700 if weight == 'bold' else 400), f'IME weight: {native.weight}'
                        assert native.charset == 1, f'IME forces one language charset: {native.charset}'
                        assert native.quality == 5, f'IME font quality: {native.quality}'
        finally:
            user.ActivateKeyboardLayout(previous, 0)
finally:
    window.theme_watcher.stop()
    window.root.destroy()
print('PASS', MODE, LANGUAGE)
'''


@unittest.skipUnless(os.name == 'nt', 'Windows input method integration')
class NativeImeTests(unittest.TestCase):
    def run_probe(self, mode, language=0x804):
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))
        code = f'MODE={mode!r}\nLANGUAGE={language}\n' + PROBE
        result = subprocess.run([sys.executable, '-c', code], env=env,
                                capture_output=True, text=True, timeout=45)
        if result.returncode == 77:
            self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_frontend_renders_without_windows_bitmap_scaling(self):
        self.run_probe('dpi')

    def test_initial_window_fits_the_dpi_scaled_controls(self):
        self.run_probe('geometry')

    def test_chinese_composition_matches_each_input_at_all_scales(self):
        self.run_probe('font', 0x804)

    def test_japanese_composition_matches_each_input_at_all_scales(self):
        self.run_probe('font', 0x411)


if __name__ == '__main__':
    unittest.main()
