"""2026-10-06 report failures: private fixtures, offline SDK, no Praat dispatch."""
import copy
import json
import tempfile
import threading
import time
import asyncio
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

try:
    import httpx
except ImportError:
    import httpx2 as httpx
from openai import APITimeoutError, APIConnectionError, DefaultAsyncHttpxClient
from pydantic_ai.exceptions import ModelAPIError, ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from praat_ai.cloud_agent import UNVERIFIED_NUMBER, rescued_report, run_cloud_turn, run_dialogue_turn
from praat_ai.escape_policy import AnalysisState, Budget, Evidence
from praat_ai.session_context import PhaseContext
from test_cloud_escape import config

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\tSound あなた\t1\n'


def wrapped(error=None):
    outer = ModelAPIError('fixture', 'Request timed out.')
    outer.__cause__ = error or APITimeoutError(request=httpx.Request('POST', 'http://fixture.invalid/v1'))
    return outer


def response(text):
    return ModelResponse([TextPart(json.dumps({'analysis':text, 'coverage':[]}, ensure_ascii=False))])


@contextmanager
def timeout_endpoint():
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            # Every request times out: SDK retries would make this exceed two.
            time.sleep(1.5)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1', requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


class ReportChainTests(unittest.TestCase):
    def run_report(self, respond, state=None, cancel=None, progress=None):
        state = state or AnalysisState('解释已有测量', '')
        cfg = config()
        cfg.api.audio_input_enabled = False
        run_cloud_turn(cfg, state, execute_action=lambda *_:self.fail('No Praat dispatch'),
                       cancel=cancel or threading.Event(), model=FunctionModel(respond), progress=progress)
        return state

    def test_rescue_masks_numeric_tokens_without_corrupting_ids_decimals_or_labels(self):
        draft = 'ID: 1；计数 1；爆破 0.551 秒；范围 0.446194 秒；18.4 dB；13.4 dB；VOT 38.0 毫秒。[E1] F1'
        result = rescued_report(draft, '数值“1 ”未匹配；Request timed out.', ['1'])
        self.assertIn('ID: 1', result)
        self.assertIn('计数 ' + UNVERIFIED_NUMBER, result)
        for value in ('0.551 秒', '0.446194 秒', '18.4 dB', '13.4 dB', '38.0 毫秒', '[E1]', 'F1'):
            self.assertIn(value, result)

    def test_rescue_matches_full_quantity_with_flexible_whitespace(self):
        draft = '平均基频 999\tHz；编号 ID: 1；合法数值 1999 Hz；F1 [E1]；0.999 秒。'
        result = rescued_report(draft, '数值“999 Hz”未匹配', ['999 Hz'])
        self.assertNotIn('999\tHz', result)
        self.assertIn('平均基频 ' + UNVERIFIED_NUMBER, result)
        self.assertIn('1999 Hz', result)
        self.assertIn('0.999 秒', result)

    def test_rescue_preserves_equal_verified_value_and_known_identifier(self):
        state = AnalysisState('分析录音', CONTEXT, evidence=[
            Evidence('formant_frequency', {'object':1}, ['F1 = 220 Hz'], CONTEXT)])
        draft = '对象 Sound あなた（ID: 1）。\n平均基频 220 Hz。[E1]\nF1 为 220 Hz。[E1]'
        result = rescued_report(draft, '数值“220 Hz”未匹配', ['220 Hz'], state=state)
        self.assertIn('平均基频 ' + UNVERIFIED_NUMBER, result)
        self.assertIn('F1 为 220 Hz。[E1]', result)
        self.assertIn('ID: 1', result)
        self.assertNotIn('guard_blocks', state.metrics)

    def test_wrapped_timeout_retries_pending_request_once(self):
        requests = []
        def respond(messages, info):
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                raise wrapped()
            return response('原始测量支持阶段解释，现有材料仍缺少参照录音，需要补充证据后进一步分析。')
        state = self.run_report(respond)
        self.assertEqual(len(requests), 2)
        self.assertEqual([[p.content for m in request for p in m.parts] for request in requests][0],
                         [[p.content for m in request for p in m.parts] for request in requests][1])
        self.assertTrue(state.metrics.get('report_verified'), state.reason)
        self.assertEqual([t['attempt'] for t in state.metrics['request_timings']], [1, 2])

    def test_wrapped_connection_error_and_timeout_do_not_exceed_one_retry(self):
        for inner in (APITimeoutError(request=httpx.Request('POST', 'http://fixture.invalid')),
                      APIConnectionError(request=httpx.Request('POST', 'http://fixture.invalid'))):
            calls = []
            def respond(messages, info):
                calls.append(1)
                raise wrapped(inner)
            state = self.run_report(respond)
            self.assertEqual(len(calls), 2)
            self.assertFalse(state.metrics.get('report_verified', False))

    def test_permanent_error_message_alone_does_not_authorize_retry(self):
        for error in (ModelAPIError('fixture', 'Request timed out.'), ModelHTTPError(401, 'fixture', 'Invalid key'),
                      ModelHTTPError(400, 'fixture', 'Invalid request')):
            calls = []
            def respond(messages, info):
                calls.append(1)
                raise error
            self.run_report(respond)
            self.assertEqual(calls, [1])

    def test_total_budget_and_cancel_prevent_retry(self):
        for cancel_on_failure in (False, True):
            calls, cancel = [], threading.Event()
            def respond(messages, info):
                calls.append(1)
                if cancel_on_failure:
                    cancel.set()
                raise wrapped()
            state = AnalysisState('解释已有测量', '', budget=Budget(request_limit=1 if not cancel_on_failure else 8))
            self.run_report(respond, state, cancel)
            self.assertEqual(calls, [1])
            self.assertEqual(state.requests, 1)
            self.assertFalse(state.metrics.get('report_verified', False))

    def test_timeout_after_tool_does_not_reexecute_tool(self):
        calls, executions = [], []
        def respond(messages, info):
            calls.append('planner' if info.function_tools else 'report')
            if calls == ['planner']:
                return ModelResponse([ToolCallPart('pitch', {'object':1, 'time':.5}, tool_call_id='pitch-1')])
            if info.function_tools:
                return ModelResponse([TextPart('测量完成')])
            if calls.count('report') == 1:
                raise wrapped()
            return response('本录音基频为 220 Hz。[E1] 当前只有测量结果，仍需要参照材料才能评估具体缺陷。')
        from praat_ai import tools
        cfg, state = config(), AnalysisState('分析基频', CONTEXT)
        def execute(name, args, index):
            from praat_ai import chat
            executions.append((name, args, index))
            context = tools.ToolContext(tools.parse_object_context(CONTEXT), Path('unused-result.tsv'), Path('unused-state.txt'))
            return chat._execute_action({'tool':name, 'arguments':args}, context,
                                        lambda _: (True, ['基频 = 220 Hz'], ''), index)
        run_cloud_turn(cfg, state, execute_action=execute, cancel=threading.Event(), model=FunctionModel(respond),
                       tool_schemas=[s for s in tools.tool_schemas() if s['function']['name'] == 'pitch'])
        self.assertEqual(len(executions), 1)
        self.assertEqual(calls.count('report'), 2)
        self.assertEqual(len(state.evidence), 1)
        self.assertTrue(state.metrics.get('report_verified'), state.reason)

    def test_visible_text_blocks_wrapped_timeout_retry(self):
        calls, chunks = [], []
        async def stream(messages, info):
            calls.append(1)
            yield '已经显示的正文'
            await asyncio.sleep(.08)
            raise wrapped()
        state = AnalysisState('你好', '')
        run_dialogue_turn(config(), state, kind='conversation', cancel=threading.Event(),
                          on_text=chunks.append, model=FunctionModel(stream_function=stream))
        self.assertEqual(calls, [1])
        self.assertTrue(chunks)
        self.assertNotEqual(state.status, 'complete')

    def test_stream_timeout_before_text_resumes_without_duplicate_prompt(self):
        requests, chunks = [], []
        async def stream(messages, info):
            requests.append(copy.deepcopy(messages))
            if len(requests) == 1:
                raise wrapped()
            yield '你好，这次重试已经成功，仍沿用同一个原始问题。'
        state = AnalysisState('你好', '')
        run_dialogue_turn(config(), state, kind='conversation', cancel=threading.Event(),
                          on_text=chunks.append, model=FunctionModel(stream_function=stream))
        self.assertEqual(len(requests), 2, state.reason)
        self.assertEqual([[p.content for m in request for p in m.parts] for request in requests][0],
                         [[p.content for m in request for p in m.parts] for request in requests][1])
        self.assertEqual(state.status, 'complete', state.reason)
        self.assertTrue(chunks)

    def test_timeout_during_guard_correction_keeps_single_output_retry(self):
        requests = []
        bad = '本录音基频 999 Hz。[E1] 当前缺少可靠的参照录音，因此无法据此判断具体的发音缺陷。'
        def respond(messages, info):
            requests.append(copy.deepcopy(messages))
            if len(requests) == 2:
                raise wrapped()
            return response(bad)
        state = AnalysisState('解释测量', CONTEXT, continuation=True, continuation_mode='L2',
                              evidence=[Evidence('pitch', {'object':1}, ['基频 = 220 Hz'], CONTEXT)])
        self.run_report(respond, state)
        self.assertEqual(len(requests), 4, state.reason)
        self.assertEqual([t['phase'] for t in state.metrics['request_timings']],
                         ['report', 'report', 'report', 'report_repair'])
        self.assertEqual(state.requests, len(state.metrics['request_timings']))
        self.assertEqual(state.metrics['guard_repairs']['report.number_retried'], 1)
        self.assertEqual(state.metrics['guard_repairs']['report.number_masked'], 1)
        self.assertIn('999 Hz', state.metrics['audit']['masked'])
        self.assertFalse(state.metrics.get('report_verified', False))
        self.assertNotIn('999 Hz', state.report)
        from pydantic_ai.messages import RetryPromptPart
        for request in requests[1:3]:
            self.assertEqual(sum(isinstance(p, RetryPromptPart) for m in request for p in m.parts), 1)
        self.assertFalse(any(isinstance(p, RetryPromptPart) for m in requests[3] for p in m.parts))

    def test_latest_draft_does_not_inherit_prior_rejected_values(self):
        item = Evidence('pitch', {'object':1}, ['基频 = 220 Hz'], CONTEXT)
        drafts = ['平均基频 999 Hz。[E1] 当前只有目标录音的测量结果，仍缺少可靠的对照材料，无法判断具体发音缺陷。',
                  '平均基频 888 Hz。[E1]\n一般知识：示例频率为 999 Hz，仍缺少对照材料。']
        def respond(messages, info):
            return response(drafts.pop(0))
        state = AnalysisState('解释已有测量', CONTEXT, evidence=[item], continuation=True, continuation_mode='L2')
        state = self.run_report(respond, state)
        self.assertIn('示例频率为 999 Hz', state.report)
        self.assertNotIn('平均基频 888 Hz', state.report)
        self.assertFalse(state.metrics.get('report_verified', False))

    def test_real_sdk_wrapped_timeout_has_no_stacked_sdk_retries(self):
        cfg, state = config(), AnalysisState('解释已有测量', '')
        cfg.api.request_timeout_sec = .5
        cfg.api.audio_input_enabled = False
        with timeout_endpoint() as (base, requests), patch('openai.DefaultAsyncHttpxClient',
                side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            run_cloud_turn(cfg, state, execute_action=lambda *_:self.fail('No Praat dispatch'), cancel=threading.Event())
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0], requests[1])
        self.assertEqual(state.requests, 2)
        self.assertTrue(all(t['error_type'] == 'ModelAPIError' for t in state.metrics['request_timings']))
        self.assertFalse(state.metrics.get('report_verified', False))

    def assert_incomplete_storage(self, *, cancelled=False):
        from praat_ai.modern_app import ModernApplication
        class Executor:
            def capture_target(self, text):
                return ''
            def run(self, **kwargs):
                if cancelled:
                    kwargs['cancel'].set()
                return dict(content='未通过核对的草稿', formal=cancelled, status='complete' if cancelled else 'partial',
                            model_contexts={'report':PhaseContext(messages=[ModelResponse([TextPart('草稿')])], complete=True)})
        with tempfile.TemporaryDirectory() as directory:
            cfgpath = Path(directory)/'config.json'
            cfgpath.write_text(json.dumps({'api':{'enabled':True, 'stop_local_service':False,
                                                 'base_url':'http://127.0.0.1:1/v1', 'model':'fixture',
                                                 'max_context_tokens':32768, 'token_mode':'manual'}}), encoding='utf8')
            app = ModernApplication(Path(directory), cfgpath, executor=Executor())
            session = app.store.new_session()['id']
            app.submit(session, '解释结果')
            for task in list(app.tasks.values()):
                task['thread'].join(3)
            messages = app.store.get(session)['messages']
            self.assertIn('formal', messages[-1], messages[-1])
            self.assertFalse(messages[-1]['formal'])
            self.assertEqual(messages[-1]['status'], 'cancelled' if cancelled else 'partial')
            self.assertFalse(any(item['role'] == 'assistant' for item in app.store.context(session)[0]))
            with app.store.connect() as db:
                self.assertEqual(db.execute('select status from model_events').fetchone()[0], 'incomplete')
            app.close()

    def test_failed_draft_is_stored_only_as_incomplete_context(self):
        self.assert_incomplete_storage()

    def test_cancelled_draft_is_never_persisted_as_formal_reply(self):
        self.assert_incomplete_storage(cancelled=True)


if __name__ == '__main__':
    unittest.main()
