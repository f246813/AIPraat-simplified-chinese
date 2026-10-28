"""Reviewed-topic contradictions have bounded correction, not silent prompt accumulation."""
import threading
import unittest

from praat_ai import answer_contract
from praat_ai.cloud_agent import run_dialogue_turn
from praat_ai.escape_policy import AnalysisState
import test_dialogue_protocol as protocol
from test_dialogue_stream import config

GOAL = '日语 /u/ 和 /ɯ/ 有什么区别？请用知识讲解，不测录音。'
BAD = '在清辅音环境中（如すき、はし的末元音），[ɯ]易发生清化。'
GOOD = ('日语「う」通常记作/u/，实际音值可记作[ɯ̟ᵝ]，唇部可以轻度压缩而不明显前突。'
        '「はし」的末元音是/i/，不能用作/u/的例词；可用「すき」讨论/u/在清辅音间的清化。')


class KnowledgeBoundaryTests(unittest.TestCase):
    def dialogue(self, bodies, budget=8):
        state = AnalysisState(GOAL, '')
        state.budget.request_limit = budget
        chunks = []
        with protocol.endpoint(bodies=bodies, chunk_delay=0) as (base, requests, _), \
                protocol.DialogueProtocolTests().redirect(base):
            run_dialogue_turn(config(), state, kind='concept', cancel=threading.Event(), on_text=chunks.append)
        return state, chunks, requests

    def test_known_conflict_is_detected_without_rejecting_correct_correction(self):
        self.assertTrue(answer_contract.knowledge_issues(GOAL, BAD))
        self.assertFalse(answer_contract.knowledge_issues(GOAL, GOOD))
        self.assertFalse(answer_contract.knowledge_issues('英语/i/怎么练', BAD))
        self.assertFalse(answer_contract.knowledge_issues(GOAL, '「かた」不含/u/，不要拿它说明/u/的音值。'))

    def test_factual_correction_is_once_and_bad_sentence_is_not_streamed(self):
        state, chunks, requests = self.dialogue([BAD, GOOD])
        self.assertEqual(len(requests), 2)
        self.assertEqual(state.report, GOOD)
        self.assertEqual(''.join(chunks), GOOD)
        self.assertEqual(state.metrics['guard_blocks']['dialogue.knowledge_conflict'], 1)
        self.assertEqual(state.status, 'complete')

    def test_repeat_conflict_uses_reviewed_reference_without_more_requests(self):
        state, chunks, requests = self.dialogue([BAD, BAD])
        self.assertEqual(len(requests), 2)
        self.assertEqual(state.report, answer_contract.knowledge_fallback(GOAL))
        self.assertEqual(state.metrics['knowledge_delivery'], 'reviewed_reference')
        self.assertEqual(state.status, 'complete')
        self.assertNotIn(BAD, ''.join(chunks))
        self.assertNotIn('按照要求', state.report)
        self.assertFalse(answer_contract.knowledge_issues(GOAL, state.report))

    def test_exhausted_budget_uses_reference_without_an_extra_request(self):
        state, chunks, requests = self.dialogue([BAD], budget=1)
        self.assertEqual(len(requests), 1)
        self.assertEqual(state.requests, 1)
        self.assertEqual(state.metrics['knowledge_delivery'], 'reviewed_reference')
        self.assertNotIn(BAD, ''.join(chunks))

    def test_unverified_articulator_distance_is_not_a_phonetic_standard(self):
        self.assertTrue(answer_contract.knowledge_issues(GOAL, '日语/u/的舌位比普通话前移约 1–2 cm。'))
        self.assertTrue(answer_contract.knowledge_issues(GOAL, '上下唇距离应保持 2 毫米。'))
        self.assertFalse(answer_contract.knowledge_issues(GOAL, '不要把舌位描述为固定前移 1 cm。'))
        self.assertFalse(answer_contract.knowledge_issues(GOAL, '先放松唇部，重复练习 5 次。'))

    def test_source_filter_conflicts_and_correct_explanation(self):
        goal = '基频和共振峰有什么区别？'
        self.assertTrue(answer_contract.knowledge_issues(goal, '基频是其中频率最低的一条谐波。'))
        self.assertTrue(answer_contract.knowledge_issues(goal, 'F0是一条位于谐波上方的追踪线。'))
        good = '基频是周期声源的重复频率，不是最低可见谐波；共振峰是声道的共振。'
        self.assertFalse(answer_contract.knowledge_issues(goal, good))
        fallback = answer_contract.knowledge_fallback(goal)
        self.assertIn('声道', fallback)
        self.assertNotIn('日语', fallback)
        self.assertFalse(answer_contract.knowledge_issues(goal, fallback))


if __name__ == '__main__':
    unittest.main()
