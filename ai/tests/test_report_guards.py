import unittest

from pydantic_ai import ModelRetry

from praat_ai.cloud_agent import AnalysisReport, Coverage, UNVERIFIED_NUMBER
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.report_guards import (UNVERIFIED_CALC, report_context_data, report_guard_instructions,
                                    validate_report)


CONTEXT = '1\tSound\ttarget\t1\t0.2\t0.4\n2\tSound\tother\t0\n'
#: 跨对象比较用的对象列表：对象 1 被选中，对象 2、3 都在快照里但未选中。
CROSS_CONTEXT = '1\tSound\tfirst\t1\t0.0\t1.0\n2\tSound\tsecond\t0\t0.0\t1.0\n3\tSound\tthird\t0\t0.0\t1.0\n'
#: 真实 high 模型的目标：用户点名了两个对象，并要两者的差值。
CROSS_GOAL = '分别测量对象 1 和对象 2 的平均基频，然后告诉我两者的差值。'


def report(text, explanation='已有解释和实际证据，仍应核对录音范围。'):
    return AnalysisReport(analysis=text + '\n报告依据已有记录，范围外的声音特征仍需相应测量。',
                          coverage=[Coverage(item='分析录音', status='partial', explanation=explanation)])


def evidence(tool, rows, obj=1, **args):
    return Evidence(tool, {'object': obj, **args}, rows, CONTEXT)


def pitch(mean=220, start=0, end=1, obj=1):
    return evidence('pitch_statistics',
                    [f'基频统计（{start:.3f}–{end:.3f} 秒）：平均 {mean:.3f} Hz，最低 200.000 Hz，最高 240.000 Hz，最高点出现在 0.600 秒'],
                    obj=obj, **{'from': start, 'to': end})


