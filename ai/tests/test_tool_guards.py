import json
import threading
import unittest
from pathlib import Path

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import chat, tools
from praat_ai.cloud_agent import run_cloud_turn
from praat_ai.config import AppConfig
from praat_ai.escape_policy import AnalysisState, Evidence


CONTEXT = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
           '1\tSound\ttone\t1\t0.2\t0.4\n'
           '2\tSound\treference\t0\t\t\n'
           '3\tTextGrid\tnotes\t0\t\t\n')


class NativeHookTests(unittest.TestCase):
    def test_negated_whole_request_allows_the_explicit_segment(self):
        for goal in ('只统计对象 1 的 0.1–0.4 秒基频，不要统计整段。',
                     'Measure object 1 from 0.1 to 0.4 seconds, not the whole recording.'):
            with self.subTest(goal=goal):
                _, actions, scripts, _ = self.run_case(
                    [('pitch_statistics', {'object': 1, 'from': .1, 'to': .4})], goal=goal)
                self.assertEqual(len(actions), 1)
                self.assertIn('tmin = 0.100000', scripts[0])

    def test_affirmed_whole_request_survives_a_negated_other_clause(self):
        self.assertTrue(chat._whole_recording_request('不要只测选区，测量整段录音。'))
        self.assertTrue(chat._whole_recording_request('先分析整段，然后不要重复分析整段。'))

    def test_custom_script_pitch_conversion_rejects_harmonicity_before_dispatch(self):
        context = CONTEXT + '9\tHarmonicity\thnr\t0\t\t\n'
        _, actions, _, _ = self.run_case(
            [('custom_script', {'script': 'selectObject: 9\nTo Pitch: 0.75, 75, 600'})],
            goal='对 Harmonicity 对象 9 执行 To Pitch，不要换成 Sound。', context=context)
        self.assertEqual(actions, [])

    def test_custom_script_pitch_conversion_allows_a_real_sound(self):
        _, actions, _, _ = self.run_case(
            [('custom_script', {'script': 'selectObject: 1\nTo Pitch: 0.75, 75, 600'})])
        self.assertEqual(len(actions), 1)

    def run_case(self, calls, *, goal='分析目标', evidence=(), executor=None,
                 refresh=None, reports=(), coverage=None, context=CONTEXT):
        cfg = AppConfig()
        cfg.api.model, cfg.api.base_url = 'test', 'https://test.invalid/v1'
        cfg.api.audio_input_enabled = False
        state = AnalysisState(goal, context, evidence=list(evidence))
        pending, scripts, actions, report_calls = list(calls), [], [], []
        schemas = [schema for schema in tools.tool_schemas()
                   if schema['function']['name'] in {name for name, _ in calls}]
        provided_reports = list(reports)

        def respond(messages, info):
            if info.function_tools:
                if pending:
                    name, args = pending.pop(0)
                    return ModelResponse([ToolCallPart(name, args)])
                return ModelResponse([TextPart('工具阶段结束')])
            report_calls.append(messages)
            text = provided_reports.pop(0) if provided_reports else '现有证据已经保存。具体结论仍需对应专业测量和参照资料，缺少证据的部分无法确定。'
            payload = {'analysis':text, 'coverage':coverage if coverage is not None else
                       [{'item':goal, 'status':'partial',
                         'explanation':'保留现有证据，仍缺少对应的参考测量。'}]}
            return ModelResponse([TextPart(json.dumps(payload, ensure_ascii=False))])

        def action(name, args, index):
            actions.append((name, dict(args)))
            context = tools.ToolContext(tools.parse_object_context(state.context_text), Path('result'), Path('state'))
            def execute(script):
                scripts.append(script)
                return executor(script) if executor else (True, ['基频 = 220 Hz'], '')
            return chat._execute_action({'tool':name, 'arguments':args}, context, execute, index)

        run_cloud_turn(cfg, state, execute_action=action, cancel=threading.Event(),
                       model=FunctionModel(respond), tool_schemas=schemas, refresh_context=refresh)
        return state, actions, scripts, report_calls

    def test_nonexistent_object_is_rejected_before_dispatch(self):
        state, actions, _, _ = self.run_case([('pitch', {'object':99}), ('pitch', {'object':1})])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0][1]['object'], 1)
        self.assertTrue(any(a.get('guard') for a in state.attempts))

    def test_partial_object_name_does_not_guess(self):
        _, actions, _, _ = self.run_case([('pitch', {'object':'ton'})])
        self.assertEqual(actions, [])

    def test_existing_complete_name_resolves_to_real_id(self):
        _, actions, scripts, _ = self.run_case([('pitch', {'object':'Sound tone'})])
        self.assertEqual(actions[0][1]['object'], 1)
        self.assertIn('selectObject: 1', scripts[0])

    def test_user_specified_id_is_not_replaced_by_other_real_object(self):
        _, actions, _, _ = self.run_case([('pitch', {'object':1}), ('pitch', {'object':2})], goal='测量对象 2 的基频')
        self.assertEqual([args['object'] for _, args in actions], [2])

    def test_user_specified_id_and_class_mismatch_rejects(self):
        _, actions, _, _ = self.run_case([('pitch', {'object':3})], goal='测量 Sound 3 的基频')
        self.assertEqual(actions, [])

    def test_wrong_tool_object_type_rejects_before_dispatch(self):
        _, actions, _, _ = self.run_case([('pitch', {'object':3})])
        self.assertEqual(actions, [])

    def test_whole_request_default_does_not_use_editor_selection(self):
        _, actions, scripts, _ = self.run_case([('pitch_statistics', {})], goal='测量整个声音的基频')
        self.assertEqual(len(actions), 1)
        self.assertIn('tmin = 0\ntmax = duration', scripts[0])
        self.assertNotIn('tmin = 0.200000', scripts[0])
        self.assertNotIn('按编辑器圈选', scripts[0])

    def test_whole_point_query_defaults_to_actual_object_midpoint(self):
        _, _, scripts, _ = self.run_case([('pitch', {})], goal='测量整个声音的基频')
        self.assertIn('time = duration / 2', scripts[0])
        self.assertNotIn('time = 0.300', scripts[0])

    def test_whole_request_copy_of_old_selection_rejects(self):
        _, actions, _, _ = self.run_case([('pitch_statistics', {'from':.2, 'to':.4})], goal='测量整个声音的基频')
        self.assertEqual(actions, [])

    def test_whole_request_verified_full_range_is_allowed(self):
        evidence = Evidence('duration', {'object':1}, ['tone 总时长 = 1.000000 秒'], CONTEXT)
        _, actions, scripts, _ = self.run_case([('pitch_statistics', {'from':0, 'to':1})],
            goal='测量整个声音的基频', evidence=[evidence])
        self.assertEqual(len(actions), 1)
        self.assertIn('tmax = 1.000000', scripts[0])

    def test_whole_prosody_can_measure_verified_subdivisions(self):
        evidence = Evidence('duration', {'object':1}, ['tone 总时长 = 1.000000 秒'], CONTEXT)
        _, actions, _, _ = self.run_case([('pitch_statistics', {'from':0, 'to':.5}),
            ('pitch_statistics', {'from':.5, 'to':1})], goal='分析整个声音的音调走向', evidence=[evidence])
        self.assertEqual(len(actions), 2)

    def test_whole_and_explicit_segment_request_can_use_both_scopes(self):
        _, actions, _, _ = self.run_case([('pitch_statistics', {}),
            ('pitch_statistics', {'from':.2, 'to':.4})], goal='先测量整个声音，然后再检查 0.2 到 0.4 秒的片段')
        self.assertEqual(len(actions), 2)

    def test_negated_old_segment_is_not_an_explicit_segment_authorization(self):
        _, actions, _, _ = self.run_case([('pitch_statistics', {'from':.2, 'to':.4})],
            goal='测量整个声音，不要用 0.2 到 0.4 秒的旧选区片段')
        self.assertEqual(actions, [])

    def test_repeating_failure_with_explicit_default_does_not_execute_again(self):
        state, actions, _, _ = self.run_case([('pitch', {}), ('pitch', {'object':1, 'pitch_floor':75})],
            executor=lambda script:(False, [], 'fixture bad query'))
        self.assertEqual(len(actions), 1)
        self.assertTrue(any(a.get('reason') == '相同失败调用没有区别' for a in state.attempts))

    def test_different_failure_repair_can_execute_once(self):
        state, actions, _, _ = self.run_case([('pitch', {'time':.3}), ('pitch', {'time':.5})],
            executor=lambda script:(False, [], 'fixture bad query'))
        self.assertEqual(len(actions), 2)
        self.assertEqual(state.failures['pitch'], 2)

    def test_successful_measurement_is_reused_across_equivalent_defaults(self):
        state, actions, _, _ = self.run_case([('pitch', {}), ('pitch', {'object':1, 'pitch_floor':75})])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(state.evidence), 1)
        self.assertTrue(any(a.get('reason') == '复用成功测量' for a in state.attempts))

    def test_persisted_successful_measurement_is_reused(self):
        evidence = Evidence('pitch', {'time':.5}, ['基频 = 220 Hz'], CONTEXT)
        _, actions, _, _ = self.run_case([('pitch', {'time':.5, 'object':1})], evidence=[evidence])
        self.assertEqual(actions, [])

    def test_alias_ranges_reuse_success_instead_of_repeating_operation(self):
        _, actions, _, _ = self.run_case([('extract_part', {'from':.2, 'to':.4}),
                                        ('extract_part', {'start':.2, 'end':.4, 'object':1})])
        self.assertEqual(len(actions), 1)

    def test_invented_new_name_rejects_and_omission_can_repair(self):
        _, actions, _, _ = self.run_case([('duplicate_object', {'name':'invented'}), ('duplicate_object', {})], goal='复制当前声音')
        self.assertEqual(len(actions), 1)
        self.assertNotIn('name', actions[0][1])

    def test_user_provided_name_is_allowed(self):
        _, actions, scripts, _ = self.run_case([('rename_object', {'new_name':'新 名称'})], goal='改名为“新 名称”')
        self.assertEqual(len(actions), 1)
        self.assertIn('Rename: "新 名称"', scripts[0])

    def test_other_languages_do_not_reach_praat(self):
        for script in ('import numpy as np\nprint(1)', 'x = 1\nfor i in range(10):\n    x += i',
                       'const x = 1;\nconsole.log(x);', '$x = 1', 'nonsense text'):
            with self.subTest(script=script):
                _, actions, _, _ = self.run_case([('custom_script', {'script':script})])
                self.assertEqual(actions, [])

    def test_praat_script_can_query_actual_object(self):
        _, actions, scripts, _ = self.run_case([('custom_script', {'script':'selectObject: 1\nduration = Get total duration\nappendInfoLine: duration'})])
        self.assertEqual(len(actions), 1)
        self.assertIn('duration = Get total duration', scripts[0])
        self.assertNotIn('appendInfoLine:', scripts[0])

    def test_custom_script_cannot_select_invented_or_different_target(self):
        for goal, script in [('分析目标', 'selectObject: 99\nx = Get total duration'),
                             ('测量对象 2', 'selectObject: 1\nx = Get total duration')]:
            with self.subTest(script=script):
                _, actions, _, _ = self.run_case([('custom_script', {'script':script})], goal=goal)
                self.assertEqual(actions, [])

    def test_custom_script_cannot_invent_name(self):
        for script in ('selectObject: 1\nRename: "invented"', 'selectObject: 1\nRename... invented',
                       'Create Sound from formula: "invented", 1, 0, 1, 44100, "0"'):
            _, actions, _, _ = self.run_case([('custom_script', {'script':script})])
            self.assertEqual(actions, [])

    def test_legacy_script_selection_cannot_switch_user_target(self):
        _, actions, _, _ = self.run_case([('custom_script', {'script':'select Sound tone\nx = Get total duration'})], goal='测量对象 2')
        self.assertEqual(actions, [])

    def test_custom_script_without_selection_is_bound_to_original_target(self):
        _, actions, scripts, _ = self.run_case([('custom_script', {'script':'duration = Get total duration\nappendInfoLine: duration'})])
        self.assertEqual(len(actions), 1)
        self.assertTrue(scripts[0].startswith('selectObject: 1\n'))

    def test_created_object_is_usable_only_after_confirmed_refresh(self):
        fresh = CONTEXT + '4\tSound\t新建声音\t0\t\t\n'
        refreshed = []
        def refresh():
            refreshed.append(1)
            return fresh
        _, actions, _, _ = self.run_case([('create_sound', {}), ('pitch', {'object':4})],
            goal='新建声音然后测量它的基频', refresh=refresh)
        self.assertEqual(len(actions), 2)
        self.assertEqual(refreshed, [1])

    def test_report_guard_gives_a_bad_number_one_rewrite_chance(self):
        """坏数值先用掉报告阶段那一次 output 重试：模型改对就按改对的交付。

        不论改没改对，语义都不变——不重新测量、不丢正文、坏数值不进交付。
        """

        state, actions, _, reports = self.run_case([('pitch', {'time':.5})], reports=[
            '本录音基频是 999 Hz [E1]。该数字用于评估，但仍然缺少参照录音，无法进一步判断具体缺陷。',
            '本录音基频是 220 Hz [E1]。该数字来自真实测量，仍然缺少参照录音，无法进一步判断具体缺陷。'])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(reports), 2)
        self.assertNotIn('999 Hz', state.report)
        self.assertIn('220 Hz', state.report)
        self.assertTrue(state.metrics.get('report_verified'))
        self.assertEqual(state.metrics['audit']['masked_count'], 0)

    def test_report_failure_keeps_real_evidence_and_masks_fabricated_output(self):
        bad = '本录音基频是 999 Hz [E1]。这个结果能够用于评估音高，但是仍缺少可靠的参照录音材料。'
        state, actions, _, reports = self.run_case([('pitch', {'time':.5})], reports=[bad, bad])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(reports), 2)   # 一次改写机会用完仍是错的
        self.assertEqual(len(state.evidence), 1)
        self.assertEqual(state.status, 'partial')
        self.assertNotIn('999 Hz', state.report)
        self.assertIn('[未通过核对的数值]', state.report)

    def test_report_rejections_keep_the_written_draft_instead_of_a_stage_record(self):
        bad = '本录音基频是 999 Hz [E1]。这个结果能够用于评估音高，但是仍缺少可靠的参照录音材料。'
        state, actions, _, reports = self.run_case([('pitch', {'time':.5})], reports=[bad, bad])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(reports), 2)
        # 2026-10-06 实测：守卫连拒两次后整份回答被换成「本轮仅交付阶段记录」，用户看不到
        # 任何结论。现在正文保留、没核上的数值遮掉；为此至多多发一次请求（改不动就不再要）。
        self.assertNotIn('本轮仅交付阶段记录', state.report)
        self.assertIn('这个结果能够用于评估音高', state.report)
        self.assertIn('[未通过核对的数值]', state.report)
        self.assertFalse(state.metrics.get('report_verified', False))
        self.assertEqual(state.metrics['guard_repairs'].get('report.number_retried'), 1)

    def test_multi_parameter_measure_is_accepted_and_dispatched(self):
        state, actions, _, _ = self.run_case([('measure', {'parameter': 'f1,f2', 'object': 1})])
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0][1]['parameter'], 'f1,f2')
        self.assertFalse([a for a in state.attempts if a.get('guard')])

    def test_parameter_writing_error_does_not_pause_the_whole_branch(self):
        state, actions, _, _ = self.run_case([('measure', {'parameter': 'f1,nope'}),
                                              ('measure', {'parameter': 'f1'})])
        # 参数名写错是可纠正的格式问题：不记进 failures（否则第二次就 BranchExit，
        # 整个工具阶段终止），模型能在同一轮里改正并真正执行。
        self.assertNotIn('measure', state.failures)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0][1]['parameter'], 'f1')
        self.assertTrue(any('不认识参数' in str(a.get('reason')) for a in state.attempts))
        self.assertEqual(state.status, 'partial')
        self.assertEqual(state.metrics['guard_blocks'], {'tool.schema.measure': 1})

    def test_tool_policy_blocks_are_counted_by_rule_and_tool(self):
        state, actions, _, _ = self.run_case([('pitch', {'object': 99})])
        self.assertEqual(actions, [])
        self.assertEqual(state.metrics['guard_blocks'], {'tool.policy.pitch': 1})
        self.assertNotIn('pitch', state.failures)

    def test_coverage_mismatch_is_repaired_instead_of_rejected(self):
        state, actions, _, reports = self.run_case(
            [('pitch', {'time':.5})],
            coverage=[{'item': '另一个交付项', 'status': 'complete',
                       'explanation': '模型自己写的一项，和原始交付项对不上。'}])
        self.assertEqual(len(actions), 1)
        self.assertEqual(len(reports), 1)          # 结构问题不再消耗重试
        self.assertEqual([item['item'] for item in state.coverage], state.deliveries)
        self.assertEqual(state.coverage[0]['status'], 'partial')
        self.assertEqual(state.metrics['guard_repairs']['report.coverage_repaired'], 1)


if __name__ == '__main__':
    unittest.main()
