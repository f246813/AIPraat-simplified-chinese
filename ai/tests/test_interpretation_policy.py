"""解释准确性策略的回归测试（``praat_ai.interpretation_policy``）。

场景取自用户复核过的真实报告：``50.67`` / ``50.92`` 的首尾、``50.599`` 的均值与
``334.57 Hz/s`` 的平均绝对斜率被拿来说「语调平淡」，再据此诊断喉部紧张 / 气息不足；
``563.45 Hz`` 的孤立峰被说成「突然喉部收紧造成的」；没有词典字段却指定了日语词的
标准重音型。这里锁住几条边界：

* 声学测量不能推出当前说话人的喉部 / 气息 / 性别舒适音域，也不能推断性格情绪；
* 弱化措辞（可能 / 或许）不能让本录音的病因断言通过；
* 允许：一般生理知识、用户自述、温和练习建议、「无法判断」的否定；
* ``sanitize_interpretation`` 保留测量句、删掉结论本身，不是末尾加免责声明；
* 日语重音只给「需要来源 + 变体 + 核后下降 vs 核内下降」的指导，不认任何具体词，
  也没有 ``50 Hz`` 之类的硬阈值。
"""

import inspect
import json
import re
import unittest

from praat_ai import interpretation_policy
from praat_ai.escape_policy import AnalysisState, Evidence

CONTEXT = 'id\tclass\tname\tselected\tsel_start\tsel_end\n1\tSound\tai-chat-temp\t1\t0.0\t0.892\n'
GOAL = '这段录音的语调是不是太平淡？我的喉部有没有紧张？'
JAPANESE_GOAL = '日语单词的重音怎么读才对？'
MEASURE_GOAL = '测量这段录音的平均基频。'


def evidence(tool, rows, **arguments):
    return Evidence(tool, {'object': 1, **arguments}, rows, CONTEXT)


def object_info():
    return evidence('object_info', ['Sound ai-chat-temp：时长 0.892 秒，1 个通道，采样率 44100 Hz'])


def intensity_stats(mean=50.599, first=50.67, last=50.92):
    return evidence('intensity_statistics',
                    [f'强度统计（0.000–0.892 秒）：平均 {mean:.3f} dB，最高 74.200 dB'],
                    **{'from': 0.0, 'to': 0.892}), first, last


def pitch_slope(value=334.57):
    return evidence('measure', [f'基频平均绝对斜率 = {value:.3f} Hz/s'], parameter='pitch_slope')


def pitch_window(start, end, mean, peak):
    return evidence('pitch_statistics',
                    [f'基频统计（{start:.3f}–{end:.3f} 秒）：平均 {mean:.3f} Hz，最低 190.000 Hz，'
                     f'最高 {peak:.3f} Hz，最高点出现在 {(start + end) / 2:.3f} 秒'],
                    **{'from': start, 'to': end})


def recording_state(goal=GOAL):
    """本轮真实形状：强度统计 + 基频斜率 + 含 563.45 Hz 孤立峰的基频窗口。"""

    return AnalysisState(goal, CONTEXT, evidence=[
        object_info(), intensity_stats()[0], pitch_slope(),
        pitch_window(0.000, 0.446, 51.120, 563.450), pitch_window(0.446, 0.892, 50.920, 210.000)])


def empty_state(goal=GOAL):
    """同一句目标，但这一轮没有任何测量证据（判据与证据无关，结果应当一样）。"""

    return AnalysisState(goal, CONTEXT, evidence=[object_info()])


#: 真实报告里那一段（测量 + 「语调平淡」+ 喉部/气息诊断）。
REPORT = ('本次录音强度均值 50.599 dB，首尾 50.67/50.92 dB；'
          '基频平均绝对斜率 334.57 Hz/s，说明你的语调平淡，也说明你气息支撑不足。')


