"""Scientific method selection, report structure and landmark provenance."""
import asyncio
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
from types import SimpleNamespace
import unittest

from praat_ai import pitch_analysis, report_layout, vot_request
from praat_ai.escape_policy import AnalysisState, Evidence


class ReportStructureTests(unittest.TestCase):
    def test_calculations_move_out_of_conclusions_and_render_idempotently(self):
        state = AnalysisState('比较', '')
        state.evidence = [Evidence('duration', {}, ['时长 = 1 秒'], '')]
        state.metrics['calculation_receipts'] = [dict(equation='(2 / 1) × 100 = 200 % [E1]', value='200 % [E1]')]
        rendered = report_layout.present(state, '结论：(2 / 1) × 100 = 200 % [E1]。')
        self.assertTrue(rendered.startswith('## 分析结论\n\n'))
        self.assertNotIn('×', rendered.split('## 计算过程')[0])
        self.assertIn('(2 / 1) × 100 = 200 %', rendered.split('## 计算过程')[1])
        self.assertEqual(report_layout.present(state, rendered), rendered)

    def test_reversed_negative_vot_does_not_remove_headings_or_other_sentences(self):
        state = AnalysisState('VOT', '')
        state.evidence = [Evidence('vot', {}, [], '')]
        rendered = report_layout.sanitize_vot_explanation(state,
            '## 测量结论\n\n已测得候选值。负值的 VOT 表明在声门开启后存在一段无声闭塞期。\n## 限制\n仍需复核。')
        self.assertIn('## 测量结论', rendered)
        self.assertIn('已测得候选值。', rendered)
        self.assertIn('声带振动早于除阻', rendered)
        self.assertIn('## 限制', rendered)

    def test_model_numbers_cannot_be_promoted_to_manual_landmarks(self):
        with self.assertRaisesRegex(ValueError, '不是用户提供'):
            vot_request.validate_manual_landmarks({'burst': .3, 'voicing': .2}, '计算 VOT')
        vot_request.validate_manual_landmarks({'burst': .3, 'voicing': .2, 'closure_start': .1},
            '爆破300ms，预浊起始200ms，闭塞起始100ms，计算VOT')

    def test_cloud_tool_guard_rejects_invented_manual_landmarks(self):
        from praat_ai import tools
        from praat_ai.tool_guards import ToolGuards
        source='id\tclass\tname\tselected\n1\tSound\tfixture\t1\n'
        state=AnalysisState('计算当前选区的VOT',source)
        guard=ToolGuards(SimpleNamespace(state=state,schemas=tools.tool_schemas(),original_object_ids={1}))
        with self.assertRaisesRegex(tools.ToolError,'不是用户提供'):
            guard.normalize('vot',dict(burst=.3,voicing=.2,closure_start=.1))
        state.goal='爆破300ms，预浊起始200ms，计算VOT'
        self.assertEqual(guard.normalize('vot',dict(burst=.3,voicing=.2))['voicing'],.2)

    def test_different_pitch_methods_cannot_be_merged_as_identical_extraction(self):
        from praat_ai.evidence_quality import compatible_pitch_configurations
        source='id\tclass\tname\tselected\n1\tSound\tfixture\t1\n'
        records=[Evidence('pitch_statistics',{},['基频质量：已筛查','平均基频 = 220 Hz'],source,
            acoustic=dict(kind='pitch',method=dict(name=method)))
            for method in ('filtered_autocorrelation','raw_autocorrelation')]
        self.assertFalse(compatible_pitch_configurations(records))

    def test_rendered_pitch_values_match_the_derived_measurements(self):
        from praat_ai import pitch_events
        rows=['@aipraat-pitch-v1\t0\t0.04\t0.01\tfiltered_autocorrelation\t50\t600\t已筛查',
              *[f'@aipraat-frame-v1\t{time}\t{value}\t1\t1\t1'
                for time,value in ((.01,100),(.02,200),(.03,300))],
              '基频中位数 = 199.8 Hz']
        analysis,_=pitch_events.consume(rows,{'parameter':'median_pitch,pitch_slope'},{},'measure')
        rendered=pitch_events.result_rows(analysis,rows,'measure')
        self.assertIn('基频中位数 = 200.000 Hz',rendered)
        self.assertFalse(any('199.8' in row for row in rendered))
        slope=next(m for m in analysis.measures if m.name=='pitch.slope_absolute')
        self.assertAlmostEqual(slope.value,10000)


