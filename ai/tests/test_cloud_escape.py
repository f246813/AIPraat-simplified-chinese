import json
import threading
import unittest
from pathlib import Path
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from praat_ai.config import AppConfig
from praat_ai import chat, tools
from praat_ai.escape_policy import AnalysisState, Budget
from praat_ai.cloud_agent import run_cloud_turn

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n'


def config():
    c = AppConfig()
    c.api.model = 'test-model'
    c.api.base_url = 'https://test.invalid/v1'
    c.api.thinking_level = 'auto'
    return c


class ScriptedModel:
    def __init__(self, calls):
        self.calls = list(calls)
        self.report_messages = []
        self.tool_requests = 0

    def response(self, messages, info):
        if info.function_tools:
            self.tool_requests += 1
            name, args = self.calls.pop(0) if self.calls else ('', {})
            return ModelResponse([ToolCallPart(name, args, tool_call_id='call-' + str(self.tool_requests))]) if name else ModelResponse([TextPart('工具阶段结束')])
        self.report_messages.append(messages)
        return ModelResponse([TextPart(json.dumps({'analysis':'针对原始目标，现有证据支持阶段性解释，但缺少录音听感与参考测量，不能判断具体缺陷。',
              'coverage':[{'item':'分析目标', 'status':'partial', 'explanation':'已有测量可解释趋势，具体对比缺证据。'}]}, ensure_ascii=False))])


