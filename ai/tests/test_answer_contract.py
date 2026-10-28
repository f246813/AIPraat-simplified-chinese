"""Task-tail guidance and deterministic missing-target delivery, without cloud calls."""
import asyncio
import json
import threading
import unittest

from pydantic_ai.messages import ModelResponse, TextPart, SystemPromptPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import answer_contract
from praat_ai.cloud_agent import CloudSession, dialogue_instructions, run_cloud_turn
from praat_ai.escape_policy import AnalysisState
from praat_ai.workflow_policy import WorkflowPolicy
import test_dialogue_stream as dialogue_fixture
import test_tool_guards as tool_fixture


class AnswerContractTests(unittest.TestCase):
    def test_dialogue_guidance_is_task_tail_not_dynamic_system(self):
        cfg = dialogue_fixture.config()
        session = CloudSession(cfg, AnalysisState('元音如何练习', ''), None,
                               threading.Event(), None, FunctionModel(lambda *_: None), [], None, None, None)
        captured = []
        async def drive(agent, prompt, **kwargs):
            captured.extend(session.state.phase_contexts['dialogue'].messages)
            return '先明确目标语言的音值，再选择真实例词逐步练习，不能仅凭符号差异认定音位对立。'
        session.drive = drive
        asyncio.run(session.dialogue('advice', lambda _: None))
        system = [p.content for m in captured for p in m.parts if isinstance(p, SystemPromptPart)]
        users = [p.content for m in captured for p in m.parts if isinstance(p, UserPromptPart)]
        self.assertEqual(system, [dialogue_instructions(cfg)])
        self.assertTrue(any('音位对立' in str(p) for p in users))
        self.assertFalse(any('专业讲解约定' in str(p) for p in system))
        self.assertEqual(answer_contract.dialogue_guidance('conversation'), '')

    def test_report_request_carries_scope_and_exact_coverage_contract(self):
        state, _, _, messages = tool_fixture.NativeHookTests().run_case(
            [('pitch', {'time': .5})], goal='测量对象 1 的基频并解释结果')
        payloads = [p.content for m in messages[0] for p in m.parts if isinstance(p, UserPromptPart)]
        prompt = '\n'.join(str(p) for p in payloads)
        self.assertIn('answer_contract', prompt)
        self.assertIn('同类证据', prompt)
        self.assertIn('一个合并目标只写一个item', prompt)

    def test_missing_target_is_local_and_actionable(self):
        for workflow in (None, WorkflowPolicy()):
            with self.subTest(workflow=workflow):
                state = AnalysisState('测量当前这段声音的平均基频', '', workflow=workflow)
                cfg = dialogue_fixture.config()
                def no_model(*_):
                    self.fail('Missing objects must not trigger model requests')
                run_cloud_turn(cfg, state, execute_action=lambda *_: self.fail('No object to measure'),
                               cancel=threading.Event(), model=FunctionModel(no_model))
                self.assertEqual(state.requests, 0)
                self.assertEqual(state.status, 'partial')
                self.assertIn('请先加载录音', state.report)
                self.assertIn('尚未取得测量结果', state.report)
                self.assertEqual(state.metrics['route_reason'], 'missing_target')
                self.assertEqual(state.coverage[0]['status'], 'missing')
                self.assertNotIn('预算', state.report)

    def test_disabled_tool_workflow_is_not_misreported_as_missing_target(self):
        policy = WorkflowPolicy(l0_enabled=False)
        state = AnalysisState('测量当前这段声音的平均基频', '', workflow=policy)
        cfg = dialogue_fixture.config()
        run_cloud_turn(cfg, state, execute_action=lambda *_: self.fail('No dispatch'),
                       cancel=threading.Event(), model=FunctionModel(lambda *_: self.fail('No cloud')))
        self.assertIn('工作流', state.report)
        self.assertNotEqual(state.metrics.get('route_reason'), 'missing_target')

    def test_reviewed_knowledge_is_selective_and_appended_once_to_three_wire_turns(self):
        from unittest.mock import patch
        from openai import DefaultAsyncHttpxClient
        from praat_ai.cloud_agent import run_dialogue_turn
        from praat_ai.cloud_runtime import CloudRuntime
        from praat_ai.session_context import PhaseContext
        from test_dialogue_protocol import endpoint
        self.assertEqual(answer_contract.knowledge_notes('英语元音怎么练习'), [])
        self.assertEqual(answer_contract.knowledge_notes('日语重音是什么'), [])
        cfg = dialogue_fixture.config()
        cfg.api.token_mode = 'provider'
        context = PhaseContext()
        with endpoint(chunk_delay=0) as (base, requests, _), patch('openai.DefaultAsyncHttpxClient',
                side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            runtime = CloudRuntime()
            try:
                for goal in ('什么是基频？', '日语 /u/ 与 /ɯ/ 有什么区别？', '给一个练习方法。'):
                    state = AnalysisState(goal, '')
                    state.phase_context = context
                    run_dialogue_turn(cfg, state, kind='concept', cancel=threading.Event(),
                                      on_text=lambda _: None, runtime=runtime)
                    self.assertEqual(state.status, 'complete', state.reason)
                for first, second in zip(requests, requests[1:]):
                    self.assertEqual(first['messages'], second['messages'][:len(first['messages'])])
                wire = json.dumps(requests[-1]['messages'], ensure_ascii=False)
                self.assertEqual(wire.count('近后、高元音'), 1)
                self.assertEqual(requests[0]['messages'][0], requests[-1]['messages'][0])
            finally:
                runtime.close(wait=True)


if __name__ == '__main__':
    unittest.main()
