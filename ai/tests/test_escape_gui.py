import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from praat_ai import chat, api_settings
from praat_ai.config import AppConfig
from praat_ai.escape_policy import AnalysisState


class GuiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.config = AppConfig()
        self.config.api.base_url, self.config.api.model = 'https://custom.invalid/v1', 'test-model'
        self.config.api.enabled = True
        self.config.api.audio_input_enabled = True
        self.path = self.directory / 'config.json'
        self.path.write_text(json.dumps(self.config.to_dict()), encoding='utf-8')
        self.patches = [patch.object(chat, 'load_config', return_value=self.config),
                        patch.object(chat, 'runtime_dir', return_value=self.directory),
                        patch.object(chat, 'config_path', return_value=self.path),
                        patch.object(chat.parent_watch, 'should_watch', return_value=False)]
        for p in self.patches: p.start()
        self.window = chat.ChatWindow()
        self.window.root.withdraw()

    def tearDown(self):
        self.window._turn_worker = None
        self.window.busy = False
        self.window.close()
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def test_continue_click_double_guard_and_visible_prompt(self):
        state = AnalysisState('比较原目标发音', 'original selection')
        state.can_continue = True
        self.window.analysis_state = state
        self.window.show_analysis_choices()
        with patch.object(chat.threading, 'Thread') as worker:
            self.window.continue_analysis('补充解释', state)
            self.window.continue_analysis('补充解释', state)
        self.assertEqual(worker.call_count,1)
        self.assertEqual(self.window.transcript.get('1.0','end').count('继续分析原始任务：比较原目标发音'),1)
        self.assertEqual(self.window.pending_analysis.context_text,'original selection')

    def test_configuration_presets_manual_and_correction_refresh(self):
        dialog = api_settings.ApiSettingsDialog(self.window.root, config=self.config, config_path=self.path)
        try:
            self.assertTrue(dialog.audio_enabled.get())
            dialog.model.set('qwen3.8-omni-flash')
            self.assertTrue(dialog.audio_enabled.get())
            dialog.audio_enabled.set(False)
            dialog._on_audio_manual()
            self.assertFalse(dialog.collect()['audio_input_enabled'])
            self.assertEqual(dialog.collect()['audio_input_source'],'manual')
            self.assertTrue(dialog.audio_test_button.winfo_exists())
        finally:
            dialog.close()

    def test_old_probe_cannot_override_manual_audio_change(self):
        dialog = api_settings.ApiSettingsDialog(self.window.root, config=self.config, config_path=self.path)
        queued = []
        class InlineThread:
            def __init__(self, target, **kwargs): self.target = target
            def start(self): self.target()
        try:
            with patch('praat_ai.audio_probe.probe_audio', return_value={'status':'verified','reason':'tones identified'}), patch.object(api_settings.threading, 'Thread', InlineThread), patch.object(dialog.window, 'after', side_effect=lambda delay, callback: queued.append(callback)):
                dialog.test_audio()
                dialog.audio_enabled.set(False)
                dialog._on_audio_manual()
                queued[-1]()
            self.assertFalse(dialog.audio_enabled.get())
            self.assertEqual(dialog.audio_source, 'manual')
        finally:
            dialog.close()

    def test_choices_offer_settings_and_hide_missing_audio(self):
        from praat_ai.materials import TaskMaterials
        state = AnalysisState('分析原目标', 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n')
        state.can_continue = True
        self.window.analysis_state = state
        material = TaskMaterials(self.directory / 'tasks')
        material.snapshot_attempted = True
        material.snapshot_error = 'original missing'
        self.window.task_materials.append(material)
        self.window.current_materials = material
        self.window.show_analysis_choices()
        transcript = self.window.transcript.get('1.0','end')
        self.assertNotIn('分析原目标音频', transcript)
        self.assertIn('设置或验证音频能力', transcript)

    def test_closing_configuration_cancels_audio_probe(self):
        dialog = api_settings.ApiSettingsDialog(self.window.root, config=self.config, config_path=self.path)
        self.assertTrue(dialog.window.protocol('WM_DELETE_WINDOW'))
        dialog.close()
        self.assertTrue(dialog._audio_probe_cancel.is_set())

    def test_continuation_preflight_does_not_start_impossible_report(self):
        state = AnalysisState('比较原目标发音', 'original selection')
        state.can_continue = True
        self.window.config.api.limit_tokens = True
        self.window.config.api.max_context_tokens = 2048
        self.window.config.api.plan_max_tokens = 1900
        with patch.object(self.window, 'submit') as submit:
            self.window.continue_analysis('补充解释', state)
        submit.assert_not_called()
        self.assertTrue(state.can_continue)

    def test_praat_exit_preserves_cloud_window(self):
        with patch.object(self.window, 'close') as close, patch.object(self.window.root, 'after') as after:
            self.window._on_praat_gone()
        close.assert_not_called()
        after.assert_not_called()
        self.assertTrue(self.window.root.winfo_exists())

    def test_record_view_is_read_only_and_contains_saved_report(self):
        self.window.store.append(self.window.session_id, 'turn', {'report':'原目标阶段报告'})
        self.window.view_records()
        dialogs = [w for w in self.window.root.winfo_children() if w.winfo_class() == 'Toplevel']
        self.assertEqual(len(dialogs),1)
        body = [w for w in dialogs[0].winfo_children() if w.winfo_class() == 'Text'][0]
        self.assertEqual(body.cget('state'),'disabled')
        self.assertIn('原目标阶段报告',body.get('1.0','end'))
        dialogs[0].destroy()


def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()

if __name__ == '__main__':
    unittest.main()