PRAAT = Path(os.environ.get('PRAAT_AI_PRAAT_EXECUTABLE') or Path(__file__).resolve().parents[2]/'Praat.exe')


@unittest.skipUnless(PRAAT.exists(), '需要原生 Praat')
class PitchNativeComparisonTests(unittest.TestCase):
    def test_actual_chat_keeps_absolute_times_frames_checks_and_derived_values(self):
        from praat_ai import chat, tools, execution_core
        for tool,args in [('pitch_statistics',{'from':1.9,'to':2.5}),
                          ('pitch',{'time':2.0}), ('pitch',{'time':4.0}),
                          ('measure',{'parameter':'mean_pitch,minimum_pitch','from':1.9,'to':2.5})]:
            with self.subTest(tool=tool,args=args), tempfile.TemporaryDirectory() as d:
                root=Path(d)
                source='id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tfixture\t1\t1.9\t2.5\n'
                context=tools.ToolContext(tools.parse_object_context(source),root/'result.tsv',root/'state.txt')
                def execute(script):
                    full=('Create Sound from formula: "fixture", 1, 0, 1, 16000, ~ '
                          'if x > 0.1 and x < 0.9 then 0.1*sin(2*pi*220*x)+0.03*sin(2*pi*440*x) else 0 fi\n'
                          'Shift times by: 1.7\n'+script)
                    (root/'run.praat').write_text(full,encoding='utf-8')
                    run=subprocess.run([str(PRAAT),'--no-pref-files','--no-plugins','--FULL-TRUST','--run',str(root/'run.praat')],capture_output=True,timeout=30)
                    return run.returncode==0,context.result_path.read_text(encoding='utf-8-sig').splitlines() if context.result_path.exists() else [],run.stderr.decode('utf-16-le','replace')
                step=chat._execute_action(dict(tool=tool,arguments=args),context,execute,1)
                self.assertTrue(step.ok,step.observation)
                self.assertTrue(step.acoustic,step.results)
                self.assertFalse(any(row.startswith('@aipraat-') for row in step.results))
                self.assertEqual(step.acoustic['method']['name'],'filtered_autocorrelation')
                frames=json.loads(Path(step.acoustic['observations_path']).read_text(encoding='utf-8'))['frames']
                self.assertTrue(frames)
                self.assertTrue(all(1.7<=f['time']<=2.7 for f in frames))
                for interval in step.acoustic['intervals']:
                    self.assertLess(interval['start'],interval['end'])
                    self.assertGreaterEqual(interval['start'],1.7)
                for measure in step.acoustic['measures']:
                    if args.get('time')==4:
                        self.assertIsNone(measure['value'])
                    else:
                        self.assertAlmostEqual(measure['value'],220,delta=.2)
                evidence=execution_core.evidence_row(step,source)
                from praat_ai.report_guards import _quantities
                quantities=_quantities('E1',evidence)
                if args.get('time')==4:
                    self.assertFalse(any(q.role=='pitch.value' for q in quantities))
                else:
                    self.assertTrue(any(q.role.startswith('pitch.') for q in quantities))

    def test_embedded_adapter_matches_native_across_methods_and_acoustic_conditions(self):
        import numpy as np
        import parselmouth
        rng = np.random.default_rng(4301)
        rate = 16000
        times = (np.arange(rate) + .5) / rate
        f0 = 150 + 80 * times + 12 * np.sin(2*np.pi*3*times)
        phase = np.cumsum(f0) / rate
        voiced = sum(np.sin(2*np.pi*h*phase)/h for h in range(1, 18))
        envelope = np.where((times > .12) & (times < .86), .08, .0002)
        signal = voiced * envelope + rng.normal(0, .0001, len(times))
        # High harmonic energy and weak voicing exercise more than sine tracking.
        signal += .1*np.sin(2*np.pi*8*phase)*np.exp(-((times-.48)/.16)**2)
        for purpose in pitch_analysis.METHODS:
            for channels in (signal, np.vstack((signal, .7*signal + rng.normal(0,.0001,len(times))))):
                with self.subTest(purpose=purpose, channels=channels.ndim), tempfile.TemporaryDirectory() as d:
                    root=Path(d)
                    sound=parselmouth.Sound(channels, rate)
                    sound.shift_times_by(1.7)
                    sound.save_as_binary_file(str(root/'source.Sound'))
                    script=(f'Read from file: "{(root/"source.Sound").as_posix()}"\n'
                            +pitch_analysis.command(60,600,purpose=purpose,step=.01)+'\n'
                            +f'Save as binary file: "{(root/"native.Pitch").as_posix()}"\n')
                    (root/'compare.praat').write_text(script,encoding='utf-8')
                    run=subprocess.run([str(PRAAT),'--no-pref-files','--no-plugins','--FULL-TRUST','--run',str(root/'compare.praat')],capture_output=True,timeout=30)
                    self.assertEqual(run.returncode,0,run.stderr.decode('utf-16-le','replace'))
                    expected=parselmouth.read(str(root/'native.Pitch'))
                    actual=pitch_analysis.parselmouth_pitch(sound,60,600,purpose=purpose,step=.01)
                    np.testing.assert_allclose(actual.xs(),expected.xs(),atol=1e-12)
                    np.testing.assert_allclose(actual.selected_array['frequency'],expected.selected_array['frequency'],atol=.015,rtol=.0001)
                    self.assertEqual(actual.xmin,sound.xmin)
                    self.assertEqual(actual.xmax,sound.xmax)


