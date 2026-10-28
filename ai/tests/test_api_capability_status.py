"""Observable capability/diagnostic behavior; only external API responses are mocked."""
import json
import tempfile
import tkinter as tk
import unittest
from pathlib import Path
from unittest.mock import patch

from praat_ai import api_settings, audio_probe, ui_windows
from praat_ai.config import load_config

OFFICIAL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'
MODEL = 'qwen3.7-flash'
URL_ERROR = 'status_code: 400, model_name: qwen3.7-flash, body: The provided URL does not appear to be valid. Ensure it is correctly formatted.'


class InlineThread:
    def __init__(self, target, **kwargs): self.target = target
    def start(self): self.target()


class ProbeDiagnosticTests(unittest.TestCase):
    def test_real_audio_probe_retains_code_without_claiming_unsupported(self):
        from pydantic_ai.exceptions import ModelHTTPError
        from pydantic_ai.models.function import FunctionModel
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'
            path.write_text(json.dumps({'api':{'enabled':True,'base_url':OFFICIAL,'model':MODEL}}))
            def reject(messages, info):
                raise ModelHTTPError(400, MODEL, body={'message':'The provided URL does not appear to be valid'})
            result = audio_probe.probe_audio(load_config(path), Path(directory)/'tasks', model=FunctionModel(reject))
            self.assertEqual(result['status'], 'unverified')
            self.assertEqual(result.get('status_code'), 400)


class CapabilityDialogTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)/'config.json'
        self.path.write_text(json.dumps({'api':{'enabled':True,'base_url':OFFICIAL,'model':MODEL,'api_key':'fixture-secret'}}),encoding='utf-8')
        # Match the frontend's DPI bootstrap before the first Tk HWND.
        self.root = ui_windows.create_root(); self.root.withdraw()
        self.dialog = api_settings.ApiSettingsDialog(self.root, config_path=self.path)
        self.dialog.window.withdraw()
        self.addCleanup(self.close)

    def close(self):
        if self.dialog.window.winfo_exists(): self.dialog.close()
        for callback in self.root.tk.splitlist(self.root.tk.call('after', 'info')):
            self.root.after_cancel(callback)
        self.root.update_idletasks(); self.root.destroy()

    def probe(self, result, edit=None):
        queued = []
        with patch('praat_ai.audio_probe.probe_audio', return_value=result), \
             patch.object(api_settings.threading, 'Thread', InlineThread), \
             patch.object(self.dialog.window, 'after', side_effect=lambda delay, callback: queued.append(callback)):
            self.dialog.test_audio()
            if edit: edit()
        for callback in queued: callback()

    def test_official_preset_displays_support_and_unverified_state(self):
        hint = self.dialog.audio_hint.get()
        self.assertIn('不支持直接音频输入', hint)
        self.assertIn('官方文档', hint)
        self.assertIn('尚未验证', hint)

    def test_unknown_gateway_does_not_claim_official_support(self):
        self.dialog.base_url.set('https://fixture-gateway.invalid/v1')
        hint = self.dialog.audio_hint.get()
        self.assertIn('按名称推定', hint)
        self.assertIn('网关未验证', hint)
        self.assertNotIn('官方文档', hint)

    def test_400_error_code_and_model_precede_preset_and_test_state(self):
        self.probe({'status':'unverified','status_code':400,'reason':URL_ERROR})
        hint = self.dialog.audio_hint.get()
        self.assertTrue(hint.startswith('status code: 400 · model: qwen3.7-flash'),hint)
        self.assertLess(hint.index('model:'),hint.index('模型预设'))
        self.assertLess(hint.index('模型预设'),hint.index('未能验证'))
        self.assertIn('URL', hint)
        self.assertNotIn('body:',self.dialog.status.get())
        self.assertEqual(self.dialog.details_button.winfo_manager(),'grid')

    def test_unverified_preserves_manual_enabled_setting(self):
        self.dialog.audio_enabled.set(True); self.dialog._on_audio_manual()
        self.probe({'status':'unverified','status_code':400,'reason':URL_ERROR})
        self.assertTrue(self.dialog.audio_enabled.get())
        self.assertEqual(self.dialog.audio_source,'manual')
        self.assertIn('未能验证',self.dialog.audio_hint.get())
        self.assertIn('已启用',self.dialog.audio_hint.get())

    def test_verified_reports_basic_validation_and_enables_audio(self):
        self.probe({'status':'verified','reason':'基础音频特征校验通过'})
        self.assertTrue(self.dialog.audio_enabled.get())
        self.assertIn('基础音频验证通过',self.dialog.audio_hint.get())
        self.assertTrue(self.dialog.collect()['audio_verified_at'])

    def test_explicit_rejection_displays_unsupported_and_disables_audio(self):
        self.dialog.audio_enabled.set(True); self.dialog._on_audio_manual()
        self.probe({'status':'unsupported','status_code':400,'reason':'This model does not support audio input'})
        self.assertFalse(self.dialog.audio_enabled.get())
        self.assertIn('接口明确不支持音频输入',self.dialog.audio_hint.get())

    def test_timeout_has_explicit_unverified_status_without_fake_http_code(self):
        self.probe({'status':'unverified','status_code':None,'reason':'request timed out'})
        hint=self.dialog.audio_hint.get()
        self.assertIn('未能验证',hint); self.assertIn('超时',hint)
        self.assertNotIn('400',hint)

    def test_cancelled_probe_preserves_setting(self):
        self.dialog.audio_enabled.set(True); self.dialog._on_audio_manual()
        self.probe({'status':'cancelled','reason':'音频验证已取消'})
        self.assertTrue(self.dialog.audio_enabled.get())
        self.assertIn('已取消',self.dialog.audio_hint.get())

    def test_changed_key_invalidates_previous_success_and_blocks_inflight_result(self):
        self.probe({'status':'verified','reason':'基础音频特征校验通过'})
        self.dialog.api_key.set('different-account')
        self.assertIn('尚未验证',self.dialog.audio_hint.get())
        self.assertFalse(self.dialog.collect()['audio_verified_at'])
        self.probe({'status':'verified','reason':'obsolete result'},lambda:self.dialog.api_key.set('third-account'))
        self.assertIn('尚未验证',self.dialog.audio_hint.get())
        self.assertNotIn('obsolete result',self.dialog.audio_hint.get())

    def test_model_change_invalidates_previous_error_and_details(self):
        self.probe({'status':'unverified','status_code':400,'reason':URL_ERROR})
        self.dialog.model.set('new-unknown-model')
        hint=self.dialog.audio_hint.get()
        self.assertNotIn('status code:',hint)
        self.assertIn('未知',hint); self.assertIn('尚未验证',hint)
        self.assertEqual(self.dialog.details_button.winfo_manager(),'')

    def test_details_are_read_only_and_hide_credentials(self):
        self.probe({'status':'unverified','status_code':401,'reason':'HTTP 401 Bearer fixture-secret; fixture-secret rejected'})
        self.dialog.show_diagnostic_details()
        children=[w for w in self.dialog.window.winfo_children() if w.winfo_class()=='Toplevel']
        self.assertEqual(len(children),1)
        def texts(widget):
            return ([widget] if widget.winfo_class()=='Text' else [])+[child for w in widget.winfo_children() for child in texts(w)]
        body=texts(children[0])[0]
        self.assertEqual(body.cget('state'),'disabled')
        self.assertNotIn('fixture-secret',body.get('1.0','end'))
        self.assertIn('401',body.get('1.0','end'))
        children[0].destroy()

    def test_saved_failed_test_reopens_and_new_credentials_invalidate_it(self):
        self.probe({'status':'unverified','status_code':400,'reason':URL_ERROR})
        api_settings.save_settings(self.dialog.collect(),self.path)
        other=api_settings.ApiSettingsDialog(self.root,config_path=self.path); other.window.withdraw()
        try: self.assertIn('status code: 400',other.audio_hint.get())
        finally: other.close()
        api_settings.save_settings({'api_key':'different-account'},self.path)
        other=api_settings.ApiSettingsDialog(self.root,config_path=self.path); other.window.withdraw()
        try:
            self.assertNotIn('status code: 400',other.audio_hint.get())
            self.assertIn('尚未验证',other.audio_hint.get())
        finally: other.close()

    def test_connection_is_text_only_and_does_not_mark_audio_verified(self):
        queued=[]
        with patch.object(api_settings.qwen,'probe_api',return_value=(True,'连接成功：fixture')), \
             patch.object(api_settings.threading,'Thread',InlineThread), \
             patch.object(self.dialog.window,'after',side_effect=lambda delay,callback:queued.append(callback)):
            self.dialog.test_connection()
        for callback in queued: callback()
        self.assertIn('仅文字请求',self.dialog.status.get())
        self.assertIn('尚未验证',self.dialog.audio_hint.get())

    def test_runtime_rejection_replaces_saved_verified_observation(self):
        from praat_ai import model_capabilities as caps
        self.probe({'status':'verified','reason':'基础音频特征校验通过'})
        api_settings.save_settings(self.dialog.collect(),self.path)
        identity=caps.config_identity(load_config(self.path).api)
        self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        other=api_settings.ApiSettingsDialog(self.root,config_path=self.path); other.window.withdraw()
        try:
            self.assertIn('接口明确不支持音频输入',other.audio_hint.get())
            self.assertNotIn('验证通过',other.audio_hint.get())
        finally: other.close()

    def test_saving_failed_probe_cannot_undo_later_runtime_correction(self):
        from praat_ai import model_capabilities as caps
        # Enable in the saved configuration before opening a fresh dialog.
        api_settings.save_settings({'audio_input_enabled':True,'audio_input_source':'manual'},self.path)
        self.dialog.close()
        self.dialog=api_settings.ApiSettingsDialog(self.root,config_path=self.path); self.dialog.window.withdraw()
        self.probe({'status':'unverified','status_code':400,'reason':URL_ERROR})
        identity=caps.config_identity(load_config(self.path).api)
        self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        self.dialog.save()
        self.assertFalse(load_config(self.path).api.audio_input_enabled)
        self.assertEqual(load_config(self.path).api.audio_input_source,'corrected')

    def test_refresh_preserves_new_test_after_historical_runtime_rejection(self):
        from praat_ai import model_capabilities as caps
        identity=caps.config_identity(load_config(self.path).api)
        self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        self.dialog.close()
        self.dialog=api_settings.ApiSettingsDialog(self.root,config_path=self.path); self.dialog.window.withdraw()
        self.probe({'status':'unverified','status_code':400,'reason':URL_ERROR})
        api_settings.save_settings(self.dialog.collect(),self.path)
        self.dialog._audio_dirty=False
        self.dialog._refresh_audio_correction()
        self.assertIn('未能验证',self.dialog.audio_hint.get())
        self.assertIn('URL',self.dialog.audio_hint.get())
        self.assertFalse(self.dialog.audio_enabled.get())

    def test_old_verified_dialog_cannot_undo_newer_runtime_rejection(self):
        from praat_ai import model_capabilities as caps
        self.probe({'status':'verified','reason':'基础音频特征校验通过'})
        identity=caps.config_identity(load_config(self.path).api)
        self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        api_settings.save_settings(self.dialog.collect(),self.path)
        saved=load_config(self.path).api
        self.assertFalse(saved.audio_input_enabled)
        self.assertEqual(saved.audio_input_source,'corrected')
        self.assertFalse(saved.audio_verified_at)

    def test_explicit_manual_change_after_rejection_is_preserved(self):
        from praat_ai import model_capabilities as caps
        self.probe({'status':'verified','reason':'基础音频特征校验通过'})
        identity=caps.config_identity(load_config(self.path).api)
        self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        self.dialog.audio_enabled.set(True); self.dialog._on_audio_manual()
        api_settings.save_settings(self.dialog.collect(),self.path)
        saved=load_config(self.path).api
        self.assertTrue(saved.audio_input_enabled)
        self.assertEqual(saved.audio_input_source,'manual')

    def test_real_save_old_manual_setting_cannot_undo_later_rejection(self):
        from praat_ai import model_capabilities as caps
        self.dialog.audio_enabled.set(True); self.dialog._on_audio_manual()
        identity=caps.config_identity(load_config(self.path).api)
        self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        self.dialog.save()
        self.assertFalse(load_config(self.path).api.audio_input_enabled)

    def test_real_save_manual_setting_after_rejection_is_preserved(self):
        from praat_ai import model_capabilities as caps
        identity=caps.config_identity(load_config(self.path).api)
        self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        self.dialog.audio_enabled.set(True); self.dialog._on_audio_manual()
        self.dialog.save()
        self.assertTrue(load_config(self.path).api.audio_input_enabled)

    def test_queued_old_success_cannot_undo_later_runtime_rejection(self):
        from praat_ai import model_capabilities as caps
        def reject_before_callback():
            identity=caps.config_identity(load_config(self.path).api)
            self.assertTrue(caps.correct_audio_setting(self.path,identity,'status_code: 400; model does not support audio input'))
        self.probe({'status':'verified','reason':'基础音频特征校验通过'},reject_before_callback)
        self.dialog.save()
        self.assertFalse(load_config(self.path).api.audio_input_enabled)
        self.assertEqual(load_config(self.path).api.audio_test_result['status'],'unsupported')



def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()

if __name__=='__main__': unittest.main()
