"""投递事实（delivery）回归：脚本没送到 Praat 时不能当成「执行状态不明」。

现场（2026-10-02 19:44，用户原话「测量选中这段的vot」）：前端是在受限环境里启动的，
``%APPDATA%\\Praat\\Message.txt`` 写不进去 → 快照导出失败被记成「状态不明」→ 同一轮
后面的 ``vot`` 一律拒绝投递 → 最终报告把「投递失败」解释成「模型不支持音频」，
一个数值都没测到。这里钉住三件事：

1. ``_send_script`` 要能说清「没送出去」和「送出去了没等到结果」；
2. 「没送出去」是确定的失败，只允许一次有区别的修正，后面的工具照常执行；
3. 「送出去了没等到结果」仍然按设计停下来，并且真实失败原因要进报告上下文。
"""
import json
import queue
import tempfile
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import chat, delivery, tools
from praat_ai.cloud_agent import blocked_attempts, report_context, run_cloud_turn
from praat_ai.cloud_workflow import process_cloud
from praat_ai.config import AppConfig
from praat_ai.conversation_store import ConversationStore
from praat_ai.escape_policy import AnalysisState, Budget
from praat_ai.materials import TaskMaterials

CONTEXT = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tあなた\t1\t0.437403\t0.635206\n'
GOAL = '测量选中这段的vot'
MESSAGE_FILE_ERROR = ("无法写入 Praat 消息文件（C:\\Users\\x\\AppData\\Roaming\\Praat\\Message.txt）："
                      "[Errno 13] Permission denied: 'C:\\\\Users\\\\x\\\\AppData\\\\Roaming\\\\Praat\\\\Message.txt'")
BLOCKED_REASON = '程序化测试里的「本轮已停止投递」'
VOT_LINE = 'VOT 估计值 = 0.0400 秒（40.1 毫秒）：爆破 0.437 秒 → 浊音起始 0.477 秒'


