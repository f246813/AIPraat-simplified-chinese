"""RPC 健壮性回归：轮询封顶、取消幂等、缺参数、重启清扫、submit 不占着 guard。

对应 2026-10-07 扩展排查里的 RPC 层问题：一次 ``events.poll`` 可以序列化约 2 MB；
重复取消会重复发事件；缺参数把裸 ``KeyError`` 抛给前端；重启清扫只处理 ``running``；
``submit`` 持全局 guard 跨 Praat ping（25 秒上限），期间前端轮询一起卡住。
"""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path

from praat_ai import modern_app as app_module
from praat_ai.modern_app import ModernApplication


def config_file(directory):
    path = Path(directory) / 'config.json'
    path.write_text(json.dumps({'api': {'enabled': True, 'stop_local_service': False,
                                        'base_url': 'http://127.0.0.1:1/v1', 'model': 'fixture',
                                        'max_context_tokens': 32768, 'token_mode': 'manual'}}),
                    encoding='utf8')
    return path


class SlowCaptureExecutor:
    """capture_target 阻塞，直到测试放行——模拟 Praat 忙时最长 25 秒的等待。"""

    def __init__(self):
        self.release = threading.Event()
        self.entered = threading.Event()
        self.runs = []

    def capture_target(self, text):
        self.entered.set()
        self.release.wait(10)
        return ''

    def capture_workflow_target(self, text, workflow):
        return self.capture_target(text)

    def run(self, **kwargs):
        self.runs.append(kwargs)
        return dict(content='完成', formal=True, status='complete')


class QuotedExecutor:
    def capture_target(self, text):
        return ''

    def run(self, **kwargs):
        return dict(content='完成', formal=True, status='complete')