class OutputTerminationTests(unittest.TestCase):
    def test_complete_json_with_length_finish_is_still_truncated(self):
        from pydantic_ai import Agent, PromptedOutput
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models.function import FunctionModel
        from praat_ai.cloud_agent import CloudSession, OutputTruncated, AnalysisReport
        from test_dialogue_stream import config
        body=json.dumps(dict(analysis='已有证据仍须核对，完整 JSON 不代表模型已完成回答。',coverage=[]),ensure_ascii=False)
        model=FunctionModel(lambda *_: ModelResponse(parts=[TextPart(body)],finish_reason='length'))
        state=AnalysisState('解释', '')
        session=CloudSession(config(),state,None,threading.Event(),None,model,[],None,None,None)
        session.phase_context('report', '固定报告规则')
        async def run():
            with self.assertRaises(OutputTruncated):
                await session.drive(Agent(model,output_type=PromptedOutput(AnalysisReport)), '解释')
        asyncio.run(run())
        self.assertEqual(state.metrics['request_timings'][-1]['finish_reason'],'length')

    def test_truncated_report_preserves_available_draft_without_claiming_completion(self):
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models.function import FunctionModel
        from praat_ai.cloud_agent import CloudSession
        from test_dialogue_stream import config
        body='已有测量支持局部解释，候选事件的归属仍需要结合录音和标注核对，当前不能判断具体发音错误。'
        model=FunctionModel(lambda *_:ModelResponse(parts=[TextPart(json.dumps(dict(analysis=body,coverage=[]),ensure_ascii=False))],finish_reason='length'))
        state=AnalysisState('分析', '')
        session=CloudSession(config(),state,None,threading.Event(),None,model,[],None,None,None)
        asyncio.run(session.report(allow_escalation=False))
        self.assertEqual(state.status,'partial')
        self.assertFalse(state.metrics.get('report_verified'))
        self.assertIn('length',state.reason)
        self.assertIn('候选事件',state.report)


if __name__=='__main__':
    unittest.main()