class SendScriptContractTests(unittest.TestCase):
    """``_send_script`` 的投递事实出口。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        for name, value in (('script_path', lambda request_id='':self.root / 'cmd.praat'),
                            ('_clear_result_files', lambda:None), ('prune_command_scripts', lambda:0),
                            ('send_log_path', lambda:self.root / 'send.log')):
            self.stack.enter_context(patch.object(chat, name, side_effect=value))

    def test_write_failure_is_reported_as_not_delivered(self):
        outcome = []
        with patch.object(chat.sendpraat, 'deliver', return_value=(False, MESSAGE_FILE_ERROR)):
            ok, note = chat._send_script('Praat.exe', 'selectObject: 1\n', outcome=outcome)
        self.assertFalse(ok)
        self.assertEqual(note, MESSAGE_FILE_ERROR)
        self.assertEqual(outcome, [delivery.NOT_DELIVERED])

    def test_delivered_without_completion_is_not_confused_with_not_delivered(self):
        outcome = []
        with patch.object(chat.sendpraat, 'deliver', return_value=(True, '')), \
             patch.object(chat, '_wait_for_result', return_value=(False, 'Praat 在 25 秒内没有执行这个脚本。')):
            ok, _ = chat._send_script('Praat.exe', 'selectObject: 1\n', outcome=outcome)
        self.assertFalse(ok)
        self.assertEqual(outcome, [delivery.DELIVERED])

    def test_send_mode_defaults_to_no_declaration_for_other_callers(self):
        with patch.object(chat.sendpraat, 'deliver', return_value=(False, MESSAGE_FILE_ERROR)):
            ok, _ = chat._send_script('Praat.exe', 'selectObject: 1\n')
        self.assertFalse(ok)  # 不传 outcome 时行为不变


class ReportContextTests(unittest.TestCase):
    @staticmethod
    def guidance(config, state, phase='report'):
        from praat_ai.cloud_agent import report_instructions, planner_system
        from praat_ai.session_context import PhaseContext
        from praat_ai.skill_registry import load
        context = PhaseContext()
        load(context, phase, state)
        system = report_instructions(config, state) if phase == 'report' else planner_system(config)
        return system + '\n'.join(p.content for m in context.messages for p in m.parts)
    """报告上下文必须带上真实失败原因（没成的调用）。"""

    def test_blocked_attempts_keep_the_real_reason(self):
        state = AnalysisState(GOAL, CONTEXT)
        state.attempts = [
            {'tool':'export_original_audio', 'status':'failed', 'reason':MESSAGE_FILE_ERROR},
            {'tool':'vot', 'status':'failed', 'reason':MESSAGE_FILE_ERROR},
            {'tool':'vot', 'status':'not_executed', 'reason':'复用成功测量'},
            {'tool':'pitch', 'status':'success', 'reason':''},
        ]
        rows = blocked_attempts(state)
        self.assertEqual([row['tool'] for row in rows], ['export_original_audio', 'vot'])
        self.assertIn(MESSAGE_FILE_ERROR, report_context(state)['blocked_attempts'][0]['reason'])

    def test_failure_instructions_appear_only_when_something_failed(self):
        """失败说明必须真的能到模型，而且只在本轮有失败记录时才付这笔提示词开销。"""
        from praat_ai.cloud_agent import report_instructions
        config = AppConfig()
        config.api.model = 'review-fixture'
        clean = AnalysisState('测量选中这段的vot', CONTEXT)
        self.assertNotIn('skill=failure', self.guidance(config, clean))
        failed = AnalysisState('测量选中这段的vot', CONTEXT)
        failed.attempts = [{'tool':'export_original_audio', 'status':'failed', 'reason':MESSAGE_FILE_ERROR}]
        text = self.guidance(config, failed)
        self.assertIn('具体失败原因', text)
        self.assertIn('成功测量照常交付', text)
        self.assertIn('Praat 测量仍使用桌面声音对象', text)
        paused = AnalysisState('测量选中这段的vot', CONTEXT)
        paused.failures = {'vot':2}
        self.assertIn('skill=failure', self.guidance(config, paused))
        self.assertEqual(report_instructions(config, clean), report_instructions(config, failed))

    def test_report_places_segments_from_evidence_without_a_mandatory_section(self):
        """定位使用实际边界，单项测量直接交付结果。"""

        for goal in ('与标准音对比，指出发音不足之处与待改进点', '分析选中这段的vot'):
            with self.subTest(goal=goal):
                text = self.guidance(AppConfig(), AnalysisState(goal, CONTEXT))
                self.assertIn('实际对齐区间或人工标注', text)
                self.assertIn('不凭对象名猜音素', text)
                self.assertIn('单项声学测量直接给出所求结果', text)
                self.assertNotIn('报告开头先写一节', text)
        plain = self.guidance(AppConfig(), AnalysisState('直接听原始音频', CONTEXT))
        self.assertNotIn('skill=placement', plain)

    def test_report_refuses_an_accent_claim_without_a_pitch_contour(self):
        """2026-10-04：只拿全段平均基频 81.6 Hz 就断言「あなた」是平板型，并据此给出
        「あ低な高」的练习；同一段的更早一次量过前半 84.98 / 后半 79.06，结论正好相反。"""

        config = AppConfig()
        prosody = self.guidance(config, AnalysisState('指出语调不足之处', CONTEXT))
        self.assertIn('不要下音调结论', prosody)
        comparison = self.guidance(config, AnalysisState('与东京标准音对比，指出发音的不足之处', CONTEXT))
        self.assertIn('不要下音调结论', comparison)
        plain = self.guidance(config, AnalysisState('直接听原始音频', CONTEXT))
        self.assertNotIn('不要下音调结论', plain)
        # 规划阶段也要先量走向，否则报告阶段只剩「缺测量」可说。
        self.assertIn('先用工具量出音高走向', self.guidance(config, AnalysisState('指出语调不足之处', CONTEXT), 'planner'))
        self.assertNotIn('先用工具量出音高走向', self.guidance(config, AnalysisState('直接听原始音频', CONTEXT), 'planner'))

    def test_notation_skill_carries_number_writing_and_only_when_measured(self):
        """数值写法从守卫的硬规则挪进 skill=notation：有测量才付这笔提示词开销。

        2026-10-07：22 次真实提问里 12 次因为「推导没写成 calc 占位式」「一般知识没标注」
        之类写法问题连吃两次拒绝，最后整份报告被换掉。写法属于提示词层，规则和例子交给
        skill，守卫只在第一次拒一次、第二次改成遮数值交付。
        """

        from praat_ai.cloud_agent import AnalysisReport, Coverage, repaired_report
        from praat_ai.escape_policy import Evidence
        config = AppConfig()
        empty = AnalysisState('测量当前声音平均基频', CONTEXT)
        self.assertNotIn('skill=notation', self.guidance(config, empty))
        measured = AnalysisState('测量当前声音平均基频', CONTEXT)
        measured.evidence = [Evidence('pitch_statistics', {'object': 1}, ['基频统计（0.000–1.000 秒）：平均 220.000 Hz'], CONTEXT)]
        text = self.guidance(config, measured)
        self.assertIn('skill=notation', text)
        self.assertIn('{{calc:', text)
        self.assertIn('一般知识', text)
        # 直接写出来的推导值会被遮掉，但正文和结论留下。
        measured.metrics['rejected_values'] = ['0.215386 秒']
        draft = AnalysisReport(analysis='该选段时长为 0.215386 秒，说明节奏稳定。\n报告依据已有记录，仍须核对范围。',
                               coverage=[Coverage(item='测量当前声音平均基频', status='partial',
                                                  explanation='已有解释和实际证据，仍应核对录音范围。')])
        repaired = repaired_report(draft, '数值“0.215386 秒”未匹配同对象/范围/量名的测量。',
                                   measured, measured)
        self.assertNotIn('0.215386', repaired.analysis)
        self.assertIn('说明节奏稳定', repaired.analysis)
        self.assertTrue(measured.metrics['report_repaired'])

    def test_report_context_carries_the_users_own_words_about_themselves(self):
        """用户自述（说话人/录音条件）要随报告上下文一起给；没有自述时不加这个键。"""

        from praat_ai.cloud_agent import report_context
        state = AnalysisState('指出语调不足之处', CONTEXT)
        state.dialogue_context = [
            {'role': 'user', 'content': '与东京标准音对比，指出发音的不足之处'},
            {'role': 'assistant', 'content': '若为男性…若为女性…'},
            {'role': 'user', 'content': '我是成年男性，针对这个结论可以做什么练习'},
            {'role': 'user', 'content': '历史专业证据与投递事实（保留来源/原范围；不能当作本轮新测量）：[{"tool":"vot"}]'},
        ]
        self.assertEqual(report_context(state)['user_facts'],
                         ['我是成年男性，针对这个结论可以做什么练习'])
        self.assertNotIn('user_facts', report_context(AnalysisState('指出语调不足之处', CONTEXT)))


class StaircaseDeliveryTests(unittest.TestCase):
    """走一遍真实的 process_cloud 阶梯（只有投递层是替身）。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = self.root / 'source.wav'
        source.write_bytes(b'RIFF')
        self.material = TaskMaterials(self.root / 'tasks')
        self.material.snapshot_attempted = False
        self.addCleanup(self.material.close)
        self.cfg = AppConfig()
        self.cfg.api.enabled = True
        self.cfg.api.base_url, self.cfg.api.model = 'https://test.invalid/v1', 'delivery-fixture'
        self.cfg.api.limit_tokens = False
        self.cfg.api.audio_input_enabled = True
        self.fresh = [CONTEXT]
        self.scripts = []
        self.report_requests = []
        self.store = ConversationStore(self.root / 'records.sqlite')

    def window(self):
        window = chat.ChatWindow.__new__(chat.ChatWindow)
        window.config, window.messages, window.history = self.cfg, queue.Queue(), []
        window.cancel_event = threading.Event()
        window.pending_analysis = None
        window.task_materials, window.current_materials = [self.material], self.material
        window.store, window.session_id = self.store, self.store.new_session()
        window._closed = False
        return window

    def model(self, calls):
        scripts, report_requests = self.scripts, self.report_requests
        pending = list(calls)
        def respond(messages, info):
            if info.function_tools:
                if pending:
                    name, args = pending.pop(0)
                    return ModelResponse([ToolCallPart(name, args, tool_call_id='call-' + name)])
                return ModelResponse([TextPart('工具阶段结束')])
            report_requests.append(messages)
            return ModelResponse([TextPart(json.dumps({
                'analysis':'本轮没有取得测量数值：投递失败的原因已记录，需要先让 Praat 收到指令再重试，'
                           '此前不能声称测量过，也不能把投递故障说成模型能力不足。',
                'coverage':[{'item':GOAL, 'status':'missing',
                             'explanation':'脚本没有送到 Praat，本轮没有取得任何测量数值。',
                             'missing_evidence':['measurement']}],
            }, ensure_ascii=False))])
        return FunctionModel(respond)

    def patches(self, stack, send):
        for name, value in (
            ('object_context', lambda:self.fresh[0]), ('_request_context', lambda text, goal:text),
            ('result_path', lambda:self.root / 'results.tsv'), ('state_path', lambda:self.root / 'state.txt'),
            ('runtime_dir', lambda:self.root), ('praat_executable', lambda:self.root / 'Praat.exe'),
            ('praat_process_ids', lambda *_:[101]), ('praat_process_running_from', lambda *_:True),
            ('refresh_object_context', lambda *args, **kwargs:(True, 'refreshed')),
            ('_send_script', send), ('_read_failure', lambda:''),
        ):
            stack.enter_context(patch.object(chat, name, side_effect=value))

    def run_turn(self, goal, send, calls, *, audio=None):
        window = self.window()
        before = len(self.store.records(window.session_id))
        with ExitStack() as stack:
            self.patches(stack, send)
            stack.enter_context(patch('praat_ai.cloud_agent.run_cloud_turn',
                side_effect=lambda config, state, **kwargs: run_cloud_turn(
                    config, state, model=self.model(calls), **kwargs)))
            process_cloud(window, goal)
        turns = [item['payload'] for item in self.store.records(window.session_id)[before:] if item['kind'] == 'turn']
        return window.analysis_state, turns[0]

    def test_not_delivered_snapshot_does_not_stop_the_measurement(self):
        """快照导出「没送出去」之后，vot 仍然要真的投递并取得证据。"""
        def send(executable, script, **kwargs):
            outcome = kwargs.get('outcome')
            self.scripts.append(script)
            if 'Save as WAV file' in script:
                if outcome is not None:
                    outcome.append(delivery.NOT_DELIVERED)
                return False, MESSAGE_FILE_ERROR
            if outcome is not None:
                outcome.append(delivery.DELIVERED)
            return True, ''
        with patch.object(chat, '_read_results', side_effect=lambda:([VOT_LINE] if self.scripts and
                'Save as WAV file' not in self.scripts[-1] else ['ok'])):
            state, turn = self.run_turn(GOAL, send, [('vot', {'from':0.437403, 'to':0.635206, 'object':'Sound あなた'})])
        exported = [script for script in self.scripts if 'Save as WAV file' in script]
        measured = [script for script in self.scripts if 'Save as WAV file' not in script]
        self.assertEqual(len(exported), 1, '快照导出应该只试一次')
        self.assertEqual(len(measured), 1, '导出失败不能拦住 vot 的投递：' + state.reason)
        self.assertTrue(any(VOT_LINE in row for item in state.evidence for row in item.rows), state.evidence)
        self.assertNotIn('状态不明', state.reason)
        self.assertEqual([a['execution'] for a in state.attempts if a['tool'] == 'vot'], [delivery.DELIVERED])
        # 真实失败原因进了报告请求，模型才有机会把它讲清楚
        self.assertTrue(self.report_requests)
        self.assertIn('无法写入 Praat 消息文件', str(self.report_requests[-1]))

    def test_audio_capability_off_skips_the_eager_snapshot(self):
        """音频能力关闭时不该为用不上的材料多投一条脚本。"""
        self.cfg.api.audio_input_enabled = False
        sent = []
        def send(executable, script, **kwargs):
            outcome = kwargs.get('outcome')
            sent.append(script)
            if outcome is not None:
                outcome.append(delivery.DELIVERED)
            return True, ''
        with patch.object(chat, '_read_results', return_value=[VOT_LINE]):
            self.run_turn(GOAL, send, [('vot', {'from':0.437403, 'to':0.635206, 'object':'Sound あなた'})])
        self.assertEqual(len(sent), 1, sent)
        self.assertNotIn('Save as WAV file', sent[0])

    def test_delivered_without_completion_still_stops_and_keeps_the_reason(self):
        """送出去了没等到完成标记：停下来，并且保留超时原文。"""
        self.cfg.api.audio_input_enabled = False
        timeout = 'Praat 在 25 秒内没有执行这个脚本。常见原因：Praat 里有没关掉的对话框。'
        def send(executable, script, **kwargs):
            outcome = kwargs.get('outcome')
            self.scripts.append(script)
            if outcome is not None:
                outcome.append(delivery.DELIVERED)
            return False, timeout
        state, turn = self.run_turn(GOAL, send, [('vot', {'from':0.437403, 'to':0.635206}),
                                                ('vot', {'from':0.4, 'to':0.6})])
        self.assertEqual(len(self.scripts), 1, '状态不明之后不能继续投递')
        self.assertEqual([a['status'] for a in state.attempts if a['tool'] == 'vot'], ['unknown'])
        self.assertEqual([a['execution'] for a in state.attempts if a['tool'] == 'vot'],
                         [delivery.EXECUTION_UNKNOWN])
        self.assertIn('没有执行这个脚本', state.reason)

    def test_snapshot_timeout_blocks_further_delivery_as_not_executed(self):
        """快照超时（真的状态不明）之后，后面的调用是「未执行」，不是第二条「状态不明」。"""
        timeout = 'Praat 在 25 秒内没有执行这个脚本。'
        def send(executable, script, **kwargs):
            outcome = kwargs.get('outcome')
            self.scripts.append(script)
            if outcome is not None:
                outcome.append(delivery.DELIVERED)
            return False, timeout
        state, turn = self.run_turn(GOAL, send, [('vot', {'from':0.437403, 'to':0.635206}),
                                                ('pitch', {'time':0.5})])
        self.assertEqual(len(self.scripts), 1, '状态不明之后不能继续投递')
        vot = [a for a in state.attempts if a['tool'] == 'vot']
        self.assertEqual([a['status'] for a in vot], ['not_executed'])
        self.assertEqual([a['execution'] for a in vot], [delivery.EXECUTION_BLOCKED])
        self.assertIn('没有执行这个脚本', state.reason)
        self.assertIn('没有执行这个脚本', str(self.report_requests[-1]))

    def test_blocked_call_is_not_a_second_unknown(self):
        """本轮已停止投递时，那次调用是「未执行」，不是又一条「状态不明」。"""
        blocked = chat.AgentStep(1, 'vot', {'from':0.4, 'to':0.6}, False, BLOCKED_REASON, 'selectObject: 1\n',
                                 [], '', delivery.EXECUTION_BLOCKED)
        state = AnalysisState(GOAL, CONTEXT, budget=Budget(4096, 800))
        seen = []
        def action(name, arguments, index):
            seen.append(name)
            return blocked
        run_cloud_turn(self.cfg, state, execute_action=action, cancel=threading.Event(),
                       model=self.model([('vot', {'from':0.4, 'to':0.6}), ('pitch', {'time':0.5})]),
                       tool_schemas=[schema for schema in tools.tool_schemas()
                                     if schema['function']['name'] in ('vot', 'pitch')])
        self.assertEqual(seen, ['vot'], '停止投递后不该再调用工具')
        self.assertEqual([a['status'] for a in state.attempts], ['not_executed'])
        self.assertEqual(state.attempts[0]['execution'], delivery.EXECUTION_BLOCKED)
        self.assertNotIn('状态不明', state.reason)


if __name__ == '__main__':
    unittest.main()
