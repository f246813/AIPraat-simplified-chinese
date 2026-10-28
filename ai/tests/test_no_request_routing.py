"""无请求输入不进测量阶梯：随手敲的一个词走对话，真实测量请求不受影响。

判定只做保守的通用判断（没有材料引用、领域词、请求动作词的短输入），不针对
某个具体词做白名单或黑名单，所以漏判的代价只是这类输入仍按分析处理。
"""

import unittest

from praat_ai.cloud_agent import entry_mode
from praat_ai.dialogue_policy import dialogue_kind, no_executable_request

CONTEXT = ('id\tclass\tname\tselected\tsel_start\tsel_end\n'
           '1\tSound\tSound あなた\t1\t0.2\t0.4\n')


class NoRequestRoutingTests(unittest.TestCase):
    def test_input_without_any_request_is_dialogue(self):
        for text in ('test', 'asdf', 'asdfghjkl', '你好', '嗯', '？？', 'ok'):
            with self.subTest(text=text):
                self.assertTrue(no_executable_request(text))
                self.assertEqual(dialogue_kind(text), 'conversation')
                self.assertEqual(entry_mode(text, CONTEXT), 'L4')

    def test_capability_question_routes_to_dialogue_not_measurement(self):
        self.assertEqual(dialogue_kind('你都能测哪些参数'), 'conversation')
        self.assertEqual(entry_mode('你都能测哪些参数', CONTEXT), 'L4')

    def test_real_measurement_requests_keep_the_tool_staircase(self):
        for text in ('测一下当前声音时长', '测量这段的 VOT', '测量当前声音的平均基频',
                     '分析一下这个声音', 'what is the pitch of this recording?'):
            with self.subTest(text=text):
                self.assertFalse(no_executable_request(text))
                self.assertIsNone(dialogue_kind(text))
                self.assertEqual(entry_mode(text, CONTEXT), 'L0')

    def test_capability_question_with_a_real_operation_stays_in_the_tools(self):
        for text in ('你能测哪些参数？顺便测量当前声音的平均基频',
                     '你都能测什么？然后分析一下这个声音',
                     '帮我把聊天窗口的字体调大，再测量当前声音的时长'):
            with self.subTest(text=text):
                self.assertIsNone(dialogue_kind(text))
                self.assertEqual(entry_mode(text, CONTEXT), 'L0')

    def test_pure_capability_and_interface_questions_stay_in_dialogue(self):
        for text in ('你都能测哪些参数', '你有哪些功能？', '支持哪些测量',
                     '帮我把聊天窗口的字体调大一点。'):
            with self.subTest(text=text):
                self.assertEqual(dialogue_kind(text), 'conversation')
                self.assertEqual(entry_mode(text, CONTEXT), 'L4')

    def test_capability_phrasing_does_not_hide_a_measurement_request(self):
        """裸「你能测」被当成能力问句剥掉后，后面的真实操作就漏进对话了。"""
        for text in ('你能测当前声音的时长吗', '你能测当前声音的平均基频吗',
                     '你能分析当前声音吗', '你能看当前声音的共振峰吗'):
            with self.subTest(text=text):
                self.assertIsNone(dialogue_kind(text))
                self.assertEqual(entry_mode(text, CONTEXT), 'L0')

    def test_parameter_shorthand_is_a_real_request(self):
        """「F0 多少」这类简写是测量请求，不能因为没有动词就当成闲聊。"""
        for text in ('F0 多少', 'F1 是多少', 'F2 呢', 'HNR 多少', 'CPPS 多少',
                     '斜率多少', '抖动多少', '频谱重心是多少', '谐噪比是多少'):
            with self.subTest(text=text):
                self.assertFalse(no_executable_request(text))
                self.assertIsNone(dialogue_kind(text))
                self.assertEqual(entry_mode(text, CONTEXT), 'L0')

    def test_plain_capability_and_negated_advice_are_not_measurements(self):
        for text in ('你都能测哪些参数', '你能测吗', '你能看什么', '你能分析什么'):
            with self.subTest(text=text):
                self.assertEqual(dialogue_kind(text), 'conversation')
                self.assertEqual(entry_mode(text, CONTEXT), 'L4')
        for text, kind in (('讲日语发音练习方法，不测录音', 'advice'),
                           ('用你自己的知识教我纠音，不测录音', 'advice'),
                           ('你能讲一下 HNR 吗？不测录音', 'concept')):
            with self.subTest(text=text):
                self.assertEqual(dialogue_kind(text), kind)
                self.assertEqual(entry_mode(text, CONTEXT, dialogue=kind), 'L4')

    def test_negated_measurement_statement_does_not_trigger_the_tools(self):
        for text, kind in (('用你自己的知识帮我纠音，讲日语发音练习，不测录音', 'advice'),
                           ('讲日语发音练习方法，不需要测量我的录音', 'advice'),
                           ('解释一下基频是什么，不用测量录音', 'concept'),
                           ('我没有录音，能讲日语发音练习吗', 'advice'),
                           ('practice pronunciation with your own knowledge, '
                            'without measuring the recording', 'advice')):
            with self.subTest(text=text):
                self.assertEqual(dialogue_kind(text), kind)
                self.assertEqual(entry_mode(text, CONTEXT, dialogue=kind), 'L4')

    def test_positive_operation_next_to_a_negated_phrase_still_uses_the_tools(self):
        for text in ('帮我分析当前录音，不用测量别的',
                     '不测录音，但请测量当前声音的平均基频',
                     '先删除对象，再告诉我怎么练习'):
            with self.subTest(text=text):
                self.assertIsNone(dialogue_kind(text))
                self.assertEqual(entry_mode(text, CONTEXT), 'L0')

    def test_material_domain_and_operation_words_are_never_no_request(self):
        for text in ('当前', 'Sound 1', '基频', 'VOT', '测量', '把结果导出', '再做一次',
                     'Explain its average', '这个怎么样？', '你好，删除对象'):
            with self.subTest(text=text):
                self.assertFalse(no_executable_request(text))


if __name__ == '__main__':
    unittest.main()