class ReportGuardsTests(unittest.TestCase):
    def test_vot_frequency_band_range_keeps_units_and_does_not_become_pitch(self):
        state=self.state(evidence('vot', ['VOT 估计值 = 0.0050 秒：爆破 0.168 秒（高频带 2–8 kHz 能量升 17.3 dB）→ 浊音起始 0.173 秒'], **{'from':.1,'to':.4}))
        self.accepts(state,'VOT 估计值为 0.0050 秒；爆破检测依据高频带 2–8 kHz 的能量上升 17.3 dB。[E1]')
        self.accepts(state,'检测使用的高频带为 2000–8000 Hz。[E1]')
        self.rejects(state,'检测使用的高频带为 4–8 kHz。[E1]')
        self.rejects(state,'平均基频为 2 kHz。[E1]')

    def test_pitch_min_max_range_can_summarize_separate_extrema(self):
        state=self.state(pitch(220))
        self.accepts(state,'平均基频 220 Hz，基频范围 200–240 Hz。[E1]')
        self.rejects(state,'平均基频 220 Hz，基频范围 180–240 Hz。[E1]')
        self.rejects(state,'强度范围 200–240 Hz。[E1]')

    def test_vot_report_accepts_known_id_colon_but_rejects_unknown_id(self):
        context = 'id\tclass\tname\tselected\n1\tSound\tSound あなた\t1\n'
        item = Evidence('vot', {'object':1, 'from':0.446194, 'to':0.66158},
                        ['VOT 估计值 = 0.0380 秒（38.0 毫秒）：爆破 0.551 秒 → 浊音起始 0.589 秒'], context)
        for label in ('ID: 1', 'id：1', '对象 1', 'object: 1', '编号 1'):
            with self.subTest(label=label):
                state = AnalysisState('分析录音', context, evidence=[item])
                self.accepts(state, f'本次分析针对对象 Sound あなた（{label}）。\nVOT 估计值为 38.0 毫秒。[E1]')
        for label in ('ID: 99', 'id：99', 'object: 99', '编号 99'):
            with self.subTest(label=label):
                state = AnalysisState('分析录音', context, evidence=[item])
                self.rejects(state, f'本次分析针对对象 Sound あなた（{label}）。')
                self.assertEqual(state.metrics['guard_blocks'], {'report.object_unknown':1})
        self.rejects(AnalysisState('分析录音', context, evidence=[item]), '本次测得计数为 1。')

    def state(self, *items):
        return AnalysisState('分析录音语调', CONTEXT, evidence=list(items))

    def accepts(self, state, text):
        return validate_report(state, report(text))

    def rejects(self, state, text):
        with self.assertRaises(ModelRetry):
            self.accepts(state, text)

    def annotated(self, state, text):
        """走向结论缺证据时不再拦整份报告，而是在那一行后面写明未核对。

        """

        result = self.accepts(state, text)
        self.assertIn('缺少同目标的走向证据', result.analysis)
        self.assertIn('report.prosody_unverified', state.metrics.get('guard_repairs', {}))
        return result

    def test_context_indexes_have_roles_without_duplicate_values(self):
        state = self.state(pitch())
        context = report_context_data(state)
        self.assertEqual(context['evidence_ids'], ['E1'])
        self.assertEqual(context['quantity_refs']['E1'][:3], ['q1:range.start', 'q2:range.end', 'q3:pitch.mean'])
        self.assertNotIn('220', str(context))
        self.assertIn('E1.q1', report_guard_instructions(state))
        self.assertEqual(report_context_data(self.state()), {})
        self.assertIn('没有测量', report_guard_instructions(self.state()))

    def test_real_measurement_with_range_and_unit_conversion(self):
        self.accepts(self.state(pitch()), '本段在 0.000–1.000 秒的平均基频为 0.220 kHz。[E1]')
        self.accepts(self.state(pitch(219.96)), '平均基频约 220.0 Hz。[E1]')

    def test_fabricated_number_without_or_with_unrelated_citation(self):
        # 编造的数值照旧拦；但「漏写引证」和「写错编号」不再算编造——守卫自己会把引证
        # 补上、把不存在的编号清掉（2026-10-06 起：引证是守卫的记账方式，不是模型必须
        # 逐行照抄的格式）。
        self.rejects(self.state(), '这段录音的平均基频是 220 Hz。')
        self.rejects(self.state(pitch()), '平均基频是 300 Hz。[E1]')
        self.rejects(self.state(pitch()), '平均基频是 300 Hz。')
        state = self.state(pitch())
        result = self.accepts(state, '平均基频是 220 Hz。[E99]')
        self.assertIn('[E1]', result.analysis)
        self.assertNotIn('[E99]', result.analysis)
        self.assertIn('[E1]', self.accepts(self.state(pitch()), '平均基频是 220 Hz。').analysis)
        self.assertEqual(state.metrics['guard_repairs'],
                         {'report.citation_unknown': 1, 'report.citation_filled': 1})

    def test_wrong_units_are_not_the_same_quantity(self):
        self.rejects(self.state(pitch()), '平均基频是 220 kHz。[E1]')
        self.rejects(self.state(pitch()), '平均基频是 220 ms。[E1]')
        self.rejects(self.state(pitch()), '平均基频是 220。[E1]')

    def test_matching_number_from_f1_is_not_mean_pitch(self):
        item = evidence('formant_frequency', ['F1 = 220.000 Hz'])
        self.accepts(self.state(item), '第一共振峰 F1 为 220 Hz。[E1]')
        self.rejects(self.state(item), '平均基频为 220 Hz。[E1]')

    def test_vot_components_keep_their_own_wording(self):
        # 2026-10-06 实测：vot 那一行的每个数都被打成 vot.value，而报告里「爆破 0.551 秒」
        # 这类换个说法的句子拿不到量名，两边标签一比就判成编造，两次拒绝后整份回答被
        # 换掉。没点名量名的出处标签现在退回「同对象 + 同范围 + 同量纲 + 同数值」核对。
        item = evidence('vot', ['VOT 估计值 = 0.0380 秒（38.0 毫秒）：爆破 0.551 秒；浊音起始 0.589 秒'])
        for text in ['爆破 0.551 秒 [E1]，浊音起始 0.589 秒 [E1]。',
                     '该音的爆破出现在 0.551 秒 [E1]',
                     '- 浊音起始：0.589 秒 [E1]']:
            with self.subTest(text=text):
                self.accepts(self.state(item), text)
        self.rejects(self.state(item), '爆破 0.777 秒 [E1]。')
        # 漏写引证不再是编造：守卫自己把数字落在哪条证据上写进正文。
        self.assertIn('[E1]', self.accepts(self.state(item), '爆破 0.551 秒。').analysis)

    def test_verbatim_code_block_quote_may_omit_the_unit(self):
        """代码块/赋值式是在抄工具输出：省略单位可以，但数值必须唯一对得上证据。

        2026-10-07 实测：模型把工具输出原样放进代码块（``mean_pitch = 81.558``），
        守卫因为「没写单位」把它判成未核对；抄输出不是对录音下断言，不该按实测主张处理。
        """

        result = self.accepts(self.state(pitch(220)), '```\nmean_pitch = 220.000\n```')
        self.assertIn('220.000', result.analysis)
        self.accepts(self.state(pitch(220)), 'pitch = 220.0')
        # 编造的代码块数值仍然拦：没有任何证据能唯一对上它。
        self.rejects(self.state(pitch(220)), '```\nmean_pitch = 999.900\n```')
        # 不带单位的**实测主张**仍然要拦，逐字引用不等于「是 220 Hz」。
        self.rejects(self.state(pitch(220)), '平均基频是 220。')

    def test_inline_general_knowledge_marker_is_a_real_escape_hatch(self):
        """提示词承诺「一般知识明确标注」，标注就得真的管用。

        2026-10-07 实测：``（一般知识，非本次测量）成年男性典型基频范围为 85–180 Hz``
        仍然被拦——因为标注文字里的「本次」命中了「指名本次录音」的判定。但从
        「一般知识」标签里读出实测主张本来就不成立；真正点名本录音的句子照旧要拦。
        """

        state = self.state(pitch(220))
        self.accepts(state, '（一般知识，非本次测量）成年男性典型基频范围为 85–180 Hz。')
        self.accepts(state, '一般知识：成年男性典型基频范围约 85–180 Hz。')
        # 用「一般知识」当挡箭牌把录音数值说成实测，仍然拦。
        self.rejects(self.state(), '一般知识：本录音的平均基频为 220 Hz。')
        self.rejects(self.state(pitch(220)), '（一般知识）本段实测平均基频为 300 Hz。[E1]')

    def test_locale_and_chinese_number_forms_are_checked(self):
        """千分位/小数逗号、全角数字、中文数字都要按数值核对。

        2026-10-07 实测：``81,558 Hz`` 被拆成 81 和 558 判成未核对；``８１．５５８``
        整段跳过核对；``平均基频约八十一点六赫兹`` 更是完全不看——编造的数字只要写成
        中文或全角就能躲过守卫。
        """

        state = self.state(pitch(220))
        self.accepts(state, '平均基频 220,000 Hz。[E1]')      # 小数逗号读法 = 220.000
        self.accepts(state, '平均基频 ２２０．０ Hz。[E1]')     # 全角数字
        self.accepts(state, '平均基频约为二百二十赫兹。')        # 中文数字（与实测一致）
        self.rejects(self.state(pitch(220)), '平均基频约为二百五十赫兹。')
        self.rejects(self.state(pitch(220)), '平均基频 220,900 Hz。[E1]')

    def test_no_input_claim_is_rejected_when_the_recording_is_measurable(self):
        """「没有音频输入所以不能测量」不能当结论：测量由 Praat 对对象执行。

        2026-10-07 实测：一轮里已经测到 10 行数据、报告仍以 audio_input=false 收尾；
        另一轮完全没有测量，却把「对象列表里有 Sound」说成无法测量。
        """

        from praat_ai.cloud_agent import AnalysisReport, Coverage
        measured = self.state(pitch(220))
        self.rejects(measured, '由于 audio_input=false，未提供音频输入，无法执行任何测量。')
        # 没有测量、但对象列表里有可测量的声音对象，同样不能说「测不了」。
        with_sound = AnalysisState('测量当前声音平均基频', CONTEXT)
        with self.assertRaises(ModelRetry):
            validate_report(with_sound, AnalysisReport(
                analysis='系统显示 audio_input 为 false，因此无法进行任何声学测量，结果缺失。',
                coverage=[Coverage(item='分析录音', status='missing',
                                   explanation='没有音频输入因此无法测量这一项')]))
        # 引用了证据的报告不受影响。
        self.accepts(measured, '已有测量：平均基频 220 Hz [E1]。本轮未听原始音频，属正常。')

    def test_missing_object_must_be_named_as_missing(self):
        """用户点名的对象不存在时，回答要明说找不到，而不是换个理由搪塞。"""

        from praat_ai.cloud_agent import AnalysisReport, Coverage
        state = AnalysisState('测量名为 my_recording.wav 的对象的平均基频', CONTEXT)
        state.context_text = CONTEXT + ('# 用户提到但这些对象不在当前 Praat 对象列表里：'
                                        '「my_recording.wav」；只能就现有对象测量，'
                                        '并在回答里明确说明它们不存在，不要改用别的对象。\n')
        text = '本次没有取得 my_recording.wav 的数值，任务尚未完成。'
        with self.assertRaises(ModelRetry) as caught:
            validate_report(state, AnalysisReport(
                analysis=text + '\n报告依据已有记录，范围外的声音特征仍需相应测量。',
                coverage=[Coverage(item='测量平均基频', status='missing',
                                   explanation='没有音频输入因此无法测量这一项')]))
        self.assertIn('report.missing_object_unmentioned', str(state.metrics['guard_blocks']))
        # 说清了「找不到」就通过。
        self.accepts(state, '对象「my_recording.wav」不在当前对象列表里，找不到，无法测量。')

    def test_numeric_object_and_range_must_match(self):
        self.rejects(self.state(pitch(obj=2)), '本段平均基频为 220 Hz。[E1]')
        self.accepts(self.state(pitch(obj=2)), '对象 2 other 的平均基频为 220 Hz。[E1]')
        self.rejects(self.state(pitch()), '在 0.000–0.500 秒的平均基频是 220 Hz。[E1]')
        self.rejects(self.state(pitch(210, 0, .5), pitch(220, .5, 1)),
                     '在 0.000–0.500 秒的平均基频是 220 Hz。[E1] [E2]')

    def test_block_counters_name_the_rule(self):
        # 守卫的强制力必须可统计：哪条规则真的在拦、拦了几次，是决定它继续当闸门还是
        # 退回提示词的唯一依据（2026-10-06 那次事故只能靠猜）。
        state = self.state(pitch())
        self.rejects(state, '平均基频是 300 Hz。[E1]')
        self.assertEqual(state.metrics['guard_blocks'], {'report.number_unmatched': 1})
        # 走向结论缺证据现在是可见提醒（repair），不再占阻断名额。
        self.annotated(state, '本段音高下降。[E1]')
        self.assertEqual(state.metrics['guard_blocks'], {'report.number_unmatched': 1})
        self.rejects(state, '对象 99 的平均基频为 220 Hz。[E1]')
        self.assertEqual(state.metrics['guard_blocks']['report.object_unknown'], 1)

    def test_general_knowledge_and_ordinary_dialogue_need_no_tool(self):
        self.accepts(self.state(), '一般知识：一八度包含 12 个半音，这只是概念说明。')
        self.accepts(self.state(), '# 一般知识\n通常用 75–600 Hz 作为分析示例。\n# 本次结果\n没有测量，不能判断这段录音是否为平板型。')
        self.accepts(self.state(), '你好，我们可以先明确你的目标，再选择合适的操作。')
        self.rejects(self.state(), '一般知识：本录音的平均基频为 220 Hz。')

    def test_honest_missing_measurement_is_not_a_prosody_claim(self):
        for text in ('不能判断平板型，缺少音高走向测量。',
                     '只有全段平均值，无法判断这段语调是否平淡。',
                     '平均值不代表音高下降，需要先取得分段测量。'):
            self.accepts(self.state(), text)
        self.annotated(self.state(), '不能判断平板型，但这段音高下降。')
        self.annotated(self.state(), '这段不是平板型，而是头高型。')

    def test_mean_alone_cannot_support_prosody(self):
        self.annotated(self.state(pitch()), '平均基频 220 Hz，所以本段是平板型。[E1]')
        self.annotated(self.state(pitch()), '这段音高下降。[E1]')
        self.accepts(self.state(pitch()), '平均基频为 220 Hz，仍不能判断平板型，需要走向测量。[E1]')

    def test_defined_slope_supports_variability_but_absolute_slope_not_direction(self):
        slope = evidence('measure', ['基频平均绝对斜率 = 1.00 Hz/s'], parameters=['pitch_slope'])
        self.accepts(self.state(slope), '基频平均绝对斜率为 1 Hz/s，本段语调平坦；局部情况仍须核对。[E1]')
        self.annotated(self.state(slope), '本段音高下降。[E1]')
        octave_free = evidence('measure', ['不含倍频跳变的基频斜率 = 1.00 半音/s'], parameters=['pitch_slope_octave_free'])
        self.accepts(self.state(octave_free), '不含倍频跳变的基频斜率为 1 半音/s，本段语调平坦。[E1]')
        self.annotated(self.state(octave_free), '本段音高下降。[E1]')

    def test_defined_start_end_pair_same_target_and_range(self):
        start = evidence('measure', ['起点基频（0.000–1.000 秒）= 240.00 Hz'], parameters=['pitch_start'], **{'from': 0, 'to': 1})
        end = evidence('measure', ['终点基频（0.000–1.000 秒）= 200.00 Hz'], parameters=['pitch_end'], **{'from': 0, 'to': 1})
        self.accepts(self.state(start, end), '本段音高下降；起终点测量可支持整体趋势，局部轨迹仍要核对。[E1] [E2]')
        self.annotated(self.state(start), '本段音高下降。[E1]')
        other = evidence('measure', end.rows, obj=2, parameters=['pitch_end'], **{'from': 0, 'to': 1})
        self.annotated(self.state(start, other), '本段音高下降。[E1] [E2]')
        different_range = evidence('measure', ['终点基频（1.000–2.000 秒）= 200.00 Hz'], parameters=['pitch_end'], **{'from': 1, 'to': 2})
        self.annotated(self.state(start, different_range), '本段音高下降。[E1] [E2]')

    def test_defined_ordered_segments_support_trajectory(self):
        self.accepts(self.state(pitch(240, 0, .5), pitch(200, .5, 1)),
                     '本段音高下降，分段平均值显示前后差异，但音调分类仍须结合对齐。[E1] [E2]')
        self.annotated(self.state(pitch(240, 0, 1), pitch(200, 0, 1)), '本段音高下降。[E1] [E2]')
        self.annotated(self.state(pitch(240, 0, .5), pitch(200, .5, 1, obj=2)), '本段音高下降。[E1] [E2]')

    def test_undefined_irrelevant_and_nonmeasurement_do_not_support_prosody(self):
        undefined = evidence('measure', ['起点基频（0.000–1.000 秒）无法计算：这一段里没有可用的数据',
                                        '终点基频（0.000–1.000 秒）无法计算：这一段里没有可用的数据'],
                             parameters=['pitch_start', 'pitch_end'])
        undefined_slope = evidence('measure', ['基频平均绝对斜率 = --undefined--'], parameters=['pitch_slope'])
        intensity = evidence('measure', ['强度斜率 = 1.00 dB'], parameters=['mean_intensity'])
        audio = Evidence('listen', {}, ['基频斜率 = 1.00 Hz/s'], CONTEXT, kind='audio')
        for item in (undefined, undefined_slope, intensity, audio, pitch(obj=2)):
            self.annotated(self.state(item), '这段语调平坦。[E1]')
        # 数值仍然照拦：说「起点基频为 0 Hz」而证据里那一行是 undefined。
        self.rejects(self.state(undefined), '起点基频为 0 Hz。[E1]')

    def test_deterministic_transparent_percentage_derivation(self):
        state = self.state(evidence('pitch_statistics', ['基频统计（0.200–0.400 秒）：平均 220.000 Hz']),
                           evidence('duration', ['target 总时长 = 1.000000 秒']))
        result = self.accepts(state, '选段起点占总时长的比例为 {{calc:E1.q1/E2.q1*100|%}}。')
        self.assertIn('(0.200 秒)/(1.000000 秒)*100 = 20 % [E1] [E2]', result.analysis)
        self.assertNotIn('{{calc:', result.analysis)
        self.rejects(state, '选段占比为 90 %；算式为 {{calc:E1.q1/E2.q1*100|%}}。')

    def test_derivation_checks_reference_units_arithmetic_and_no_code(self):
        state = self.state(pitch(), evidence('duration', ['target 总时长 = 1.000000 秒']))
        # 算不出来的推导式只把那一处标成「未核对」，不再阻断整份报告：这一处不成立不等于
        # 报告在编造测量（2026-10-06 起；原来 20 个阻断点里有 13 个是这类内部语法问题）。
        for expression in ('E1.q99/E2.q1*100|%', 'E1.q3+E2.q1|Hz', 'E1.q3/E2.q1|ms',
                           'E1.q3/0|Hz', '999|Hz', '__import__("os")|Hz', 'E1.q3**2|Hz',
                           'E1.q3+999|Hz', 'E1.q3|madeup', 'E1.q1/E2.q1*100|Hz'):
            with self.subTest(expression=expression):
                result = self.accepts(state, '透明推导为 {{calc:' + expression + '}}。')
                self.assertIn(UNVERIFIED_CALC, result.analysis)
                self.assertNotIn('{{calc:', result.analysis)
        result = self.accepts(state, '透明推导为 {{calc:E1.q3}}。')
        self.assertIn(UNVERIFIED_CALC, result.analysis)
        self.assertNotIn('os', result.analysis)     # 注入文本既不会执行，也不会留在正文里
        # 算得出来的照样由程序写结果。
        self.assertIn('(220.000 Hz)', self.accepts(state, '平均基频换算为 {{calc:E1.q3|kHz}}。').analysis)
        self.assertIn('(1.000000 秒)', self.accepts(state, '总时长换算为 {{calc:E2.q1|ms}}。').analysis)

    def test_derivation_cannot_launder_f1_into_mean_pitch(self):
        state = self.state(evidence('formant_frequency', ['F1 = 220.000 Hz']))
        self.rejects(state, '平均基频为 {{calc:E1.q1|Hz}}。')

    def test_derivation_cannot_launder_other_recording_as_original_target(self):
        state = self.state(pitch(), pitch(obj=2))
        self.rejects(state, '本段平均基频为 {{calc:E2.q3*1|Hz}}。')
        self.accepts(state, '对象 2 other 的平均基频为 {{calc:E2.q3|Hz}}。')

    def test_non_pitch_directions_and_noise_recommendations_are_not_prosody(self):
        self.accepts(self.state(), '下一步先降低噪声，再取得可靠测量；调整位置有助于降低误差。')
        self.accepts(self.state(), '强度下降可能影响响度，需要强度测量才能具体说明。')
        self.accepts(self.state(), '共振峰升高可能反映口形改变，这里尚未取得相应测量。')

    def test_whole_recording_conclusion_cannot_use_two_tiny_unrelated_segments(self):
        state = self.state(pitch(240, 0, .1), pitch(200, .9, 1),
                           evidence('duration', ['target 总时长 = 1.000000 秒']))
        state.goal = '分析整段录音语调'
        self.annotated(state, '整段录音音高下降。[E1] [E2]')
        state.evidence[:2] = [pitch(240, 0, .5), pitch(200, .5, 1)]
        self.accepts(state, '整段录音音高下降；分段测量只支持整体趋势。[E1] [E2]')

    def test_percentage_and_milliseconds_from_real_measurement(self):
        state = self.state(evidence('vot', ['VOT = 25.000 毫秒']),
                           evidence('measure', ['jitter = 1.25 %']))
        self.accepts(state, 'VOT 为 0.025 秒。[E1]')
        self.accepts(state, 'jitter = 1.25 %。[E2]')
        self.rejects(state, 'VOT 为 25 秒。[E1]')
        self.rejects(state, 'jitter = 125 %。[E2]')

    def test_coverage_explanations_are_checked_too(self):
        with self.assertRaises(ModelRetry):
            validate_report(self.state(pitch()), report('现有平均值不足以判断具体音调类型，须先取得走向测量。',
                                                        '本段平均基频为 999 Hz。[E1]'))

    def test_next_step_parameters_are_not_fabricated_measurements(self):
        self.accepts(self.state(), '建议重复测量 3 次，再选择 75 Hz 的基频下限作参数对照。')
        self.rejects(self.state(), '建议使用本录音测得的 220 Hz 作为依据。')

    def test_report_cannot_invent_object_identifier(self):
        self.rejects(self.state(pitch()), '对象 99 的平均基频为 220 Hz。[E1]')

    def test_whole_mean_cannot_relabel_segment_mean(self):
        state = self.state(pitch(220, .2, .4), evidence('duration', ['target 总时长 = 1.000000 秒']))
        state.goal = '分析整个声音的音调'
        self.rejects(state, '整段平均基频为 220 Hz。[E1]')
        self.rejects(state, '整段平均基频为 {{calc:E1.q3|Hz}}。')
        self.accepts(state, '在 0.200–0.400 秒的平均基频为 220 Hz。[E1]')


