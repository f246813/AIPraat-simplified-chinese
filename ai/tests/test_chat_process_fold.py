"""请求结束后的过程折叠、耗时和右键交互（真实 Tk 文本区）。"""

import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

from praat_ai import chat


class ProcessFoldTests(unittest.TestCase):
    def setUp(self) -> None:
        # A GUI fixture must not probe a real model endpoint from user configuration.
        probe = patch.object(chat, 'running_model_info', return_value={})
        probe.start()
        self.addCleanup(probe.stop)
        # 假线程必须自报「已结束」：``close()`` 里用 ``worker.is_alive()`` 决定是立刻销毁
        # 窗口还是排 ``after(100, _destroy_when_idle)`` 再等；MagicMock 的返回值恒真，
        # 会让 root 永远等不到销毁，每个用例泄漏一个活解释器。
        self.fake_thread = MagicMock()
        self.fake_thread.is_alive.return_value = False
        try:
            self.window = chat.ChatWindow()
        except tk.TclError as error:
            raise unittest.SkipTest(f"没有可用的显示：{error}") from error
        self.window.root.update()

    def tearDown(self) -> None:
        for callback in self.window.root.tk.splitlist(self.window.root.tk.call("after", "info")):
            self.window.root.after_cancel(callback)
        self.window.close()

    def submit(self, started_at=100.0) -> None:
        self.window.entry.insert("1.0", "查询基频")
        # 避免连接真实模型；保留 submit 和消息队列的 UI 路径。
        with patch.object(chat.threading, "Thread", return_value=self.fake_thread), patch.object(
            chat.time, "monotonic", return_value=started_at
        ):
            self.window.submit()

    def finish(self, finished_at=225.9) -> None:
        self.window.messages.put(("assistant", "基频是 **220 Hz**。"))
        self.window.messages.put(("done", str(finished_at)))
        self.window.flush_messages()

    def body_tag(self, text: str) -> str:
        index = self.window.transcript.search(text, "1.0", stopindex="end", elide=True)
        self.assertTrue(index)
        tags = self.window.transcript.tag_names(index)
        process_tags = [tag for tag in tags if tag.startswith("process_body_")]
        self.assertEqual(len(process_tags), 1, "每条过程信息应属于本轮的折叠区")
        return process_tags[0]

    def collapsed(self, tag: str) -> bool:
        # Tk 8.6 返回字符串，用户前端所用的 Tk 9.0 返回整数。
        return self.window.transcript.tk.getboolean(
            self.window.transcript.tag_cget(tag, "elide")
        )

    def click(self, text: str, button: int, *, settle: bool = True) -> None:
        transcript = self.window.transcript
        index = transcript.search(text, "1.0", stopindex="end")
        self.assertTrue(index)
        transcript.see(index)
        self.window.root.update()
        box = transcript.bbox(index)
        self.assertIsNotNone(box)
        x, y = box[0] + box[2] // 2, box[1] + box[3] // 2
        if settle:
            # 连续两次右键在真实使用中隔着好几秒；测试里要显式让过去抖窗口，
            # 否则「防重复投递」会把第二次点击吃掉。
            for block in self.window._process_blocks.values():
                block.last_toggle_at = 0.0
        transcript.event_generate("<Motion>", x=x, y=y)
        transcript.event_generate(f"<Button-{button}>", x=x, y=y)
        transcript.event_generate(f"<ButtonRelease-{button}>", x=x, y=y)
        self.window.root.update()

    def right_click(self, text: str, *, settle: bool = True) -> None:
        self.click(text, button=3, settle=settle)

    def test_completed_turn_collapses_only_its_process_and_shows_elapsed_time(self) -> None:
        self.submit()
        self.window.messages.put(("hint", "正在查询当前声音的基频"))
        self.window.flush_messages()
        tag = self.body_tag("正在查询当前声音的基频")
        self.assertFalse(self.collapsed(tag))
        self.finish()
        transcript = self.window.transcript
        self.assertIn("已完成，用时2分5秒", transcript.get("1.0", "end"))
        self.assertTrue(self.collapsed(tag))
        answer = transcript.search("220 Hz", "1.0", stopindex="end")
        self.assertNotIn(tag, transcript.tag_names(answer))
        welcome = transcript.search("例如：", "1.0", stopindex="end")
        self.assertNotIn(tag, transcript.tag_names(welcome))
        self.assertIn("基频是 **220 Hz**。", self.window._copy_registry.values())

    def test_repeated_events_from_one_right_click_toggle_only_once(self) -> None:
        """同一次右键引出的重复事件只能切换一次。

        两次切换会互相抵消，用户看到的就是「点了没反应 / 闪一下又收起」。
        这里直接连调两次处理器（Tk 在有些平台上会为一次右键投递
        ``<Button-3>`` 和 ``<ButtonRelease-3>`` 两个事件），第二次必须被去抖吃掉。

        实测：本机 Tk 9.0.4 只把 ``<Button-3>`` 送到处理器，所以这条用直调来钉住契约，
        不依赖平台的事件投递细节。
        """

        self.submit()
        self.window.append_hint("重复投递验收")
        self.finish()
        tag = self.body_tag("重复投递验收")
        transcript = self.window.transcript
        header = transcript.search("已完成", "1.0", stopindex="end")
        transcript.see(header)
        self.window.root.update()
        box = transcript.bbox(header)
        event = type("E", (), {"x": box[0] + max(1, box[2] // 2), "y": box[1] + box[3] // 2})()
        first = self.window._on_process_right_click(event)
        self.assertEqual(first, "break")
        self.assertFalse(self.collapsed(tag))
        second = self.window._on_process_right_click(event)
        self.assertEqual(second, "break")
        self.assertFalse(self.collapsed(tag), "同一轮事件被处理了两次，折叠被抵消")
        self.assertIn("已完成，用时2分5秒  ▴", transcript.get("1.0", "end"))

    def test_right_drag_within_the_debounce_window_toggles_only_once(self) -> None:
        """右键按下再拖一下别的地方，不能把刚展开的折叠区又收起。"""

        self.submit()
        self.window.append_hint("拖动验收")
        self.finish()
        tag = self.body_tag("拖动验收")
        transcript = self.window.transcript
        header = transcript.search("已完成", "1.0", stopindex="end")
        transcript.see(header)
        self.window.root.update()
        box = transcript.bbox(header)
        x, y = box[0] + max(1, box[2] // 2), box[1] + box[3] // 2
        transcript.event_generate("<Motion>", x=x, y=y)
        transcript.event_generate("<Button-3>", x=x, y=y)
        self.window.root.update()
        self.assertFalse(self.collapsed(tag))
        # 同一次拖动（去抖窗口内）再落到标题上：不再切换。
        transcript.event_generate("<Motion>", x=x + 30, y=y)
        transcript.event_generate("<Button-3>", x=x + 30, y=y)
        self.window.root.update()
        self.assertFalse(self.collapsed(tag))

    def test_two_separate_right_clicks_still_toggle_twice(self) -> None:
        """防重复投递只吃「同一次」点击，间隔开的两次点击照旧各切一次。"""

        self.submit()
        self.window.append_hint("连续点击验收")
        self.finish()
        tag = self.body_tag("连续点击验收")
        self.right_click("已完成，用时2分5秒")
        self.assertFalse(self.collapsed(tag))
        self.right_click("已完成，用时2分5秒")
        self.assertTrue(self.collapsed(tag))

    def test_right_click_expands_and_collapses_preserving_the_process(self) -> None:
        self.submit()
        self.window.append_hint("第一步：读取声音")
        self.window.append_hint("第二步：计算基频")
        self.finish()
        tag = self.body_tag("第一步：读取声音")
        self.right_click("已完成，用时2分5秒")
        self.assertFalse(self.collapsed(tag))
        self.assertIn("已完成，用时2分5秒  ▴", self.window.transcript.get("1.0", "end"))
        self.right_click("已完成，用时2分5秒")
        self.assertTrue(self.collapsed(tag))
        self.assertIn("已完成，用时2分5秒  ▾", self.window.transcript.get("1.0", "end"))
        self.assertIn("第二步：计算基频", self.window.transcript.get("1.0", "end"))
        self.assertEqual(self.window.transcript.cget("state"), "disabled")

    def test_right_click_on_the_arrow_itself_expands_and_collapses(self) -> None:
        self.submit()
        self.window.append_hint("箭头右键验收过程")
        self.finish()
        tag = self.body_tag("箭头右键验收过程")
        self.right_click("▾")
        self.assertFalse(self.collapsed(tag))
        self.right_click("▴")
        self.assertTrue(self.collapsed(tag))

    def test_right_click_after_a_long_answer_really_displays_the_process(self) -> None:
        self.submit()
        self.window.append_hint("完整过程的第一行\n完整过程的第二行")
        self.window.append("assistant", "很长的回答\n" * 80)
        self.finish()
        transcript = self.window.transcript
        body = transcript.search("完整过程的第一行", "1.0", stopindex="end", elide=True)
        start, end = transcript.tag_ranges(self.body_tag("完整过程的第一行"))
        self.assertEqual(transcript.count(start, end, "displaychars"), None)
        self.right_click("▾")
        self.assertGreater(transcript.count(start, end, "displaychars")[0], 0)
        transcript.see(body)
        self.window.root.update()
        self.assertIsNotNone(transcript.dlineinfo(body))

    def test_right_click_does_not_require_a_prior_motion_event_on_the_header(self) -> None:
        self.submit()
        self.window.append_hint("原生点击过程")
        self.finish()
        transcript = self.window.transcript
        arrow = transcript.search("▾", "1.0", stopindex="end")
        transcript.see(arrow)
        self.window.root.update()
        box = transcript.bbox(arrow)
        transcript.event_generate("<Motion>", x=0, y=0)
        transcript.event_generate("<Button-3>", x=box[0] + box[2] // 2, y=box[1] + box[3] // 2)
        self.window.root.update()
        self.assertFalse(self.collapsed(self.body_tag("原生点击过程")))

    def click_point(self, x: int, y: int, *, settle: bool = True) -> None:
        """在文本区的像素坐标上点右键。"""

        transcript = self.window.transcript
        if settle:
            for block in self.window._process_blocks.values():
                block.last_toggle_at = 0.0
        transcript.event_generate("<Motion>", x=x, y=y)
        transcript.event_generate("<Button-3>", x=x, y=y)
        self.window.root.update()

    def click_index(self, index: str) -> None:
        """按字符索引点右键（含该行行尾、下一行等原本点不到的位置）。

        折叠区里的行 ``bbox`` 会是 ``None``（没有显示），所以这里不要求可见：
        用户点的是那段行距，坐标由相邻可见行决定，照样要能命中。
        """

        transcript = self.window.transcript
        transcript.see(index)
        self.window.root.update()
        box = transcript.bbox(index)
        if box is None:
            # 隐藏行：退回主窗口右下角附近的合法坐标不会命中任何标题，
            # 这里改用标题行本身做锚点（真正的行距点击由上面那条用例覆盖）。
            header = transcript.search("已完成", "1.0", stopindex="end")
            box = transcript.bbox(header)
        self.assertIsNotNone(box, f"{index} 附近没有可点的位置")
        x = box[0] + max(1, box[2] // 2)
        y = box[1] + box[3] // 2
        self.click_point(x, y)

    def test_right_click_in_the_row_gap_above_the_header_still_toggles(self) -> None:
        """标题文字上方那条行距带没有字符，@x,y 落在标题行本身，也必须能点开。"""

        self.submit()
        self.window.append_hint("上行距验收")
        self.finish()
        tag = self.body_tag("上行距验收")
        transcript = self.window.transcript
        header = transcript.search("已完成", "1.0", stopindex="end")
        transcript.see(header)
        self.window.root.update()
        box = transcript.bbox(header)
        self.assertIsNotNone(box)
        # 文字顶边往上 2px：落在 hint 的 spacing1 里。
        self.click_point(box[0] + 40, box[1] - 2)
        self.assertFalse(self.collapsed(tag))
        # 标题下方（折叠后那块隐藏正文所占的行距）同样要能收起。
        self.click_point(box[0] + 40, box[1] + box[3] + 6)
        self.assertTrue(self.collapsed(tag))

    def test_right_click_on_the_blank_tail_of_the_header_still_toggles(self) -> None:
        """标题行右侧没有文字的空白（@x,y 落到行尾）以前右键没反应。"""

        self.submit()
        self.window.append_hint("行尾空白验收")
        self.finish()
        tag = self.body_tag("行尾空白验收")
        transcript = self.window.transcript
        header_line = transcript.index(transcript.search("已完成", "1.0", stopindex="end")).split(".")[0]
        self.click_index(f"{header_line}.end")
        self.assertFalse(self.collapsed(tag))
        self.click_index(f"{header_line}.end")
        self.assertTrue(self.collapsed(tag))

    def test_right_click_one_row_below_the_header_expands_the_collapsed_process(self) -> None:
        """标题下面那一行的行距，同一个点上去也要能展开、能再收起。

        折叠状态下这一行的字符是隐藏的，但用户点的是那段行距（``spacing3`` 与隐藏正文
        的高度），``@x,y`` 解析出来仍在标题行的下面一行；命中判定必须覆盖它。
        这里固定用标题文字的像素位置 + 一段固定偏移，保证两次点的是同一处。
        """

        self.submit()
        self.window.append_hint("差一行验收")
        self.finish()
        tag = self.body_tag("差一行验收")
        transcript = self.window.transcript
        header = transcript.search("已完成", "1.0", stopindex="end")
        transcript.see(header)
        self.window.root.update()
        box = transcript.bbox(header)
        self.assertIsNotNone(box)
        x = box[0] + 40
        y = box[1] + box[3] + 6
        self.click_point(x, y)
        self.assertFalse(self.collapsed(tag))
        self.assertIn("差一行验收", transcript.get("1.0", "end"))
        self.click_point(x, y)
        self.assertTrue(self.collapsed(tag))

    def test_right_click_far_from_any_header_does_nothing(self) -> None:
        self.submit()
        self.window.append_hint("远处点击验收")
        self.finish()
        tag = self.body_tag("远处点击验收")
        transcript = self.window.transcript
        welcome = transcript.search("例如：", "1.0", stopindex="end")
        self.assertTrue(welcome)
        before = transcript.get("1.0", "end")
        self.click_index(welcome)
        self.assertTrue(self.collapsed(tag))
        self.assertEqual(before, transcript.get("1.0", "end"))

    def test_turns_toggle_independently_and_keep_hints_after_the_answer(self) -> None:
        self.submit()
        self.window.append_hint("第一轮的过程")
        self.finish()
        first = self.body_tag("第一轮的过程")
        self.submit(started_at=300.0)
        self.window.append("assistant", "第二轮的回答")
        self.window.append_hint("第二轮的后续说明")
        self.finish(finished_at=309.0)
        second = self.body_tag("第二轮的后续说明")
        self.assertNotEqual(first, second)
        self.right_click("已完成，用时0分9秒")
        self.assertTrue(self.collapsed(first))
        self.assertFalse(self.collapsed(second))
        self.window.append_hint("请求之外的设置提示")
        index = self.window.transcript.search("请求之外的设置提示", "1.0", stopindex="end")
        self.assertNotIn(second, self.window.transcript.tag_names(index))
        self.window.theme.set_dark(True)
        self.assertTrue(self.collapsed(first))
        header = self.window.transcript.search("已完成，用时0分9秒", "1.0", stopindex="end")
        self.assertIn("hint", self.window.transcript.tag_names(header))
        self.assertEqual(self.window.transcript.tag_cget("hint", "foreground"),
                         self.window.theme.color("textMuted"))

    def test_empty_process_and_failed_response_still_finish(self) -> None:
        self.submit()
        # Cloud now deliberately bypasses the global Praat precondition. Keep
        # this existing test focused on local failure/process timing.
        self.window.config.api.enabled = False
        self.window.config.api.locked = False
        with patch.object(chat, "praat_executable", return_value=""), patch.object(
            chat.time, "monotonic", return_value=100.4
        ):
            self.window.process_message("查询基频")
        # UI 迟到两分钟才处理队列，耗时仍以工作线程结束为准。
        with patch.object(chat.time, "monotonic", return_value=220.4):
            self.window.flush_messages()
        self.assertFalse(self.window.busy)
        self.assertIn("处理失败：", self.window.transcript.get("1.0", "end"))
        self.assertIn("已完成，用时0分0秒", self.window.transcript.get("1.0", "end"))
        self.right_click("已完成，用时0分0秒")
        self.window.messages.put(("done", ""))  # 模型切换仍使用原来的完成事件。
        self.window.flush_messages()
        self.assertEqual(self.window.transcript.get("1.0", "end").count("已完成，用时"), 1)

    def test_stopped_turn_keeps_its_process_available(self) -> None:
        self.submit()
        self.window.append_hint("正在等待 Praat 返回结果")
        self.window.cancel_turn()
        self.assertTrue(self.window.cancel_event.is_set())
        self.finish(finished_at=103.0)
        tag = self.body_tag("已请求停止")
        self.assertTrue(self.collapsed(tag))
        self.assertFalse(self.window.cancel_event.is_set())
        self.right_click("已完成，用时0分3秒")
        self.assertFalse(self.collapsed(tag))



def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()

if __name__ == "__main__":
    unittest.main()