class RpcRobustnessTests(unittest.TestCase):
    def make_app(self, executor):
        self._directory = tempfile.TemporaryDirectory()
        # 注意注册顺序：cleanup 后进先出，先注册目录清理、再注册 app.close，
        # 退出时才会先关应用再删临时目录（否则库里写不进去）。
        self.addCleanup(self._directory.cleanup)
        app = ModernApplication(Path(self._directory.name), config_file(self._directory.name),
                                executor=executor)
        self.addCleanup(app.close)
        return app

    def test_poll_payload_is_capped_and_cursor_advances(self):
        app = self.make_app(QuotedExecutor())
        session = app.store.new_session()['id']
        task = dict(id='t1', sessionId=session)
        for index in range(app_module.POLL_EVENT_LIMIT + 50):
            app.event(task, 'delta', {'text': f'#{index}'})
        first = app.poll(0)
        self.assertEqual(len(first['events']), app_module.POLL_EVENT_LIMIT)
        self.assertTrue(first['reset'])
        self.assertEqual(first['cursor'], first['events'][-1]['seq'])
        second = app.poll(first['cursor'])
        self.assertEqual(len(second['events']), 50)
        self.assertEqual(second['events'][-1]['seq'], app.cursor)
        # 游标跟着最后一条返回的事件走，两次合起来正好是全部事件。
        self.assertEqual(len(first['events']) + len(second['events']), app_module.POLL_EVENT_LIMIT + 50)

    def test_cancel_is_idempotent_in_the_event_stream(self):
        app = self.make_app(QuotedExecutor())
        session = app.store.new_session()['id']
        task = dict(id='t1', sessionId=session, status='running', cancel=threading.Event(),
                    message=dict(id='m1', role='assistant', content='', status='running', taskId='t1'))
        app.tasks['t1'] = task
        app.cancel('t1')
        app.cancel('t1')
        app.cancel('t1')
        cancelling = [e for e in app.events if e['type'] == 'task'
                      and e['payload'].get('status') == 'cancelling']
        self.assertEqual(len(cancelling), 1)

    def test_missing_arguments_report_the_parameter_name(self):
        app = self.make_app(QuotedExecutor())
        for method, params, expected in (('sessions.get', {}, 'sessionId'),
                                         ('tasks.cancel', {}, 'taskId'),
                                         ('sessions.rename', {'sessionId': 'x'}, 'title')):
            with self.subTest(method=method):
                with self.assertRaises(ValueError) as caught:
                    app.rpc(method, params)
                message = str(caught.exception)
                self.assertIn(expected, message)
                self.assertNotIn("KeyError", message)
                self.assertNotIn('Traceback', message)

    def test_unknown_attachment_does_not_leak_a_host_path(self):
        app = self.make_app(QuotedExecutor())
        with self.assertRaises(ValueError) as caught:
            app.rpc('attachments.preview', {'attachmentId': '0' * 32})
        self.assertNotIn(self._directory.name, str(caught.exception))

    def test_restart_converts_every_unfinished_status(self):
        app = self.make_app(QuotedExecutor())
        session = app.store.new_session()['id']
        for status in ('running', 'queued', 'cancelling'):
            app.store.put_message(session, dict(id='m-' + status, role='assistant', content='x',
                                                status=status, taskId='t-' + status))
        app.close()
        again = ModernApplication(Path(self._directory.name), config_file(self._directory.name),
                                  executor=QuotedExecutor())
        self.addCleanup(again.close)
        messages = {m['id']: m for m in again.store.get(session)['messages']}
        for status in ('running', 'queued', 'cancelling'):
            self.assertEqual(messages['m-' + status]['status'], 'interrupted')

    def test_submit_does_not_hold_the_guard_while_waiting_for_praat(self):
        executor = SlowCaptureExecutor()
        app = self.make_app(executor)
        session = app.store.new_session()['id']
        result = {}

        def submit():
            try:
                result['task'] = app.submit(session, '测量当前声音平均基频')
            except Exception as error:      # noqa: BLE001 - 测试要把失败也报出来
                result['error'] = error

        thread = threading.Thread(target=submit)
        thread.start()
        self.assertTrue(executor.entered.wait(5), 'capture_target 没有被调用')
        started = time.monotonic()
        app.rpc('events.poll', {'after': 0})     # 前端 220ms 心跳
        elapsed = time.monotonic() - started
        executor.release.set()
        thread.join(10)
        self.assertLess(elapsed, 1.0, 'Praat 目标捕获期间 events.poll 被挡住了')
        self.assertNotIn('error', result)


    def test_dispatcher_poison_is_cleared_by_a_verified_ping(self):
        """投递状态不明之后，一次成功的核对 ping 就该放行——不必重启桌面。

        2026-10-07 实测：Praat 被模态窗口挡住导致一次 25 秒超时，之后所有测量都报
        「blocked until desktop restart」，整批验证只能中断。
        """
        from unittest.mock import patch
        from praat_ai import chat as chat_module
        from praat_ai import modern_execution

        with patch.object(modern_execution, '_send', return_value=(True, [], '', 'delivered')), \
             patch.object(chat_module, 'object_context',
                          return_value='id\tclass\tname\tselected\n1\tSound\ttone\t1\n'):
            modern_execution._POISON = '完成未确认'
            self.addCleanup(setattr, modern_execution, '_POISON', '')
            with self.assertRaises(modern_execution.BranchExit):
                modern_execution._gate(threading.Event())
            text = modern_execution._refresh('Praat.exe', 1, threading.Event())
            self.assertIn('Sound', text)
            self.assertFalse(modern_execution._POISON)
            modern_execution._gate(threading.Event())     # 不再抛


    def test_oversized_evidence_is_bounded(self):
        """失控输出（几万行）不该把整份证据写进库、再整份回读。

        2026-10-07 复查：一条 4 MB 的 evidence 记录在持 self.guard 时写库并回读。
        """

        app = self.make_app(QuotedExecutor())
        session = app.store.new_session()['id']
        huge = {'attempts': [{'tool': 'custom_script', 'rows': ['x' * 200] * 20000}],
                'evidence': [{'tool': 'custom_script', 'rows': ['y' * 200] * 20000}]}
        self.assertGreater(len(app.store.encode(huge)), app.store.EVIDENCE_PAYLOAD_LIMIT)
        app.store.save_evidence(session, 'task-1', huge)
        stored = app.store.task_evidence(session, 'task-1')
        self.assertTrue(stored.get('truncated'))
        self.assertIn('过长输出已截断', stored['attempts'][0]['rows'][0])
        self.assertIn('过长输出已截断', stored['evidence'][0]['rows'][0])
        self.assertLess(len(app.store.encode(stored)), app.store.EVIDENCE_PAYLOAD_LIMIT)


if __name__ == '__main__':
    unittest.main()
