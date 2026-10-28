"""Verify protocol and state boundaries through the real SDK/HTTP adapter."""
import copy
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from praat_ai.cloud_agent import planner_system, report_instructions, run_dialogue_turn
from praat_ai.cloud_runtime import CloudRuntime
from praat_ai.escape_policy import AnalysisState
from praat_ai.modern_store import ModernStore
from test_dialogue_protocol import endpoint
from test_dialogue_stream import config


class PhaseContextTests(unittest.TestCase):
    def test_legacy_report_budget_matches_final_http_dialogue_excerpt(self):
        import json
        from praat_ai.cloud_agent import run_cloud_turn
        from praat_ai.escape_policy import Budget
        cfg = config()
        cfg.api.model = 'qwen3.8-omni-flash'  # This local endpoint returns SSE.
        cfg.api.token_mode = None
        cfg.api.limit_tokens = True
        cfg.api.max_context_tokens, cfg.api.plan_max_tokens = 4096, 1500
        cfg.api.audio_input_enabled = False
        state = AnalysisState('解释已有录音依据', '', continuation=True, continuation_mode='L4',
                              budget=Budget(context_tokens=4096, response_tokens=1500))
        state.dialogue_context = [dict(role='user' if index % 2 == 0 else 'assistant',
                                       content=f'HISTORY-{index}：' + '证据' * 396) for index in range(4)]
        body = json.dumps(dict(analysis='针对原始目标，目前没有实际测量和录音听感，因此只能说明依据缺口，不能判断具体录音缺陷。',
                               coverage=[]), ensure_ascii=False)
        from openai import DefaultAsyncHttpxClient
        with endpoint(chunk_delay=0, bodies=[body]) as (base, requests, _), patch('openai.DefaultAsyncHttpxClient',
                side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            run_cloud_turn(cfg, state, execute_action=lambda *_:self.fail('No Praat dispatch'), cancel=threading.Event())
        self.assertTrue(state.metrics.get('report_verified'), state.reason)
        self.assertEqual(len(requests), 1)
        wire = json.dumps(requests[0]['messages'], ensure_ascii=False)
        for item in state.dialogue_context:
            self.assertNotIn(item['content'], wire)
            self.assertIn(item['content'][:100], wire)
        self.assertEqual(wire.count('HISTORY-'), 4)
        self.assertEqual(state.metrics['request_timings'][0]['phase'], 'report')

    def test_static_rules_do_not_depend_on_goal_failures_or_evidence(self):
        cfg = config()
        self.assertEqual(planner_system(cfg, '测量时长'), planner_system(cfg, '比较标准音和语调'))
        a, b = AnalysisState('解释原理', ''), AnalysisState('分析录音发音选段', '')
        b.failures['measure'] = 1
        self.assertEqual(report_instructions(cfg, a), report_instructions(cfg, b))

    def test_three_native_dialogue_turns_keep_wire_prefix_and_connection(self):
        from praat_ai.session_context import PhaseContext
        cfg = config()
        cfg.api.thinking_level = 'off'
        cfg.api.token_mode = 'provider'
        ctx = PhaseContext()
        from openai import DefaultAsyncHttpxClient
        with endpoint(chunk_delay=0) as (base, requests, ports), patch('openai.DefaultAsyncHttpxClient',
                side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            runtime = CloudRuntime()
            try:
                for goal in ('什么是基频？', '它与音高有什么关系？', '举一个例子。'):
                    state = AnalysisState(goal, '')
                    state.phase_context = ctx
                    run_dialogue_turn(cfg, state, kind='concept', cancel=threading.Event(),
                                      on_text=lambda _: None, runtime=runtime)
                    self.assertEqual(state.status, 'complete', state.reason)
                self.assertEqual(len(requests), 3)
                self.assertEqual(len(set(ports)), 1)
                for a, b in zip(requests, requests[1:]):
                    self.assertEqual(a['messages'], b['messages'][:len(a['messages'])])
                self.assertEqual([m['role'] for m in requests[-1]['messages']],
                                 ['system', 'user', 'user', 'assistant', 'user', 'assistant', 'user'])
                guidance = str(requests[-1]['messages'][1]['content'])
                self.assertIn('专业讲解约定', guidance)
                self.assertEqual(sum('专业讲解约定' in str(m['content'])
                                     for m in requests[-1]['messages']), 1)
                self.assertIn('wire', state.metrics['request_timings'][0])
            finally:
                runtime.close(wait=True)

    def test_usage_missing_inclusive_exclusive_and_no_double_add(self):
        from praat_ai.cloud_metrics import normalize_usage
        self.assertIsNone(normalize_usage({})['cache_read_tokens'])
        usage = normalize_usage({'prompt_tokens':100, 'completion_tokens':12,
                                 'prompt_tokens_details':{'cached_tokens':80}})
        self.assertEqual(usage['input_tokens_total'], 100)
        self.assertEqual(usage['cache_read_tokens'], 80)
        written = normalize_usage({'prompt_tokens':100,
                                   'prompt_tokens_details':{'cached_tokens':0,'cache_creation_input_tokens':60}})
        self.assertEqual(written['cache_write_tokens'], 60)
        usage = normalize_usage({'input_tokens':20, 'cache_read_input_tokens':80,
                                 'cache_creation_input_tokens':5}, exclusive=True)
        self.assertEqual(usage['input_tokens_total'], 105)

    def test_persisted_protocol_roundtrip_and_epoch_compaction(self):
        from praat_ai.session_context import PhaseContext, native_history
        with tempfile.TemporaryDirectory() as root:
            store = ModernStore(Path(root) / 'history.sqlite3')
            sid = store.new_session()['id']
            ctx = PhaseContext(messages=native_history([dict(role='user', content='早期问题'),
                                                       dict(role='assistant', content='正式回答')]))
            store.save_model_context(sid, 'task', 'dialogue', ctx)
            recovered = store.model_context(sid, 'dialogue')
            self.assertEqual(len(recovered.messages), 2)
            store.summarize(sid, '忠实摘要', 9)
            self.assertGreater(store.context_epoch(sid), recovered.epoch)
            self.assertIsNone(store.model_context(sid, 'dialogue'))

    def test_filename_hint_is_bound_metadata_with_user_and_dictionary_priority(self):
        from praat_ai.materials import filename_pronunciation
        hint = filename_pronunciation('こんにちは_take2.wav', [], dictionary_available=False)
        self.assertEqual(hint['candidate'], 'こんにちは')
        self.assertEqual(hint['source'], 'filename')
        self.assertIn('根据文件名推测', hint['status'])
        for name in ('recording_001.wav', 'segment.wav', 'take2.wav', 'apple_or_pear.wav', 'aaa02.wav',
                     '日语练习.wav', 'こんにちは_こんばんは.wav', '[sample].wav'):
            self.assertIsNone(filename_pronunciation(name, [], dictionary_available=False))
        self.assertIsNone(filename_pronunciation('こんにちは.wav', ['我读的是「こんばんは」'], dictionary_available=False))
        self.assertIsNone(filename_pronunciation('こんにちは.wav', [], dictionary_available=True))

    def test_phase_summary_preserves_counters_evidence_and_complete_tool_transaction(self):
        import asyncio
        from pydantic_ai import Agent
        from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart, ToolCallPart, ToolReturnPart
        from pydantic_ai.models.function import FunctionModel
        from praat_ai.cloud_agent import CloudSession
        from praat_ai.escape_policy import Budget, Evidence
        from praat_ai.session_context import PhaseContext
        state = AnalysisState('原任务', '', budget=Budget(12000, 256))
        state.failures['measure'] = 1
        state.stagnant = 1
        state.tool_attempts = 2
        state.requests = 3
        state.evidence = [Evidence('duration', {}, ['时长 = 1 s'], '原对象')]
        state.attempts = [dict(status='unknown', execution='unknown', reason='原投递结果不明')]
        model = FunctionModel(lambda messages, info: ModelResponse(parts=[TextPart('原任务的忠实摘要：保留测量来源与未知投递事实。')]))
        cfg = config()
        cfg.api.limit_tokens = True
        cfg.api.token_mode = 'manual'
        session = CloudSession(cfg, state, None, threading.Event(), None, model, [], None, None, None)
        session.phase = 'report'
        context = PhaseContext()
        context.configure('固定报告规则')
        for i in range(6):
            context.messages.extend([ModelRequest(parts=[UserPromptPart('旧目标' + '材料' * 900)]),
                                     ModelResponse(parts=[ToolCallPart('measure', {}, tool_call_id=f'call-{i}')]),
                                     ModelRequest(parts=[ToolReturnPart('measure', '来源完整的测量结果', tool_call_id=f'call-{i}')]),
                                     ModelResponse(parts=[TextPart('已有正式报告')])])
        state.phase_contexts['report'] = context
        before = copy.deepcopy((state.failures, state.stagnant, state.tool_attempts, state.evidence, state.attempts))
        asyncio.run(session.compact_phase('新增分析目标'))
        self.assertEqual(state.requests, 4)
        self.assertEqual(before, (state.failures, state.stagnant, state.tool_attempts, state.evidence, state.attempts))
        self.assertEqual(context.epoch, 1)
        self.assertTrue(context.archives)
        calls = [p.tool_call_id for m in context.messages for p in m.parts if isinstance(p, ToolCallPart)]
        returns = [p.tool_call_id for m in context.messages for p in m.parts if isinstance(p, ToolReturnPart)]
        self.assertEqual(calls, returns)
        self.assertEqual(calls, ['call-4', 'call-5'])

    def test_configuration_change_keeps_formal_history_and_evidence_projection_omits_metrics(self):
        from praat_ai.session_context import PhaseContext, evidence_projection
        history = [dict(role='user', content='明确的用户约束'), dict(role='assistant', content='正式答复')]
        ctx = PhaseContext()
        ctx.sync_formal(history)
        ctx.configure('旧规则', baseline=history)
        ctx.configure('新规则', baseline=history)
        self.assertIn('明确的用户约束', str(ctx.messages))
        self.assertEqual(ctx.epoch, 1)
        projected = evidence_projection([dict(evidence=[dict(rows=['真实测量 15 ms'], source='原范围')],
                                             metrics=dict(secret='not model input'), attempts=[dict(status='unknown',execution='unknown',script='not model input')])])
        self.assertIn('15 ms', str(projected))
        self.assertIn('unknown', str(projected))
        self.assertNotIn('secret', str(projected))
        self.assertNotIn('script', str(projected))

    def test_dialogue_compression_does_not_budget_unrelated_execution_logs(self):
        from praat_ai.modern_budget import compact
        cfg = config()
        cfg.api.token_mode = 'manual'
        cfg.api.max_context_tokens = 5000
        cfg.api.plan_max_tokens = 256
        history = [dict(role='user' if i % 2 == 0 else 'assistant', content='x' * 1900, _seq=i+1) for i in range(12)]
        huge = [dict(evidence=[dict(rows=['q' * 80000], source='另一任务')], metrics=dict(log='q'*80000))]
        with tempfile.TemporaryDirectory() as root:
            store = ModernStore(Path(root)/'history.sqlite3')
            sid = store.new_session()['id']
            result = compact(store, sid, cfg, history, huge, '什么是基频？', '', phase='dialogue',
                             cancel=threading.Event(), emit=lambda *_:None,
                             requester=lambda *a, **kw:'较早对话的忠实短摘要')
            self.assertEqual(len(result), 7)
            self.assertEqual(store.context_epoch(sid), 1)

    def test_output_limit_retry_preserves_report_validator_request_without_duplicate_goal(self):
        import asyncio
        from pydantic_ai import Agent, ModelRetry
        from pydantic_ai.exceptions import ModelHTTPError
        from pydantic_ai.messages import ModelResponse, TextPart, UserPromptPart, RetryPromptPart
        from pydantic_ai.models.function import FunctionModel
        from praat_ai.cloud_agent import CloudSession
        seen = []
        def respond(messages, info):
            seen.append(copy.deepcopy(messages))
            if len(seen) == 2:
                raise ModelHTTPError(400, 'fixture', {'error':'max_tokens output length rejected'})
            return ModelResponse(parts=[TextPart('invalid' if len(seen) == 1 else 'corrected')])
        model = FunctionModel(respond)
        state = AnalysisState('原报告目标', '')
        session = CloudSession(config(), state, None, threading.Event(), None, model, [], None, None, None)
        session.phase_context('report', '固定报告规则')
        agent = Agent(model, output_type=str, retries=2)
        @agent.output_validator
        def validate(result):
            if result == 'invalid':
                raise ModelRetry('保留此原生校验纠错消息')
            return result
        self.assertEqual(asyncio.run(session.drive(agent, '原报告目标')), 'corrected')
        self.assertEqual(state.requests, 3)
        self.assertTrue(any(isinstance(p, RetryPromptPart) for m in seen[-1] for p in m.parts))
        self.assertEqual(sum(isinstance(p, UserPromptPart) and p.content == '原报告目标'
                             for m in seen[-1] for p in m.parts), 1)

    def test_final_http_planner_and_report_prefixes_are_separate_and_append_only(self):
        import asyncio
        import json
        from praat_ai import tools
        from praat_ai.cloud_agent import CloudSession, create_model
        from praat_ai.cloud_metrics import wire_fingerprint
        cfg = config()
        report = json.dumps(dict(analysis='针对原始目标，目前没有实际测量和录音听感，因此只能说明依据缺口，不能判断具体录音缺陷。',
                                 coverage=[dict(item='分析目标', status='partial', explanation='缺少专业测量与录音证据，保留原目标。')]), ensure_ascii=False)
        from openai import DefaultAsyncHttpxClient
        with endpoint(chunk_delay=0, bodies=['规划阶段结束',report]*3) as (base, requests, ports), patch('openai.DefaultAsyncHttpxClient',
                side_effect=lambda **kw: DefaultAsyncHttpxClient(trust_env=False, **kw)):
            cfg.api.base_url = base
            async def run():
                model = create_model(cfg)
                state = AnalysisState('分析声调', '')
                schemas = [s for s in tools.tool_schemas() if s['function']['name'] == 'pitch']
                session = CloudSession(cfg, state, lambda *_:self.fail('fixture must not call a tool'),
                                       threading.Event(), None, model, schemas, None, None, None)
                try:
                    for _ in range(3):
                        await session.execute()
                        await session.report(allow_escalation=False)
                    self.assertEqual(state.requests, 6, state.reason)
                    self.assertEqual(set(state.metrics['request_timings'][i]['phase'] for i in range(6)), {'planner','report'})
                finally:
                    await model.client.close()
            asyncio.run(run())
            self.assertEqual(len(set(ports)), 1)
            for phase_requests in (requests[::2],requests[1::2]):
                fingerprints = [wire_fingerprint(p) for p in phase_requests]
                self.assertEqual(len(set(p['static'] for p in fingerprints)), 1)
                for request in phase_requests:
                    guidance = [m for m in request['messages'] if 'skill=prosody' in str(m.get('content', ''))]
                    self.assertEqual(len(guidance), 1)
                    self.assertEqual(guidance[0]['role'], 'user')
                    self.assertIn('非负变化幅度', str(guidance[0]['content']))
                for a,b in zip(phase_requests,phase_requests[1:]):
                    self.assertEqual(a['messages'], b['messages'][:len(a['messages'])])
            self.assertNotEqual(wire_fingerprint(requests[0])['static'],wire_fingerprint(requests[1])['static'])

    def test_session_summary_seeds_shared_sdk_usage_and_request_budget(self):
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models.function import FunctionModel
        from praat_ai.cloud_agent import CloudSession
        state = AnalysisState('原目标', '')
        state.requests = 1
        state.metrics['request_timings'] = [dict(phase='summary', request=1, input_tokens_total=1000,
                                               cache_read_tokens=800, output_tokens=100, elapsed_seconds=.5)]
        model = FunctionModel(lambda *_:ModelResponse(parts=[TextPart('answer')]))
        session = CloudSession(config(), state, None, threading.Event(), None, model, [], None, None, None)
        self.assertEqual((session.usage.requests,session.usage.input_tokens,session.usage.output_tokens), (1,1000,100))
        self.assertEqual(state.requests, 1)


if __name__ == '__main__':
    unittest.main()
