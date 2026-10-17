"""S4：交付文案不出现内部术语，原因分类可查。"""
import unittest

from praat_ai import delivery_text
from praat_ai.escape_policy import AnalysisState, Evidence
from praat_ai.workflow_policy import finish_locally

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n'


def state(**fields):
    current = AnalysisState('解释录音语调', CONTEXT,
                            evidence=[Evidence('pitch', {'object': 1}, ['基频 = 220 Hz'], CONTEXT)])
    current.measurement_content = '基频 = 220 Hz'
    for key, value in fields.items():
        setattr(current, key, value)
    return current


class DeliveryTextTests(unittest.TestCase):
    def test_internal_reasons_map_to_one_of_six_classes(self):
        cases = {
            'L2 阶段时间上限已用完': 'budget',
            '本轮模型请求总预算已用完': 'budget',
            '工作流程未启用 L2': 'policy',
            '当前安装目录缺少 Praat.exe。': 'capability',
            '连续两轮没有新增有效证据，停止当前执行分支': 'budget',
            '和模型服务的连接超时': 'transient',
            '已取消本次操作': 'cancelled',
            '整段请求不能使用旧选区或未经核实的局部范围': 'user_fixable',
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(delivery_text.classify(reason), expected)

    def test_every_user_message_is_free_of_internal_terms(self):
        for kind, message in delivery_text.MESSAGES.items():
            with self.subTest(kind=kind):
                self.assertTrue(message)
                self.assertFalse(delivery_text.has_internal_terms(message))

    def test_finish_locally_writes_a_human_reason_and_keeps_the_detail(self):
        current = state(reason='L2 阶段时间上限已用完')
        finish_locally(current)
        self.assertIn('基频 = 220 Hz', current.report)
        self.assertIn('尚未完成：', current.report)
        self.assertFalse(delivery_text.has_internal_terms(current.report))
        # 内部原文只进 metrics，供排查
        self.assertEqual(current.metrics['reason_detail'], 'L2 阶段时间上限已用完')
        self.assertEqual(current.metrics['reason_class'], 'budget')
        self.assertEqual(current.metrics['finish_state'], 'partial_with_text')

    def test_finish_locally_keeps_a_written_body_instead_of_measurements(self):
        current = state(report='这段录音的平均基频偏低，说明发声较放松。',
                        reason='L2 阶段时间上限已用完')
        finish_locally(current)
        self.assertIn('这段录音的平均基频偏低', current.report)
        self.assertNotIn(current.measurement_content, current.report)
        self.assertFalse(delivery_text.has_internal_terms(current.report))

    def test_coverage_explanation_is_user_facing(self):
        current = state(reason='L2 阶段时间上限已用完')
        finish_locally(current)
        for item in current.coverage:
            self.assertFalse(delivery_text.has_internal_terms(item['explanation']))


if __name__ == '__main__':
    unittest.main()
