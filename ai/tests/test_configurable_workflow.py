"""Offline acceptance of workflow policy, request counts and immutable delivery."""
import asyncio
import copy
import hashlib
import json
import tempfile
import threading
import time
import unittest
import wave
from dataclasses import asdict, replace
from pathlib import Path
from unittest.mock import patch

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import chat, delivery, tools
from praat_ai.cloud_agent import CloudSession, run_cloud_turn
from praat_ai.config import AppConfig
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.modern_settings import SettingsService
from praat_ai.modern_store import ModernStore
from praat_ai.workflow_policy import WorkflowPolicy, measurement_complete

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n2\tSound\tother\t0\n'


def config():
    cfg = AppConfig()
    cfg.api.base_url, cfg.api.model = 'https://test.invalid/v1', 'fixture'
    cfg.api.thinking_level = 'off'
    cfg.api.audio_input_enabled = False
    return cfg


def report(goal, value='1.25'):
    return json.dumps(dict(analysis=f'工具测量确认该声音的总时长为 {value} 秒，这是原目标的真实测量；其余声音性质需要另外的证据。',
                          coverage=[dict(item=goal, status='complete', explanation='目标所需结果与解释已交付')]), ensure_ascii=False)


class WorkflowSettingsTests(unittest.TestCase):
    def test_legacy_read_no_write_and_lossless_save_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'config.json'
            original = dict(api=dict(api_key='fixture-secret'), modern=dict(extra='keep'), unknown=dict(old=1))
            path.write_text(json.dumps(original), encoding='utf8')
            service = SettingsService(path)
            before = path.read_bytes()
            self.assertEqual(service.get()['workflow'], asdict(WorkflowPolicy()))
            self.assertEqual(path.read_bytes(), before)
            snapshot = service.candidate()
            service.save(dict(workflow=asdict(WorkflowPolicy.for_preset('deep'))))
            self.assertEqual(snapshot.workflow.preset, 'balanced')
            service.save(dict(preferences=dict(theme='dark')))
            raw = json.loads(path.read_text(encoding='utf8'))
            self.assertEqual(raw['modern']['workflow']['l2_deadline_sec'],60)
            self.assertEqual(raw['modern']['extra'], 'keep')
            self.assertEqual(raw['unknown'], dict(old=1))
            self.assertEqual(raw['api']['api_key'],'fixture-secret')
            self.assertEqual(SettingsService(path).candidate().workflow.preset, 'deep')

    def test_strict_types_and_invalid_save_is_atomic(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)/'config.json'; path.write_text('{}')
            for key, value in [('l0_enabled',1),('l4_enabled','false'),('l1_repair_limit',True),
                               ('l2_deadline_sec','15'),('l2_deadline_sec',0),('l2_request_limit',3),
                               ('l3_mode','unlimited'),('schema_version',2)]:
                with self.subTest(key=key,value=value), self.assertRaises(ValueError):
                    SettingsService(path).save(dict(workflow={key:value}))
                self.assertEqual(path.read_text(),'{}')
            self.assertEqual(WorkflowPolicy.validate(dict(l2_mode='off')).preset,'custom')

    def test_host_persists_results_before_cancel_and_followup_reuses_source_snapshot(self):
        from praat_ai.modern_app import ModernApplication
        class Executor:
            def __init__(self):self.calls=[];self.captures=0;self.ready=threading.Event();self.gate=threading.Event()
            def capture_target(self,text):self.captures+=1;return CONTEXT
            def run(self,**kwargs):
                self.calls.append(kwargs)
                payload=dict(content='tone 总时长 = 1.25 秒',results=[dict(id='fact',status='success')],
                    goal='当前声音时长',deliveries=['当前声音时长'],evidence=[asdict(Evidence('duration',{'object':1},['tone 总时长 = 1.25 秒'],CONTEXT))],
                    attempts=[dict(tool='duration',arguments={'object':1},status='success',execution=delivery.DELIVERED)],
                    target=CONTEXT,material_metadata={},metrics=dict(workflow_policy=asdict(kwargs['config'].workflow)))
                kwargs['emit']('results',payload);self.ready.set()
                self.gate.wait(2)
                return {**payload,'result_content':payload['content'],'status':'cancelled' if kwargs['cancel'].is_set() else 'complete'}
        with tempfile.TemporaryDirectory() as root:
            path=Path(root)/'config.json'
            path.write_text(json.dumps(dict(api=dict(enabled=True,base_url='http://127.0.0.1:8999/v1',model='fixture',token_mode='manual',stop_local_service=False),modern=dict(workflow=asdict(WorkflowPolicy.for_preset('fast'))))),encoding='utf8')
            executor=Executor();app=ModernApplication(Path(root)/'store',path,executor=executor)
            try:
                sid=app.store.new_session()['id'];task=app.submit(sid,'当前声音时长')
                self.assertTrue(executor.ready.wait(2))
                self.assertIn('1.25',str(app.store.task_evidence(sid,task['id'])))
                self.assertIn('1.25',app.get_session(sid)['messages'][-1]['result_content'])
                app.settings.save(dict(workflow=asdict(WorkflowPolicy.for_preset('deep'))))
                self.assertEqual(executor.calls[0]['config'].workflow.preset,'fast')
                app.cancel(task['id']);executor.gate.set();app.tasks[task['id']]['thread'].join(2)
                self.assertEqual(app.get_session(sid)['messages'][-1]['status'],'cancelled')
                next_task=app.followup(sid,task['id'],'L2');app.tasks[next_task['id']]['thread'].join(2)
                self.assertEqual(executor.captures,1)
                self.assertEqual(executor.calls[-1]['continuation']['target'],CONTEXT)
                self.assertEqual(executor.calls[-1]['config'].workflow.preset,'fast')
                self.assertEqual(app.settings.get()['workflow']['preset'],'deep')
            finally:executor.gate.set();app.close()

    def test_raw_followup_disappears_when_original_version_or_capability_is_unavailable(self):
        from praat_ai.modern_app import ModernApplication
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'fixture.wav';source.write_bytes(b'original version')
            cfg=config();cfg.workflow=WorkflowPolicy.for_preset('fast')
            cfg.api.audio_input_enabled=True;cfg.api.audio_input_source='preset'
            record=dict(evidence=[{'tool':'duration'}],material_metadata=dict(recording_source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest()))
            app=ModernApplication.__new__(ModernApplication)
            self.assertIn('L3',app.followup_actions(cfg,record))
            source.write_bytes(b'changed version')
            self.assertNotIn('L3',app.followup_actions(cfg,record))
            self.assertIn('L2',app.followup_actions(cfg,record))
            source.write_bytes(b'original version');cfg.api.audio_input_enabled=False
            self.assertNotIn('L3',app.followup_actions(cfg,record))

    def test_general_discussion_followup_needs_actual_results(self):
        """「基于这些结果进行通用讨论」只在真有结果时提供：没有测量的轮次不给续接入口。"""
        from praat_ai.modern_app import ModernApplication
        cfg = config(); cfg.workflow = WorkflowPolicy.for_preset('balanced')
        app = ModernApplication.__new__(ModernApplication)
        self.assertEqual(app.followup_actions(cfg, dict(evidence=[], goal='test')), [])
        self.assertIn('L4', app.followup_actions(cfg, dict(evidence=[{'tool': 'duration'}], goal='测时长')))