class RepairedDeliveryTests(unittest.TestCase):
    """第二次仍不过时交付遮过数值的正文，而不是丢掉整份回答。

    2026-10-07 实测：报告守卫连拒两次会抛 ``UnexpectedModelBehavior('Exceeded maximum
    output retries (1)')``，工作流分支再把正文塞进没人读的 ``unverified_draft``——22 次
    真实提问里 12 次只剩测量表加一句英文内部错误。守卫管的是「没测过的数字不能当结论」，
    不是「这份回答不合格就不给」。
    """

    def setUp(self):
        self.state = AnalysisState('分析录音', CONTEXT, evidence=[pitch()])

    def test_second_rejection_keeps_prose_and_masks_both_rounds_values(self):
        from praat_ai.cloud_agent import repaired_report
        self.state.metrics['rejected_values'] = ['300 Hz']
        draft = report('平均基频是 300 Hz。该选段时长为 0.215386 秒，说明节奏稳定。')
        repaired = repaired_report(draft, '数值“0.215386 秒”未匹配同对象/范围/量名的测量。',
                                   self.state, self.state)
        self.assertIsNotNone(repaired)
        self.assertNotIn('300 Hz', repaired.analysis)
        self.assertNotIn('0.215386', repaired.analysis)
        self.assertIn(UNVERIFIED_NUMBER, repaired.analysis)
        self.assertIn('说明节奏稳定', repaired.analysis)   # 正文和结论留着
        self.assertIn('依赖这些数值的判断暂不能作为结论', repaired.analysis)
        self.assertTrue(self.state.metrics['report_repaired'])
        self.assertEqual(self.state.metrics['guard_repairs']['report.delivered_with_masks'], 1)

    def test_masked_number_survives_as_unverified_marker_not_as_measurement(self):
        from praat_ai.cloud_agent import repaired_report
        draft = report('本段平均基频为 300 Hz，最低 200 Hz，说明节奏稳定。')
        repaired = repaired_report(draft, '数值“300 Hz”未匹配同对象/范围/量名的测量。',
                                   self.state, self.state)
        body, _, note = repaired.analysis.partition('（部分数值未能从测量结果中确认')
        self.assertNotIn('300 Hz', repaired.analysis)
        self.assertIn('最低 200 Hz', body)              # 核对通过的实测值原样保留
        self.assertEqual(body.count(UNVERIFIED_NUMBER), 1)
        self.assertIn('说明节奏稳定', body)
        self.assertNotIn('请按这条提示核对', note)      # 交付文案不对模型提要求
        self.assertIn('依赖这些数值的判断暂不能作为结论', note)

    def test_bracket_pair_of_selection_bounds_is_a_range_not_a_stray_number(self):
        """``[0.446194, 0.66158] 秒`` 和 ``0.446194–0.66158 秒`` 是同一个范围。

        2026-10-07 实测：模型用括号对写编辑器圈选，守卫不认这种写法，把一个**正确**的范围
        当成孤立数字连拒两次，最后正文里留下 ``[[未通过核对的数值], 0.66158]``。
        """

        state = AnalysisState('测量选中这段录音的平均基频', CONTEXT,
                              evidence=[pitch(mean=220, start=0.446194, end=0.66158)])
        for text in ('编辑器圈选区间为 [0.446194, 0.66158] 秒。[E1]',
                     '编辑器圈选区间为 0.446194–0.66158 秒。[E1]',
                     '编辑器圈选区间为 [0.446194, 0.66158]。[E1]'):
            with self.subTest(text=text):
                validate_report(state, report(text))

    def test_repair_masks_every_number_the_guard_still_objects_to(self):
        from praat_ai.cloud_agent import repaired_report
        state = AnalysisState('分析录音语调', CONTEXT, evidence=[pitch()])
        draft = report('本段平均基频为 300 Hz，比基线高 80 Hz，说明节奏稳定。')
        repaired = repaired_report(draft, '数值“300 Hz”未匹配同对象/范围/量名的测量。', state, state)
        body, _, note = repaired.analysis.partition('（部分数值未能从测量结果中确认')
        self.assertNotIn('300 Hz', repaired.analysis)
        self.assertNotIn('80 Hz', repaired.analysis)
        self.assertEqual(body.count(UNVERIFIED_NUMBER), 2)
        self.assertIn('说明节奏稳定', body)
        self.assertNotIn('请按这条提示核对', note)      # 交付文案不对模型提要求
        self.assertIn('依赖这些数值的判断暂不能作为结论', note)

    def test_repaired_delivery_needs_prose_to_rescue(self):
        from praat_ai.cloud_agent import repaired_report
        empty = AnalysisReport(analysis='\n' * 40,
                               coverage=[Coverage(item='分析录音', status='partial',
                                                  explanation='没有可保留的解释性正文内容')])
        self.assertIsNone(repaired_report(empty, '数值“300 Hz”未匹配。', self.state, self.state))
        self.assertNotIn('report_repaired', self.state.metrics)

