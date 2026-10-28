"""Recent two-turn regressions: audit protocol and user delivery stay separate."""
import copy
import json
import threading
import unittest
from unittest.mock import patch

from openai import DefaultAsyncHttpxClient
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import delivery_text
from praat_ai.cloud_agent import _finalize_analysis_delivery, report_context, run_cloud_turn
from praat_ai.cloud_runtime import CloudRuntime
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.session_context import PhaseContext
from test_dialogue_protocol import endpoint
from test_dialogue_stream import config

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n'
GOOD = '该声音平均基频为 220 Hz。[E1] 这只说明声学观测，尚不能据此定位具体音素或判断其发音好坏。'


def evidence():
    return Evidence('pitch_statistics', {'object': 1, 'from': 0, 'to': 1},
                    ['基频统计（0–1 秒）：平均 220 Hz，最低 210 Hz，最高 230 Hz'], CONTEXT)


def state(goal='解释已有声学测量'):
    return AnalysisState(goal, CONTEXT, evidence=[evidence()], continuation=True, continuation_mode='L2')


def response(text):
    return ModelResponse([TextPart(json.dumps(dict(analysis=text, coverage=[]), ensure_ascii=False))])


class AnalysisDeliveryTests(unittest.TestCase):
    def test_presentation_strips_only_evidence_citations(self):
        text = '1. F1/F2 与音名 E4 不同。[E1] 测得 220 Hz [E2, E3]。\n2. /u/ 与 [ɯ]。'
        result = delivery_text.present_analysis(text)
        self.assertEqual(result, '1. F1/F2 与音名 E4 不同。 测得 220 Hz。\n2. /u/ 与 [ɯ]。')
        self.assertEqual(delivery_text.present_analysis(result), result)

    def test_final_delivery_records_visible_text_without_mutating_prefix(self):
        current = state()
        current.report, current.status = GOOD, 'complete'
        current.metrics['report_verified'] = True
        ctx = PhaseContext(messages=[ModelResponse([TextPart(GOOD)])])
        current.phase_contexts['report'] = ctx
        original = copy.deepcopy(ctx.messages)
        before = copy.deepcopy(current.evidence)
        _finalize_analysis_delivery(current)
        self.assertNotIn('[E1]', current.report)
        self.assertIn('220 Hz', current.report)
        self.assertEqual(ctx.messages[:1], original)
        self.assertEqual(ctx.messages[-1].parts[0].content, current.report)
        self.assertEqual(current.evidence, before)

    def test_last_boundary_fails_closed_for_unrendered_protocol(self):
        current = state()
        current.report, current.status = '平均 {{q:E1.r1}}，结论见 report.number_unmatched。', 'complete'
        current.metrics['report_verified'] = True
        _finalize_analysis_delivery(current)
        self.assertFalse(delivery_text.has_answer_leak(current.report))
        self.assertFalse(current.metrics['report_verified'])
        self.assertEqual(current.status, 'partial')
        self.assertTrue(current.metrics['delivery_leaks_blocked'])
        self.assertEqual(current.evidence[0].rows, evidence().rows)

    def test_missing_alignment_does_not_erase_valid_acoustic_observations(self):
        current = state('分析发音')
        current.report = '平均基频为 220 Hz。[E1]\n0–1 秒对应音素「o」。'
        current.status = 'complete'
        current.metrics['report_verified'] = True
        _finalize_analysis_delivery(current)
        self.assertIn('220 Hz', current.report)
        self.assertNotIn('对应音素', current.report)
        self.assertIn('音素定位缺少对齐区间或人工标注', current.report)
        self.assertFalse(current.metrics['report_verified'])
        self.assertEqual(current.status, 'partial')

    def test_boundary_guidance_is_task_context_not_static_system(self):
        current = state('与标准音对比发音')
        facts = report_context(current)
        self.assertEqual(facts['boundary_evidence']['status'], 'none')
        self.assertFalse(facts['acoustic_time_windows_are_phone_boundaries'])
        self.assertEqual(facts['reference_evidence'], 'not_recorded')

    def test_sdk_report_never_publishes_invalid_row_placeholders_as_verified(self):
        current = state()
        run_cloud_turn(config(), current, execute_action=lambda *_: self.fail('No remeasurement'),
                       cancel=threading.Event(), model=FunctionModel(lambda *_: response(
                           '平均基频 {{q:E1.r1}}。这些窗口只是声学观测，缺少音素边界时不能定位具体音素。')))
        self.assertNotIn('{{', current.report)
        self.assertNotIn('[E1]', current.report)
        self.assertFalse(current.metrics.get('report_verified'), current.metrics)
        self.assertIn('音素边界', current.report)

    def test_sdk_report_retries_unsupported_phone_mapping_once(self):
        current = state('分析发音')
        calls = []
        def respond(*_):
            calls.append(1)
            return response('0–1 秒对应音素「o」。其他测量只用于声学观察，不能判断具体音素。' if len(calls) == 1 else GOOD)
        run_cloud_turn(config(), current, execute_action=lambda *_: self.fail('No remeasurement'),
                       cancel=threading.Event(), model=FunctionModel(respond))
        self.assertEqual(len(calls), 2, current.reason)
        self.assertTrue(current.metrics.get('report_verified'), current.reason)
        self.assertNotIn('对应音素', current.report)

    def test_three_report_turns_keep_final_http_prefix_and_visible_delivery(self):
        cfg = config()
        cfg.api.model = 'qwen3.8-omni-flash'  # Local fixture returns SSE.
        cfg.api.thinking_level = 'off'
        cfg.api.token_mode = 'provider'
        cfg.api.audio_input_enabled = False
        ctx = PhaseContext()
        body = json.dumps(dict(analysis=GOOD, coverage=[]), ensure_ascii=False)
        with endpoint(chunk_delay=0, bodies=[body] * 3) as (base, requests, _), patch(
                'openai.DefaultAsyncHttpxClient',
                side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            runtime = CloudRuntime()
            try:
                for goal in ('解释已有声学测量', '再说明其适用范围', '总结这些观测'):
                    current = state(goal)
                    current.phase_contexts['report'] = ctx
                    run_cloud_turn(cfg, current, execute_action=lambda *_: self.fail('No remeasurement'),
                                   cancel=threading.Event(), runtime=runtime)
                    self.assertTrue(current.metrics.get('report_verified'), current.reason)
                    ctx = current.phase_contexts['report']
                    self.assertEqual(ctx.messages[-1].parts[0].content, current.report)
                    self.assertNotIn('[E1]', current.report)
                self.assertEqual(len(requests), 3)
                for previous, following in zip(requests, requests[1:]):
                    self.assertEqual(previous['messages'], following['messages'][:len(previous['messages'])])
                    for key in ('tools', 'response_format'):
                        self.assertEqual(previous.get(key), following.get(key))
            finally:
                runtime.close(wait=True)


if __name__ == '__main__':
    unittest.main()
