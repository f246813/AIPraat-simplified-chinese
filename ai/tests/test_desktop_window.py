import unittest
from unittest.mock import Mock, call

from praat_ai.desktop_window import _activate


class DesktopWindowTests(unittest.TestCase):
    def test_focus_attaches_foreground_thread_and_always_detaches(self):
        user32, kernel32 = Mock(), Mock()
        user32.IsIconic.return_value = True
        user32.GetForegroundWindow.return_value = 20
        user32.GetWindowThreadProcessId.return_value = 200
        kernel32.GetCurrentThreadId.return_value = 100
        user32.AttachThreadInput.return_value = True
        _activate(user32, kernel32, 10)
        user32.ShowWindow.assert_called_once_with(10, 9)
        user32.GetWindowThreadProcessId.assert_called_once_with(20, None)
        self.assertEqual(user32.AttachThreadInput.call_args_list,
                         [call(100, 200, True), call(100, 200, False)])
        user32.SetForegroundWindow.assert_called_once_with(10)

        user32.reset_mock()
        user32.SetForegroundWindow.side_effect = OSError('activation failed')
        with self.assertRaises(OSError):
            _activate(user32, kernel32, 10)
        user32.AttachThreadInput.assert_has_calls([call(100, 200, True), call(100, 200, False)])

    def test_visible_window_keeps_geometry_and_same_thread_is_not_attached(self):
        user32, kernel32 = Mock(), Mock()
        user32.IsIconic.return_value = False
        user32.GetForegroundWindow.return_value = 10
        user32.GetWindowThreadProcessId.return_value = 100
        kernel32.GetCurrentThreadId.return_value = 100
        _activate(user32, kernel32, 10)
        user32.ShowWindow.assert_not_called()
        user32.AttachThreadInput.assert_not_called()


if __name__ == '__main__':
    unittest.main()
