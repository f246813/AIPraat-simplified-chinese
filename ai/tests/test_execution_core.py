"""S5：两条执行路径共用的核心（投递四态 / 账本记录 / 证据折法）。"""
import unittest

from praat_ai import delivery, execution_core
from praat_ai.chat import AgentStep

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n'


def step(tool='pitch', ok=True, rows=('基频 = 220 Hz',), script='selectObject: 1',
         arguments=None, observation='ok'):
    return AgentStep(0, tool, dict(arguments or {'object': 1}), ok, observation,
                     script=script, results=list(rows))


class DeliveryFactTests(unittest.TestCase):
    def test_success_is_delivered(self):
        self.assertEqual(execution_core.delivery_fact(ok=True, facts=[], note=''), delivery.DELIVERED)

    def test_declared_not_delivered_is_kept(self):
        self.assertEqual(execution_core.delivery_fact(
            ok=False, facts=[delivery.NOT_DELIVERED], note='blocked'), delivery.NOT_DELIVERED)

    def test_failure_without_a_declared_fact_is_unknown(self):
        self.assertEqual(execution_core.delivery_fact(ok=False, facts=[], note='timeout'),
                         delivery.EXECUTION_UNKNOWN)


class AttemptRecordTests(unittest.TestCase):
    def test_status_mapping(self):
        cases = ((True, delivery.DELIVERED, True, 'success'),
                 (False, delivery.EXECUTION_UNKNOWN, True, 'unknown'),
                 (False, delivery.NOT_DELIVERED, True, 'not_executed'),
                 (False, delivery.EXECUTION_BLOCKED, True, 'not_executed'),
                 (False, delivery.DELIVERED, False, 'not_executed'),
                 (False, delivery.DELIVERED, True, 'failed'))
        for ok, execution, has_script, expected in cases:
            with self.subTest(ok=ok, execution=execution, has_script=has_script):
                self.assertEqual(execution_core.attempt_status(
                    ok=ok, execution=execution, has_script=has_script), expected)

    def test_record_carries_the_fields_both_paths_need(self):
        record = execution_core.attempt_record(step(observation='工具 pitch 执行成功'),
                                               execution=delivery.DELIVERED)
        for key in ('tool', 'arguments', 'status', 'execution', 'reason', 'script', 'rows'):
            self.assertIn(key, record)
        self.assertEqual(record['status'], 'success')
        self.assertEqual(record['rows'], ['基频 = 220 Hz'])

    def test_guard_and_signature_are_optional(self):
        record = execution_core.attempt_record(step(), execution=delivery.DELIVERED,
                                               guard_key='k1', proposal_signature='s1')
        self.assertEqual(record['guard_key'], 'k1')
        self.assertEqual(record['proposal_signature'], 's1')
        self.assertNotIn('guard_key', execution_core.attempt_record(
            step(), execution=delivery.DELIVERED))


class EvidenceTests(unittest.TestCase):
    def test_failed_and_empty_steps_are_not_evidence(self):
        state = execution_core.evidence_from_steps(
            CONTEXT, [step(), step(ok=False), step(rows=())])
        self.assertEqual(len(state.evidence), 1)
        self.assertEqual(state.evidence[0].kind, 'measurement')

    def test_mutating_tools_are_recorded_as_operations(self):
        from praat_ai import tools
        state = execution_core.evidence_from_steps(
            CONTEXT, [step(tool='rename_object'), step(tool='pitch')],
            is_mutating=tools.is_mutating)
        kinds = {item.tool: item.kind for item in state.evidence}
        self.assertEqual(kinds.get('pitch'), 'measurement')
        if 'rename_object' in kinds:
            self.assertEqual(kinds['rename_object'], 'operation')

    def test_duplicate_measurements_are_not_recorded_twice(self):
        state = execution_core.evidence_from_steps(CONTEXT, [step(), step()])
        self.assertEqual(len(state.evidence), 1)

    def test_valid_evidence_requires_results(self):
        self.assertTrue(execution_core.valid_evidence(step(), mutating=False))
        self.assertFalse(execution_core.valid_evidence(step(ok=False), mutating=False))
        # 空结果行不算证据，操作类也一样（云端与本机原本就是同一口径）
        self.assertFalse(execution_core.valid_evidence(step(rows=()), mutating=False))
        self.assertFalse(execution_core.valid_evidence(step(rows=()), mutating=True))


if __name__ == '__main__':
    unittest.main()
