"""Request classification and manual thinking policy, with private configuration."""
import json
import tempfile
import unittest
from pathlib import Path

from praat_ai import api_settings, cloud_agent, qwen
from praat_ai.config import AppConfig, QwenConfig, load_config

OFFICIAL = 'https://dashscope.aliyuncs.com/compatible-mode/v1'


class DialoguePolicyTests(unittest.TestCase):
    def test_only_unambiguous_duration_queries_use_direct_measurement(self):
        direct=getattr(cloud_agent,'direct_measurement',None)
        self.assertTrue(callable(direct), 'Missing conservative direct measurement dispatch')
        for goal in ('测量当前声音的时长','查询选中声音时长','what is the duration of the selected sound?'):
            with self.subTest(goal=goal): self.assertEqual(direct(goal), {'tool':'duration','arguments':{}})
        for goal in ('什么是时长','测量基频和时长','测量对象3的时长','截取声音前一秒','你好，删除对象'):
            with self.subTest(goal=goal): self.assertIsNone(direct(goal))

    def classify(self, text):
        classify = getattr(cloud_agent, 'dialogue_kind', None)
        self.assertTrue(callable(classify), 'Missing conservative ordinary-dialogue route')
        return classify(text)

    def test_capability_questions_do_not_need_a_greeting_prefix(self):
        for text in ('Hello,what can u do for me?', 'what can u do for me?', '你能帮我做什么', '你有哪些功能？'):
            with self.subTest(text=text): self.assertEqual(self.classify(text), 'conversation')

    def test_greetings_identity_and_ordinary_chat(self):
        for text in ('你好', 'Hi!', 'thanks', 'Who are you?', '你是什么模型', 'How are you?', '讲个笑话'):
            with self.subTest(text=text): self.assertEqual(self.classify(text), 'conversation')

    def test_pure_concept_discussion(self):
        for text in ('什么是基频？', 'Explain what a formant is', '什么是 VOT', '如何理解共振峰？'):
            with self.subTest(text=text): self.assertEqual(self.classify(text), 'concept')

    def test_material_and_mixed_requests_are_not_dialogue(self):
        for text in ('Hello，请测量当前声音的基频', 'Hi, measure the selected sound duration',
                     '打开文件 C:/fixture/test.wav', '分析一下这个声音', 'what is the pitch of this recording?',
                     '删除 Sound 3', 'Compare these two recordings', '继续刚才的分析', '这个怎么样？',
                     '你能帮我测量基频吗', 'measure intensity between 0.1 and 0.3 seconds'):
            with self.subTest(text=text): self.assertIsNone(self.classify(text))

    def test_capability_question_with_a_real_operation_is_not_dialogue(self):
        for text in ('你能测哪些参数？顺便测量当前声音的平均基频',
                     '你有哪些功能？另外分析一下这个声音'):
            with self.subTest(text=text): self.assertIsNone(self.classify(text))

    def test_negated_measurement_clause_keeps_knowledge_requests_out_of_the_tools(self):
        for text in ('用你自己的知识帮我纠音，讲日语发音练习，不测录音',
                     '讲日语发音练习方法，不需要测量我的录音'):
            with self.subTest(text=text): self.assertEqual(self.classify(text), 'advice')

    def test_negated_measurement_clause_does_not_hide_a_positive_operation(self):
        for text in ('帮我分析当前录音，不用测量别的',
                     '不测录音，但请测量当前声音的平均基频'):
            with self.subTest(text=text): self.assertIsNone(self.classify(text))

    def test_general_knowledge_marker_keeps_positive_operations_in_analysis(self):
        for text in ('请解释 F0，这是一般知识问题，另外测量对象 1 的基频。',
                     '一般知识问题：解释基频，不需要测量我的录音，但删除 Sound 3。'):
            with self.subTest(text=text): self.assertIsNone(self.classify(text))

    def test_unknown_operations_and_references_keep_analysis_pipeline(self):
        for text in ('你好，复制一份','生成一个 1 秒的 220 Hz 正弦音','rename object 7 to sample',
                     'resample to 16000 Hz','再做一次','它的平均值呢？','Explain its average',
                     '解释它的平均值','Hello, plot it','invert channel 1'):
            with self.subTest(text=text): self.assertIsNone(self.classify(text))

    def test_concept_prefix_does_not_override_operations_targets_or_ranges(self):
        for text in ('什么是基频？顺便复制一份','解释0.1到0.3秒的基频变化',
                     '解释 Sound 3 的共振峰','Explain formant values for Sound 3',
                     '解释基频，然后翻转信号','什么是共振峰；把结果导出','Explain pitch and then reverse it',
                     '解释基频并拼接两段','What is a formant and join the two clips',
                     'Explain pitch and merge both clips','解释0.1秒处基频'):
            with self.subTest(text=text): self.assertIsNone(self.classify(text))

    def test_english_identity_and_capability_are_ordinary_dialogue(self):
        for text in ('What are your capabilities?','What is your name?'):
            with self.subTest(text=text): self.assertEqual(self.classify(text),'conversation')

    def effective(self, level, force, kind):
        decide = getattr(cloud_agent, 'effective_thinking_level', None)
        self.assertTrue(callable(decide), 'Missing per-request thinking policy')
        cfg = AppConfig().api
        cfg.thinking_level = level
        self.assertTrue(hasattr(cfg, 'force_deep_thinking'), 'Missing API setting')
        cfg.force_deep_thinking = force
        return decide(cfg, kind)

    def test_high_ordinary_dialogue_relaxes_unless_forced(self):
        self.assertEqual(self.effective('high', False, 'conversation'), 'off')
        self.assertEqual(self.effective('high', True, 'conversation'), 'high')

    def test_force_setting_does_not_upgrade_other_manual_levels(self):
        for level in ('low', 'medium', 'off', 'auto'):
            with self.subTest(level=level): self.assertEqual(self.effective(level, True, 'conversation'), level)

    def test_concepts_and_analysis_keep_selected_high_level(self):
        for kind in ('concept', None):
            with self.subTest(kind=kind): self.assertEqual(self.effective('high', False, kind), 'high')


