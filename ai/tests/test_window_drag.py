"""The in-page conversation header hands dragging over to the native window move loop."""
import ctypes
import os
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from praat_ai.modern_app import ModernApplication


class WindowDragTests(unittest.TestCase):
    def app(self):
        application = ModernApplication.__new__(ModernApplication)
        application.window = None
        return application

    def test_missing_native_window_is_reported_without_touching_windows(self):
        self.assertEqual(self.app().begin_window_drag(), {'ok': False, 'reason': 'no-native-window'})

    @unittest.skipIf(os.name != 'nt', 'caption messages only exist on Windows')
    def test_native_window_posts_the_caption_move_message(self):
        application = self.app()
        pending = []
        application.window = NS(native=NS(Handle=NS(ToInt64=lambda: 0x123456789),
                                          BeginInvoke=lambda callback: pending.append(callback)))
        calls = {}
        def cursor(point):
            position = ctypes.cast(point, ctypes.POINTER(ctypes.wintypes.POINT)).contents
            position.x, position.y = -120, 240
            return True
        def post(hwnd, message, wparam, lparam):
            calls['post'] = (hwnd, message, wparam, lparam)
            return True
        class User32:
            ReleaseCapture = Mock(return_value=True)
            GetCursorPos = Mock(side_effect=cursor)
            PostMessageW = Mock(side_effect=post)
        with patch.object(ctypes, 'WinDLL', return_value=User32()), patch.dict('sys.modules', {'System': NS(Action=lambda callback: callback)}):
            self.assertEqual(application.begin_window_drag(), {'ok': True})
            User32.ReleaseCapture.assert_not_called()
            self.assertEqual(len(pending), 1)
            pending[0]()
        User32.ReleaseCapture.assert_called_once()
        self.assertEqual(calls.get('post'), (0x123456789, 0x00A1, 2, (240 << 16) | (-120 & 0xffff)))


if __name__ == '__main__':
    unittest.main()
