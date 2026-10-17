"""S3：路由判定（正则 + 模型分类，默认完整分析）。"""
import json
import unittest
from types import SimpleNamespace

from praat_ai import router
from praat_ai.cloud_agent import entry_mode
from praat_ai.dialogue_policy import dialogue_kind
from praat_ai.workflow_policy import WorkflowPolicy


def config(mode='off'):
    return SimpleNamespace(api=SimpleNamespace(base_url='https://test.invalid/v1', model='m'),
                           workflow=WorkflowPolicy(router_mode=mode))


def responder(payload=None, *, error=None, calls=None):
    def call(config, messages):
        if calls is not None:
            calls.append(messages)
        if error is not None:
            raise error
        return json.dumps(payload, ensure_ascii=False)
    return call


class RouterTests(unittest.TestCase):
    def setUp(self):
        router.clear_cache()

    def test_off_mode_keeps_regex_behaviour_without_a_model_call(self):
        calls = []
        for goal in ('你好', '请说明什么是基频共振峰', '分析当前声音的音调走向'):
            with self.subTest(goal=goal):
                intent = router.classify(config('off'), goal,
                                         requester=responder({'intent': 'chat', 'confidence': 1}, calls=calls))
                self.assertEqual(intent.dialogue, dialogue_kind(goal))
                self.assertEqual(intent.source, 'regex' if intent.dialogue else 'default')
        self.assertEqual(calls, [])

    def test_single_measurement_contract_is_detected_without_a_model_call(self):
        calls = []
        intent = router.classify(config('auto'), '测量当前声音平均基频',
                                 requester=responder(error=AssertionError('不该调用模型'), calls=calls))
        self.assertEqual(intent.intent, 'measure')
        self.assertEqual(intent.source, 'regex')
        self.assertEqual(calls, [])

    def test_auto_mode_asks_the_model_when_regex_has_no_answer(self):
        intent = router.classify(config('auto'), '帮我看看这段录音怎么样',
                                 requester=responder({'intent': 'concept', 'confidence': 0.9}))
        self.assertEqual(intent.intent, 'concept')
        self.assertEqual(intent.dialogue, 'concept')
        self.assertEqual(intent.source, 'model')

    def test_low_confidence_falls_back_to_full_analysis(self):
        intent = router.classify(config('auto'), '帮我看看这段录音怎么样',
                                 requester=responder({'intent': 'chat', 'confidence': 0.2}))
        self.assertEqual(intent.intent, 'chat')
        self.assertIsNone(intent.dialogue)          # 拿不准就走完整分析
        self.assertTrue(intent.analyze)

    def test_model_failure_falls_back_to_analysis_with_reason(self):
        state = SimpleNamespace(metrics={})
        intent = router.classify(config('auto'), '帮我看看这段录音怎么样', state=state,
                                 requester=responder(error=RuntimeError('offline')))
        self.assertEqual(intent.source, 'fallback')
        self.assertIsNone(intent.dialogue)
        self.assertEqual(state.metrics['router']['source'], 'fallback')
        self.assertIn('offline', state.metrics['router']['error'])

    def test_malformed_reply_is_not_trusted(self):
        state = SimpleNamespace(metrics={})
        for raw in ('好的，我认为这是概念问题', '{"intent":"nonsense","confidence":1}'):
            with self.subTest(raw=raw):
                router.clear_cache()
                intent = router.classify(config('auto'), '帮我看看这段录音怎么样', state=state,
                                         requester=lambda *_: raw)
                self.assertEqual(intent.source, 'fallback')
                self.assertIsNone(intent.dialogue)

    def test_cache_avoids_a_second_call_for_the_same_goal(self):
        calls = []
        payload = {'intent': 'concept', 'confidence': 0.9}
        first = router.classify(config('auto'), '什么是基频', requester=responder(payload, calls=calls))
        second = router.classify(config('auto'), '什么是基频', requester=responder(payload, calls=calls))
        self.assertEqual(first.source, 'model')
        self.assertEqual(second.source, 'cache')
        self.assertEqual(second.dialogue, 'concept')
        self.assertEqual(len(calls), 1)

    def test_entry_mode_honours_the_router_decision(self):
        context = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n'
        # 正则看不出来，但路由说是概念问题 → L4（不跑工具）
        self.assertEqual(entry_mode('帮我看看这段录音怎么样', context), 'L0')
        self.assertEqual(entry_mode('帮我看看这段录音怎么样', context, dialogue='concept'), 'L4')
        self.assertEqual(entry_mode('帮我看看这段录音怎么样', context, dialogue='advice'), 'L4')


if __name__ == '__main__':
    unittest.main()
