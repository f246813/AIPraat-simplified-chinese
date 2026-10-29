"""音素对齐证据边界的回归测试（``praat_ai.evidence_boundaries``）。

场景取自真实近两轮：本轮只有 ``object_info`` / ``pitch_statistics`` / ``measure`` /
``intensity_statistics`` / ``query_points``，没有任何音素对齐调用。第一轮按文件名猜
「思い出す」，把等分的基频窗口一一对应到「おも／い／だ／す」；第二轮承认无音素级对齐却
看起来照样做了对应。这里把「事实口径」和「声明核对」都钉住：

* 事实：没有边界证据、只有声学时间窗；文件名推测不是转写；没有参考材料记录。
* 核对：没有边界证据却把具体音素与测量窗口一一对应 → 命中；明确否定、一般知识示例、
  不带录音时间的词典知识、正常基频窗口描述、弱化假设 → 不命中。
* 正向能力：真的存在带 provenance 的对齐证据时，同一句话不再命中（不靠宽泛误杀）。
"""

import json
import unittest

from pydantic_ai import ModelRetry

from praat_ai import evidence_boundaries
from praat_ai.escape_policy import AnalysisState, Evidence

#: 真实的编辑器快照形状（``tools.parse_object_context`` 的格式）。
SOUND_CONTEXT = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tai-chat-temp\t1\t0.0\t0.892\n'
GOAL = '这段录音的发音标准吗？有没有音素级的偏差？'


def evidence(tool, rows, *, context=SOUND_CONTEXT, **arguments):
    return Evidence(tool, {'object': 1, **arguments}, rows, context)


def object_info():
    return evidence('object_info', ['Sound ai-chat-temp：时长 0.892 秒，1 个通道，采样率 44100 Hz'])


def pitch_window(start, end, mean):
    return evidence('pitch_statistics',
                    [f'基频统计（{start:.3f}–{end:.3f} 秒）：平均 {mean:.3f} Hz，'
                     '最低 190.000 Hz，最高 240.000 Hz，最高点出现在 %.3f 秒' % (start + (end - start) / 2)],
                    **{'from': start, 'to': end})


def intensity_window(start, end):
    return evidence('intensity_statistics',
                    [f'强度统计（{start:.3f}–{end:.3f} 秒）：平均 68.500 dB，最高 74.200 dB'],
                    **{'from': start, 'to': end})


def round_one_state():
    """第一轮的真实形状：文件名推测 + 四等分基频窗口，没有任何对齐调用。"""

    state = AnalysisState(GOAL, SOUND_CONTEXT,
                          evidence=[object_info(), pitch_window(0.0, 0.223, 210.5),
                                    pitch_window(0.223, 0.446, 186.25),
                                    pitch_window(0.446, 0.669, 199.75),
                                    pitch_window(0.669, 0.892, 174.5),
                                    intensity_window(0.0, 0.892)])
    state.material_metadata = {
        'original_filename': 'omoidasu.wav',
        'user_pronunciation': '',
        'pronunciation_hint': {'original_filename': 'omoidasu.wav', 'candidate': '思い出す',
                               'source': 'filename', 'status': '根据文件名推测；未验证实际录音内容'},
        'provenance': {'sha256': '0' * 64, 'source': 'D:/materials/omoidasu.wav', 'source_kind': 'file',
                       'range': [0.0, 0.892], 'snapshot': 'window-only',
                       'original_filename': 'omoidasu.wav', 'audio_available': True},
    }
    return state


def phone_tier_evidence():
    """真实 ``textgrid_info`` 输出形状：有边界，但标签来源没登记。"""

    return Evidence('textgrid_info', {'object': 2},
                    ['ai-chat-temp：时长 0.892 秒，共 1 层',
                     '第 1 层「phones」（区间层）：3 个区间',
                     '    区间 1：0.100–0.200 秒，标签「o」',
                     '    区间 2：0.200–0.300 秒，标签「mo」',
                     '    区间 3：0.300–0.400 秒，标签「i」'],
                    'id\tclass\tname\tselected\n2\tTextGrid\tai-chat-temp\t1\n')