class WorkflowRoutingTests(unittest.TestCase):
    def run_tools(self, goal='测量 Sound 1 的总时长', policy=None, calls=None, failing=False):
        policy = policy or WorkflowPolicy()
        state = AnalysisState(goal, CONTEXT, workflow=policy)
        seen, dispatched, visible = [], [], []
        planned = calls if calls is not None else [('duration', {'object':1})]
        def response(messages, info):
            seen.append(copy.deepcopy(messages))
            if info.function_tools:
                if len(seen) == 1:
                    return ModelResponse([ToolCallPart(name,args,tool_call_id=str(i)) for i,(name,args) in enumerate(planned)])
                return ModelResponse([TextPart('结束规划')])
            self.assertTrue(visible, 'Results must precede report request')
            return ModelResponse([TextPart(report(goal))])
        def action(name, arguments, index):
            dispatched.append(name)
            return chat.AgentStep(index, name, arguments, not failing, '完成' if not failing else 'invalid parameter',
                                  script='template', results=['tone 总时长 = 1.250000 秒'] if not failing else [],
                                  execution=delivery.DELIVERED)
        state.on_results = lambda state:visible.append(copy.deepcopy(state.results))
        run_cloud_turn(config(), state, execute_action=action, cancel=threading.Event(),
                       model=FunctionModel(response), tool_schemas=[s for s in tools.tool_schemas() if s['function']['name'] in {'duration','object_info'}])
        return state, seen, dispatched

    def test_planned_measurement_stops_with_complete_native_tool_pair_no_report(self):
        state, seen, dispatched = self.run_tools()
        self.assertEqual(state.status,'complete',state.reason)
        self.assertEqual(state.requests,1)
        self.assertEqual(len(seen),1)
        self.assertEqual(dispatched,['duration'])
        self.assertNotIn('report',state.phase_contexts)
        returns = [p for m in state.phase_contexts['planner'].messages if isinstance(m,ModelRequest) for p in m.parts if isinstance(p,ToolReturnPart)]
        self.assertEqual(len(returns),1)
        self.assertEqual(returns[0].tool_call_id,'0')

    def test_mixed_calls_and_wrong_quantity_do_not_short_circuit(self):
        for goal,calls in [('测量 Sound 1 的总时长',[('duration',{'object':1}),('object_info',{'object':1})]),
                           ('测量 Sound 1 的总时长',[('object_info',{'object':1})]),
                           ('测量时长并保存文件',[('duration',{'object':1})])]:
            state,seen,_ = self.run_tools(goal=goal,calls=calls)
            self.assertFalse(state.metrics.get('planner_completed_locally',False))
            self.assertGreaterEqual(state.requests,2)

    def test_explanation_disabled_keeps_published_results_and_gap(self):
        state,seen,_ = self.run_tools(goal='测量当前声音时长并解释结果',policy=WorkflowPolicy.for_preset('fast'))
        self.assertEqual(state.requests,1)
        self.assertEqual(state.status,'partial')
        self.assertIn('1.250000',state.report)
        self.assertNotIn('report',state.phase_contexts)

    def test_each_disabled_stage_is_obeyed_and_token_mode_does_not_enable_it(self):
        for token_limit in (True,False):
            cfg=config();cfg.api.limit_tokens=token_limit
            for mode in ('L0','L2','L3','L4'):
                policy=WorkflowPolicy.validate(dict(l0_enabled=False,l2_mode='off',l3_mode='off',l4_enabled=False))
                state=AnalysisState('分析当前声音',CONTEXT,workflow=policy,
                    continuation=mode!='L0',continuation_mode=mode if mode!='L0' else '')
                run_cloud_turn(cfg,state,execute_action=lambda *_:self.fail('No tools'),cancel=threading.Event(),
                               model=FunctionModel(lambda *_:self.fail('No model')))
                self.assertEqual(state.requests,0,(mode,state.reason))
        state,_,dispatched=self.run_tools(policy=WorkflowPolicy.for_preset('fast'),failing=True)
        self.assertEqual(dispatched,['duration'])
        self.assertEqual(state.failures['duration'],1)

    def test_completion_requires_current_success_not_unknown_or_filename(self):
        state,_,_=self.run_tools()
        self.assertTrue(measurement_complete(state))
        state.attempts[0]['status']='unknown'
        self.assertFalse(measurement_complete(state))
        state.evidence=[]
        state.material_metadata={'pronunciation_hint':{'candidate':'1.25','source':'filename'}}
        self.assertFalse(measurement_complete(state))

    def test_auto_raw_analysis_with_missing_measurement_skips_redundant_l2(self):
        from praat_ai.materials import TaskMaterials
        with tempfile.TemporaryDirectory() as root:
            source=Path(root)/'source.wav'
            with wave.open(str(source),'wb') as wav:
                wav.setparams((1,2,16000,0,'NONE','not compressed'));wav.writeframes(b'\0\0'*1600)
            material=TaskMaterials(Path(root)/'tasks');material.snapshot_wav(source)
            material.source_kind='praat'  # Existing immutable snapshot; normal tool entry first.
            cfg=config();cfg.api.audio_input_enabled=True
            state=AnalysisState('分析当前声音',CONTEXT,workflow=WorkflowPolicy.for_preset('deep'))
            seen=[]
            def response(messages,info):
                seen.append(copy.deepcopy(messages))
                if info.function_tools:return ModelResponse([ToolCallPart('duration',{'object':1},tool_call_id='d')])
                return ModelResponse([TextPart(json.dumps(dict(analysis='已实际输入原始音频，仅提供定性观察；专业测量依赖缺失，因此相关测量目标仍未完成。',coverage=[dict(item=state.goal,status='partial',explanation='专业测量依赖缺失，需要补充可用工具')]),ensure_ascii=False))])
            try:
                run_cloud_turn(cfg,state,execute_action=lambda name,args,index:chat.AgentStep(index,name,args,False,'依赖未安装',script='template',execution=delivery.DELIVERED),
                               cancel=threading.Event(),model=FunctionModel(response),materials=material,
                               tool_schemas=[s for s in tools.tool_schemas() if s['function']['name']=='duration'])
            finally:material.close()
            self.assertEqual(state.requests,2,state.reason)
            self.assertTrue(state.audio_report_completed,state.report)
            self.assertEqual([t['mode'] for t in state.metrics['request_timings']],['L0','L3'])

    def test_l0_disabled_cannot_export_through_raw_analysis_override(self):
        cfg=config();cfg.api.audio_input_enabled=True
        state=AnalysisState('本次分析原材料',CONTEXT,workflow=WorkflowPolicy.for_preset('chat'),workflow_override='L3')
        run_cloud_turn(cfg,state,execute_action=lambda *_:self.fail('No Praat'),prepare_audio=lambda:self.fail('No hidden export'),
                       cancel=threading.Event(),model=FunctionModel(lambda *_:self.fail('No model without material')))
        self.assertEqual(state.requests,0)
        self.assertEqual(state.status,'partial')


