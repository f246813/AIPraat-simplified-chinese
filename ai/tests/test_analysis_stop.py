"""阶段 3：分析阶段的「停止」按钮。

设计要点（也是这里的断言对象）：

① ``progress_popup.MiniProgress`` 只在传了 ``on_stop`` 时才长出停止按钮，点一次只触发一次，
   按钮随即变成不可再点的「正在停止…」；不传回调的小窗与以前完全一样（对话窗口那一路没变）。
② ``analysis_progress.run_with_stop`` 把分析放在 worker 线程、进度经 ``root.after`` 回主线程，
   点停止置位 ``cancel``；**拿不到 GUI 时退回同步执行**（命令行/无人值守行为不变）。
③ ``tutor.run_from_praat`` 把 cancel/progress 贯进去；取消（测量还没完成）如实报「没有报告」，
   退出码 3 而不是把取消当成失败或成功。

这里不用真实的 Tk 事件循环去测 ②（那要靠时序），而是注入一个与真实 Tk 线程模型一致的最小替身；
① 用真实 Tk 走一遍，因为按钮是 UI 契约（拿不到 Tk 就跳过）。
"""

import io
import threading
import time
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from praat_ai.analysis_progress import run_with_stop
from praat_ai.forced_alignment import AlignmentCancelled
from praat_ai.progress_popup import MiniProgress
from praat_ai.tutor import run_from_praat


class FakeRoot:
    """最小 Tk 替身：``after`` 只排队，``mainloop`` 在**主线程**按序执行（与真实 Tk 一致）。"""

    def __init__(self):
        self._queue = []
        self._lock = threading.Lock()
        self._closed = threading.Event()

    def after(self, _delay, callback):
        with self._lock:
            self._queue.append(callback)

    def mainloop(self):
        while True:
            with self._lock:
                pending, self._queue = self._queue, []
            for callback in pending:
                callback()
            if self._closed.is_set() and not self._queue:
                return
            if not pending:
                time.sleep(0.005)

    def quit(self):
        self._closed.set()

    def destroy(self):
        self._closed.set()


class FakeWindow:
    def __init__(self, cancel, *, on_stop=None):
        self.cancel = cancel
        self.on_stop = on_stop
        self.updates = []
        self.completed = []
        self.on_complete = None
        self.closed = False

    def update(self, fraction, message=None):
        self.updates.append((fraction, message))

    def complete(self, message=None):
        self.completed.append(message)
        self.update(1.0, message)
        if self.on_complete is not None:
            self.on_complete()          # 模拟收尾那一瞬间用户又点了一下停止（最坏情况）

    def remaining_minimum_seconds(self):
        return 0.0

    def close(self):
        self.closed = True