class PhysiologyClaimTests(unittest.TestCase):
    """声学值 → 生理诊断：必须报出，且与数值大小无关。"""

    def issues(self, text, state=None):
        return interpretation_policy.check_interpretation_claims(state or recording_state(), text)

    def rules(self, text, state=None):
        return [issue['rule'] for issue in self.issues(text, state)]

    def test_flat_intonation_then_laryngeal_diagnosis_is_reported(self):
        issues = self.issues(REPORT)
        self.assertEqual([issue['rule'] for issue in issues], ['interpretation.acoustic_to_physiology'])
        self.assertIn('气息', issues[0]['clause'])
        self.assertIn('不能确定', issues[0]['message'])
        self.assertIn('「气息」', issues[0]['message'])

    def test_bare_breath_support_claim_is_reported(self):
        # 「你气息支撑不足」没有数值也照样是诊断：报告里就是这么写的。
        issues = self.issues('你气息支撑不足。')
        self.assertEqual([issue['rule'] for issue in issues], ['interpretation.acoustic_to_physiology'])
        self.assertIn('气息', issues[0]['clause'])

    def test_measured_value_does_not_change_the_verdict(self):
        # 没有硬阈值：40 Hz、50 Hz、563.45 Hz 和「均值」这种不带数目的写法，判定完全一样。
        for text in ('40 Hz 的均值说明喉部肌肉过度紧张。',
                     '50 Hz 的均值说明喉部肌肉过度紧张。',
                     '563.45 Hz 的孤立峰说明喉部肌肉过度紧张。',
                     '基频均值说明你喉部肌肉过度紧张。'):
            with self.subTest(text=text):
                self.assertEqual(self.rules(text), ['interpretation.acoustic_to_physiology'])

    def test_hedged_recording_cause_is_still_reported(self):
        issues = self.issues('563.45 Hz 的孤立峰可能是突然喉部收紧造成的。')
        self.assertEqual([issue['rule'] for issue in issues], ['interpretation.acoustic_to_physiology'])
        self.assertIn('可能', issues[0]['clause'])

    def test_issues_are_independent_of_available_evidence(self):
        text = '基频均值说明你喉部肌肉过度紧张。'
        self.assertEqual(self.rules(text, recording_state()), self.rules(text, empty_state()))

    def test_gender_and_comfort_range_claim_is_reported(self):
        issues = self.issues('50.599 dB 的均值说明这落在女性的舒适音域里。')
        self.assertEqual([issue['rule'] for issue in issues], ['interpretation.acoustic_to_body_norm'])
        self.assertIn('舒适音域', issues[0]['message'])

    def test_personality_attribution_is_reported(self):
        issues = self.issues('基频斜率说明你性格比较内向。')
        self.assertEqual([issue['rule'] for issue in issues], ['interpretation.acoustic_to_psychology'])

    def test_issue_shape_is_rule_clause_message_and_serialisable(self):
        issues = self.issues(REPORT)
        self.assertTrue({'rule', 'clause', 'message'} <= set(issues[0]))
        json.dumps(issues, ensure_ascii=False)
        self.assertEqual(self.issues(REPORT), issues)   # 确定性：同一输入同一输出


class ClaimAllowanceTests(unittest.TestCase):
    """允许的口径：一般知识、用户自述、练习建议、明确否定。"""

    def issues(self, text, state=None):
        return interpretation_policy.check_interpretation_claims(state or recording_state(), text)

    def test_general_physiology_knowledge_is_allowed(self):
        for text in ('一般来说，喉部紧张会让基频升高。',
                     '一般知识：气息支撑不足时声音容易发虚，这是常见现象。',
                     '喉部肌肉紧张时声带闭合会更用力，这类机制与个人诊断无关。',
                     '通常 50–300 Hz 的基频对应成年人的说话声，喉部紧张会让基频升高。'):
            with self.subTest(text=text):
                self.assertEqual(self.issues(text), [])

    def test_general_knowledge_label_does_not_rescue_a_recording_cause(self):
        # 「一般知识 / 一般原理」只是标签：句子里真的指向你时，仍然算本录音的因果断言。
        for text in ('（一般知识）563.45 Hz 的孤立峰说明你的喉部肌肉过度紧张。',
                     '这是一般原理，50 Hz 说明你气息支撑不足。'):
            with self.subTest(text=text):
                self.assertEqual([issue['rule'] for issue in self.issues(text)],
                                 ['interpretation.acoustic_to_physiology'])

    def test_user_self_report_is_allowed(self):
        for text in ('你提到气息支撑不足，这是你的自述，不是本次测量结果。',
                     '你说自己唱歌时喉咙发紧，我们按你的感觉来调整练习。'):
            with self.subTest(text=text):
                self.assertEqual(self.issues(text), [])

    def test_denial_of_inference_is_allowed(self):
        for text in ('本次测量无法判断你喉部是否紧张。',
                     '这些数值不代表气息支撑不足，也不能诊断喉部状态。',
                     '50.599 dB 只是强度均值，不能说明你的气息支撑。',
                     '563.45 Hz 的孤立峰不能说明突然喉部收紧。'):
            with self.subTest(text=text):
                self.assertEqual(self.issues(text), [])

    def test_gentle_practice_advice_is_allowed(self):
        for text in ('建议放松下颌、自然发声，练习时别憋气。',
                     '可以尝试先放松下巴，自然发声，不要用力。',
                     '放松下颌、自然发声就好。'):
            with self.subTest(text=text):
                self.assertEqual(self.issues(text), [])

    def test_mechanism_with_conditional_is_allowed(self):
        self.assertEqual(self.issues('你喉部紧张的时候声音会发抖，所以先放松下颌。'), [])

    def test_plain_measurement_report_is_clean(self):
        self.assertEqual(self.issues('本次录音强度均值 50.599 dB，基频平均绝对斜率 334.57 Hz/s。'), [])


