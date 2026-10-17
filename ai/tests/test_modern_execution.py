"""No real Praat, cloud, Tk windows or local HTTP requests in this suite."""
import copy
import json
import os
import tempfile
import threading
import unittest
import wave
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from praat_ai import chat, delivery, modern_execution as m, tools
from praat_ai.cloud_agent import run_cloud_turn as graph_turn
from praat_ai.cloud_runtime import CloudRuntime
from praat_ai.config import AppConfig
from praat_ai.escape_policy import BranchExit

CONTEXT = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
           '1\tSound\tone\t1\t0.2\t0.5\n'
           '2\tSound\ttwo\t0\t\t\n# praat-pid=42\n')
IDENTITY = {'executable':'Praat.exe', 'started':'win:123'}


def cloud_config():
    config = AppConfig()
    config.api.enabled = True
    config.api.base_url = 'https://test.invalid/v1'
    config.api.model = 'mock-model'
    config.api.thinking_level = 'off'
    config.api.audio_input_enabled = False
    return config


class ModernExecutionTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory(dir=os.getenv('PI_SCRATCH_DIR')))
        self.root = Path(temp)
        self.executor = m.ModernExecutor(self.root, self.root / 'config.json')
        self.addCleanup(self.executor.close)
        self.fresh = CONTEXT
        self.sends, self.events, self.runtimes = [], [], []
        self.rows = ['时长 = 1.25 秒']
        self.send_behavior = None
        self.stack.enter_context(patch.object(m, '_POISON', ''))
        self.stack.enter_context(patch.object(m, 'process_identity', return_value=IDENTITY))
        self.stack.enter_context(patch.object(chat, 'praat_executable', return_value='Praat.exe'))
        self.ids = self.stack.enter_context(patch.object(chat, 'praat_process_ids', return_value=[42]))
        self.stack.enter_context(patch.object(chat.sendpraat, 'send_mode', return_value='mock-native'))
        self.stack.enter_context(patch.object(chat, 'object_context', side_effect=lambda:self.fresh))
        self.stack.enter_context(patch.object(chat, 'context_ping_script', return_value='PING'))
        self.stack.enter_context(patch.object(chat, 'result_path', return_value=self.root / 'result.tsv'))
        self.stack.enter_context(patch.object(chat, 'state_path', return_value=self.root / 'state.txt'))
        self.stack.enter_context(patch.object(chat, '_send_script', side_effect=self.send))
        self.stack.enter_context(patch.object(chat, '_read_failure', return_value=''))
        self.stack.enter_context(patch.object(chat, '_read_results', side_effect=lambda:list(self.rows)))
        self.stack.enter_context(patch.object(chat, 'ChatWindow', side_effect=AssertionError('Tk forbidden')))
        self.stack.enter_context(patch.object(chat, 'queue', SimpleNamespace(Queue=Mock(side_effect=AssertionError('Tk queue forbidden')))))
        self.cloud = self.stack.enter_context(patch.object(m, 'run_cloud_turn', side_effect=AssertionError('cloud not mocked')))
        self.dialogue = self.stack.enter_context(patch.object(m, 'run_dialogue_turn', side_effect=AssertionError('dialogue not mocked')))
        self.stack.enter_context(patch.object(m, 'CloudRuntime', side_effect=self.make_runtime))
        self.model_factory = lambda _: (_ for _ in ()).throw(AssertionError('network forbidden'))

    def make_runtime(self):
        runtime = CloudRuntime(model_factory=self.model_factory)
        self.runtimes.append(runtime)
        return runtime

    def send(self, executable, script, *, process_id, cancel, outcome):
        self.assertEqual(process_id, 42)
        self.sends.append((threading.current_thread().name, script))
        if self.send_behavior:
            return self.send_behavior(script, cancel, outcome)
        outcome.append(delivery.DELIVERED)
        return True, ''

    def run_task(self, text='当前声音时长', target=None, config=None, cancel=None,
                 history=None, attachments=None, emit=None):
        return self.executor.run(config=config or cloud_config(), text=text,
            target=self.executor.capture_target(text) if target is None else target,
            history=[] if history is None else history,
            cancel=cancel or threading.Event(), emit=emit or (lambda kind, payload:self.events.append((kind, payload))),
            attachments=[] if attachments is None else attachments)

    def dispatcher(self, target=None, cancel=None):
        return m._Dispatcher(target or self.executor.capture_target('测量对象'),
            cancel or threading.Event(), lambda *args, **kwargs:None, self.root)

    def test_direct_measurement_no_model_and_real_events(self):
        result = self.run_task()
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['content'], self.rows[0])
        self.assertEqual(result['evidence'][0]['rows'], self.rows)
        self.assertEqual(result['attempts'][0]['execution'], delivery.DELIVERED)
        self.assertEqual(result['metrics']['requests'], 0)
        self.assertEqual([kind for kind, _ in self.events], ['activity', 'results', 'delta'])
        self.assertEqual(self.events[0][1]['type'], 'tool')
        self.cloud.assert_not_called()
        self.dialogue.assert_not_called()
        self.assertEqual(self.runtimes, [])

    def test_deterministic_statistics_and_forced_thinking_still_make_no_requests(self):
        from praat_ai.workflow_policy import WorkflowPolicy
        cfg=cloud_config();cfg.api.thinking_level='high';cfg.api.force_deep_thinking=True
        cfg.workflow=WorkflowPolicy()
        for goal,row,tool in [('测量当前声音平均基频','基频统计（0.200–0.500 秒）：平均 220.000 Hz','pitch_statistics'),
                              ('测量当前声音平均强度','强度统计（0.200–0.500 秒）：平均 65.000 dB，最高 70.000 dB','intensity_statistics')]:
            self.rows=[row]
            result=self.run_task(goal,config=cfg)
            self.assertEqual(result['status'],'complete',result)
            self.assertEqual(result['metrics']['requests'],0)
            self.assertEqual(result['evidence'][0]['tool'],tool)
            self.assertEqual(result['attempts'][0]['arguments']['from'],.2)
            self.assertEqual(result['attempts'][0]['arguments']['to'],.5)
            self.assertTrue(result['results'][0]['quantities'])
        self.cloud.assert_not_called();self.dialogue.assert_not_called()

    def test_chat_policy_never_captures_praat_or_initializes_tool_graph(self):
        from praat_ai.workflow_policy import WorkflowPolicy
        cfg=cloud_config();cfg.workflow=WorkflowPolicy.for_preset('chat')
        self.ids.side_effect=AssertionError('Pure chat must not inspect Praat')
        target=self.executor.capture_workflow_target('聊聊声音',cfg.workflow)
        self.assertEqual(target,'')
        def reply(config,state,**kwargs):state.report='通用讨论';state.status='complete'
        self.dialogue.side_effect=reply
        result=self.run_task('聊聊声音',target=target,config=cfg)
        self.assertEqual(result['status'],'complete')
        self.assertEqual(self.sends,[]);self.cloud.assert_not_called()

    def test_input_without_a_request_routes_to_dialogue_and_never_runs_tools(self):
        """随手敲的一个词不当成测量任务：不建工具图、不发 Praat 脚本、不调云端规划。"""
        goals = []
        def reply(config, state, **kwargs):
            goals.append(state.goal)
            state.report, state.status = '你想测量什么？可以说出对象和要测的量。', 'complete'
        self.dialogue.side_effect = reply
        for text in ('test', 'asdf'):
            with self.subTest(text=text):
                result = self.run_task(text=text)
                self.assertEqual(result['status'], 'complete')
                self.assertIn('你想测量什么', result['content'])
        self.assertEqual(goals, ['test', 'asdf'])
        self.assertEqual(self.sends, [])
        self.cloud.assert_not_called()

    def test_undefined_pitch_is_a_saved_result_without_success_evidence(self):
        self.rows=['基频统计（0.200–0.500 秒）无法计算：该区间没有周期性声源']
        result=self.run_task('测量当前声音平均基频')
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['evidence'],[])
        self.assertFalse(result['attempts'][0]['result_valid'])
        self.assertIn('无法计算',str(result['results']))
        self.assertEqual(result['metrics']['requests'],0)

    def test_followup_binds_evidence_and_never_remeasures_when_l2_globally_off(self):
        from praat_ai.workflow_policy import WorkflowPolicy
        cfg=cloud_config();cfg.workflow=WorkflowPolicy.for_preset('fast')
        first=self.run_task(config=cfg)
        self.cloud.side_effect=graph_turn
        def response(messages,info):
            self.assertFalse(info.function_tools)
            return ModelResponse([TextPart(json.dumps(dict(analysis='原测量确认该对象的时长为 1.25 秒。本次复用原证据进行说明，没有重新测量原录音。',
                coverage=[dict(item=first['goal'],status='complete',explanation='原结果与本次说明已交付')]),ensure_ascii=False))])
        self.model_factory=lambda _:FunctionModel(response)
        self.sends.clear()
        result=self.executor.run(config=cfg,text='本次解释结果',history=[],target=first['target'],
            cancel=threading.Event(),emit=lambda *_:None,continuation={**first,'mode':'L2'})
        self.assertEqual(self.sends,[])
        self.assertEqual(result['evidence'],first['evidence'])
        self.assertEqual(result['metrics']['l2_requests'],1)
        self.assertTrue(result['formal'],result)
        self.assertEqual(cfg.workflow.preset,'fast')

    def test_submission_real_refresh_and_explicit_range(self):
        target = self.executor.capture_target('测量对象 2 的 0.1-0.3 秒')
        self.assertEqual(len(self.sends), 1)
        self.assertEqual(self.sends[0][1], 'PING')
        rows = tools.parse_object_context(target)
        self.assertFalse(rows[0].selected)
        self.assertTrue(rows[1].selected)
        self.assertEqual(rows[1].selection, (0.1, 0.3))
        self.assertIn(m._IDENTITY_PREFIX, target)

    def test_no_stale_snapshot_when_praat_unavailable(self):
        self.ids.return_value = []
        self.assertEqual(self.executor.capture_target('测量声音'), '')
        result = self.run_task(target='')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['evidence'], [])
        self.assertEqual(result['attempts'][0]['execution'], delivery.NOT_DELIVERED)
        self.assertEqual(self.sends, [])

    def test_refresh_failure_never_returns_old_target(self):
        def fail(script, cancel, outcome):
            outcome.append(delivery.NOT_DELIVERED)
            return False, 'Message.txt write failed'
        self.send_behavior = fail
        with self.assertRaisesRegex(BranchExit, '未使用旧快照'):
            self.executor.capture_target('测量对象')
        self.assertEqual(m._POISON, '')

    def test_unknown_refresh_poisons_all_executors(self):
        self.send_behavior = lambda script, cancel, outcome: (False, 'completion unknown')
        with self.assertRaises(BranchExit):
            self.executor.capture_target('测量对象')
        other = m.ModernExecutor(self.root, self.root / 'other.json')
        # 投递状态不明时仍然不能发新脚本：核对 ping 也回不来，就继续挡住。
        with self.assertRaisesRegex(BranchExit, '状态无法确认'):
            other.capture_target('测量对象')
        with self.assertRaisesRegex(BranchExit, 'blocked'):
            m._gate(threading.Event())
        self.assertEqual(other.capture_target('你好'), '')

    def test_original_selection_and_range_never_follow_current(self):
        dispatcher = self.dispatcher()
        self.fresh = CONTEXT.replace('one\t1\t0.2\t0.5', 'one\t0\t0.7\t0.9').replace('two\t0', 'two\t1')
        step = dispatcher.cloud_action('duration', {}, 1)
        self.assertTrue(step.ok)
        self.assertIn('selectObject: 1', step.script)
        self.assertEqual(dispatcher.context.objects[0].selection, (0.2, 0.5))
        self.assertTrue(dispatcher.context.objects[0].selected)
        self.assertFalse(dispatcher.context.objects[1].selected)

    def test_unselected_original_identity_is_checked(self):
        dispatcher = self.dispatcher()
        self.fresh = CONTEXT.replace('two', 'replacement')
        step = dispatcher.cloud_action('duration', {'object':2}, 1)
        self.assertFalse(step.ok)
        self.assertIn('原对象 2', step.observation)
        self.assertEqual(step.execution, delivery.NOT_DELIVERED)
        self.assertEqual([script for _, script in self.sends], ['PING', 'PING'])

    def test_reused_pid_does_not_replace_session(self):
        dispatcher = self.dispatcher()
        with patch.object(m, 'process_identity', return_value={**IDENTITY, 'started':'win:999'}):
            step = dispatcher.cloud_action('duration', {}, 1)
        self.assertFalse(step.ok)
        self.assertEqual(len(self.sends), 1)

    def test_unknown_operation_blocks_later_tasks_until_a_verified_ping(self):
        """投递状态不明后：先挡住；一次成功的核对 ping 之后才允许继续。

        2026-10-07：以前要重启桌面才能恢复，一次 25 秒超时就把整批验证锁死。
        """

        target = self.executor.capture_target('测量对象')
        def unknown(script, cancel, outcome):
            outcome.append(delivery.DELIVERED)
            return (True, '') if script == 'PING' else (False, 'execution unknown')
        self.send_behavior = unknown
        first = self.run_task(target=target)
        self.assertEqual(first['attempts'][0]['execution'], delivery.EXECUTION_UNKNOWN)
        # 状态不明时严格闸门拦住一切新脚本。
        with self.assertRaisesRegex(BranchExit, 'blocked'):
            m._gate(threading.Event())
        count = len(self.sends)
        self.executor.close()
        other = m.ModernExecutor(self.root, self.root / 'other.json')
        second = other.run(config=cloud_config(), text='当前声音时长', history=[], target=target,
                           cancel=threading.Event(), emit=lambda *args:None)
        # 新任务先发核对 ping（成功即解除封锁），再照常执行——仍然不会「不明之后紧跟新脚本」。
        self.assertEqual(self.sends[count][1], 'PING')
        self.assertEqual(second['attempts'][0]['execution'], delivery.EXECUTION_UNKNOWN)
        # 核对 ping 已经解除了上一次的封锁；现在的封锁来自这一轮**自己**那次不明投递。
        self.assertEqual(m._POISON, 'execution unknown')

    def test_not_delivered_allows_distinct_followup(self):
        dispatcher = self.dispatcher()
        failed = False
        def send(script, cancel, outcome):
            nonlocal failed
            if script != 'PING' and not failed:
                failed = True
                outcome.append(delivery.NOT_DELIVERED)
                return False, 'write failed'
            outcome.append(delivery.DELIVERED)
            return True, ''
        self.send_behavior = send
        first = dispatcher.cloud_action('duration', {}, 1)
        second = dispatcher.cloud_action('duration', {'object':2}, 2)
        self.assertEqual(first.execution, delivery.NOT_DELIVERED)
        self.assertTrue(second.ok)
        self.assertEqual(m._POISON, '')
        self.assertEqual(len(dispatcher.evidence), 1)

    def test_entire_local_tool_segment_is_atomic_including_result_read(self):
        target = self.executor.capture_target('测量对象')
        first, second = self.dispatcher(target), self.dispatcher(target)
        between = threading.Event()
        release = threading.Event()
        result = []
        def segment(args, context, environment):
            environment.execute('EXPORT')
            between.set()
            self.assertTrue(release.wait(3))
            environment.execute('IMPORT')
            return True, ['actual multi-operation result'], ''
        local = SimpleNamespace(run=segment)
        with patch.dict(tools.LOCAL_TOOLS, {'atomic_test':local}):
            a = threading.Thread(name='A', target=lambda:result.append(first.cloud_action('atomic_test', {}, 1)))
            b = threading.Thread(name='B', target=lambda:result.append(second.cloud_action('duration', {}, 1)))
            a.start()
            self.assertTrue(between.wait(3))
            b.start()
            # B cannot even ping/clear shared files during A's multi-send segment.
            self.assertFalse(any(name == 'B' for name, _ in self.sends))
            release.set()
            a.join(3)
            b.join(3)
        self.assertFalse(a.is_alive() or b.is_alive())
        sequence = [(name, script if script in {'PING', 'EXPORT', 'IMPORT'} else 'DURATION') for name, script in self.sends[1:]]
        self.assertEqual(sequence, [('A','PING'), ('A','EXPORT'), ('A','IMPORT'), ('B','PING'), ('B','DURATION')])
        self.assertEqual(len(result), 2)
        self.assertTrue(all(step.ok for step in result))

    def test_unknown_inside_segment_preserves_each_delivery_fact(self):
        dispatcher = self.dispatcher()
        def send(script, cancel, outcome):
            outcome.append(delivery.DELIVERED)
            return (True, '') if script == 'PING' else (False, 'unknown')
        def segment(args, context, environment):
            environment.execute('EXPORT')
            environment.execute('IMPORT')
            return False, [], 'segment failed'
        self.send_behavior = send
        with patch.dict(tools.LOCAL_TOOLS, {'atomic_test':SimpleNamespace(run=segment)}):
            step = dispatcher.cloud_action('atomic_test', {}, 1)
        self.assertEqual(step.execution, delivery.EXECUTION_UNKNOWN)
        self.assertEqual(dispatcher.attempts[0]['deliveries'], [delivery.EXECUTION_UNKNOWN, delivery.EXECUTION_BLOCKED])
        self.assertNotIn('IMPORT', [script for _, script in self.sends])

    def test_cancel_before_dispatch_and_during_unknown_preserves_facts(self):
        target = self.executor.capture_target('测量对象')
        cancel = threading.Event()
        cancel.set()
        result = self.run_task(target=target, cancel=cancel)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['attempts'], [])
        self.assertEqual(len(self.sends), 1)
        cancel.clear()
        def send(script, event, outcome):
            outcome.append(delivery.DELIVERED)
            if script != 'PING':
                event.set()
                return False, 'cancelled after delivery'
            return True, ''
        self.send_behavior = send
        result = self.run_task(target=target, cancel=cancel)
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['attempts'][0]['execution'], delivery.EXECUTION_UNKNOWN)

    def test_dialogue_stream_full_history_is_task_local_and_no_praat_lock(self):
        history = [{'role':'user' if i % 2 == 0 else 'assistant', 'content':str(i) + 'x' * 1200} for i in range(12)]
        config = cloud_config()
        original = copy.deepcopy(config)
        def reply(snapshot, state, **kwargs):
            self.assertEqual(state.dialogue_context, history)
            self.assertIsNot(state.dialogue_context, history)
            snapshot.api.model = 'task-only-change'
            acquired = []
            def probe():
                with m.PRAAT_LOCK:
                    acquired.append(True)
            thread = threading.Thread(target=probe)
            thread.start()
            thread.join(2)
            self.assertEqual(acquired, [True])
            kwargs['progress']('backend actual progress')
            kwargs['on_text']('hello ')
            kwargs['on_text']('world')
            state.report, state.status = 'hello world', 'complete'
        self.dialogue.side_effect = reply
        first = self.run_task(text='hello', config=config, history=history)
        second = self.run_task(text='hello', config=config, history=history)
        self.assertEqual(first['content'], 'hello world')
        self.assertEqual(second['content'], 'hello world')
        self.assertEqual(config, original)
        self.assertEqual(self.sends, [])
        self.assertEqual(len(self.runtimes), 2)
        self.assertIsNot(self.runtimes[0], self.runtimes[1])
        self.assertTrue(all(runtime._closed for runtime in self.runtimes))
        self.assertEqual([payload['text'] for kind, payload in self.events if kind == 'delta'], ['hello ', 'world', 'hello ', 'world'])

    def test_cancel_keeps_actual_streamed_content(self):
        cancel = threading.Event()
        def reply(config, state, **kwargs):
            kwargs['on_text']('partial real content')
            cancel.set()
            state.report, state.status = 'cancel marker', 'cancelled'
        self.dialogue.side_effect = reply
        result = self.run_task(text='你好', cancel=cancel)
        self.assertEqual(result['content'], 'partial real content')
        self.assertEqual(result['status'], 'cancelled')
        self.assertEqual(result['evidence'], [])

    def test_actual_graph_finite_correction_separate_report_and_evidence(self):
        self.cloud.side_effect = graph_turn
        calls = [('pitch', {'time':0.4}), ('pitch', {'time':0.5})]
        report_messages = []
        def response(messages, info):
            if info.function_tools:
                name, args = calls.pop(0) if calls else ('', {})
                return ModelResponse([ToolCallPart(name, args, tool_call_id='call-' + str(len(calls)))]) if name else ModelResponse([TextPart('工具阶段结束')])
            report_messages.extend(messages)
            return ModelResponse([TextPart(json.dumps({'analysis':'根据实际基频测量，本次能解释声音的阶段性趋势，但没有音频听感证据，不能判断具体录音缺陷。',
                'coverage':[{'item':'分析目标', 'status':'partial', 'explanation':'已有实际测量，仍然缺少音频与参考证据。'}]}, ensure_ascii=False))])
        self.model_factory = lambda _:FunctionModel(response)
        failed = False
        failure = ''
        def send(script, cancel, outcome):
            nonlocal failed, failure
            outcome.append(delivery.DELIVERED)
            failure = ''
            if script != 'PING' and not failed:
                failed, failure = True, 'bad parameter'
            return True, ''
        self.send_behavior = send
        with patch.object(chat, '_read_failure', side_effect=lambda:failure):
            result = self.run_task(text='分析目标')
        self.assertEqual(len(result['attempts']), 2, result)
        self.assertEqual([item['status'] for item in result['attempts']], ['failed', 'success'])
        self.assertEqual(len(result['evidence']), 1)
        self.assertEqual(result['evidence'][0]['tool'], 'pitch')
        self.assertNotIn('ToolReturnPart', str(report_messages))
        self.assertIn('根据实际基频', result['content'])
        self.assertTrue(self.runtimes[0]._closed)

    def test_local_reuses_turn_constraints_with_task_private_atomic_hook(self):
        actions = iter([([{'tool':'duration', 'arguments':{}}], ''), ([], 'actual local reply')])
        planner = SimpleNamespace(next=lambda:next(actions), observe=lambda *args:None,
                                  update_context=lambda *args:None)
        original_action = chat._execute_action
        original_run = chat._run_agent_turn
        config = AppConfig()
        config.qwen.limit_tokens = False
        history = [{'role':'user', 'content':'full' * 500} for _ in range(9)]
        with patch.object(chat, '_NativePlanner', return_value=planner) as factory, patch.object(m.qwen, 'planner_mode', return_value='native'):
            result = self.run_task(text='测量对象', config=config, history=history)
        self.assertEqual(result['content'], 'actual local reply')
        self.assertEqual(result['evidence'][0]['rows'], self.rows)
        self.assertEqual(factory.call_args.kwargs['history'], history)
        self.assertIs(chat._execute_action, original_action)
        self.assertIs(chat._run_agent_turn, original_run)
        self.cloud.assert_not_called()

    def test_attachment_text_wav_are_real_bounded_task_owned_and_cleaned(self):
        text_path, wav_path = self.root / 'input.txt', self.root / 'input.wav'
        text_path.write_text('真实用户材料', encoding='utf-8')
        with wave.open(str(wav_path), 'wb') as writer:
            writer.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            writer.writeframes(b'\x00\x00' * 800)
        owned = []
        def analyse(config, state, **kwargs):
            self.assertIn('真实用户材料', state.goal)
            materials = kwargs['materials']
            kwargs['prepare_audio']()
            self.assertEqual(materials.source_kind, 'file')
            self.assertNotEqual(materials.audio_path, wav_path)
            self.assertTrue(materials.bytes())
            owned.append(materials.directory)
            state.report, state.status = 'actual attached material response', 'complete'
        self.cloud.side_effect = analyse
        result = self.run_task(text='分析附件', target='', attachments=[
            {'path':str(text_path), 'name':'input.txt', 'mime':'text/plain'},
            {'path':str(wav_path), 'name':'input.wav', 'mime':'audio/wav'}])
        self.assertEqual(result['status'], 'complete')
        self.assertTrue(all(not directory.exists() for directory in owned))
        self.assertTrue(text_path.exists() and wav_path.exists())
        self.assertEqual(self.sends, [])
        text_path.write_bytes(b'x' * (m._MAX_TEXT_BYTES + 1))
        result = self.run_task(text='分析附件', target='', attachments=[{'path':str(text_path), 'mime':'text/plain'}])
        self.assertEqual(result['status'], 'failed')
        self.assertIn('1 MiB', result['content'])
        self.assertEqual(list((self.root / 'tasks').glob('task-*')), [])

    def test_original_filename_hint_and_user_correction_stay_bound_to_recording(self):
        paths = [self.root/'uuid-a.wav', self.root/'uuid-b.wav']
        for path in paths:
            with wave.open(str(path), 'wb') as writer:
                writer.setparams((1,2,8000,0,'NONE','not compressed'))
                writer.writeframes(b'\0\0'*800)
        def analyse(config, state, **kwargs):
            self.assertFalse(state.evidence)
            self.assertFalse(config.api.audio_input_enabled)
            state.report, state.status = '未上传音频，仅保留来源线索', 'complete'
        self.cloud.side_effect = analyse
        def run(path, name, text, prior=(), cfg=None):
            return self.executor.run(config=cfg or cloud_config(), text=text, history=[], target='',
                cancel=threading.Event(), emit=lambda *_:None, historical_evidence=prior,
                attachments=[dict(path=str(path),name=name,mime='audio/wav')])
        first = run(paths[0], 'こんにちは_take2.wav', '分析附件')
        self.assertEqual(first['material_metadata']['pronunciation_hint']['candidate'], 'こんにちは')
        corrected = run(paths[0], 'こんにちは_take2.wav', '我读的是「こんばんは」', [first])
        self.assertIsNone(corrected['material_metadata']['pronunciation_hint'])
        same = run(paths[0], 'こんにちは_take2.wav', '分析附件', [corrected])
        self.assertIn('こんばんは', same['material_metadata']['user_pronunciation'])
        self.assertIsNone(same['material_metadata']['pronunciation_hint'])
        other = run(paths[1], 'ありがとう.wav', '分析附件', [corrected])
        self.assertEqual(other['material_metadata']['pronunciation_hint']['candidate'], 'ありがとう')
        cfg = cloud_config()
        dictionary = self.root/'pronunciation.dict'
        dictionary.write_text('hello h e l o', encoding='utf-8')
        cfg.alignment.mfa.dictionary_path = str(dictionary)
        with_dictionary = run(paths[0], 'こんにちは_take2.wav', '分析附件', cfg=cfg)
        self.assertIsNone(with_dictionary['material_metadata']['pronunciation_hint'])

    def test_session_runtime_is_reused_and_released_on_delete(self):
        def reply(config, state, **kwargs):
            state.report, state.status = '正式答复', 'complete'
        self.dialogue.side_effect = reply
        for _ in range(2):
            self.executor.run(config=cloud_config(), text='你好', history=[], target='',
                cancel=threading.Event(), emit=lambda *_:None, session_id='session-a')
        self.assertEqual(len(self.runtimes), 1)
        self.assertFalse(self.runtimes[0]._closed)
        self.executor.release_session('session-a')
        self.assertTrue(self.runtimes[0]._closed)

    def test_audio_correction_uses_supplied_path_and_identity_comparison(self):
        config = cloud_config()
        config.api.audio_input_enabled = True
        def analyse(snapshot, state, **kwargs):
            snapshot.api.audio_input_enabled = False  # Actual graph correction mutates its task copy first.
            kwargs['correct_audio']('actual rejection')
            state.report, state.status = 'report', 'partial'
        self.cloud.side_effect = analyse
        with patch.object(m.caps, 'correct_audio_setting', return_value=False) as correction:
            self.run_task(text='分析目标', target='', config=config)
        correction.assert_called_once_with(self.root / 'config.json', m.caps.config_identity(config.api), 'actual rejection')
        self.assertTrue(any('配置身份已改变' in item['text'] for kind, item in self.events if kind == 'activity'))

    def test_actual_graph_uploads_attached_wav_without_praat(self):
        path = self.root / 'source.wav'
        with wave.open(str(path), 'wb') as writer:
            writer.setparams((1, 2, 8000, 0, 'NONE', 'not compressed'))
            writer.writeframes(b'\x00\x00' * 800)
        requests = []
        def response(messages, info):
            self.assertFalse(info.function_tools)
            requests.extend(messages)
            return ModelResponse([TextPart(json.dumps({'analysis':'针对用户提供的原始音频，本轮实际接收了音频材料。定性观察不能替代专业测量，因此无法给出未经测量的数值结论。',
                'coverage':[{'item':'分析附件', 'status':'partial', 'explanation':'已接收音频，但没有专业测量与参考证据。'}]}, ensure_ascii=False))])
        self.model_factory = lambda _:FunctionModel(response)
        self.cloud.side_effect = graph_turn
        config = cloud_config()
        config.api.audio_input_enabled = True
        result = self.run_task(text='分析附件', target='', config=config,
                               attachments=[{'path':str(path), 'mime':'audio/wav'}])
        self.assertTrue(result['metrics']['audio_input'], result)
        self.assertTrue(result['metrics']['audio_report_completed'])
        self.assertIn('BinaryContent', str(requests))
        self.assertEqual(result['audio_observations'][0]['source']['source_kind'], 'file')
        self.assertEqual(self.sends, [])
        self.assertEqual(list((self.root / 'tasks').glob('task-*')), [])

    def test_confirmed_rename_adopts_only_original_object_name(self):
        dispatcher = self.dispatcher()
        def send(script, cancel, outcome):
            outcome.append(delivery.DELIVERED)
            if script != 'PING':
                self.fresh = CONTEXT.replace('one', 'new_name')
            return True, ''
        self.send_behavior = send
        step = dispatcher.cloud_action('rename_object', {'object':1, 'new_name':'new_name'}, 1)
        self.assertTrue(step.ok, step.observation)
        self.assertEqual(step.execution, delivery.DELIVERED)
        self.assertEqual(dispatcher.context.objects[0].name, 'new_name')
        self.assertEqual(dispatcher.context.objects[0].selection, (0.2, 0.5))
        self.assertIn(m._IDENTITY_PREFIX, dispatcher.target)
        self.assertEqual(m._POISON, '')
        renamed = False
        def ambiguous_refresh(script, cancel, outcome):
            nonlocal renamed
            outcome.append(delivery.DELIVERED)
            if script != 'PING':
                renamed = True
            elif renamed:
                return False, 'rename completed, refresh completion unknown'
            return True, ''
        self.send_behavior = ambiguous_refresh
        second = dispatcher.cloud_action('rename_object', {'object':1, 'new_name':'again'}, 2)
        self.assertEqual(second.execution, delivery.DELIVERED)  # Known rename is not undone.
        self.assertEqual(dispatcher.attempts[-1]['refresh_deliveries'],
                         [delivery.DELIVERED, delivery.EXECUTION_UNKNOWN])
        count = len(self.sends)
        # 状态不明之后：先核对（这里的核对 ping 又失败，所以仍然封锁），不发测量脚本。
        self.assertEqual(dispatcher.cloud_action('duration', {}, 3).execution, delivery.EXECUTION_BLOCKED)
        self.assertGreater(len(self.sends), count)          # 只多了一次核对 ping
        self.assertEqual(self.sends[count][1], 'PING')

    def test_graph_policy_refusals_emit_real_tool_activity(self):
        def analyse(config, state, **kwargs):
            state.attempts.append({'tool':'pitch', 'arguments':{'time':-1},
                'status':'not_executed', 'reason':'actual schema validation refused negative time'})
            kwargs['progress']('实际上下文压缩摘录')
            state.report, state.status = 'actual report', 'partial'
        self.cloud.side_effect = analyse
        result = self.run_task(text='分析目标', target='')
        self.assertEqual(result['attempts'][0]['status'], 'not_executed')
        self.assertEqual([item['type'] for item in result['activities']], ['compression', 'tool'])
        self.assertEqual(result['evidence'], [])

    def test_close_cancels_only_owned_active_tasks_and_forbids_new_runs(self):
        other = threading.Event()
        task = threading.Event()
        self.executor._active.add(task)
        self.executor.close()
        self.assertTrue(task.is_set())
        self.assertFalse(other.is_set())
        with self.assertRaises(RuntimeError):
            self.executor.capture_target('hello')
        with self.assertRaises(RuntimeError):
            self.run_task(text='hello', target='')

    def cleanup_dispatcher(self):
        dispatcher = self.dispatcher()
        dispatcher.attempts = [
            {'tool': 'extract_part', 'status': 'success', 'arguments': {},
             'rows': ['已截取片段：片段（id 145）']},
            {'tool': 'pitch_statistics', 'status': 'success',
             'arguments': {'object': 145}, 'rows': ['平均基频 = 100 Hz']},
        ]
        TemporaryObjectCleanupTests.appear(dispatcher, 145)
        self.fresh = CONTEXT + '145\tSound\t片段\t0\t\t\n'
        return dispatcher

    def test_cleanup_reconciles_real_snapshot_after_remove_with_or_without_marker(self):
        for marker_received in (True, False):
            with self.subTest(marker_received=marker_received):
                dispatcher = self.cleanup_dispatcher()
                def send(script, cancel, outcome):
                    if 'Remove' in script:
                        # Praat 的快照已变，dispatcher 的内存快照仍有 145。
                        self.fresh = CONTEXT
                        outcome.append(delivery.DELIVERED if marker_received
                                       else delivery.EXECUTION_UNKNOWN)
                        return marker_received, '' if marker_received else 'completion not confirmed'
                    outcome.append(delivery.DELIVERED)
                    return True, ''
                self.send_behavior = send
                self.assertEqual(dispatcher.cleanup_created_objects('测量片段的基频'), [145])
                self.assertNotIn(145, {row.id for row in dispatcher.context.objects})
                self.assertNotIn(145, {row.id for row in tools.parse_object_context(dispatcher.target)})
                self.assertEqual(m._POISON, '')
                self.assertEqual(self.sends[-1][1], 'PING')

    def test_cleanup_does_not_report_objects_still_in_verified_snapshot(self):
        dispatcher = self.cleanup_dispatcher()
        self.assertEqual(dispatcher.cleanup_created_objects('测量片段的基频'), [])
        self.assertIn(145, {row.id for row in dispatcher.context.objects})

    def test_cleanup_failed_reconciliation_is_not_a_verified_empty_result(self):
        dispatcher = self.cleanup_dispatcher()
        def send(script, cancel, outcome):
            if 'Remove' in script:
                self.fresh = CONTEXT
            outcome.append(delivery.EXECUTION_UNKNOWN)
            return False, 'completion not confirmed'
        self.send_behavior = send
        with self.assertRaises(BranchExit):
            dispatcher.cleanup_created_objects('测量片段的基频')
        self.assertIn(145, {row.id for row in dispatcher.context.objects})

    def test_cleanup_still_rejects_disappearance_of_original_objects(self):
        dispatcher = self.cleanup_dispatcher()
        self.fresh = CONTEXT.replace('2\tSound\ttwo\t0\t\t\n', '')
        with self.assertRaisesRegex(BranchExit, '原对象 2'):
            dispatcher.cleanup_created_objects('测量片段的基频')

    def test_cleanup_metrics_record_candidates_and_verified_removals(self):
        dispatcher = self.cleanup_dispatcher()
        def analyse(config, state, **kwargs):
            state.report, state.status = 'actual report', 'complete'
        self.cloud.side_effect = analyse
        def send(script, cancel, outcome):
            if 'Remove' in script:
                self.fresh = CONTEXT
                outcome.append(delivery.EXECUTION_UNKNOWN)
                return False, 'completion not confirmed'
            outcome.append(delivery.DELIVERED)
            return True, ''
        self.send_behavior = send
        with patch.object(m, '_Dispatcher', return_value=dispatcher):
            result = self.run_task(text='分析目标', target=dispatcher.target)
        self.assertEqual(result['metrics']['cleanup_candidates'], [145])
        self.assertEqual(result['metrics']['cleaned_objects'], [145])

    def test_cleanup_metrics_distinguish_no_candidates_from_cancelled(self):
        result = self.run_task()
        self.assertEqual(result['metrics']['cleanup_candidates'], [])
        cancel = threading.Event()
        cancel.set()
        result = self.run_task(target=CONTEXT, cancel=cancel)
        self.assertNotIn('cleanup_candidates', result['metrics'])

    def test_cleanup_metrics_preserve_candidates_when_reconciliation_fails(self):
        dispatcher = self.cleanup_dispatcher()
        def analyse(config, state, **kwargs):
            state.report, state.status = 'actual report', 'complete'
        self.cloud.side_effect = analyse
        def send(script, cancel, outcome):
            if 'Remove' in script:
                self.fresh = CONTEXT
            outcome.append(delivery.EXECUTION_UNKNOWN)
            return False, 'completion not confirmed'
        self.send_behavior = send
        with patch.object(m, '_Dispatcher', return_value=dispatcher):
            result = self.run_task(text='分析目标', target=dispatcher.target)
        self.assertEqual(result['metrics']['cleanup_candidates'], [145])
        self.assertNotIn('cleaned_objects', result['metrics'])
        self.assertIn('completion not confirmed', result['metrics']['cleanup_errors'][0])
        self.assertTrue(any('清理结果未能确认' in item.get('text', '')
                            for item in result['activities']))