class CrossObjectCalculationTests(unittest.TestCase):
    """原目标明确点名多个对象时，跨对象的差值/比例就是用户要的结果，不能被当成冒用拦下。

    2026-10 真机（high）：目标「分别测量对象 1 和对象 2 的平均基频，然后告诉我两者的差值。」
    （E1 对象 1 平均 220 Hz、E2 对象 2 平均 200.001 Hz）报告
    「两者平均基频差值{{calc:E1.q3-E2.q3|Hz}}」被 report.calc_other_target 拒绝，
    原因是 _validate_text.calculate 的 _line_targets 默认落到选中对象 1。

    授权只来自原目标点名的、且已有证据确认的对象；行里点了名就只认点名的对象，
    目标没点名的对象永远不放行（不能夹带）。
    """

    def state(self, *items, goal=CROSS_GOAL):
        return AnalysisState(goal, CROSS_CONTEXT, evidence=list(items))

    def pair(self):
        return pitch(220, 0, 1), pitch(200.001, 0, 1, obj=2)

    def test_goal_authorized_difference_between_two_objects_is_rendered(self):
        state = self.state(*self.pair())
        result = validate_report(state, report('两者平均基频差值{{calc:E1.q3-E2.q3|Hz}}'))
        self.assertIn('= 19.999 Hz', result.analysis)
        self.assertIn('[E1] [E2]', result.analysis)
        self.assertNotIn('{{calc:', result.analysis)
        self.assertNotIn('report.calc_other_target', state.metrics.get('guard_blocks', {}))

    def test_goal_authorized_ratio_between_two_objects_is_rendered(self):
        state = self.state(*self.pair())
        result = validate_report(state, report('对象 1 与对象 2 的平均基频比例为{{calc:E1.q3/E2.q3|倍}}'))
        self.assertIn('倍', result.analysis)
        self.assertNotIn('{{calc:', result.analysis)
        self.assertNotIn('report.calc_other_target', state.metrics.get('guard_blocks', {}))

    def test_line_naming_object_one_cannot_spend_object_two_numbers(self):
        state = self.state(*self.pair())
        with self.assertRaises(ModelRetry):
            validate_report(state, report('对象 1 的平均基频为{{calc:E2.q3|Hz}}。'))
        self.assertEqual(state.metrics['guard_blocks'], {'report.calc_other_target': 1})

    def test_authorization_does_not_cover_an_object_the_goal_never_named(self):
        state = self.state(pitch(220, 0, 1), pitch(200.001, 0, 1, obj=2), pitch(180, 0, 1, obj=3))
        with self.assertRaises(ModelRetry):
            validate_report(state, report('两者平均基频差值{{calc:E1.q3-E3.q3|Hz}}'))
        self.assertEqual(state.metrics['guard_blocks'], {'report.calc_other_target': 1})

    def test_single_object_goal_keeps_cross_object_calculation_blocked(self):
        state = self.state(pitch(220, 0, 1), pitch(200.001, 0, 1, obj=2),
                           goal='测量对象 1 的平均基频')
        with self.assertRaises(ModelRetry):
            validate_report(state, report('两段平均基频差值{{calc:E1.q3-E2.q3|Hz}}'))
        self.assertEqual(state.metrics['guard_blocks'], {'report.calc_other_target': 1})

    def test_goal_can_authorize_complete_confirmed_object_names(self):
        state = self.state(*self.pair(), goal='比较 target 和 other 的平均基频，计算两者差值')
        result = validate_report(state, report('两者平均基频差值{{calc:E1.q3-E2.q3|Hz}}'))
        self.assertIn('= 19.999 Hz', result.analysis)
        self.assertNotIn('{{calc:', result.analysis)

    def test_multi_object_goal_does_not_authorize_an_unlabelled_single_foreign_value(self):
        state = self.state(*self.pair())
        with self.assertRaises(ModelRetry):
            validate_report(state, report('平均基频为{{calc:E2.q3|Hz}}。'))
        self.assertEqual(state.metrics['guard_blocks'], {'report.calc_other_target': 1})

    def test_multi_object_measurements_alone_do_not_authorize_implicit_arithmetic(self):
        state = self.state(*self.pair(), goal='分别测量对象 1 和对象 2 的平均基频')
        with self.assertRaises(ModelRetry):
            validate_report(state, report('该值为{{calc:E1.q3-E2.q3|Hz}}。'))
        self.assertEqual(state.metrics['guard_blocks'], {'report.calc_other_target': 1})

    def test_authorized_comparison_still_checks_quantity_role(self):
        state = self.state(*self.pair(), Evidence('measure', {'object': 2},
                                                  ['mean_intensity = 65.00 dB'], CROSS_CONTEXT))
        with self.assertRaises(ModelRetry):
            validate_report(state, report('两者平均基频差值{{calc:E1.q3-E3.q1|Hz}}'))
        self.assertEqual(state.metrics['guard_blocks'], {'report.calc_quantity_mismatch': 1})

