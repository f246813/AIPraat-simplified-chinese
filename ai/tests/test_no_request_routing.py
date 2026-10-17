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

    def test_material_domain_and_operation_words_are_never_no_request(self):
        for text in ('当前', 'Sound 1', '基频', 'VOT', '测量', '把结果导出', '再做一次',
                     'Explain its average', '这个怎么样？', '你好，删除对象'):
            with self.subTest(text=text):
                self.assertFalse(no_executable_request(text))


if __name__ == '__main__':
    unittest.main()