class SanitizeTests(unittest.TestCase):
    """``sanitize_interpretation``：删结论、留测量，不做末尾免责声明。"""

    def sanitize(self, text, state=None):
        return interpretation_policy.sanitize_interpretation(state or recording_state(), text)

    def test_measurement_is_kept_and_diagnosis_is_removed(self):
        text = '本次录音强度均值 50.599 dB；基频平均绝对斜率 334.57 Hz/s 说明你的喉部肌肉过度紧张。'
        safe, issues = self.sanitize(text)
        self.assertIn('50.599 dB', safe)
        self.assertIn('334.57 Hz/s', safe)
        self.assertNotIn('过度紧张', safe)
        self.assertNotIn('喉部肌肉', safe)
        self.assertIn('现有声学测量不能确定', safe)
        self.assertEqual([issue['rule'] for issue in issues], ['interpretation.acoustic_to_physiology'])

    def test_hedged_cause_is_neutralised_but_the_value_stays(self):
        safe, issues = self.sanitize('563.45 Hz 的孤立峰可能是突然喉部收紧造成的，建议放松下颌、自然发声。')
        self.assertEqual(len(issues), 1)
        self.assertNotIn('喉部收紧', safe)
        self.assertIn('563.45 Hz', safe)
        self.assertIn('放松下颌', safe)      # 练习建议保留
        self.assertIn('自然发声', safe)

    def test_bare_claim_is_replaced_in_place_not_disclaimed_at_the_end(self):
        safe, issues = self.sanitize('你气息支撑不足。')
        self.assertEqual(len(issues), 1)
        self.assertNotIn('支撑不足', safe)
        self.assertEqual(safe, '现有声学测量不能确定喉部或气息状态。')

    def test_valid_observations_survive_a_mixed_report(self):
        safe, issues = self.sanitize(REPORT)
        self.assertEqual(len(issues), 1)
        self.assertIn('本次录音强度均值 50.599 dB', safe)
        self.assertIn('首尾 50.67/50.92 dB', safe)
        self.assertIn('基频平均绝对斜率 334.57 Hz/s', safe)
        self.assertNotIn('气息支撑不足', safe)
        # 测量句在正文里仍然位于原来的位置（不是被整段重写）。
        self.assertTrue(safe.startswith('本次录音强度均值 50.599 dB'))

    def test_clean_text_is_returned_unchanged(self):
        text = '本次录音强度均值 50.599 dB，基频平均绝对斜率 334.57 Hz/s。'
        self.assertEqual(self.sanitize(text), (text, []))

    def test_sanitize_reports_the_same_issues_as_the_check(self):
        safe, issues = self.sanitize(REPORT)
        checked = interpretation_policy.check_interpretation_claims(recording_state(), REPORT)
        self.assertEqual([issue['rule'] for issue in issues], [issue['rule'] for issue in checked])
        self.assertEqual(safe, self.sanitize(REPORT)[0])


class StateIsNotMutatedTests(unittest.TestCase):
    """两个入口都不得改写 state（metrics / evidence / 正文）。"""

    def test_metrics_and_evidence_stay_untouched(self):
        state = recording_state()
        before = [item.signature for item in state.evidence]
        interpretation_policy.check_interpretation_claims(state, REPORT)
        safe, _ = interpretation_policy.sanitize_interpretation(state, REPORT)
        self.assertEqual(state.metrics, {})
        self.assertEqual([item.signature for item in state.evidence], before)
        self.assertNotEqual(safe, REPORT)