if __name__ == '__main__':
    unittest.main()


class ReportVoiceTests(unittest.TestCase):
    """报告指令、守卫理由和交付文案说的是同一件事，而且都对用户说话。"""

    def test_report_instructions_and_context_do_not_offer_the_blocked_phrase(self):
        from praat_ai.cloud_agent import report_context, report_instructions
        from praat_ai.config import AppConfig

        state = AnalysisState('分析录音', CONTEXT, evidence=[pitch()])
        instruction = report_instructions(AppConfig(), state)
        self.assertNotIn('audio_input', instruction)
        self.assertNotIn('未听本轮录音', instruction)
        # 本轮没有音频时不发这个字段：模型没有现成句式可以照抄成「没有音频输入」。
        self.assertNotIn('audio_input', report_context(state))
        self.assertIs(report_context(state, audio=True)['audio_input'], True)

    def test_input_claim_block_states_the_fact_instead_of_ordering_the_model(self):
        state = AnalysisState('分析录音语调', CONTEXT, evidence=[pitch(220)])
        with self.assertRaises(ModelRetry) as caught:
            validate_report(state, report('由于 audio_input=false，未提供音频输入，无法执行任何测量。'))
        reason = str(caught.exception)
        self.assertIn('report.input_claim_conflict', str(state.metrics['guard_blocks']))
        self.assertIn('测量由 Praat 对对象执行', reason)
        self.assertNotIn('请执行测量并引用结果', reason)

    def test_delivered_text_never_addresses_the_model(self):
        from praat_ai.cloud_agent import repaired_report, rescued_report

        state = AnalysisState('分析录音', CONTEXT, evidence=[pitch()])
        detail = '数值“300 Hz”未匹配同对象/范围/量名的测量。'
        draft = report('本段平均基频为 300 Hz，说明节奏稳定。')
        repaired = repaired_report(draft, detail, state, state)
        self.assertIsNotNone(repaired)
        rescued = rescued_report(draft.analysis, detail, ['300 Hz'], state=state)
        for text in (repaired.analysis, rescued):
            self.assertIn('依赖这些数值的判断暂不能作为结论', text)
            self.assertNotIn('自动数值核对', text)
            self.assertNotIn('其余结论仍基于本次实测', text)
            for order in ('请执行测量并引用结果', '请按这条提示核对引用的数值和范围',
                          '请按这条提示核对', '为什么失败'):
                self.assertNotIn(order, text)


