"""User-visible delivery and persisted native dialogue must agree after correction."""
import asyncio
import copy
import json
import threading
import unittest
from unittest.mock import patch

from openai import DefaultAsyncHttpxClient
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import answer_contract
from praat_ai.cloud_agent import run_dialogue_turn
from praat_ai.cloud_runtime import CloudRuntime
from praat_ai.escape_policy import AnalysisState
from praat_ai.session_context import PhaseContext
import test_dialogue_protocol as protocol
from test_dialogue_stream import config

GOAL = '请解释日语 /u/ 和 /ɯ/ 的区别，不测录音。'
PREFIX = '先确认，这是一般语音学说明。'
BAD = '在はし的末元音中，[ɯ]常发生清化。'
GOOD = '日语「う」常写作/u/，实际音值可记作[ɯ̟ᵝ]，通常没有明显的向前撮唇。放松唇部，再结合可靠示范练习。'
LEAK = '按照要求我不得随便猜定论，将测得的参数填入公式后提交给用户。'


class DeliveryConsistencyTests(unittest.TestCase):
    def stream_case(self, second):
        calls, chunks = [], []
        async def stream(messages, info):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                yield PREFIX
                await asyncio.sleep(.08)
                yield BAD
            else:
                yield second
        state = AnalysisState(GOAL, '')
        run_dialogue_turn(config(), state, kind='concept', cancel=threading.Event(),
                          on_text=chunks.append, model=FunctionModel(stream_function=stream))
        return state, chunks, calls

    def test_rejected_attempt_prefix_is_not_published_before_corrected_answer(self):
        state, chunks, calls = self.stream_case(GOOD)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state.report, GOOD)
        self.assertEqual(''.join(chunks), GOOD)
        self.assertNotIn(PREFIX, ''.join(chunks))

    def test_reference_fallback_has_exact_visible_and_native_delivery(self):
        state, chunks, calls = self.stream_case(BAD)
        expected = answer_contract.knowledge_fallback(GOAL)
        self.assertEqual(len(calls), 2)
        self.assertEqual(state.report, expected)
        self.assertEqual(''.join(chunks), expected)
        context = state.phase_contexts['dialogue']
        self.assertTrue(context.complete)
        self.assertEqual(context.messages[-1].parts[0].content, expected)
        self.assertIn('应用交付记录', str(context.messages[-2].parts))

    def test_reference_delivery_survives_restore_and_next_http_prefix(self):
        cfg = config()
        cfg.api.token_mode = 'provider'
        with protocol.endpoint(bodies=[BAD, BAD, GOOD], chunk_delay=0) as (base, requests, _), \
                patch('openai.DefaultAsyncHttpxClient',
                      side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            runtime = CloudRuntime()
            try:
                first = AnalysisState(GOAL, '')
                run_dialogue_turn(cfg, first, kind='concept', cancel=threading.Event(),
                                  on_text=lambda _: None, runtime=runtime)
                history = [dict(role='user', content=GOAL), dict(role='assistant', content=first.report)]
                context = first.phase_contexts['dialogue']
                context.mark_formal(history)
                restored = PhaseContext.restore(context.payload())
                restored.sync_formal(history)
                second = AnalysisState('再给一个简单练习。', '', phase_context=restored, dialogue_context=history)
                run_dialogue_turn(cfg, second, kind='advice', cancel=threading.Event(),
                                  on_text=lambda _: None, runtime=runtime)
                self.assertEqual(len(requests), 3)
                self.assertEqual(requests[1]['messages'], requests[2]['messages'][:len(requests[1]['messages'])])
                deliveries = [m for m in requests[2]['messages']
                              if m['role'] == 'assistant' and m['content'] == first.report]
                self.assertEqual(len(deliveries), 1)
                self.assertIn('应用交付记录', json.dumps(requests[2]['messages'], ensure_ascii=False))
            finally:
                runtime.close(wait=True)

    def test_rejected_dialogue_is_not_marked_as_complete_native_context(self):
        cfg = config()
        with protocol.endpoint(bodies=[LEAK, LEAK], chunk_delay=0) as (base, requests, _), \
                protocol.DialogueProtocolTests().redirect(base):
            state = AnalysisState(GOAL, '')
            run_dialogue_turn(cfg, state, kind='concept', cancel=threading.Event(), on_text=lambda _: None)
        self.assertEqual(len(requests), 2)
        self.assertEqual(state.status, 'partial')
        self.assertFalse(state.phase_contexts['dialogue'].complete)

    def delivery_mode(self, goal, kind):
        async def stream(messages, info):
            yield GOOD
        state = AnalysisState(goal, '')
        run_dialogue_turn(config(), state, kind=kind, cancel=threading.Event(),
                          on_text=lambda _: None, model=FunctionModel(stream_function=stream))
        return state

    def test_delivery_mode_and_first_text_are_recorded(self):
        # 首字延迟的取舍必须可观测：命中复核主题时整份验证后发布，其余逐句流式。
        reviewed = self.delivery_mode(GOAL, 'concept')
        self.assertEqual(reviewed.metrics['dialogue_delivery_mode'], 'validated')
        self.assertIsInstance(reviewed.metrics['first_visible_text_seconds'], float)
        plain = self.delivery_mode('那今天先聊到这儿，谢谢。', 'conversation')
        self.assertEqual(plain.metrics['dialogue_delivery_mode'], 'incremental')
        self.assertIsInstance(plain.metrics['first_visible_text_seconds'], float)

    def test_cancel_does_not_reuse_previous_complete_phase_flag(self):
        async def stream(messages, info):
            await asyncio.sleep(.5)
            yield GOOD
        context = PhaseContext(messages=[ModelResponse([TextPart('以前的完整答复。')])], complete=True)
        state = AnalysisState(GOAL, '', phase_context=context)
        cancel = threading.Event()
        timer = threading.Timer(.05, cancel.set)
        timer.start()
        try:
            run_dialogue_turn(config(), state, kind='concept', cancel=cancel,
                              on_text=lambda _: None, model=FunctionModel(stream_function=stream))
        finally:
            timer.cancel()
            timer.join()
        self.assertEqual(state.status, 'cancelled')
        self.assertFalse(context.complete)


if __name__ == '__main__':
    unittest.main()
