"""router_mode=auto 的模型分类必须进**同一份**任务预算与 metrics（离线）。

修复前的问题（见 scout 证据）：``modern_execution`` 在路由前只把 ``state.requests``
对齐一次；``router.classify`` 调 ``modern_budget.text_request`` 时不传 state，
``text_request`` 自己建 ``Agent.run``，于是这次真实云端请求

* 不检查 ``state.budget.request_limit``（预算用完了还照发）；
* 不记 ``state.requests``（后续 ``CloudSession`` 的 request_limit 少一次）；
* 不写 ``state.metrics['request_timings']``（tokens/耗时在任务账本里消失）。

这里用真 ``CloudRuntime`` + ``FunctionModel`` 跑同一条 invoker，不联网、不发 HTTP。
"""

import threading
import unittest

from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from praat_ai import modern_budget, router
from praat_ai.cloud_agent import CloudSession
from praat_ai.cloud_runtime import CloudRuntime
from praat_ai.config import AppConfig
from praat_ai.escape_policy import AnalysisState, Budget
from praat_ai.workflow_policy import WorkflowPolicy

#: 正则给不出答案的中性输入，才会真的走模型分类（见 dialogue_policy）。
GOAL = '帮我看看这段材料怎么样'
GOAL_OTHER = '嗯，这个情况该怎么处理'
GOAL_THIRD = '这段材料需要注意什么'

CONTEXT = 'id\tclass\tname\tselected\n1\tSound\ttone\t1\n'
CLASSIFICATION = '{"intent":"concept","confidence":0.9,"needs_audio":false}'


def config(router_mode='auto'):
    cfg = AppConfig()
    cfg.api.enabled = True
    cfg.api.base_url, cfg.api.model = 'https://test.invalid/v1', 'fixture'
    cfg.api.thinking_level = 'off'
    cfg.api.force_deep_thinking = False
    cfg.api.audio_input_enabled = False
    cfg.workflow = WorkflowPolicy(router_mode=router_mode)
    return cfg


def state(goal=GOAL, *, requests=0, request_limit=12):
    value = AnalysisState(goal, CONTEXT, budget=Budget(request_limit=request_limit))
    value.requests = requests
    return value


def messages(text='帮我看看'):
    return [dict(role='system', content='sys'), dict(role='user', content=text)]


class Fixture:
    """一次真 CloudRuntime + FunctionModel：共用与产品同一条 invoker，不发 HTTP。"""

    def __init__(self, reply=CLASSIFICATION, fail=None):
        self.calls = []
        self.reply = reply
        self.fail = fail
        self.model = FunctionModel(self.respond)
        self.runtime = CloudRuntime(model_factory=lambda _config: self.model)

    def respond(self, _messages, _info):
        self.calls.append(_messages)
        if self.fail is not None:
            raise self.fail
        return ModelResponse(parts=[TextPart(self.reply)])

    def close(self):
        self.runtime.close(wait=True)


