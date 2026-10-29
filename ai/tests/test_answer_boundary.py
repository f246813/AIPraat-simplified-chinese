"""Offline end-to-end answer boundaries: real SDK + isolated localhost SSE."""
import asyncio
import threading
import unittest

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from praat_ai.cloud_agent import CloudSession, AnalysisReport, Coverage, entry_mode, run_dialogue_turn
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai import delivery_text, report_audit
from test_dialogue_protocol import endpoint
import test_dialogue_protocol as dialogue_tests
from test_dialogue_stream import config
import test_tool_guards as tool_tests
from test_tool_guards import CONTEXT


LEAK = '按照要求我不得随便猜定论，将测得的参数填入公式后提交给用户。'
GOOD = '日语的这个元音通常不像英语的对应元音那样突出圆唇。可以先放松双唇，缓慢跟读，再逐渐连到短词中练习。'


class AnswerBoundaryTests(unittest.TestCase):
    def dialogue(self, bodies, request_limit=8):
        state = AnalysisState('请用自己的知识讲讲日语元音的练习方法，不测录音。', '')
        state.budget.request_limit = request_limit
        chunks = []
        with endpoint(bodies=bodies, chunk_delay=0) as (base, requests, _), \
                dialogue_tests.DialogueProtocolTests().redirect(base):
            run_dialogue_turn(config(), state, kind='advice', cancel=threading.Event(), on_text=chunks.append)
        return state, chunks, requests

    def test_dialogue_retries_echo_once_without_streaming_it(self):
        state, chunks, requests = self.dialogue([LEAK, GOOD])
        self.assertEqual(len(requests), 2)
        self.assertEqual(state.report, GOOD)
        self.assertEqual(''.join(chunks), GOOD)
        self.assertFalse(delivery_text.has_answer_leak(''.join(chunks)))
        self.assertEqual(state.metrics['guard_blocks']['dialogue.prompt_echo'], 1)
        self.assertEqual(len(state.metrics['request_timings']), 2)
        first, second = requests
        self.assertEqual(first['messages'], second['messages'][:len(first['messages'])])

    def test_dialogue_repeated_echo_is_partial_and_never_delivered(self):
        state, chunks, requests = self.dialogue([LEAK, LEAK])
        self.assertEqual(len(requests), 2)
        self.assertEqual(state.status, 'partial')
        self.assertNotIn('按照要求', state.report)
        self.assertNotIn('提交给用户', ''.join(chunks))
        self.assertFalse(delivery_text.has_answer_leak(state.report))

    def test_dialogue_echo_does_not_exceed_request_budget(self):
        state, chunks, requests = self.dialogue([LEAK], request_limit=1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(state.requests, 1)
        self.assertEqual(state.status, 'partial')
        self.assertNotIn(LEAK, ''.join(chunks))
    def test_placeholder_is_not_streamed(self):
        state, chunks, requests = self.dialogue(['{{calc:E1.q1-E2.q1|Hz}}', GOOD])
        self.assertEqual(len(requests), 2)
        self.assertNotIn('{{calc:', ''.join(chunks))
        self.assertEqual(state.report, GOOD)

    def test_report_echo_is_rejected_without_repeating_measurement(self):
        state, actions, _, reports = tool_tests.NativeHookTests().run_case(
            [('pitch', {'time': .5})], reports=[LEAK, GOOD])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(reports), 2)
        self.assertEqual(state.report, "## 分析结论\n\n" + GOOD)
        self.assertEqual(state.metrics['guard_blocks']['report.prompt_echo'], 1)

    def test_repeated_report_echo_is_not_rescued_as_user_prose(self):
        state, actions, _, reports = tool_tests.NativeHookTests().run_case(
            [('pitch', {'time': .5})], reports=[LEAK, LEAK])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(reports), 2)
        self.assertEqual(state.status, 'partial')
        self.assertNotIn(LEAK, state.report)
        self.assertTrue(state.evidence)

    def test_explanatory_range_request_keeps_tools(self):
        for goal in ('解释一下 0.1 到 0.3 秒的共振峰', '解释一下 0.1–0.3 秒平均基频'):
            with self.subTest(goal=goal):
                self.assertEqual(entry_mode(goal, CONTEXT), 'L0')

    def test_number_repair_uses_shared_request_metrics(self):
        bad = '本录音基频是 999 Hz [E1]。这个结果能够用于评估音高，但是仍缺少可靠的参照录音材料。'
        state, actions, _, calls = tool_tests.NativeHookTests().run_case(
            [('pitch', {'time': .5})], reports=[bad, bad])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(calls), 3)  # Two full reports, then one narrow repair.
        timings = state.metrics['request_timings']
        repairs = [item for item in timings if item['phase'] == 'report_repair']
        self.assertEqual(len(repairs), 1)
        self.assertEqual(state.requests, len(timings))
        self.assertEqual(state.metrics['phases']['report_repair']['requests'], 1)
        self.assertNotIn('999 Hz', state.report)
        self.assertIn('依赖这些数值的判断暂不能作为结论', state.report)

    def test_number_repair_cannot_bypass_exhausted_budget(self):
        cfg = config()
        state = AnalysisState('测量基频', CONTEXT, evidence=[Evidence('pitch', {'object': 1, 'time': .5}, ['基频 = 220 Hz'], 'fixture')])
        report = AnalysisReport(analysis='本录音基频是 999 Hz [E1]。这个结果能够用于评估音高，但是仍缺少可靠的参照录音材料。',
                                coverage=[Coverage(item=state.goal, status='partial', explanation='仍然缺少完整参考测量。')])
        report_audit.audit_report(state, report)
        state.metrics['rejected_report'] = report.analysis
        calls = []
        def respond(messages, info):
            calls.append(messages)
            return ModelResponse([TextPart('1 => 基频为 220 Hz。[E1]')])
        session = CloudSession(cfg, state, None, threading.Event(), None,
                               FunctionModel(respond), [], None, None, None)
        session.phase = 'report'
        state.requests = state.budget.request_limit
        result = asyncio.run(session.repair_numbers(report, state))
        self.assertEqual(calls, [])
        self.assertEqual(state.requests, state.budget.request_limit)
        self.assertIs(result, report)
        self.assertEqual(session.phase, 'report')
        self.assertEqual(state.metrics.get('report_number_repair'), 1)

    def test_number_repair_cannot_introduce_instruction_echo(self):
        state = AnalysisState('测量基频', CONTEXT, evidence=[Evidence(
            'pitch', {'object': 1, 'time': .5}, ['基频 = 220 Hz'], 'fixture')])
        draft = AnalysisReport(analysis='本录音基频是 999 Hz [E1]。当前仍然缺少完整参照，无法判断具体发音缺陷。',
                               coverage=[Coverage(item=state.goal, status='partial', explanation='仍然缺少完整参考测量。')])
        masked = report_audit.audit_report(state, draft)
        state.metrics['rejected_report'] = draft.analysis
        session = CloudSession(config(), state, None, threading.Event(), None,
                               FunctionModel(lambda *_: ModelResponse([TextPart('1 => ' + LEAK)])),
                               [], None, None, None)
        session.phase = 'report'
        result = asyncio.run(session.repair_numbers(masked, state))
        self.assertIs(result, masked)
        self.assertFalse(delivery_text.has_answer_leak(result.analysis))
        self.assertEqual(state.metrics['guard_blocks']['report_repair.prompt_echo'], 1)

    def test_placeholder_split_across_stream_chunks_is_never_displayed(self):
        calls = []
        async def stream(messages, info):
            calls.append(1)
            for part in (['{{ca', 'lc:', 'E1.q1|Hz}}。'] if len(calls) == 1 else [GOOD]):
                yield part
                await asyncio.sleep(.04)
        state = AnalysisState('解释元音区别', '')
        chunks = []
        run_dialogue_turn(config(), state, kind='concept', cancel=threading.Event(),
                          on_text=chunks.append, model=FunctionModel(stream_function=stream))
        self.assertEqual(len(calls), 2)
        self.assertEqual(''.join(chunks), GOOD)
        self.assertEqual(state.report, GOOD)


if __name__ == '__main__':
    unittest.main()
