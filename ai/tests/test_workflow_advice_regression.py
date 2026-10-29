"""Replay the 2026-10-06 VOT -> practice advice -> discussion regression offline."""
import copy
import json
import tempfile
import threading
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from pydantic_ai import ModelRetry
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import chat, delivery
from praat_ai.cloud_agent import AnalysisReport, Coverage, run_cloud_turn, run_dialogue_turn
from praat_ai.cloud_runtime import CloudRuntime
from praat_ai.dialogue_policy import dialogue_kind
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.modern_execution import ModernExecutor
from praat_ai.report_guards import validate_report
from praat_ai.workflow_policy import WorkflowPolicy, measurement_complete
from test_configurable_workflow import config


GOAL = '若要改进发音，应当如何练习？'
CONTEXT = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
           '1\tSound\tSound あなた\t0\t0.446194\t0.661580\n'
           '2\tHarmonicity\tHarmonicity あなた\t1\t\t\n')
ARGS = {'object': 1, 'from': 0.446194, 'to': 0.66158}
ROWS = ['VOT 估计值 = 0.0380 秒（38.0 毫秒）：爆破 0.551 秒 → 浊音起始 0.589 秒；范围 0.446–0.662 秒']


def measured():
    return Evidence('vot', dict(ARGS), list(ROWS), CONTEXT)


def discussion():
    return AnalysisReport(
        analysis='## 1. 练习建议\n1. **模仿目标发音**：先以自然语速跟读，再回听比较。\n'
                 '2. **核对边界**：已有 VOT 为 38.0 毫秒。[E1]\n'
                 '3. **保留不确定性**：仅凭这一项测量不能判断全部发音是否准确。',
        coverage=[Coverage(item=GOAL, status='complete', explanation='提供练习方法，明确现有证据的适用范围。')])