def alignment_evidence(source='mfa'):
    """带 provenance 的对齐产物（当前工具表里没有产出方，这里按 Evidence 形状构造正例）。"""

    row = f'对齐结果（来源 {source}，置信度 0.860）：o 0.100–0.200 秒；mo 0.200–0.300 秒' if source \
        else '对齐结果：o 0.100–0.200 秒；mo 0.200–0.300 秒'
    arguments = {'object': 1}
    if source:
        arguments['source'] = source
    return Evidence('alignment', arguments, [row], SOUND_CONTEXT)


CLAIM = '把录音等分成四段的基频窗口：0.000–0.223 秒对应おも，0.223–0.446 秒对应い，0.446–0.669 秒对应だ，0.669–0.892 秒对应す。'


class AlignmentClaimTests(unittest.TestCase):
    """没有边界证据时，具体音素与测量窗口的一一对应必须命中。"""

    def test_round_one_equal_windows_mapped_to_morae_are_reported(self):
        issues = evidence_boundaries.validate_alignment_claims(round_one_state(), CLAIM)
        self.assertEqual([issue['rule'] for issue in issues], ['report.window_as_phone'] * 4)
        self.assertEqual([issue['phones'] for issue in issues], [['おも'], ['い'], ['だ'], ['す']])
        self.assertIn('没有任何音素级边界证据', issues[0]['message'])
        self.assertIn('おも', issues[0]['message'])

    def test_report_is_not_rewritten_and_metrics_are_untouched(self):
        state = round_one_state()
        evidence_boundaries.validate_alignment_claims(state, CLAIM)
        self.assertEqual(state.metrics, {})
        self.assertEqual(len(state.evidence), 6)

    def test_configuration_strings_do_not_count_as_alignment(self):
        # 配置里写着 mfa / wav2vec2（真实 state.context_text 会带这类文字），但它不是对齐结果。
        state = round_one_state()
        state.context_text = SOUND_CONTEXT + 'alignment: mfa available=true; wav2vec2 model=loaded\n'
        self.assertEqual(evidence_boundaries.boundary_status(state)['status'], 'none')
        self.assertEqual(len(evidence_boundaries.validate_alignment_claims(state, CLAIM)), 4)

    def test_unknown_tool_is_never_promoted_to_alignment(self):
        # 未知工具（custom_script）能写出任何文本：行里有区间和来源，也不算对齐产物。
        script = evidence('custom_script',
                          ['区间 1：0.100–0.200 秒，标签「o」', '对齐来源：mfa'],
                          **{'script': 'WriteInfoLine: "..."'})
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[script])
        self.assertEqual(evidence_boundaries.boundary_status(state),
                         {'status': 'none', 'ids': [], 'provenance': ''})
        self.assertEqual(len(evidence_boundaries.validate_alignment_claims(state, '0.100–0.200 秒对应音素「o」。')), 1)

    def test_window_noun_carries_to_the_next_clause(self):
        # 窗口词在前一分句（「按等分窗口切成四段」），后一分句只写对应关系，仍然命中。
        state = round_one_state()
        issues = evidence_boundaries.validate_alignment_claims(state, '按等分窗口把录音切成四段，依次对应おも、い、だ、す。')
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]['phones'], ['おも', 'い', 'だ', 'す'])

    def test_vowel_placed_at_a_measured_window_is_reported(self):
        state = round_one_state()
        issues = evidence_boundaries.validate_alignment_claims(state, '0.200–0.446 秒这一段对应元音。')
        self.assertEqual(len(issues), 1)
        self.assertIn('元音', issues[0]['message'])

    def test_retry_raises_model_retry_with_rule(self):
        with self.assertRaises(ModelRetry) as caught:
            evidence_boundaries.validate_alignment_claims(round_one_state(), CLAIM, retry=True)
        self.assertEqual(caught.exception.rule, 'report.window_as_phone')

    def test_retry_stays_silent_when_nothing_is_wrong(self):
        self.assertEqual(evidence_boundaries.validate_alignment_claims(round_one_state(), '平均基频 210.500 Hz。', retry=True), [])