class L2BudgetTests(unittest.TestCase):
    def state(self, policy):
        state=AnalysisState('解释当前声音时长',CONTEXT, workflow=policy,continuation=True,continuation_mode='L2')
        state.evidence=[Evidence('duration',{'object':1},['tone 总时长 = 1.25 秒'],CONTEXT)]
        state.attempts=[dict(tool='duration',arguments={'object':1},status='success',execution=delivery.DELIVERED,rows=state.evidence[0].rows)]
        state.measurement_content='tone 总时长 = 1.25 秒'
        return state

    def test_balanced_default_allows_one_guard_correction(self):
        state=self.state(WorkflowPolicy())
        calls=[]
        def response(messages,info):
            calls.append(messages)
            return ModelResponse([TextPart(report(state.goal,'99' if len(calls)==1 else '1.25'))])
        run_cloud_turn(config(),state,execute_action=lambda *_:self.fail('No remeasurement'),
                       cancel=threading.Event(),model=FunctionModel(response))
        self.assertEqual(state.requests,2,state.reason)
        self.assertTrue(state.metrics.get('report_verified'),state.reason)

    def test_explicit_points_override_editor_selection_in_results_and_report(self):
        from praat_ai.cloud_agent import report_context
        from praat_ai.workflow_policy import publish_results
        context='id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\ttone\t1\t0.446194\t0.661580\n'
        for times in (.3,'0.2,0.3',[.2,.3]):
            with self.subTest(times=times):
                state=self.state(WorkflowPolicy());state.context_text=context
                args={'object':1,'time':times,'formant':1}
                state.evidence=[Evidence('formant_frequency',args,['第 1 共振峰（0.300 秒处）：频率 436.550 Hz'],context)]
                state.attempts=[dict(tool='formant_frequency',arguments=args,status='success',execution=delivery.DELIVERED,rows=state.evidence[0].rows)]
                publish_results(state)
                expected=[.3] if times==.3 else [.2,.3]
                self.assertEqual(state.results[0]['range'],dict(kind='query_points',requested_times=expected))
                report_data=report_context(state,preserve=True)
                self.assertEqual(report_data['measurement_scopes']['E1'],state.results[0]['range'])
                self.assertEqual(report_data['target_material'][0]['editor_selection'],(.446194,.66158))
                self.assertEqual(report_data['evidence'][0]['source'],context)

    def test_explicit_general_knowledge_with_negated_measurement_skips_planner(self):
        goal='请解释 F0 和 F1、F2 的区别。这是一般知识问题，不需要测量我的录音。'
        cfg=config();state=AnalysisState(goal,CONTEXT,workflow=WorkflowPolicy())
        seen=[]
        def response(messages,info):
            seen.append(bool(info.function_tools))
            return ModelResponse([TextPart(json.dumps(dict(analysis='一般知识：基频表示声带振动的重复频率；共振峰表示声道共振形成的频谱峰，它们分别反映音高和音色。',coverage=[dict(item=goal,status='complete',explanation='已解释概念差异，无需录音测量')]),ensure_ascii=False))])
        run_cloud_turn(cfg,state,execute_action=lambda *_:self.fail('No Praat measurement'),
                       cancel=threading.Event(),model=FunctionModel(response))
        self.assertEqual(seen,[False],state.reason)
        self.assertEqual(state.metrics['request_timings'][0]['mode'],'L4')
        self.assertTrue(state.metrics.get('report_verified'),state.reason)

    def test_brief_report_does_not_inherit_expensive_planner_thinking_on_wire(self):
        from test_dialogue_protocol import endpoint
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient
        for level in ('high', 'medium'):
            with self.subTest(level=level), endpoint(chunk_delay=0, bodies=[report('解释当前声音时长')]) as (base, requests, _):
                cfg=config();cfg.api.base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
                cfg.api.model='qwen3.8-omni-flash';cfg.api.thinking_level=level
                state=self.state(WorkflowPolicy())
                with patch('openai.AsyncOpenAI',side_effect=lambda **kw:AsyncOpenAI(
                        **{**kw,'base_url':base,'http_client':DefaultAsyncHttpxClient(trust_env=False)})):
                    run_cloud_turn(cfg,state,execute_action=lambda *_:self.fail('No remeasurement'),
                                   cancel=threading.Event())
                self.assertTrue(state.metrics.get('report_verified'),state.reason)
                self.assertEqual(state.status,'complete')
                self.assertEqual(len(requests),1)
                self.assertFalse(requests[0]['enable_thinking'])
                self.assertNotIn('thinking_budget',requests[0])
                self.assertEqual(requests[0].get('max_completion_tokens',requests[0].get('max_tokens')),1500)
                self.assertEqual(cfg.api.thinking_level,level)

    def test_full_or_forced_high_report_keeps_requested_thinking(self):
        for full,forced in ((True,False),(False,True)):
            with self.subTest(full=full,forced=forced):
                cfg=config();cfg.api.base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
                cfg.api.model='qwen3.7-flash';cfg.api.thinking_level='high';cfg.api.force_deep_thinking=forced
                state=self.state(replace(WorkflowPolicy(),l2_detail='full' if full else 'brief'))
                observed=[]
                def response(messages,info):
                    observed.append(copy.deepcopy(info.model_settings))
                    return ModelResponse([TextPart(report(state.goal))])
                run_cloud_turn(cfg,state,execute_action=lambda *_:self.fail('No remeasurement'),
                               cancel=threading.Event(),model=FunctionModel(response))
                self.assertTrue(state.metrics.get('report_verified'),state.reason)
                self.assertTrue(observed[0]['extra_body']['enable_thinking'])
                self.assertGreater(observed[0]['max_tokens'],1500)

    def test_planner_keeps_high_while_brief_report_uses_its_own_thinking(self):
        cfg=config();cfg.api.base_url='https://dashscope.aliyuncs.com/compatible-mode/v1'
        cfg.api.model='qwen3.7-flash';cfg.api.thinking_level='high'
        state=AnalysisState('分析当前声音时长',CONTEXT,workflow=WorkflowPolicy())
        observed=[]
        def response(messages,info):
            observed.append((bool(info.function_tools),copy.deepcopy(info.model_settings)))
            if info.function_tools and not any(isinstance(part,ToolReturnPart) for message in messages for part in message.parts):
                return ModelResponse([ToolCallPart('duration',{'object':1},tool_call_id='d')])
            return ModelResponse([TextPart(report(state.goal))])
        def execute(name,args,index):
            return chat.AgentStep(index,name,args,True,'measured',results=['tone 总时长 = 1.25 秒'],
                                  script='template',execution=delivery.DELIVERED)
        run_cloud_turn(cfg,state,execute_action=execute,cancel=threading.Event(),model=FunctionModel(response),
                       tool_schemas=[s for s in tools.tool_schemas() if s['function']['name']=='duration'])
        self.assertTrue(state.metrics.get('report_verified'),state.reason)
        self.assertTrue(all(settings['extra_body']['enable_thinking'] for planner,settings in observed if planner))
        self.assertFalse(next(settings for planner,settings in observed if not planner)['extra_body']['enable_thinking'])

    def test_guard_correction_shares_phase_limit_and_keeps_results(self):
        for limit,expected in [(1,1),(2,2)]:
            state=self.state(replace(WorkflowPolicy(),l2_request_limit=limit))
            calls=[]
            def response(messages,info):
                calls.append(copy.deepcopy(messages))
                return ModelResponse([TextPart(report(state.goal, '99' if len(calls)==1 else '1.25'))])
            run_cloud_turn(config(),state,execute_action=lambda *_:self.fail('No remeasurement'),
                cancel=threading.Event(),model=FunctionModel(response))
            self.assertEqual(state.requests,expected,state.reason)
            self.assertEqual(state.metrics['l2_requests'],expected)
            self.assertIn('1.25',state.measurement_content)
            self.assertEqual(bool(state.metrics.get('report_verified')),limit==2)
            if limit==1:self.assertNotIn('99',state.report)

    def test_preparation_timeout_stops_stage_without_another_route(self):
        state=self.state(replace(WorkflowPolicy(),l2_deadline_sec=1))
        async def delayed(*args,**kwargs):await asyncio.sleep(2)
        with patch.object(CloudSession,'compact_phase',side_effect=delayed):
            started=time.monotonic()
            run_cloud_turn(config(),state,execute_action=lambda *_:self.fail('No remeasurement'),cancel=threading.Event(),
                model=FunctionModel(lambda *_:self.fail('No request after preparation deadline')))
        self.assertLess(time.monotonic()-started,1.6)
        self.assertEqual(state.requests,0)
        self.assertIn('1.25',state.report)
        self.assertEqual(state.status,'partial')

    def test_results_are_durable_before_model_failure_and_one_ledger_per_task(self):
        with tempfile.TemporaryDirectory() as root:
            store=ModernStore(Path(root)/'store.sqlite3');sid=store.new_session()['id']
            state=self.state(WorkflowPolicy());state.task_id='task'
            def save(current):store.save_evidence(sid,'task',dict(evidence=[asdict(e) for e in current.evidence],attempts=current.attempts))
            state.on_results=save
            from praat_ai.workflow_policy import publish_results
            publish_results(state)
            def failing(*_):
                self.assertIn('1.25',str(store.task_evidence(sid,'task')))
                raise RuntimeError('offline model failure')
            run_cloud_turn(config(),state,execute_action=lambda *_:self.fail('No remeasurement'),cancel=threading.Event(),model=FunctionModel(failing))
            self.assertIn('1.25',state.report)
            save(state)
            self.assertEqual(len(store.context(sid)[1]),1)

    def test_cancelled_explanation_keeps_result_and_no_verified_draft(self):
        state=self.state(WorkflowPolicy());cancel=threading.Event()
        async def slow(messages,info):
            await asyncio.sleep(2)
            return ModelResponse([TextPart(report(state.goal))])
        timer=threading.Timer(.15,cancel.set);timer.start()
        try:
            run_cloud_turn(config(),state,execute_action=lambda *_:self.fail('No remeasurement'),cancel=cancel,model=FunctionModel(slow))
        finally:timer.cancel()
        self.assertEqual(state.status,'cancelled')
        self.assertEqual(state.requests,1)
        self.assertIn('1.25',state.measurement_content)
        self.assertFalse(state.metrics.get('report_verified',False))

    def test_final_http_three_turns_and_guard_correction_keep_prefix_and_static_schema(self):
        from test_dialogue_protocol import endpoint
        from praat_ai.cloud_metrics import wire_fingerprint
        from openai import DefaultAsyncHttpxClient
        goal='解释当前声音时长'
        for bodies in ([report(goal)]*3,[report(goal,'99'),report(goal)]):
            with endpoint(chunk_delay=0,bodies=bodies) as (base,requests,_), patch('openai.DefaultAsyncHttpxClient',
                    side_effect=lambda **kw:DefaultAsyncHttpxClient(trust_env=False,**kw)):
                cfg=config();cfg.api.base_url=base;cfg.api.model='qwen3.8-omni-flash'
                cfg.api.token_mode='manual'
                contexts={}
                turns=3 if len(bodies)==3 else 1
                for _ in range(turns):
                    state=self.state(replace(WorkflowPolicy(),l2_request_limit=2))
                    state.phase_contexts=contexts
                    run_cloud_turn(cfg,state,execute_action=lambda *_:self.fail('No remeasurement'),cancel=threading.Event())
                    self.assertTrue(state.metrics.get('report_verified'),state.reason)
                    self.assertLessEqual(state.requests,2)
                    contexts=state.phase_contexts
                self.assertEqual(len(requests),len(bodies))
                self.assertEqual(len({wire_fingerprint(p)['static'] for p in requests}),1)
                for before,after in zip(requests,requests[1:]):
                    self.assertEqual(before['messages'],after['messages'][:len(before['messages'])])


if __name__=='__main__':unittest.main()