class AdviceRegressionTests(unittest.TestCase):
    def test_practice_questions_do_not_reenter_measurement_planning(self):
        for text in (GOAL, '基于这些结果，应该如何练习发音？', 'How should I practice pronunciation?'):
            with self.subTest(text=text):
                self.assertEqual(dialogue_kind(text), 'advice')
        for text in ('测量这段练习录音的VOT并给出建议', '分析这段录音并给练习建议',
                     '先删除对象，再告诉我怎么练习', 'Measure VOT and give practice advice'):
            with self.subTest(text=text):
                self.assertIsNone(dialogue_kind(text))

    def test_advice_streams_once_with_prior_evidence_and_no_praat(self):
        answer = '先以自然语速模仿目标发音，再回听比较；已有结果只能支持对应片段的观察。'
        seen = []

        async def stream(messages, info):
            seen.append(copy.deepcopy(messages))
            self.assertFalse(info.function_tools)
            self.assertIn('0.0380', str(messages[-1].parts))
            self.assertIn(GOAL, str(messages[-1].parts))
            yield answer

        with tempfile.TemporaryDirectory() as root:
            executor = ModernExecutor(Path(root), Path(root) / 'config.json')
            runtime = CloudRuntime(model_factory=lambda _: FunctionModel(stream_function=stream))
            cfg = config(); cfg.api.enabled = True; cfg.workflow = WorkflowPolicy()
            record = dict(goal='计算这段的VOT', target=CONTEXT, evidence=[asdict(measured())], attempts=[])
            events = []
            try:
                with patch.object(executor, 'session_runtime', return_value=runtime), \
                     patch('praat_ai.chat.praat_executable', side_effect=AssertionError('Advice must not capture Praat')):
                    result = executor.run(config=cfg, text=GOAL,
                        history=[dict(role='user', content='计算这段的VOT')], target='',
                        cancel=threading.Event(), emit=lambda kind, value: events.append((kind, value)),
                        session_id='advice', historical_evidence=[record])
                self.assertEqual(result['status'], 'complete', result['content'])
                self.assertIn(answer, result['content'])
                self.assertEqual(result['attempts'], [])
                self.assertEqual(result['metrics']['requests'], 1)
                self.assertEqual(set(result['metrics']['phases']), {'dialogue'})
                displayed = ''.join(p['text'] for kind, p in events if kind == 'delta')
                self.assertIn(answer, displayed)
            finally:
                executor.close(); runtime.close()

    def test_three_advice_turns_append_to_wire_history_without_repeating_evidence(self):
        from openai import DefaultAsyncHttpxClient
        from praat_ai.session_context import PhaseContext
        from test_dialogue_protocol import endpoint
        ctx = PhaseContext()
        cfg = config()
        record = dict(goal='计算这段的VOT', target=CONTEXT, evidence=[asdict(measured())], attempts=[])
        with endpoint(chunk_delay=0) as (base, requests, ports), patch('openai.DefaultAsyncHttpxClient',
                side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            runtime = CloudRuntime()
            try:
                for goal in (GOAL, '应该如何练习自然语速？', '如何改善练习方法？'):
                    state = AnalysisState(goal, '', historical_evidence=[record])
                    state.phase_context = ctx
                    run_dialogue_turn(cfg, state, kind='advice', cancel=threading.Event(),
                                      on_text=lambda _: None, runtime=runtime)
                    self.assertEqual(state.status, 'complete', state.reason)
                self.assertEqual(len(requests), 3)
                self.assertEqual(len(set(ports)), 1)
                for previous, current in zip(requests, requests[1:]):
                    self.assertEqual(previous['messages'], current['messages'][:len(previous['messages'])])
                wire = json.dumps(requests[-1]['messages'], ensure_ascii=False)
                self.assertEqual(wire.count('0.0380'), 1)
                self.assertEqual(wire.count('本轮用户目标'), 1)
                self.assertTrue(all(not payload.get('tools') for payload in requests))
            finally:
                runtime.close(wait=True)

    def test_on_demand_accepts_practice_goal_when_explanation_is_needed(self):
        state = AnalysisState(GOAL, CONTEXT, evidence=[measured()], workflow=WorkflowPolicy(),
                              continuation=True, continuation_mode='L2')
        response = dict(analysis='练习时先跟读再回听，仅凭这一项测量不能判断整体发音是否准确。',
                        coverage=[dict(item=GOAL, status='complete', explanation='给出练习步骤并说明证据范围。')])
        run_cloud_turn(config(), state, execute_action=lambda *_: self.fail('No repeated VOT'),
                       cancel=threading.Event(), model=FunctionModel(lambda *_: ModelResponse([TextPart(json.dumps(response, ensure_ascii=False))])))
        self.assertEqual(state.requests, 1)
        self.assertTrue(state.metrics.get('report_verified'), state.reason)

    def test_on_demand_does_not_discard_judgment_without_classifier_keywords(self):
        state = AnalysisState('这段发音有问题吗？', CONTEXT, evidence=[measured()],
                              workflow=WorkflowPolicy())
        answer = '现有单项测量只能说明对应片段的一项特征，不足以判断整体发音；可以先跟读目标发音并回听。'
        calls = []

        def response(messages, info):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                return ModelResponse([TextPart(answer)])
            report = dict(analysis=answer, coverage=[dict(item=state.goal, status='partial',
                          explanation='单项测量不足以完成整体发音判断。')])
            return ModelResponse([TextPart(json.dumps(report, ensure_ascii=False))])

        run_cloud_turn(config(), state, execute_action=lambda *_: self.fail('No repeated VOT'),
                       cancel=threading.Event(), model=FunctionModel(response), tool_schemas=[])
        self.assertTrue(state.metrics.get('report_verified'), state.reason)
        self.assertIn(answer, state.report)
        self.assertEqual(state.requests, 2)
        self.assertNotEqual(state.metrics.get('route_reason'), 'workflow_stage_disabled')

    def test_numbered_practice_discussion_is_delivered_without_guard_retry(self):
        state = AnalysisState(GOAL, CONTEXT, evidence=[measured()], workflow=WorkflowPolicy(),
                              continuation=True, continuation_mode='L4')
        run_cloud_turn(config(), state, execute_action=lambda *_: self.fail('No repeated VOT'),
                       cancel=threading.Event(), model=FunctionModel(lambda *_: ModelResponse([TextPart(discussion().model_dump_json())])))
        self.assertEqual(state.requests, 1, state.reason)
        self.assertTrue(state.metrics.get('report_verified'), state.reason)
        self.assertIn('模仿目标发音', state.report)

    def test_markdown_ordinals_are_not_measurements_but_real_fabrications_still_fail(self):
        state = AnalysisState(GOAL, CONTEXT, evidence=[measured()])
        try:
            verified = validate_report(state, discussion())
        except ModelRetry as error:
            self.fail(f'Markdown ordinals must not reject a practice answer: {error}')
        self.assertIn('1. **模仿', verified.analysis)
        for text in ('1. 本次 VOT 为 99.0 毫秒。[E1]', '本次测得计数为 1。'):
            with self.subTest(text=text), self.assertRaises(ModelRetry):
                validate_report(AnalysisState(GOAL, CONTEXT, evidence=[measured()]), discussion().model_copy(update={'analysis': text}))

    def test_cited_sound_measurement_is_not_rebound_to_selected_harmonicity(self):
        state = AnalysisState(GOAL, CONTEXT, evidence=[measured()])
        try:
            result = validate_report(state, discussion().model_copy(update={'analysis': '已有 VOT 为 38.0 毫秒。[E1]'}))
        except ModelRetry as error:
            self.fail(f'The cited Sound evidence must keep its original object: {error}')
        self.assertIn('38.0', result.analysis)
        with self.assertRaises(ModelRetry):
            validate_report(state, discussion().model_copy(update={'analysis': '对象 2 的 VOT 为 38.0 毫秒。[E1]'}))

    def test_successful_bound_vot_finishes_without_false_gap_or_second_request(self):
        from praat_ai import tools
        state = AnalysisState('计算这段的VOT', CONTEXT, workflow=WorkflowPolicy())
        calls = []

        def response(messages, info):
            calls.append(copy.deepcopy(messages))
            if len(calls) == 1:
                return ModelResponse([ToolCallPart('vot', dict(ARGS), tool_call_id='vot')])
            return ModelResponse([TextPart('VOT 测量完成。')])

        run_cloud_turn(config(), state, cancel=threading.Event(), model=FunctionModel(response),
            execute_action=lambda name, args, index: chat.AgentStep(index, name, args, True, '工具执行成功',
                script='registered VOT template', results=list(ROWS), execution=delivery.DELIVERED),
            tool_schemas=[s for s in tools.tool_schemas() if s['function']['name'] == 'vot'])
        self.assertEqual(state.status, 'complete', state.report)
        self.assertEqual(state.requests, 1)
        self.assertNotIn('尚未完成', state.report)
        self.assertNotIn('report', state.phase_contexts)
        paired = [p.tool_call_id for m in state.phase_contexts['planner'].messages
                  if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
        self.assertEqual(paired, ['vot'])
        state.attempts[0]['status'] = 'unknown'
        self.assertFalse(measurement_complete(state))
        state.attempts[0]['status'] = 'success'
        state.evidence[0].arguments['from'] = 0.1
        self.assertFalse(measurement_complete(state))


class FollowUpRoutingTests(unittest.TestCase):
    """自然说法的追问要进 L2 解释阶段。

    2026-10-07 实测：同一个会话里先测了平均基频，接着问「解释一下刚才那个结果是什么意思。」
    ——原来只有「本次解释结果」这种封闭写法才算 L2，于是这句话被当成一般讨论（L4）结束，
    回答是「没有可核实的原目标对象或音频材料」，用户看不到任何解释。
    """

    def test_natural_explanation_followup_routes_to_l2(self):
        from praat_ai.workflow_policy import explicit_action
        for goal in ('解释一下刚才那个结果是什么意思。', '说明一下刚才的测量', '解释一下这些数值'):
            with self.subTest(goal=goal):
                self.assertEqual(explicit_action(goal), 'L2')

    def test_a_new_measurement_is_not_taken_over_by_the_followup_rule(self):
        from praat_ai.workflow_policy import explicit_action
        for goal in ('解释一下刚才那个结果，然后重新测量强度', '帮我测量平均基频', '解释一下 0.1 到 0.4 秒的结果'):
            with self.subTest(goal=goal):
                self.assertEqual(explicit_action(goal), '')


class AudioRoutingTests(unittest.TestCase):
    """「直接听录音」的判定不能跨句匹配。

    2026-10-07 实测：一段长指令里同时出现「请直接问我」和「对这次录音分析」时，
    ``直接.*(?:听|音频|录音)`` 里的 ``.*`` 跨了整段，于是整轮被判成 L3（要直接听录音），
    图直接跳到报告阶段——0 次规划请求、0 次工具调用，用户只拿到「测量缺失」。
    """

    def test_long_instruction_with_distant_words_is_not_an_audio_request(self):
        from praat_ai.cloud_agent import entry_mode, wants_audio
        goal = ('下面这段是我对这次录音分析的完整要求。要求：如果有范围不清楚的地方请直接问我。'
                '最后一句：现在先只测量平均基频。')
        self.assertFalse(wants_audio(goal))
        context = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n'
        self.assertEqual(entry_mode(goal, context), 'L0')

    def test_real_audio_requests_still_route_to_l3(self):
        from praat_ai.cloud_agent import wants_audio
        for goal in ('直接听一下原始音频', '分析原始录音', '直接听这段录音', '说说你的听感'):
            with self.subTest(goal=goal):
                self.assertTrue(wants_audio(goal))


if __name__ == '__main__':
    unittest.main()