class CloudTests(unittest.TestCase):
    def run_case(self, calls, executor=None, state=None, cancel=None, cfg=None):
        scripted = ScriptedModel(calls)
        scripts = []
        context = tools.ToolContext(tools.parse_object_context(CONTEXT), Path('result.tsv'), Path('state.txt'))
        def execute(script):
            scripts.append(script)
            return executor(script) if executor else (True, ['基频 = 220 Hz'], '')
        def action(name, args, index):
            return chat._execute_action({'tool':name, 'arguments':args}, context, execute, index)
        state = state or AnalysisState('分析目标', CONTEXT)
        run_cloud_turn(cfg or config(), state, execute_action=action, cancel=cancel or threading.Event(),
                       model=FunctionModel(scripted.response), tool_schemas=[s for s in tools.tool_schemas() if s['function']['name'] == 'pitch'])
        return state, scripted, scripts

    def test_success_enters_independent_report(self):
        state, model, scripts = self.run_case([('pitch', {'time':0.5})])
        self.assertEqual(len(scripts), 1)
        self.assertEqual(state.mode, 'L2')
        self.assertEqual(len(state.evidence), 1)
        self.assertIn('阶段性解释', state.report)
        report_context = str(model.report_messages[0])
        self.assertNotIn('ToolReturnPart', report_context)
        self.assertNotIn('先完成', report_context)

    def test_failure_after_one_repair_reports(self):
        state, model, scripts = self.run_case([('pitch', {'time':0.5}), ('pitch', {'time':0.6}), ('pitch', {'time':0.7})],
                                               executor=lambda _: (False, ['error text'], 'invalid parameter'))
        self.assertEqual(len(scripts), 2)
        self.assertEqual(state.failures['pitch'], 2)
        self.assertEqual(state.evidence, [])
        self.assertTrue(state.report)
        self.assertTrue(any(e['mode'] == 'L1' for e in state.events))

    def test_repeats_stop_after_two_rounds_no_progress(self):
        state, model, scripts = self.run_case([('pitch', {'time':0.5})] * 6)
        self.assertEqual(len(scripts), 1)
        self.assertLessEqual(model.tool_requests, 3)
        self.assertIn('连续两轮', state.reason)

    def test_low_context_reserves_report(self):
        large_context = CONTEXT + ''.join(f'{i}\tSound\t' + '原始材料描述' * 30 + '\t0\n' for i in range(2, 30))
        state = AnalysisState('分析目标', large_context, budget=Budget(context_tokens=8192, response_tokens=500))
        cfg = config()
        cfg.api.limit_tokens = True
        state, model, scripts = self.run_case([('pitch', {'time':0.5})], state=state, cfg=cfg)
        self.assertEqual(scripts, [])
        self.assertTrue(state.report)
        self.assertTrue(state.can_continue)
        self.assertEqual(len(model.report_messages), 1)

    def test_unlimited_does_not_use_user_context_cutoff(self):
        cfg = config()
        cfg.api.limit_tokens = False
        state = AnalysisState('分析目标', CONTEXT + 'material ' * 3500,
                              budget=Budget(context_tokens=2048, response_tokens=500))
        state, model, scripts = self.run_case([], state=state, cfg=cfg)
        self.assertGreater(model.tool_requests, 0)
        self.assertTrue(model.report_messages)

    def test_cancel_prevents_all_requests(self):
        cancel = threading.Event()
        cancel.set()
        state, model, scripts = self.run_case([('pitch', {})], cancel=cancel)
        self.assertEqual(state.status, 'cancelled')
        self.assertEqual(model.tool_requests, 0)
        self.assertFalse(model.report_messages)

    def test_cancel_during_tool_retains_completed_evidence(self):
        cancel = threading.Event()
        def executor(script):
            cancel.set()
            return True, ['基频 = 220 Hz'], ''
        state, model, scripts = self.run_case([('pitch', {})], executor=executor, cancel=cancel)
        self.assertEqual(state.status, 'cancelled')
        self.assertEqual(len(state.evidence), 1)
        self.assertEqual(len(scripts), 1)
        self.assertFalse(model.report_messages)

    def test_unknown_execution_and_missing_dependency_stop_immediately(self):
        for reason in ('执行超时，状态不明', '缺少依赖，未安装'):
            state, model, scripts = self.run_case([('pitch', {}), ('pitch', {'time':0.2})],
                executor=lambda _, reason=reason: (False, [], reason))
            self.assertEqual(len(scripts), 1)
            self.assertEqual(state.evidence, [])
            self.assertTrue(model.report_messages)
            self.assertIn(reason, state.reason)

    def test_discussion_preserves_compact_visible_dialogue(self):
        state = AnalysisState('分析目标', '')
        state.dialogue_context = [{'role':'user','content':'先前讨论的是第二共振峰'}]
        state, model, scripts = self.run_case([], state=state)
        self.assertIn('先前讨论的是第二共振峰', str(model.report_messages[0]))
        self.assertEqual(state.goal, '分析目标')

    def test_cancel_while_waiting_for_model_stops_later_nodes(self):
        import asyncio
        cancel = threading.Event()
        called = []
        async def slow_model(messages, info):
            called.append(1)
            cancel.set()
            await asyncio.sleep(5)
            return ModelResponse([TextPart('should not finish')])
        state = AnalysisState('分析目标', '')
        run_cloud_turn(config(), state, execute_action=lambda *args: self.fail('no tool'),
                       cancel=cancel, model=FunctionModel(slow_model))
        self.assertEqual(state.status, 'cancelled')
        self.assertEqual(len(called), 1)
        self.assertEqual(state.requests, 1)

    def test_original_invalid_arguments_are_retained_as_proposal(self):
        for invalid in ({'time':'bad-time'}, '{"time":'):
            state, model, scripts = self.run_case([('pitch', invalid), ('pitch', {'time':0.5})])
            calls = [call for event in state.events for call in event.get('tool_proposals', [])]
            self.assertTrue(any(call['arguments'] == invalid for call in calls))
            self.assertEqual(len(scripts), 1)
            self.assertNotIn('bad-time', str([e.rows for e in state.evidence]))

    def test_discussion_needs_no_praat(self):
        state = AnalysisState('分析目标', '')
        state, model, scripts = self.run_case([], state=state)
        self.assertEqual(model.tool_requests, 0)
        self.assertEqual(state.mode, 'L4')


if __name__ == '__main__':
    unittest.main()