class RouterBudgetTests(unittest.TestCase):
    def setUp(self):
        router.clear_cache()
        self.addCleanup(router.clear_cache)

    def fixture(self, **kwargs):
        fixture = Fixture(**kwargs)
        self.addCleanup(fixture.close)
        return fixture

    # -- 计数 ---------------------------------------------------------------
    def test_auto_route_counts_one_real_request(self):
        fixture = self.fixture()
        current = state()
        result = router.classify(config(), GOAL, context_text='', runtime=fixture.runtime,
                                 cancel=threading.Event(), state=current)

        self.assertEqual(result.source, 'model')
        self.assertEqual(result.intent, 'concept')
        self.assertEqual(result.dialogue, 'concept')
        self.assertEqual(len(fixture.calls), 1, '一次分类只能发一次模型请求')
        self.assertEqual(current.requests, 1, '分类请求必须记进任务请求计数')
        timings = current.metrics['request_timings']
        self.assertEqual(len(timings), 1)
        self.assertEqual(timings[0]['phase'], 'router')
        self.assertEqual(timings[0]['request'], 1)
        self.assertEqual(timings[0]['status'], 'complete')
        self.assertIn('elapsed_seconds', timings[0])
        self.assertEqual(current.metrics['router']['source'], 'model')

    def test_used_request_token_counts_are_shared_with_metrics(self):
        fixture = self.fixture()
        current = state()
        router.classify(config(), GOAL_THIRD, context_text='', runtime=fixture.runtime,
                        cancel=threading.Event(), state=current)
        timing = current.metrics['request_timings'][0]
        # 用量只用于共享，具体数字由 provider/SDK 决定；但字段必须是数字而不是缺失。
        for key in ('input_tokens_total', 'output_tokens'):
            self.assertIsInstance(timing.get(key), int, f'{key} 没有共享到 task metrics')
        self.assertEqual(current.requests, len(current.metrics['request_timings']))

    # -- 预算 ---------------------------------------------------------------
    def test_exhausted_budget_sends_nothing(self):
        fixture = self.fixture()
        current = state(requests=1, request_limit=1)
        result = router.classify(config(), GOAL, context_text='', runtime=fixture.runtime,
                                 cancel=threading.Event(), state=current)

        self.assertEqual(fixture.calls, [], '预算已耗尽时不能再发请求')
        self.assertTrue(result.analyze)
        self.assertEqual(result.source, 'fallback')
        self.assertEqual(current.requests, 1, '预算检查失败不能改动已有计数')
        self.assertEqual(current.metrics.get('request_timings', []), [])
        self.assertIn('预算', current.metrics['router']['error'])

    def test_budget_limit_is_not_reset_or_retried(self):
        fixture = self.fixture()
        current = state(request_limit=2)
        for text in (GOAL, GOAL_OTHER, GOAL_THIRD):
            router.classify(config(), text, context_text='', runtime=fixture.runtime,
                            cancel=threading.Event(), state=current)

        self.assertEqual(len(fixture.calls), 2, '预算 2 就只能发 2 次，不重试、不重置')
        self.assertEqual(current.requests, 2)
        self.assertEqual(current.budget.request_limit, 2)
        self.assertEqual(len(current.metrics['request_timings']), 2)

    # -- 失败与取消 ---------------------------------------------------------
    def test_failed_request_still_counts(self):
        fixture = self.fixture(fail=RuntimeError('boom'))
        current = state()
        result = router.classify(config(), GOAL, context_text='', runtime=fixture.runtime,
                                 cancel=threading.Event(), state=current)

        self.assertEqual(len(fixture.calls), 1)
        self.assertEqual(current.requests, 1, '失败也算一次尝试（保守计数）')
        timings = current.metrics['request_timings']
        self.assertEqual(len(timings), 1)
        self.assertEqual(timings[0]['status'], 'failed')
        self.assertTrue(result.analyze, '分类失败必须回落分析')
        self.assertEqual(result.source, 'fallback')
        self.assertTrue(current.metrics['router']['error'])

    def test_cancel_before_request_sends_nothing(self):
        fixture = self.fixture()
        current = state()
        cancel = threading.Event()
        cancel.set()

        with self.assertRaises(RuntimeError):
            modern_budget.text_request(config(), messages(), cancel=cancel,
                                       runtime=fixture.runtime, state=current)
        self.assertEqual(fixture.calls, [], '已取消就不能发请求')
        self.assertEqual(current.requests, 0)
        self.assertEqual(current.metrics.get('request_timings', []), [])

    def test_cancelled_route_falls_back_without_request(self):
        fixture = self.fixture()
        current = state()
        cancel = threading.Event()
        cancel.set()
        result = router.classify(config(), GOAL, context_text='', runtime=fixture.runtime,
                                 cancel=cancel, state=current)
        self.assertEqual(fixture.calls, [])
        self.assertTrue(result.analyze)
        self.assertEqual(current.requests, 0)

    # -- 兼容与去重 ---------------------------------------------------------
    def test_legacy_summary_path_without_state_keeps_working(self):
        fixture = self.fixture(reply='较早对话的摘要')
        metrics = {}
        output = modern_budget.text_request(config(), messages(), cancel=threading.Event(),
                                           runtime=fixture.runtime, metrics=metrics,
                                           phase='summary')

        self.assertEqual(output, '较早对话的摘要')
        self.assertEqual(len(fixture.calls), 1)
        timings = metrics['request_timings']
        self.assertEqual(len(timings), 1)
        self.assertEqual(timings[0]['phase'], 'summary')
        self.assertEqual(timings[0]['request'], 1)
        self.assertEqual(timings[0]['status'], 'complete')

    def test_same_metrics_dict_is_not_written_twice(self):
        fixture = self.fixture()
        current = state()
        modern_budget.text_request(config(), messages(), cancel=threading.Event(),
                                   runtime=fixture.runtime, state=current,
                                   metrics=current.metrics, phase='router')

        self.assertEqual(len(fixture.calls), 1)
        self.assertEqual(len(current.metrics['request_timings']), 1,
                         '同一个 dict 不能重复记两条 timing')
        self.assertEqual(current.requests, 1, '一次请求只能加一次计数')

    def test_metrics_argument_still_receives_the_timing_with_state(self):
        fixture = self.fixture()
        current = state()
        separate = {}
        modern_budget.text_request(config(), messages(), cancel=threading.Event(),
                                   runtime=fixture.runtime, state=current, metrics=separate,
                                   phase='router')

        self.assertEqual(len(current.metrics['request_timings']), 1)
        self.assertEqual(len(separate['request_timings']), 1)
        self.assertEqual(current.requests, 1)

    # -- 后续阶段接手 -------------------------------------------------------
    def test_cloud_session_receives_request_counts_and_tokens(self):
        current = state()
        current.metrics['request_timings'] = [
            dict(request=1, phase='router', status='complete', retry=False,
                 input_tokens_total=120, output_tokens=8, elapsed_seconds=0.2)]
        current.requests = 1
        session = CloudSession(config(), current, lambda *_args: None, threading.Event(), None,
                               object(), [], None, None, None)

        self.assertEqual(session.usage.requests, current.requests)
        self.assertEqual(session.usage.input_tokens, 120)
        self.assertEqual(session.usage.output_tokens, 8)
        self.assertEqual(session.limits.request_limit, current.budget.request_limit)

    def test_cloud_session_after_real_router_call_sums_the_same_tokens(self):
        fixture = self.fixture()
        current = state()
        router.classify(config(), GOAL, context_text='', runtime=fixture.runtime,
                        cancel=threading.Event(), state=current)
        session = CloudSession(config(), current, lambda *_args: None, threading.Event(), None,
                               object(), [], None, None, None)
        timings = current.metrics['request_timings']
        self.assertEqual(session.usage.requests, current.requests)
        self.assertEqual(session.usage.input_tokens,
                         sum(item.get('input_tokens_total') or 0 for item in timings))
        self.assertEqual(session.usage.output_tokens,
                         sum(item.get('output_tokens') or 0 for item in timings))


if __name__ == '__main__':
    unittest.main()
