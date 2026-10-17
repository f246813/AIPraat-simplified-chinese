import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import start_ai_chat
import start_api_settings
from praat_ai import api_settings, desktop_launch as launch, process
from praat_ai.modern_app import ModernApplication


class ModelSettingsNavigationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_first_launch_targets_new_desktop_without_model_or_settings_calls(self):
        def spawn(directory, path): path.write_text('123'); return 0
        with patch.object(launch, 'check_desktop_dependencies'), \
             patch.object(start_ai_chat, 'start_new_chat_window', side_effect=spawn):
            self.assertEqual(start_ai_chat.main(self.root, model_settings=True), 0)
        record = json.loads((self.root/'frontend-navigation.json').read_text(encoding='utf8'))
        self.assertEqual((record['pid'], record['category']), (123, '模型'))

    def test_open_or_initializing_desktop_is_reused_and_each_click_is_new(self):
        identity = dict(executable='python.exe', started='same')
        (self.root/'chat.pid').write_text('123')
        (self.root/'chat-process.json').write_text(json.dumps(dict(pid=123, identity=identity)))
        previous = ''
        for focused in (True, False, True):
            with patch.object(launch, 'check_desktop_dependencies'), \
                 patch.object(start_ai_chat, 'process_alive', return_value=True), \
                 patch.object(start_ai_chat, 'focus_existing_window', return_value=focused), \
                 patch.object(process, 'process_identity', return_value=identity), \
                 patch.object(start_ai_chat, 'start_new_chat_window') as spawn:
                self.assertEqual(start_ai_chat.main(self.root, model_settings=True), 0)
            spawn.assert_not_called()
            record = json.loads((self.root/'frontend-navigation.json').read_text(encoding='utf8'))
            self.assertEqual(record['pid'], 123)
            self.assertNotEqual(record['id'], previous); previous = record['id']

    def test_navigation_delivered_once_and_does_not_touch_chat_events(self):
        app = object.__new__(ModernApplication)
        app.guard = threading.RLock(); app.events = []; app.cursor = 0; app.navigation_id = ''
        with patch.object(launch, 'runtime_directory', return_value=self.root):
            launch.request_model_settings(self.root, os.getpid())
            first = app.poll(0)
            self.assertEqual(first['navigation']['category'], '模型')
            self.assertEqual(first['events'], [])
            self.assertNotIn('navigation', app.poll(0))
            launch.request_model_settings(self.root, os.getpid())
            self.assertNotEqual(app.poll(0)['navigation']['id'], first['navigation']['id'])

    def test_venv_shim_reuse_focuses_verified_desktop_pid(self):
        identity = dict(executable='python.exe', started='desktop')
        (self.root/'chat.pid').write_text('123')  # venv launcher shim
        (self.root/'frontend-ready.json').write_text(json.dumps(dict(pid=456, identity=identity)))
        with patch.object(launch, 'check_desktop_dependencies'), \
             patch.object(process, 'process_identity', return_value=identity), \
             patch.object(start_ai_chat, 'focus_existing_window', return_value=True) as focus, \
             patch.object(start_ai_chat, 'start_new_chat_window') as spawn:
            self.assertEqual(start_ai_chat.main(self.root, model_settings=True), 0)
        focus.assert_called_once_with(456)
        spawn.assert_not_called()
        record = json.loads((self.root/'frontend-navigation.json').read_text(encoding='utf8'))
        self.assertEqual(record['pid'], 456)

    def test_stale_ready_record_does_not_target_reused_pid(self):
        identity = dict(executable='python.exe', started='old')
        (self.root/'frontend-ready.json').write_text(json.dumps(dict(pid=456, identity=identity)))
        def spawn(directory, path): path.write_text('789'); return 0
        with patch.object(launch, 'check_desktop_dependencies'), \
             patch.object(process, 'process_identity', return_value=dict(executable='python.exe', started='new')), \
             patch.object(start_ai_chat, 'focus_existing_window') as focus, \
             patch.object(start_ai_chat, 'start_new_chat_window', side_effect=spawn):
            self.assertEqual(start_ai_chat.main(self.root), 0)
        focus.assert_not_called()

    def test_other_pid_and_bad_record_are_ignored(self):
        launch.request_model_settings(self.root, os.getpid()+1)
        self.assertIsNone(launch.model_settings_request(runtime=self.root))
        (self.root/'frontend-navigation.json').write_text('{invalid')
        self.assertIsNone(launch.model_settings_request(runtime=self.root))

    def test_old_entries_redirect_and_preserve_config_environment(self):
        with patch.dict(os.environ, PRAAT_AI_CONFIG_PATH='original'), \
             patch.object(start_ai_chat, 'main', return_value=0) as start:
            self.assertEqual(api_settings.run_standalone(self.root/'config.json'), 0)
            start.assert_called_once_with(model_settings=True)
            self.assertEqual(os.environ['PRAAT_AI_CONFIG_PATH'], 'original')


if __name__ == '__main__': unittest.main()