class AccentClaimTests(unittest.TestCase):
    """日语词汇重音：没有词典字段，只要求来源与变体，不认具体词、不做事实裁判。"""

    def issues(self, text, goal=JAPANESE_GOAL):
        return interpretation_policy.check_interpretation_claims(recording_state(goal=goal), text)

    def test_unattributed_accent_type_is_reported(self):
        issues = self.issues('「さくら」的标准重音型是尾高型。')
        self.assertEqual([issue['rule'] for issue in issues],
                         ['interpretation.accent_norm_unattributed'])
        self.assertIn('词典来源', issues[0]['message'])
        self.assertIn('核后下降', issues[0]['message'])
        self.assertIn('核内下降', issues[0]['message'])

    def test_accent_rule_needs_a_japanese_context(self):
        # 没有假名、目标也不是日语：不当成重音断言（避免拦普通知识）。
        self.assertEqual(self.issues('标准重音型是尾高型。', goal=MEASURE_GOAL), [])

    def test_sourced_or_variant_accent_statement_is_allowed(self):
        for text in ('据词典，「はし」的标准重音型是头高型，同时存在方言变体。',
                     '「はし」的重音型取决于词义与方言，两种读法都有人用。',
                     '不能把（1）型当成这个词的固定重音型。'):
            with self.subTest(text=text):
                self.assertEqual(self.issues(text), [])

    def test_ordinary_japanese_accent_knowledge_is_allowed(self):
        for text in ('日语是高低重音语言，重音位置可以区分词义，例如「あめ」可以表示雨或糖。',
                     '一般知识：日语重音以「拍」为单位，核后下降与核内下降的听感不同。'):
            with self.subTest(text=text):
                self.assertEqual(self.issues(text), [])

    def test_sanitize_neutralises_an_unattributed_accent_type(self):
        safe, issues = interpretation_policy.sanitize_interpretation(
            recording_state(goal=JAPANESE_GOAL), '这个词的标准重音型是尾高型。')
        self.assertEqual(len(issues), 1)
        self.assertNotIn('尾高型', safe)
        self.assertIn('须核对可靠词典及可能的变体', safe)

    def test_no_specific_word_or_threshold_is_hardcoded(self):
        # 两个不同的词走同一条规则；判据里没有假名词表，也没有频率门槛。
        first = [issue['rule'] for issue in self.issues('「さくら」的标准重音型是尾高型。')]
        second = [issue['rule'] for issue in self.issues('「はし」的标准重音型是头高型。')]
        self.assertEqual(first, second)
        # 判据本身（不含只在文档里复述事故的字符串）不认具体词，也没有数值门槛。
        rules_source = ''.join(inspect.getsource(fn) for fn in (
            interpretation_policy._accent_claim, interpretation_policy._physiology_claim,
            interpretation_policy._claim, interpretation_policy._measure_fragment))
        for word in ('思い出す', 'さくら', 'はし'):
            with self.subTest(word=word):
                self.assertNotIn(word, rules_source)
        for name in ('_VALUE', '_BODY_PART', '_BODY_STATE', '_NORM', '_DEFECT', '_PSYCH'):
            with self.subTest(pattern=name):
                pattern = getattr(interpretation_policy, name).pattern
                self.assertIsNone(re.search(r'\d', pattern))


class TailGuidanceTests(unittest.TestCase):
    """尾指导只在相关录音目标出现时才有内容，且不含具体词或阈值。"""

    def guidance(self, goal):
        return interpretation_policy.task_tail_guidance(recording_state(goal=goal))

    def test_plain_measurement_goals_get_nothing(self):
        for goal in (MEASURE_GOAL, '这段录音的平均强度是多少？', '把这个 Sound 另存为文件。'):
            with self.subTest(goal=goal):
                self.assertEqual(self.guidance(goal), '')

    def test_prosody_and_voice_goal_gets_the_physiology_boundary(self):
        guidance = self.guidance(GOAL)
        self.assertIn('喉部紧张', guidance)
        self.assertIn('舒适音域', guidance)
        self.assertIn('练习建议', guidance)
        self.assertNotIn('核后下降', guidance)
        self.assertLess(len(guidance), 320)

    def test_japanese_accent_goal_gets_the_source_and_variant_rule(self):
        guidance = self.guidance(JAPANESE_GOAL)
        self.assertIn('词典来源', guidance)
        self.assertIn('变体', guidance)
        self.assertIn('核后下降', guidance)
        self.assertIn('核内下降', guidance)
        self.assertNotIn('尾高型', guidance)
        self.assertNotIn('喉部紧张', guidance)

    def test_guidance_is_stable_and_independent_of_evidence(self):
        self.assertEqual(self.guidance(GOAL), interpretation_policy.task_tail_guidance(empty_state(GOAL)))
        self.assertEqual(self.guidance(GOAL), self.guidance(GOAL))


if __name__ == '__main__':
    unittest.main()
