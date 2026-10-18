"""标注合并 / 算式收尾 / 缺对照提示 / 时刻上限提示 四条的可测语义。"""
import unittest

from praat_ai import tools
from praat_ai.cloud_agent import AnalysisReport, Coverage
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.report_guards import (UNVERIFIED_NUMBER, report_guard_instructions,
                                    validate_report)
from praat_ai.tools._common import ToolError, _query_times, _times_arg

CONTEXT = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
           '1\tSound\ttarget\t1\t0.2\t0.4\n')


def report(text):
    return AnalysisReport(analysis=text + '\n报告依据已有记录，范围外的声音特征仍需相应测量。',
                          coverage=[Coverage(item='分析录音', status='partial',
                                             explanation='已有解释和实际证据，仍应核对录音范围。')])


def pitch(mean=220, start=0, end=1):
    return Evidence('pitch_statistics', {'object': 1, 'from': start, 'to': end},
                    [f'基频统计（{start:.3f}–{end:.3f} 秒）：平均 {mean:.3f} Hz，最低 200.000 Hz，'
                     f'最高 240.000 Hz，最高点出现在 0.600 秒'], CONTEXT)


class AnnotationNoteTests(unittest.TestCase):
    def state(self, *items, goal='分析录音语调'):
        return AnalysisState(goal, CONTEXT, evidence=list(items))

    def test_repeated_annotations_collapse_into_one_trailing_sentence(self):
        """三句都缺走向证据：正文只在末尾说一次，不再逐行插括号。"""

        state = self.state(pitch())
        result = validate_report(state, report(
            '这句的音调先升后降，判断是平板型。\n'
            '那句的音调明显下降，所以不是尾高型。\n'
            '整体语调很平淡，缺乏起伏。'))
        self.assertEqual(result.analysis.count('缺少同目标的走向证据'), 1)
        self.assertTrue(result.analysis.rstrip().endswith('）'))
        # 每句各自记了账（数量仍是 3），只是正文不再重复
        self.assertEqual(state.metrics['guard_repairs']['report.prosody_unverified'], 3)

    def test_single_scope_annotation_still_carries_the_real_range(self):
        state = self.state(pitch(240, .6, .9))
        result = validate_report(state, report('全段平均基频为 240.000 Hz。[E1]'))
        self.assertIn('局部分段测量', result.analysis)
        self.assertIn('0.6–0.9 秒', result.analysis)


class FormulaLayoutTests(unittest.TestCase):
    def test_model_written_formula_is_collapsed(self):
        """模型自己写的算式不该进交付正文（数值核对不变，只收排版）。"""

        from praat_ai.report_guards import _collapse_formulas

        out = _collapse_formulas('最高值是 (240.000 Hz)/(200.000 Hz)*100 = 109.09 的比值。')
        self.assertNotIn('(240.000 Hz)/', out)
        self.assertIn('109.09', out)
        # 普通句子与带等号的范围都不动
        for plain in ('基频 94.159 Hz，范围 77.014–119.243 Hz。', '时长 0.892 秒 = 892 毫秒'):
            self.assertEqual(_collapse_formulas(plain), plain)

    def test_program_rendered_calc_keeps_its_transparent_formula(self):
        """程序渲染的推导（算式 = 结果）是有意展示的，不能被一起收拾掉。"""

        state = AnalysisState('分析录音语调', CONTEXT, evidence=[pitch()])
        result = validate_report(state, report('最高与最低的比值是 {{calc:E1.q5/E1.q4*100|%}}。'))
        self.assertIn('*100 = ', result.analysis)
        self.assertNotIn('report.formula_collapsed', state.metrics.get('guard_repairs') or {})


class CitationFormatTests(unittest.TestCase):
    def test_merged_citations_are_told_apart(self):
        """`[E7, E8]` 我们认不出来：指令里写明连写形式，省掉一次补引证。"""

        text = report_guard_instructions(AnalysisState('分析录音语调', CONTEXT, evidence=[pitch()]))
        self.assertIn('[E1][E2]（不要写成[E1, E2]）', text)

    def test_no_reference_note_in_the_chat_window(self):
        """AI 对话窗口里的纠音就是奔着世界知识来的：不要先让模型声明「没有对照音」。"""

        text = report_guard_instructions(AnalysisState('这是日语语音あなた，与标准音对比，指出发音不足',
                                                       CONTEXT, evidence=[pitch()]))
        self.assertNotIn('没有对照', text)


class PitchTimeLimitTests(unittest.TestCase):
    def test_schema_states_the_limit(self):
        self.assertIn('一次最多 8 个时刻', _times_arg('秒')['description'])

    def test_error_says_how_many_were_given(self):
        with self.assertRaises(ToolError) as caught:
            _query_times({'time': '0,0.1,0.2,0.3,0.4,0.5,0.6,0.7,0.8'}, fallback='0.5')
        message = str(caught.exception)
        self.assertIn('收到 9 个', message)
        self.assertIn('拆成多次调用', message)

    def test_tool_schema_carries_the_limit(self):
        schemas = {item['function']['name']: item for item in tools.tool_schemas()}
        pitch = schemas['pitch']['function']['parameters']['properties']['time']
        self.assertIn('一次最多 8 个时刻', pitch['description'])


if __name__ == '__main__':
    unittest.main()