class RunWithStopTests(unittest.TestCase):
    def test_headless_falls_back_to_synchronous_execution(self):
        """拿不到 Tk：直接同步跑，report 为 None——命令行行为与加这一层之前一致。"""

        seen = {}

        def work(cancel, report):
            seen["cancel"] = cancel
            seen["report"] = report
            return "done"

        def broken(*_args, **_kwargs):
            raise RuntimeError("no display")

        outputs, cancelled = run_with_stop(work, open_window=broken)

        self.assertEqual(outputs, "done")
        self.assertFalse(cancelled)
        self.assertIsInstance(seen["cancel"], threading.Event)
        self.assertIsNone(seen["report"])

    def test_disabled_never_opens_a_window(self):
        def opener(*_args, **_kwargs):  # pragma: no cover - 不该被调用
            raise AssertionError("--no-gui 不应该建窗口")

        outputs, cancelled = run_with_stop(
            lambda cancel, report: (cancel.is_set(), report), enabled=False, open_window=opener
        )

        self.assertEqual(outputs, (False, None))
        self.assertFalse(cancelled)

    def test_progress_is_marshalled_to_the_main_loop_and_outputs_return(self):
        root, window = FakeRoot(), None
        seen = {}

        def opener(cancel, **_kwargs):
            nonlocal window
            window = FakeWindow(cancel, on_stop=cancel.set)
            seen["cancel"] = cancel
            return root, window

        def work(cancel, report):
            report(0.5, "音素对齐与声学特征提取")
            return "outputs"

        def hooked(work, **kwargs):
            kwargs["open_window"] = opener
            return run_with_stop(work, **kwargs)

        outputs, cancelled = hooked(work)

        self.assertEqual(outputs, "outputs")
        self.assertFalse(cancelled)
        self.assertIn((0.5, "音素对齐与声学特征提取"), window.updates)
        self.assertEqual(window.updates[-1], (1.0, None))  # 收尾时把进度推到 100%
        self.assertTrue(window.closed)
        self.assertFalse(seen["cancel"].is_set())

    def test_clicking_stop_sets_cancel_and_propagates_the_cancellation(self):
        """点「停止」→ cancel 置位；分析在检查点中止，异常照原样带回主线程。"""

        def opener(cancel, **_kwargs):
            window = FakeWindow(cancel, on_stop=cancel.set)
            # 模拟用户点击：真实按钮的 command 就是 cancel.set。
            threading.Timer(0.02, window.on_stop).start()
            return FakeRoot(), window

        def work(cancel, report):
            while not cancel.is_set():
                time.sleep(0.005)
            raise AlignmentCancelled("已取消对齐：未生成比例回退。")

        with self.assertRaises(AlignmentCancelled):
            run_with_stop(work, open_window=opener)

    def test_cancelled_after_measurement_returns_the_outputs(self):
        """取消发生在测量之后：结果还在（run_tutor 会写出报告），只是标记 cancelled。"""

        def opener(cancel, **_kwargs):
            window = FakeWindow(cancel, on_stop=cancel.set)
            threading.Timer(0.02, window.on_stop).start()
            return FakeRoot(), window

        def work(cancel, report):
            while not cancel.is_set():
                time.sleep(0.005)
            return "report-path"

        outputs, cancelled = run_with_stop(work, open_window=opener)

        self.assertEqual(outputs, "report-path")
        self.assertTrue(cancelled)

    def test_stop_clicked_after_the_analysis_finished_is_not_reported_as_cancelled(self):
        """收尾那一段（最短显示时间）里再点停止：分析已经跑完，不能反过来报成「已取消」。

        真实窗口就是这个顺序：worker 结束 → 记下取消状态 → 推到 100% → 等最短显示时间 → 关窗。
        """

        seen = {}
        windows = []

        def opener(cancel, **_kwargs):
            window = FakeWindow(cancel, on_stop=cancel.set)
            window.on_complete = cancel.set    # 收尾那一刻用户又点了一下「停止」（最坏情况）
            windows.append(window)
            seen["cancel"] = cancel
            return FakeRoot(), window

        def work(cancel, report):
            report(0.68, "汇总音位判据与证据状态")
            return "finished"

        def hooked(work, **kwargs):
            kwargs["open_window"] = opener
            return run_with_stop(work, **kwargs)

        outputs, cancelled = hooked(work)

        self.assertEqual(outputs, "finished")
        self.assertFalse(cancelled)
        self.assertTrue(seen["cancel"].is_set())          # 用户确实点过
        self.assertEqual(windows[-1].completed, [None])   # 收尾时没有写「已取消」
        self.assertEqual(windows[-1].updates[-1], (1.0, None))