class QuantityTemplateSafetyTests(unittest.TestCase):
    """``{{q:…}}`` 只承认登记过的写法；其余一律确定性标缺口，并且不许再说「已核对」。

    2026-10-17 真机第二轮：正文里 12 处 ``{{q:E3.r1}}`` 原样交付给用户，状态还是
    ``verified``——占位正则只认 ``.qN``，模型照着 ``quantity_refs`` 里 ``r{行号}:{量名}``
    的 r1 写的模板整段漏了过去。修法只有一条：**不猜映射**（rN 不当 qN、不按行号推序号），
    已登记的照原样渲染，其余去掉内部语法、标出缺口、记修复指标。
    """

    def measured(self):
        return evidence('pitch_statistics',
                        ['基频统计（0.000–1.000 秒）：平均 220.000 Hz，最低 200.000 Hz，'
                         '最高 240.000 Hz，最高点出现在 0.600 秒'])

    def state(self, *items, goal='分析录音'):
        return AnalysisState(goal, CONTEXT, evidence=list(items))

    def test_rN_and_other_unregistered_templates_become_explicit_gaps(self):
        for template in ('{{q:E1.r1}}', '{{q:E1.q0}}', '{{q:E1.q9}}', '{{q:}}', '{{q:E1.q1'):
            with self.subTest(template=template):
                state = self.state(self.measured())
                result = validate_report(state, report(f'平均基频为{template}。'))
                self.assertNotIn('{{', result.analysis)
                self.assertNotIn('q:E1', result.analysis)
                self.assertIn(UNVERIFIED_NUMBER, result.analysis)
                # 没有把 r1 当成 q1：既没渲染 0.000 秒那个范围端点，也没渲染 220.000 Hz。
                self.assertNotIn('0.000 秒', result.analysis)
                self.assertNotIn('220.000', result.analysis)
                self.assertTrue(state.metrics['guard_repairs'])
                self.assertGreaterEqual(state.metrics['report_gap'], 1)

    def test_truncated_template_is_counted_as_malformed(self):
        state = self.state(self.measured())
        validate_report(state, report('平均基频为{{q:E1.q1 的平均基频。'))
        self.assertIn('report.quantity_malformed', state.metrics['guard_repairs'])

    def test_registered_template_still_renders_the_measured_value(self):
        state = self.state(self.measured())
        result = validate_report(state, report('平均基频为{{q:E1.q3}}，最低{{q:E1.q4}}。'))
        self.assertIn('220.000 Hz', result.analysis)
        self.assertIn('200.000 Hz', result.analysis)
        self.assertNotIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertNotIn('report_gap', state.metrics)

    def test_absurd_index_does_not_crash_the_guard(self):
        """5000 位序号：``int()`` 在 Python 3.11+ 直接抛 ValueError，不能崩在守卫里。"""
        state = self.state(self.measured())
        result = validate_report(state, report('平均基频为{{q:E1.q' + '9' * 5000 + '}}。'))
        self.assertIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertNotIn('9' * 40, result.analysis)

    def test_huge_exponent_cannot_overflow_the_tolerance(self):
        from praat_ai.report_guards import _precision

        self.assertEqual(_precision('1e999999'), 5e39)     # 指数夹在 ±40 位
        self.assertEqual(_precision('1e-999999'), 5e-41)
        state = self.state(self.measured())
        with self.assertRaises(ModelRetry):               # 拦下，但不抛 OverflowError
            validate_report(state, report('平均基频为 220e999999999 Hz。[E1]'))

    def test_rescue_exit_also_settles_templates(self):
        """``guard_delivered_text(verify=False)``：云端恢复稿出口，内部语法一样不能漏。"""
        from praat_ai.report_guards import guard_delivered_text

        state = self.state(self.measured())
        text, status = guard_delivered_text(state, '平均基频为{{q:E1.r1}}，最低{{q:E1.q4}}。',
                                            verify=False)
        self.assertNotIn('{{', text)
        self.assertIn(UNVERIFIED_NUMBER, text)
        self.assertIn('200.000 Hz', text)          # 已登记的照常逐字渲染
        self.assertEqual(status, 'rejected_masked')
        self.assertGreaterEqual(state.metrics['report_gap'], 1)

    def test_marked_gap_is_not_reported_as_verified(self):
        """标了缺口的正文可以交付，但核对状态不能说「全部对上」。"""
        from praat_ai.report_guards import guard_delivered_text

        state = self.state(self.measured())
        text, status = guard_delivered_text(state, '平均基频为{{q:E1.r1}}。')
        self.assertEqual(status, 'rejected_masked')
        self.assertIn(UNVERIFIED_NUMBER, text)
        self.assertNotIn('{{', text)
        clean = self.state(self.measured())
        _, status = guard_delivered_text(clean, '平均基频为{{q:E1.q3}}。')
        self.assertEqual(status, 'verified')

    def test_measure_slope_rows_use_the_parameter_key_the_tool_requires(self):
        """``measure`` 的参数名是 ``parameter``（schema 里 required）。

        以前只读 ``parameters``，真机 ``measure(parameter='pitch_slope_octave_free')``
        的「不含倍频跳变的斜率」被打成有方向的 ``pitch.slope``：一个绝对值白送了一条
        走向证据。``parameter`` 与旧写法 ``parameters`` 都要认成绝对斜率。
        """
        row = '不含倍频跳变的基频斜率 = 1.50 半音/s'
        for item in (evidence('measure', [row], parameter='pitch_slope_octave_free'),
                     evidence('measure', [row], parameters=['pitch_slope_octave_free'])):
            with self.subTest(arguments=item.arguments):
                state = self.state(item)
                result = validate_report(state, report('该段基频整体上升，语调上扬。'))
                self.assertIn('缺少同目标的走向证据', result.analysis)
                self.assertIn('report.prosody_unverified', state.metrics.get('guard_repairs', {}))
        mean_absolute = evidence('measure', ['基频平均绝对斜率 = 1.50 Hz/s'], parameter='pitch_slope')
        state = self.state(mean_absolute)
        self.assertIn('缺少同目标的走向证据',
                      validate_report(state, report('该段基频整体上升，语调上扬。')).analysis)

    def test_measure_row_without_a_value_registers_no_quantity(self):
        """measure 只回了「无法计算」行时 quantities 为空：模板只能成为缺口，不能造值。

        真机最近一次 E3(measure) 的 quantities 就是空的（f1 行只剩「第 1 共振峰」这个序号，
        没有任何测量值）。守卫不能因为「有这条证据」就放行 ``{{q:E3.qN}}``——那等于凭行号
        猜一个数。
        """
        undefined = evidence('measure', ['第 1 共振峰平均 无法计算：这个对象里没有可用的数据'],
                             parameter='f1')
        state = self.state(undefined)
        self.assertIsNone(report_context_data(state).get('quantity_refs'))
        result = validate_report(state, report('第一共振峰平均为{{q:E1.q1}}。'))
        self.assertIn(UNVERIFIED_NUMBER, result.analysis)
        self.assertEqual(state.metrics['guard_repairs'].get('report.quantity_unverified'), 1)

    def test_instructions_distinguish_row_numbers_from_quantity_indexes(self):
        state = self.state(self.measured())
        text = report_guard_instructions(state)
        self.assertIn('quantity_refs直接列出q序号', text)
        self.assertIn('不是证据行号', text)
        self.assertIn('{{q:E1.q2}}', text)