class TemporaryObjectCleanupTests(unittest.TestCase):
    """模型为「只看这一段」自建的对象，测量完要收掉。

    2026-10-07 实测：一轮 `extract_part` 截出的片段留在对象列表里，下一轮的基频统计
    顺手把它的时长也量了。用户明确要求创建的对象不能动。
    """

    def dispatcher(self, attempts):
        from praat_ai.modern_execution import _Dispatcher
        target = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\ttone\t1\t\t\n'
        dispatcher = _Dispatcher(target, threading.Event(), lambda *a, **k: None, Path('.'))
        dispatcher.attempts = attempts
        return dispatcher

    @staticmethod
    def appear(dispatcher, identifier, name='片段'):
        """模拟 ensure()：新建的对象出现在后续快照里，但不在任务开始时的原始列表里。"""

        from dataclasses import replace
        row = tools.ObjectRow(identifier, 'Sound', name, False)
        dispatcher.context = replace(dispatcher.context,
                                     objects=dispatcher.context.objects + (row,))
        return dispatcher

    def test_only_intermediate_objects_are_collected(self):
        dispatcher = self.dispatcher([
            {'tool': 'extract_part', 'status': 'success', 'arguments': {'start': 0.1, 'end': 0.4},
             'rows': ['已截取片段：片段（id 145），区间 0.100–0.400 秒，共 0.300 秒']},
            {'tool': 'spectral_emphasis', 'status': 'success', 'arguments': {'object': 145},
             'rows': ['谱强调 = 0.9 dB']},
            # 另一个新建对象没有被后续测量用到：它不是「临时中间对象」，不动。
            {'tool': 'create_sound', 'status': 'success', 'arguments': {},
             'rows': ['已创建300.0 Hz 纯音：新建声音（id 146），时长 1.000 秒']},
        ])
        self.assertEqual(dispatcher.created_ids(), [145])

    def test_goal_that_asks_for_the_object_keeps_it(self):
        dispatcher = self.dispatcher([
            {'tool': 'extract_part', 'status': 'success', 'arguments': {},
             'rows': ['已截取片段：片段（id 145），区间 0.100–0.400 秒']},
            {'tool': 'pitch_statistics', 'status': 'success', 'arguments': {'object': 145},
             'rows': ['基频统计（0.100–0.400 秒）：平均 100.0 Hz']},
        ])
        self.assertEqual(dispatcher.created_ids(), [145])
        self.assertEqual(dispatcher.cleanup_created_objects('把 0.1 到 0.4 秒截取出来'), [])

    def test_cleanup_removes_the_fragment_and_updates_the_snapshot(self):
        dispatcher = self.dispatcher([
            {'tool': 'extract_part', 'status': 'success', 'arguments': {},
             'rows': ['已截取片段：片段（id 145），区间 0.100–0.400 秒']},
            {'tool': 'pitch_statistics', 'status': 'success', 'arguments': {'object': 145},
             'rows': ['基频统计（0.100–0.400 秒）：平均 100.0 Hz']},
        ])
        sent = []

        def execute(script):
            sent.append(script)
            # 真机上这条 Remove 会让对象从快照里消失；清理逻辑以快照为准。
            from dataclasses import replace as _replace
            dispatcher.context = _replace(dispatcher.context, objects=tuple(
                row for row in dispatcher.context.objects if row.id != 145))
            return True, [], ''

        dispatcher.execute = execute
        dispatcher.ensure = lambda **kwargs: True
        self.appear(dispatcher, 145)
        removed = dispatcher.cleanup_created_objects('测量这一段 0.1 到 0.4 秒的平均基频（只算这一段）')
        self.assertEqual(removed, [145])
        self.assertTrue(any('selectObject: 145' in script and 'Remove' in script for script in sent))
        self.assertNotIn(145, {row.id for row in dispatcher.context.objects})


    def test_cleanup_reports_removal_even_when_the_marker_is_lost(self):
        """Praat 慢到拿不到完成标记，但脚本已经删掉了对象：以快照为准如实记账。"""

        dispatcher = self.dispatcher([
            {'tool': 'extract_part', 'status': 'success', 'arguments': {},
             'rows': ['已截取片段：片段（id 145），区间 0.100–0.400 秒']},
            {'tool': 'pitch_statistics', 'status': 'success', 'arguments': {'object': 145},
             'rows': ['基频统计（0.100–0.400 秒）：平均 100.0 Hz']},
        ])
        self.appear(dispatcher, 145)

        def execute(script):
            # 模拟「脚本执行了，但完成标记没等到」。
            from dataclasses import replace as _replace
            dispatcher.context = _replace(dispatcher.context, objects=tuple(
                row for row in dispatcher.context.objects if row.id != 145))
            return False, [], 'completion not confirmed'

        def ensure(**kwargs):
            return True

        dispatcher.execute = execute
        dispatcher.ensure = ensure
        self.assertEqual(dispatcher.cleanup_created_objects('测量这一段 0.1 到 0.4 秒的平均基频'), [145])


if __name__ == '__main__':
    unittest.main()