class MiniProgressStopButtonTests(unittest.TestCase):
    def _root(self):
        try:
            import tkinter as tk

            root = tk.Tk()
        except Exception as error:  # pragma: no cover - 无显示/无 tkinter 的环境
            self.skipTest(f"需要可用的 Tk：{error}")
        root.withdraw()
        return root

    def test_window_without_callback_has_no_stop_button(self):
        root = self._root()
        try:
            window = MiniProgress(root, title="t", message="m")
            try:
                self.assertIsNone(window.stop_button)
                self.assertIsNone(window.footer)
                self.assertFalse(window.stop_requested)
            finally:
                window.close()
        finally:
            root.destroy()

    def test_stop_button_fires_once_and_then_reports_it_is_stopping(self):
        root = self._root()
        calls = []
        try:
            window = MiniProgress(
                root, title="t", message="m", on_stop=lambda: calls.append(1), stop_label="停止分析"
            )
            try:
                self.assertEqual(window.stop_button.cget("text"), "停止分析")
                window.stop_button.invoke()
                self.assertEqual(calls, [1])
                self.assertTrue(window.stop_requested)
                self.assertEqual(window.stop_button.cget("state"), "disabled")
                self.assertEqual(window.stop_button.cget("text"), "正在停止…")
                window.stop_button.invoke()          # 再点不该重复触发
                self.assertEqual(calls, [1])
                self.assertEqual(window.message.get(), "正在停止…")
            finally:
                window.close()
        finally:
            root.destroy()

    def test_close_key_means_stop_and_nothing_happens_after_completion(self):
        """关窗键 = 点停止（只在有停止这一档时）；分析结束后再点一下不算「要求停止」。"""

        root = self._root()
        calls = []
        try:
            window = MiniProgress(
                root, title="t", message="m", on_stop=lambda: calls.append(1), stop_label="停止分析"
            )
            try:
                window._on_close()                       # 相当于用户点右上角的 X
                self.assertEqual(calls, [1])
                self.assertEqual(window.stop_button.cget("state"), "disabled")
            finally:
                window.close()

            plain = MiniProgress(root, title="t", message="m")
            try:
                plain._on_close()                        # 没有 on_stop：维持「不给中途关闭」
                self.assertIsNone(plain.stop_button)
                self.assertFalse(plain.stop_requested)
                plain.complete()                         # 没有按钮时收尾也不该出错
                self.assertEqual(float(plain.bar.cget("value")), 100.0)
            finally:
                plain.close()

            finished = MiniProgress(root, title="t", message="m", on_stop=lambda: calls.append(2))
            try:
                finished.complete("分析完成")
                self.assertEqual(float(finished.bar.cget("value")), 100.0)
                self.assertEqual(finished.stop_button.cget("state"), "disabled")
                finished.stop_button.invoke()            # 结束之后那一下不算停止
                finished._on_close()
                self.assertEqual(calls, [1])
            finally:
                finished.close()
        finally:
            root.destroy()


class RunFromPraatTests(unittest.TestCase):
    def test_cancel_is_reported_honestly_and_is_not_a_success(self):
        with patch(
            "praat_ai.tutor.run_with_stop",
            side_effect=AlignmentCancelled("已取消 MFA 对齐：对齐进程已终止。"),
        ):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = run_from_praat(request_path="absent.json", interactive=False)

        self.assertEqual(code, 3)
        self.assertIn("已停止分析", stderr.getvalue())
        self.assertIn("没有生成报告", stderr.getvalue())

    def test_analysis_receives_cancel_and_progress_from_the_progress_window(self):
        seen = {}

        def fake_tutor(**kwargs):
            seen.update(kwargs)
            raise ValueError("test-stop")

        def fake_run_with_stop(work, **kwargs):
            self.assertIs(kwargs["enabled"], False)      # --no-gui：不建窗口
            return work(threading.Event(), lambda fraction, message: None), False

        with patch("praat_ai.tutor.run_tutor", side_effect=fake_tutor), patch(
            "praat_ai.tutor.run_with_stop", side_effect=fake_run_with_stop
        ):
            with redirect_stderr(io.StringIO()):
                code = run_from_praat(request_path="absent.json", interactive=False)

        self.assertEqual(code, 1)
        self.assertIsInstance(seen["cancel"], threading.Event)
        self.assertTrue(callable(seen["progress"]))


def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()

if __name__ == "__main__":
    unittest.main()