class ThinkingFieldsTests(unittest.TestCase):
    def fields(self, level, base=OFFICIAL):
        return qwen.thinking_request_fields(QwenConfig(provider='api', base_url=base, model='qwen3.7-flash', thinking_level=level))

    def test_official_qwen_off_explicitly_disables_thinking(self):
        self.assertEqual(self.fields('off'), {'enable_thinking':False})

    def test_official_qwen_high_enables_thinking_without_openai_effort(self):
        self.assertEqual(self.fields('high'), {'enable_thinking':True})

    def test_official_qwen_low_and_medium_have_bounded_thinking(self):
        self.assertEqual(self.fields('low'), {'enable_thinking':True, 'thinking_budget':1024})
        self.assertEqual(self.fields('medium'), {'enable_thinking':True, 'thinking_budget':4096})

    def test_service_auto_stays_default_and_unknown_gateway_keeps_compatibility(self):
        self.assertEqual(self.fields('auto'), {})
        self.assertEqual(self.fields('high', 'https://fixture-gateway.invalid/v1'), {'reasoning_effort':'high'})


class ReasoningReserveTests(unittest.TestCase):
    """思考档位要占掉的输出额度：正文之外额外留，不让 reasoning 顶掉正文。"""

    def reserve(self, level):
        fn=getattr(cloud_agent,'reasoning_reserve',None)
        self.assertTrue(callable(fn), 'Missing reasoning output reservation')
        return fn(level)

    def test_levels_reserve_provider_bounded_or_conservative_output(self):
        self.assertEqual(self.reserve('off'), 0)
        self.assertEqual(self.reserve('low'), 1024)
        self.assertEqual(self.reserve('medium'), 4096)
        self.assertEqual(self.reserve('high'), 8192)
        self.assertEqual(self.reserve('HIGH'), 8192)
        # auto = 交给服务端默认（可能开思考）：按中档预留，宁可多留也别让正文被挤掉。
        self.assertEqual(self.reserve('auto'), 4096)


class ForceSettingTests(unittest.TestCase):
    def test_advanced_checkbox_is_collected_and_survives_reopening(self):
        from praat_ai import ui_windows
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'; path.write_text('{}')
            root = ui_windows.create_root(); root.withdraw()
            dialog = api_settings.ApiSettingsDialog(root, config_path=path); dialog.window.withdraw()
            try:
                variable = getattr(dialog, 'force_deep_thinking', None)
                self.assertIsNotNone(variable, 'Missing advanced checkbox')
                self.assertFalse(variable.get())
                dialog.show_advanced.set(True); dialog._toggle_advanced()
                dialog.window.update_idletasks()
                self.assertLessEqual(dialog.window.winfo_reqheight(), dialog.window.winfo_screenheight()-80,
                                     'Advanced settings and Save must fit inside the screen')
                variable.set(True)
                dialog.save()
                other = api_settings.ApiSettingsDialog(root, config_path=path); other.window.withdraw()
                try: self.assertTrue(other.force_deep_thinking.get())
                finally: other.close()
            finally:
                if dialog.window.winfo_exists(): dialog.close()
                for callback in root.tk.splitlist(root.tk.call('after','info')): root.after_cancel(callback)
                root.update_idletasks()
                root.destroy()

    def test_old_configuration_defaults_to_not_forcing_high(self):
        self.assertIs(getattr(AppConfig().api, 'force_deep_thinking', None), False)

    def test_force_setting_roundtrips_through_real_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'config.json'
            path.write_text('{}')
            api_settings.save_settings({'force_deep_thinking':True}, path)
            self.assertIs(getattr(load_config(path).api, 'force_deep_thinking', None), True)
            self.assertIs(json.loads(path.read_text(encoding='utf-8'))['api'].get('force_deep_thinking'), True)
            api_settings.save_settings({'force_deep_thinking':False}, path)
            self.assertIs(getattr(load_config(path).api, 'force_deep_thinking', None), False)


def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()

if __name__ == '__main__': unittest.main()
