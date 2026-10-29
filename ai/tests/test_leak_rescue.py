"""句子级救援：只摘掉违规句子，其余正文照旧交付。"""
import unittest
from praat_ai import delivery_text, leak_rescue, report_guards
from praat_ai.cloud_agent import rescued_report


CLEAN_ONE = '这段录音的平均基频是 220.000 Hz，属于成年说话人的常见范围。'
LEAKY = '按照要求我不得随便猜定论，最后把参数填好提交给用户。'
CLEAN_TWO = '建议用同一段音频继续练习，观察数值是否稳定。'


class StripTests(unittest.TestCase):
    def test_only_the_leaking_sentence_is_removed(self):
        cleaned, removed = leak_rescue.strip_leaking_sentences(CLEAN_ONE + LEAKY + CLEAN_TWO)
        self.assertEqual(removed, 1)
        self.assertIn('220.000 Hz', cleaned)
        self.assertIn('继续练习', cleaned)
        self.assertNotIn('按照要求', cleaned)
        self.assertFalse(delivery_text.has_answer_leak(cleaned))

    def test_all_leaking_text_is_not_delivered_as_a_fragment(self):
        cleaned, removed = leak_rescue.strip_leaking_sentences(LEAKY + '内部阶段 L2 已给出结论。')
        self.assertEqual(cleaned, '')
        self.assertEqual(removed, 2)

    def test_placeholder_only_text_is_kept_for_the_caller_to_replace(self):
        draft = CLEAN_ONE + '推导见 {{calc:220-200}}。'
        cleaned, removed = leak_rescue.strip_leaking_sentences(draft)
        self.assertEqual(removed, 0)
        self.assertIn('{{calc:220-200}}', cleaned)

    def test_too_short_remainder_falls_back_to_the_stage_record(self):
        cleaned, _ = leak_rescue.strip_leaking_sentences(LEAKY + '好的。')
        self.assertEqual(cleaned, '')

    def test_rescue_analysis_marks_what_it_dropped(self):
        cleaned, removed = leak_rescue.rescue_analysis(CLEAN_ONE + LEAKY)
        self.assertEqual(removed, 1)
        self.assertIn(leak_rescue.OMITTED_SENTENCES_NOTE, cleaned)
        self.assertFalse(delivery_text.has_answer_leak(cleaned))


class RescuedReportTests(unittest.TestCase):
    def test_mixed_draft_keeps_the_useful_part(self):
        text = rescued_report(CLEAN_ONE + LEAKY + CLEAN_TWO, '')
        self.assertIn('220.000 Hz', text)
        self.assertIn('继续练习', text)
        self.assertNotIn('按照要求', text)
        self.assertIn(leak_rescue.OMITTED_SENTENCES_NOTE, text)
        self.assertIn(report_guards.UNVERIFIED_NOTE, text)

    def test_fully_rejected_draft_still_falls_back(self):
        self.assertEqual(rescued_report(LEAKY + '内部阶段 L2 已给出结论。', ''), '')

    def test_clean_draft_is_untouched_but_still_annotated(self):
        text = rescued_report(CLEAN_ONE, '')
        self.assertIn('220.000 Hz', text)
        self.assertNotIn(leak_rescue.OMITTED_SENTENCES_NOTE, text)


if __name__ == '__main__':
    unittest.main()