class ClaimAllowanceTests(unittest.TestCase):
    """允许的口径：明确否定、词典知识、一般知识示例、正常窗口描述、弱化假设。"""

    def rejects(self, text):
        return evidence_boundaries.validate_alignment_claims(round_one_state(), text)

    def test_explicit_denial_is_allowed(self):
        for text in ('不能把 0.223–0.446 秒的窗口对应到音素「い」。',
                     '无法把声学时间窗对应成音素，本轮不做音素级切分。',
                     '本窗口不是音素边界，只是等分的声学分段。'):
            with self.subTest(text=text):
                self.assertEqual(self.rejects(text), [])

    def test_normal_pitch_window_description_is_allowed(self):
        for text in ('0.223–0.446 秒窗口内平均基频 186.250 Hz，最高点出现在 0.334 秒。',
                     '前半段（0.000–0.223 秒）平均基频 210.500 Hz，高于后半段 174.500 Hz。',
                     '整段 0.000–0.892 秒的平均强度为 68.500 dB。'):
            with self.subTest(text=text):
                self.assertEqual(self.rejects(text), [])

    def test_dictionary_knowledge_without_recording_times_is_allowed(self):
        for text in ('「思い出す」是五拍动词，モーラ序列是 お・も・い・だ・す。',
                     '音素 /o/ 与 /a/ 的区别在于舌位高低，这是一般语言学知识。',
                     '标准语里「思い出す」的重音核在「だ」之后。'):
            with self.subTest(text=text):
                self.assertEqual(self.rejects(text), [])

    def test_marked_general_example_is_allowed(self):
        self.assertEqual(self.rejects('（一般知识，示例非本录音）日语「思い出す」分成 お-も-い-だ-す 五拍。'), [])

    def test_hedged_hypothesis_is_allowed(self):
        # 取舍：弱化措辞（可能/推测）放行，代价是弱化后的断言也能躲过这条规则。
        self.assertEqual(self.rejects('可能对应「お」的窗口大约在 0.000–0.223 秒。'), [])

    def test_bare_numbers_without_phones_are_allowed(self):
        self.assertEqual(self.rejects('共测量 6 条证据，覆盖 0.000–0.892 秒。'), [])


class BoundaryEvidenceTests(unittest.TestCase):
    """有真实边界证据时：同一句话不再命中；事实里说清它是什么来源。"""

    def test_alignment_evidence_with_provenance_allows_the_claim(self):
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[alignment_evidence('mfa')])
        self.assertEqual(evidence_boundaries.validate_alignment_claims(state, '0.100–0.200 秒对应音素「o」。'), [])
        facts = evidence_boundaries.report_boundary_context(state)
        self.assertEqual(facts['boundary_evidence'],
                         {'status': 'alignment', 'ids': ['E1'], 'provenance': 'mfa'})
        self.assertIn('来源 mfa', facts['guidance'])

    def test_alignment_without_recorded_source_says_source_is_missing(self):
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[alignment_evidence('')])
        facts = evidence_boundaries.report_boundary_context(state)
        self.assertEqual(facts['boundary_evidence']['provenance'], '')
        self.assertEqual(facts['boundary_evidence']['status'], 'none')
        self.assertIn('没有音素级边界证据', facts['guidance'])

    def test_textgrid_phone_tier_allows_boundaries_but_not_alignment_claims(self):
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[phone_tier_evidence()])
        self.assertEqual(evidence_boundaries.validate_alignment_claims(
            state, '0.100–0.200 秒对应标签「o」，0.200–0.300 秒对应「mo」。'), [])
        facts = evidence_boundaries.report_boundary_context(state)
        self.assertEqual(facts['boundary_evidence']['status'], 'textgrid')
        self.assertIn('标签来源未登记', facts['guidance'])

    def test_word_tier_is_not_phone_boundary_evidence(self):
        # 词层标签（hello）不是音素边界：同样的句子仍然算「无证据却做对应」。
        word_tier = Evidence('textgrid_info', {'object': 2},
                             ['第 1 层「word」（区间层）：1 个区间',
                              '    区间 1：0.100–0.500 秒，标签「hello」'],
                             'id\tclass\tname\tselected\n2\tTextGrid\ttemp\t1\n')
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[word_tier])
        self.assertEqual(evidence_boundaries.boundary_status(state)['status'], 'none')
        self.assertEqual(len(evidence_boundaries.validate_alignment_claims(state, '0.100–0.200 秒对应音素「o」。')), 1)

    def test_unknown_textgrid_named_tool_is_not_a_boundary(self):
        item = phone_tier_evidence()
        item.tool = 'invented_textgrid_tool'
        self.assertEqual(evidence_boundaries.boundary_status([item])['status'], 'none')

    def test_kana_word_tier_is_not_a_phone_tier(self):
        item = phone_tier_evidence()
        item.rows = ['第 1 层「words」（区间层）：1 个区间',
                     '区间 1：0.100–0.500 秒，标签「おもいだす」']
        self.assertEqual(evidence_boundaries.boundary_status([item])['status'], 'none')

    def test_alignment_name_without_actual_boundary_data_is_insufficient(self):
        item = alignment_evidence()
        item.rows = ['对齐未完成']
        self.assertEqual(evidence_boundaries.boundary_status([item])['status'], 'none')


