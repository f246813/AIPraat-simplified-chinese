"""Regression scenarios from the independent 2026-10-02 staircase review."""
import json
import queue
import tempfile
import threading
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from pydantic_ai import BinaryContent
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.exceptions import ModelHTTPError

from praat_ai import chat, tools
from praat_ai.cloud_agent import run_cloud_turn
from praat_ai.cloud_workflow import process_cloud
from praat_ai.config import AppConfig
from praat_ai.conversation_store import ConversationStore
from praat_ai.escape_policy import AnalysisState, Budget
from praat_ai.materials import TaskMaterials
from praat_ai.model_capabilities import is_audio_unsupported

CONTEXT = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\ttone\t1\t0.02\t0.08\n'
GENERAL = '补充一般原理、参照和练习方法，明确具体录音证据的缺口'


def has_audio(messages):
    return any(isinstance(part, UserPromptPart) and isinstance(part.content, list)
               and any(isinstance(content, BinaryContent) for content in part.content)
               for message in messages for part in message.parts)


def report(goal, *, missing='audio', marker='阶段解释'):
    return ModelResponse([TextPart(json.dumps({
        'analysis':marker + '：现有证据支持阶段性解释，但需要区分模型音频定性观察、专业测量和参考资料，明确剩余缺口。',
        'coverage':[{'item':goal, 'status':'partial',
                     'explanation':'缺少原始音频听感证据。' if missing == 'audio' else '仍缺少可比较的专业参考测量。',
                     'missing_evidence':[missing] if missing else []}],
    }, ensure_ascii=False))])


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source.wav'
        with wave.open(str(source), 'wb') as wav:
            wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            wav.writeframes(b'\0\0' * 1600)
        self.material = TaskMaterials(self.root / 'tasks')
        self.material.snapshot_wav(source)
        self.material.source_kind = 'praat'
        self.addCleanup(self.material.close)
        self.cfg = AppConfig()
        self.cfg.api.base_url, self.cfg.api.model = 'https://test.invalid/v1', 'review-fixture'
        self.cfg.api.enabled = True
        self.cfg.api.audio_input_enabled = True
        self.cfg.api.limit_tokens = False
        self.cfg.api.thinking_level = 'auto'

    def run_turn(self, state, respond, *, names=('pitch',), action=None, **kwargs):
        dispatched = []
        def execute(name, args, index):
            dispatched.append((name, args, index))
            if action:
                return action(name, args, index)
            return chat.AgentStep(index, name, args, True, '成功', 'fixture-script', ['基频 = 220 Hz'])
        run_cloud_turn(self.cfg, state, execute_action=execute, cancel=threading.Event(),
                       model=FunctionModel(respond), materials=self.material,
                       tool_schemas=[s for s in tools.tool_schemas() if s['function']['name'] in names], **kwargs)
        return dispatched

    def successful_measurement(self, *, missing='audio', request_limit=12):
        goal = '比较这段录音与德语发音目标'
        state = AnalysisState(goal, CONTEXT, budget=Budget(request_limit=request_limit))
        calls, inputs = [], []
        def respond(messages, info):
            calls.append(bool(info.function_tools))
            inputs.append(has_audio(messages))
            if info.function_tools:
                return ModelResponse([ToolCallPart('pitch', {'time':0.05})]) if len(calls) == 1 else ModelResponse([TextPart('测量结束')])
            return report(goal, missing=missing, marker='L3 音频解释' if has_audio(messages) else 'L2 证据解释')
        dispatched = self.run_turn(state, respond)
        return state, inputs, dispatched

    def test_successful_measurement_gap_escalates_once_on_shared_budget(self):
        state, inputs, dispatched = self.successful_measurement()
        self.assertEqual(inputs, [False, False, False, True])
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(state.requests, 4)
        self.assertEqual(state.mode, 'L3')
        self.assertIn('L3 音频解释', state.report)

    def test_reference_gap_does_not_upload_audio(self):
        state, inputs, _ = self.successful_measurement(missing='reference')
        self.assertNotIn(True, inputs)
        self.assertEqual(state.mode, 'L2')

    def test_limited_or_disabled_audio_does_not_escalate(self):
        for setting in ('limit', 'disabled'):
            with self.subTest(setting=setting):
                self.cfg.api.limit_tokens = setting == 'limit'
                self.cfg.api.audio_input_enabled = setting != 'disabled'
                state, inputs, _ = self.successful_measurement()
                self.assertNotIn(True, inputs)
                self.assertEqual(state.mode, 'L2')

    def test_audio_observation_and_previous_report_survive_continuation(self):
        goal = '直接听原始音频'
        state = AnalysisState(goal, CONTEXT)
        self.run_turn(state, lambda *_:report(goal, missing='reference', marker='OBS-R7'))
        state.reason = '参考测量分支暂停'
        continued = state.continued('补充解释已有证据及原目标未完成部分')
        prompts = []
        def respond(messages, info):
            self.assertFalse(info.function_tools)
            self.assertFalse(has_audio(messages))
            prompts.append(str(messages))
            return report(goal, missing='reference')
        self.run_turn(continued, respond)
        prompt = '\n'.join(prompts)
        self.assertIn('OBS-R7', prompt)
        self.assertIn('参考测量分支暂停', prompt)
        self.assertIn(self.material.fingerprint, prompt)
        self.assertIn('model_audio', prompt)
        self.assertFalse(getattr(continued, 'audio_sent', False))
        self.assertEqual(continued.mode, 'L2')

    def test_missing_snapshot_does_not_add_an_audio_report(self):
        self.material.audio_path.unlink()
        state, inputs, _ = self.successful_measurement()
        self.assertEqual(inputs, [False, False, False])
        self.assertEqual(state.mode, 'L2')

    def test_output_retry_leaves_too_little_budget_for_automatic_audio(self):
        goal = '比较这段录音与德语发音目标'
        state = AnalysisState(goal, CONTEXT, budget=Budget(request_limit=5))
        inputs, reports = [], []
        def respond(messages, info):
            inputs.append(has_audio(messages))
            if info.function_tools:
                return ModelResponse([ToolCallPart('pitch', {'time':0.05})]) if len(inputs) == 1 else ModelResponse([TextPart('测量结束')])
            reports.append(1)
            return ModelResponse([TextPart('invalid JSON')]) if len(reports) == 1 else report(goal)
        self.run_turn(state, respond)
        self.assertEqual(inputs, [False, False, False, False])
        self.assertEqual(state.requests, 4)

    def test_failed_automatic_audio_retains_the_valid_l2_report(self):
        goal = '比较这段录音与德语发音目标'
        state = AnalysisState(goal, CONTEXT)
        calls = []
        def respond(messages, info):
            calls.append(has_audio(messages))
            if info.function_tools:
                return ModelResponse([ToolCallPart('pitch', {'time':0.05})]) if len(calls) == 1 else ModelResponse([TextPart('测量结束')])
            return ModelResponse([TextPart('invalid JSON')]) if has_audio(messages) else report(goal, marker='L2-KEEP')
        self.run_turn(state, respond)
        self.assertIn('L2-KEEP', state.report)
        self.assertEqual(calls, [False, False, False, True, True])
        self.assertTrue(state.audio_received)
        self.assertFalse(state.audio_report_completed)

    def test_rejected_or_failed_transport_records_attempt_without_acceptance(self):
        for error in (ModelHTTPError(400, 'fixture', 'This model does not support audio input'), TimeoutError('fixture timeout')):
            with self.subTest(error=error):
                self.cfg.api.audio_input_enabled = True
                state = AnalysisState('直接听原始音频', CONTEXT)
                corrections, inputs = [], []
                def respond(messages, info):
                    inputs.append(has_audio(messages))
                    if has_audio(messages):
                        raise error
                    return report(state.goal, missing='reference')
                self.run_turn(state, respond, correct_audio=corrections.append)
                self.assertTrue(state.audio_sent)
                self.assertFalse(state.audio_received)
                self.assertFalse(state.audio_report_completed)
                self.assertEqual(len(inputs), 2)
                self.assertEqual(len(corrections), 0 if isinstance(error, TimeoutError) else 1)

    def test_output_only_rejection_does_not_correct_input_setting(self):
        state = AnalysisState('直接听原始音频', CONTEXT)
        corrections = []
        def respond(*_):
            raise ModelHTTPError(400, 'fixture', 'This model does not support audio output')
        self.run_turn(state, respond, correct_audio=corrections.append)
        self.assertTrue(self.cfg.api.audio_input_enabled)
        self.assertEqual(corrections, [])
        self.assertTrue(state.audio_sent)
        self.assertFalse(state.audio_received)

    def test_invalid_report_still_records_audio_request_and_response(self):
        inputs = []
        def invalid(messages, info):
            inputs.append(has_audio(messages))
            return ModelResponse([TextPart('invalid JSON')])
        state = AnalysisState('直接听原始音频', CONTEXT)
        self.run_turn(state, invalid)
        self.assertEqual(inputs, [True, True])
        self.assertTrue(getattr(state, 'audio_sent', False))
        self.assertTrue(state.audio_received)
        self.assertFalse(getattr(state, 'audio_report_completed', False))

    def test_transport_retry_preserves_first_mutation_authorization(self):
        goal = '把对象 1 重命名为 new'
        state = AnalysisState(goal, CONTEXT)
        planning = []
        def respond(messages, info):
            if info.function_tools:
                planning.append(1)
                if len(planning) == 1:
                    raise TimeoutError('fixture transport timeout')
                if len(planning) == 2:
                    return ModelResponse([ToolCallPart('rename_object', {'new_name':'new', 'object':1})])
                if len(planning) == 3:
                    return ModelResponse([ToolCallPart('rename_object', {'new_name':'extra', 'object':1})])
                return ModelResponse([TextPart('操作结束')])
            return report(goal, missing='reference')
        dispatched = self.run_turn(state, respond, names=('rename_object',))
        self.assertEqual(len(dispatched), 1, state.attempts)
        self.assertEqual(dispatched[0][1]['new_name'], 'new')
        self.assertTrue(any('用户明确提供' in a.get('reason', '') or a.get('reason') == '未授权追加对象操作'
                            for a in state.attempts))
        self.assertEqual(state.requests, len(planning) + 1)

    def test_general_continuation_with_no_evidence_routes_to_l4(self):
        original = AnalysisState('分析录音', CONTEXT)
        original.reason = '专业工具不可用'
        state = original.continued(GENERAL)
        planning = []
        def respond(messages, info):
            planning.append(bool(info.function_tools))
            return report(original.goal, missing='reference') if not info.function_tools else ModelResponse([TextPart('工具阶段结束')])
        dispatched = self.run_turn(state, respond)
        self.assertEqual(planning, [False])
        self.assertEqual(dispatched, [])
        self.assertEqual(state.mode, 'L4')
        self.assertFalse(getattr(state, 'audio_sent', False))

    def test_input_capability_errors_exclude_output_and_tts(self):
        for text, expected in (
            ('This model does not support audio input', True),
            ('This model does not support audio', True),
            ('Audio input is unsupported by this endpoint', True),
            ('This model does not support audio output', False),
            ('This model does not support outputting audio.', False),
            ('This model does not support generating audio.', False),
            ('This endpoint does not support audio generation or TTS', False),
            ('This model does not support audio output. Audio input is supported.', False),
            ('This endpoint does not support TTS, but audio input is supported.', False),
            ('This model supports audio input but audio output is unsupported.', False),
            ('This model does not support audio output; audio input is unsupported.', True),
            ('模型不支持音频输出', False),
            ('该模型不支持输出音频', False),
            ('该模型不支持生成音频', False),
            ('Invalid input_audio format', False),
        ):
            with self.subTest(text=text):
                self.assertEqual(is_audio_unsupported(400, text), expected)

    def test_limited_continuation_excerpts_long_previous_analysis(self):
        goal = '解释原始音频分析'
        original = AnalysisState(goal, CONTEXT)
        original.report = 'OBS-LONG：' + '前一轮模型定性解释及依据。' * 1000
        original.coverage = [{'item':goal, 'status':'partial', 'explanation':'仍缺少参考测量。' * 100}]
        original.audio_observations = [{'kind':'model_audio', 'model':'fixture',
                                        'source':self.material.provenance(), 'analysis':original.report}]
        state = original.continued('解释已有观察', mode='L2')
        state.budget = Budget(context_tokens=4096, response_tokens=1500)
        self.cfg.api.limit_tokens = True
        self.cfg.api.max_context_tokens, self.cfg.api.plan_max_tokens = 4096, 1500
        self.assertTrue(self.window(original)._continuation_fits(original))
        calls = []
        def respond(messages, info):
            self.assertFalse(info.function_tools)
            self.assertFalse(has_audio(messages))
            calls.append(str(messages))
            return report(goal, missing='reference')
        self.run_turn(state, respond)
        self.assertEqual(len(calls), 1, state.reason)
        self.assertIn('OBS-LONG', calls[0])
        self.assertEqual(state.previous_report, original.report)
        self.assertEqual(state.audio_observations[0]['analysis'], original.report)

    def test_general_continue_preflight_matches_actual_dialogue_context(self):
        goal = '直接听原始音频'
        original = AnalysisState(goal, CONTEXT, budget=Budget(context_tokens=4096, response_tokens=1500))
        original.dialogue_context = [{'role':'user' if index % 2 == 0 else 'assistant', 'content':'证据' * 396} for index in range(4)]
        self.cfg.api.limit_tokens = True
        self.cfg.api.max_context_tokens, self.cfg.api.plan_max_tokens = 4096, 1500
        audio_report = {'analysis':'模型音频定性观察：' + '观察和依据。' * 181,
                        'coverage':[{'item':goal, 'status':'partial', 'explanation':'仍缺少可比较的参考测量。', 'missing_evidence':['reference']}]}
        self.run_turn(original, lambda *_:ModelResponse([TextPart(json.dumps(audio_report, ensure_ascii=False))]))
        self.assertTrue(original.audio_report_completed)
        window = self.window(original)
        window.analysis_state, window.busy = original, False
        class Entry:
            def delete(self, *_): pass
            def insert(self, *_): pass
        window.entry = Entry()
        accepted = 0
        for capacity in range(3072, 4097, 64):
            self.cfg.api.max_context_tokens = capacity
            original.can_continue = True
            with patch.object(window, 'submit') as submit, patch.object(window, 'append_hint'):
                window.continue_analysis(GENERAL, original)
            if submit.called:
                accepted += 1
                pending = window.pending_analysis
                pending.budget = Budget(context_tokens=capacity, response_tokens=1500)
                requests = []
                def respond(messages, info):
                    requests.append(1)
                    self.assertFalse(info.function_tools)
                    return report(goal, missing='reference')
                self.run_turn(pending, respond)
                self.assertEqual(len(requests), 1, f'context={capacity}: {pending.reason}')
        self.assertGreater(accepted, 0)

    def test_legacy_report_uses_budgeted_dialogue_excerpt_once(self):
        state = AnalysisState('解释已有录音依据', CONTEXT, continuation=True, continuation_mode='L4',
                              budget=Budget(context_tokens=4096, response_tokens=1500))
        state.dialogue_context = [{'role':'user' if index % 2 == 0 else 'assistant',
                                   'content':f'HISTORY-{index}：' + '证据' * 396} for index in range(4)]
        self.cfg.api.limit_tokens = True
        self.cfg.api.max_context_tokens, self.cfg.api.plan_max_tokens = 4096, 1500
        requests = []
        def respond(messages, info):
            self.assertFalse(info.function_tools)
            requests.append(messages)
            return report(state.goal, missing='reference')
        self.run_turn(state, respond)
        self.assertEqual(len(requests), 1, state.reason)
        self.assertTrue(state.metrics.get('report_verified'), state.reason)
        parts = [p.content for m in requests[0] for p in m.parts
                 if isinstance(p, UserPromptPart) and isinstance(p.content, str)]
        payloads = [json.loads(part) for part in parts if part.startswith('{')]
        excerpts = [item for payload in payloads for item in payload.get('visible_dialogue', [])]
        self.assertEqual(len(excerpts), 4)
        for original, excerpt in zip(state.dialogue_context, excerpts):
            self.assertEqual(excerpt['role'], original['role'])
            self.assertTrue(original['content'].startswith(excerpt['content']))
            self.assertLess(len(excerpt['content']), len(original['content']))
            self.assertNotIn(original['content'], parts)
        self.assertEqual(state.dialogue_context[0]['content'], 'HISTORY-0：' + '证据' * 396)

    def window(self, state=None):
        window = chat.ChatWindow.__new__(chat.ChatWindow)
        window.config, window.messages, window.history = self.cfg, queue.Queue(), []
        window.cancel_event = threading.Event()
        window.pending_analysis = state
        window.task_materials, window.current_materials = [self.material], self.material
        window.store = ConversationStore(self.root / 'records.sqlite')
        window.session_id = window.store.new_session()
        return window

    def workflow_patches(self, stack, fresh, send, cloud):
        for name, value in (
            ('object_context', lambda:fresh[0]), ('_request_context', lambda text, goal:text),
            ('result_path', lambda:self.root / 'results.tsv'), ('state_path', lambda:self.root / 'state.txt'),
            ('runtime_dir', lambda:self.root), ('praat_executable', lambda:self.root / 'Praat.exe'),
            ('praat_process_ids', lambda *_:[101]), ('praat_process_running_from', lambda *_:True),
            ('refresh_object_context', lambda *args, **kwargs:(True, 'refreshed')),
            ('_send_script', send), ('_read_failure', lambda:''), ('_read_results', lambda:['操作成功']),
        ):
            stack.enter_context(patch.object(chat, name, side_effect=value))
        stack.enter_context(patch('praat_ai.cloud_agent.run_cloud_turn', side_effect=cloud))

    def test_confirmed_rename_keeps_bound_id_range_for_next_save(self):
        goal = '把对象 1 重命名为 new，然后保存为 WAV'
        fresh, scripts = [CONTEXT], []
        window = self.window()
        def send(executable, script, **kwargs):
            scripts.append(script)
            if 'Rename: "new"' in script:
                fresh[0] = CONTEXT.replace('\ttone\t', '\tnew\t').replace('0.02\t0.08', '0.05\t0.09')
            return True, ''
        def cloud(config, state, **kwargs):
            action = kwargs['execute_action']
            rename = action('rename_object', {'object':1, 'new_name':'new'}, 1)
            self.assertTrue(rename.ok, rename.observation)
            save = action('save_sound', {'object':1, 'path':str(self.root / 'out.wav')}, 2)
            self.assertTrue(save.ok, save.observation)
            state.report, state.status = '操作完成', 'complete'
        with ExitStack() as stack:
            self.workflow_patches(stack, fresh, send, cloud)
            process_cloud(window, goal)
        self.assertEqual(len(scripts), 2)
        self.assertTrue(all('selectObject: 1' in script for script in scripts))
        binding = tools.parse_object_context(window.analysis_state.context_text)[0]
        self.assertEqual((binding.id, binding.class_name, binding.name, binding.selection), (1, 'Sound', 'new', (0.02, 0.08)))

    def test_external_rename_still_rejected(self):
        from praat_ai.cloud_workflow import bound_context
        from praat_ai.escape_policy import BranchExit
        original = tools.ToolContext(tools.parse_object_context(CONTEXT), self.root / 'result', self.root / 'state')
        with self.assertRaises(BranchExit):
            bound_context(original, CONTEXT.replace('\ttone\t', '\texternal\t'))

    def test_confirmed_rename_does_not_accept_disappeared_id_or_changed_class(self):
        from praat_ai.cloud_workflow import renamed_context
        from praat_ai.escape_policy import BranchExit
        original = tools.ToolContext(tools.parse_object_context(CONTEXT), self.root / 'result', self.root / 'state')
        for fresh in ('id\tclass\tname\tselected\n', CONTEXT.replace('\tSound\t', '\tTextGrid\t')):
            with self.subTest(fresh=fresh), self.assertRaises(BranchExit):
                renamed_context(original, fresh, 1)

    def test_create_rename_save_keeps_successful_operation_records(self):
        goal = '新建声音，命名为 created，然后重命名为 new，然后保存为 WAV'
        fresh, scripts = [CONTEXT], []
        window = self.window(AnalysisState(goal, CONTEXT))
        self.cfg.api.audio_input_enabled = False
        calls = [('create_sound', {'duration':0.1, 'frequency':220, 'name':'created'}),
                 ('rename_object', {'object':2, 'new_name':'new'}),
                 ('save_sound', {'object':2, 'path':str(self.root / 'created.wav')})]
        def send(executable, script, **kwargs):
            scripts.append(script)
            if len(scripts) == 1:
                fresh[0] += '2\tSound\tcreated\t1\t\t\n'
            elif len(scripts) == 2:
                fresh[0] = fresh[0].replace('\tcreated\t', '\tnew\t')
            return True, ''
        def respond(messages, info):
            if info.function_tools:
                if calls:
                    name, args = calls.pop(0)
                    return ModelResponse([ToolCallPart(name, args)])
                return ModelResponse([TextPart('操作结束')])
            return report(goal, missing='reference')
        def cloud(config, state, **kwargs):
            return run_cloud_turn(config, state, model=FunctionModel(respond),
                tool_schemas=[s for s in tools.tool_schemas() if s['function']['name'] in ('create_sound','rename_object','save_sound')], **kwargs)
        with ExitStack() as stack:
            self.workflow_patches(stack, fresh, send, cloud)
            process_cloud(window, goal)
        self.assertEqual(len(scripts), 3, window.analysis_state.reason)
        successful = [a['tool'] for a in window.analysis_state.attempts if a['status'] == 'success']
        self.assertEqual(successful, ['create_sound', 'rename_object', 'save_sound'])

    def test_persisted_input_fact_does_not_depend_on_report_success(self):
        state = AnalysisState('直接听原始音频', '').continued('直接分析原始音频', mode='L3')
        window = self.window(state)
        def cloud(config, state, **kwargs):
            # Exercise the workflow with the real SDK and an invalid output twice.
            kwargs.pop('prepare_audio')
            kwargs.pop('correct_audio')
            return run_cloud_turn(config, state, model=FunctionModel(lambda *_:ModelResponse([TextPart('invalid JSON')])), **kwargs)
        with ExitStack() as stack:
            self.workflow_patches(stack, [''], lambda *_:self.fail('no Praat'), cloud)
            process_cloud(window, state.goal)
        record = [item['payload'] for item in window.store.records(window.session_id) if item['kind'] == 'turn'][0]
        self.assertTrue(record['audio_input'])
        self.assertTrue(record.get('audio_response_received'))
        self.assertFalse(record.get('audio_report_completed'))


if __name__ == '__main__':
    unittest.main()