class BoundaryContextTests(unittest.TestCase):
    """``report_boundary_context`` 只报真实字段，且不往普通报告里加 token。"""

    def test_round_one_facts(self):
        facts = evidence_boundaries.report_boundary_context(round_one_state())
        self.assertEqual(facts['boundary_evidence']['status'], 'none')
        self.assertEqual(facts['acoustic_time_windows'][0], 'E2 0–0.223 秒')
        self.assertIs(facts['acoustic_time_windows_are_phone_boundaries'], False)
        self.assertEqual(facts['filename_guess']['candidate'], '思い出す')
        self.assertEqual(facts['reference_evidence'], 'not_recorded')
        self.assertIn('不等于音素/音节边界', facts['guidance'])
        self.assertIn('文件名推测', facts['guidance'])
        self.assertIn('标准音对比', facts['guidance'])
        json.dumps(facts, ensure_ascii=False)   # 必须可序列化

    def test_guidance_stays_short(self):
        facts = evidence_boundaries.report_boundary_context(round_one_state())
        self.assertLess(len(facts['guidance']), 260)

    def test_plain_measurement_round_adds_nothing(self):
        state = AnalysisState('测量这段录音的平均基频', SOUND_CONTEXT, evidence=[pitch_window(0.0, 0.892, 200.0)])
        self.assertEqual(evidence_boundaries.report_boundary_context(state), {})
        self.assertEqual(evidence_boundaries.tail_guidance(state), '')

    def test_textgrid_intervals_are_not_reported_as_acoustic_windows(self):
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[phone_tier_evidence()])
        self.assertNotIn('acoustic_time_windows', evidence_boundaries.report_boundary_context(state))

    def test_provenance_is_read_from_the_result_row_too(self):
        # 来源只写在结果行里（``来源 mfa``），没有 arguments.source：仍然算带 provenance。
        row_only = Evidence('alignment', {'object': 1},
                            ['音素对齐（来源 mfa）：o 0.100–0.200 秒；mo 0.200–0.300 秒'], SOUND_CONTEXT)
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[row_only])
        self.assertEqual(evidence_boundaries.boundary_status(state)['provenance'], 'mfa')

    def test_reference_evidence_is_reported_when_present(self):
        reference = evidence('pitch_statistics', ['基频统计（0.000–0.892 秒）：平均 205.000 Hz'],
                             **{'from': 0.0, 'to': 0.892, 'reference_material': 'standard.wav'})
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[pitch_window(0.0, 0.892, 200.0), reference])
        facts = evidence_boundaries.report_boundary_context(state)
        self.assertEqual(facts['reference_evidence'], 'present')
        self.assertEqual(facts['reference_evidence_ids'], ['E2'])
        self.assertNotIn('标准音对比', facts['guidance'])

    def test_evidence_items_accepts_state_or_sequence(self):
        item = object_info()
        self.assertEqual(evidence_boundaries.evidence_items([item]), [item])
        self.assertEqual(evidence_boundaries.evidence_items(None), [])
        self.assertEqual(evidence_boundaries.evidence_items(AnalysisState(GOAL, SOUND_CONTEXT, evidence=[item])), [item])

    def test_missing_metadata_does_not_invent_a_filename_guess(self):
        state = AnalysisState(GOAL, SOUND_CONTEXT, evidence=[object_info()])
        self.assertNotIn('filename_guess', evidence_boundaries.report_boundary_context(state))


if __name__ == '__main__':
    unittest.main()
