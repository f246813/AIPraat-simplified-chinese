"""消息渲染：最小 Markdown 子集 + 「⧉ 复制」按钮复制的是原文（guide.md §8.16）。"""

import types
import unittest

import tkinter as tk

from praat_ai import chat


class RenderMessageTests(unittest.TestCase):
    """``render_message`` 是纯函数：返回 ``(片段, tag)``，tag 直接喂给 Text.insert。"""

    def test_plain_text(self) -> None:
        self.assertEqual(chat.render_message("你好"), [("你好", "")])

    def test_headings(self) -> None:
        for text in ("# 标题", "## 标题", "### 标题"):
            with self.subTest(text=text):
                self.assertEqual(chat.render_message(text), [("标题", "heading")])

    def test_bullets_become_dots(self) -> None:
        for text in ("- 第一点", "* 第一点", "• 第一点"):
            with self.subTest(text=text):
                self.assertEqual(chat.render_message(text), [("• 第一点", "bullet")])

    def test_bold_and_code(self) -> None:
        self.assertEqual(chat.render_message("**粗**"), [("粗", "bold")])
        self.assertEqual(chat.render_message("`x = 1`"), [("x = 1", "code")])

    def test_inline_tags_stack_on_the_block_tag(self) -> None:
        segments = chat.render_message("- **F1** = `742` Hz")
        self.assertEqual(
            segments,
            [
                ("• ", "bullet"),
                ("F1", ("bullet", "bold")),
                (" = ", "bullet"),
                ("742", ("bullet", "code")),
                (" Hz", "bullet"),
            ],
        )

    def test_lines_are_separated_by_newlines(self) -> None:
        self.assertEqual(
            chat.render_message("第一行\n第二行"),
            [("第一行", ""), ("\n", ""), ("第二行", "")],
        )

    def test_windows_line_endings_behave_the_same(self) -> None:
        self.assertEqual(chat.render_message("a\r\nb"), chat.render_message("a\nb"))

    def test_lookalikes_are_left_alone(self) -> None:
        """不是标记的写法原样显示（别把用户/模型的普通文字吃掉）。"""

        self.assertEqual(chat.render_message("1. 第一条"), [("1. 第一条", "")])
        self.assertEqual(chat.render_message("__下划线__"), [("__下划线__", "")])
        self.assertEqual(chat.render_message("2 * 3"), [("2 * 3", "")])

    def test_empty_input(self) -> None:
        self.assertEqual(chat.render_message(""), [("", "")])
        self.assertEqual(chat.render_message(None), [("", "")])

    def test_trailing_spaces_are_trimmed(self) -> None:
        self.assertEqual(chat.render_message("正文   "), [("正文", "")])


class ChatMessageBlockTests(unittest.TestCase):
    """真开一个对话窗口：消息块 tag 存在，复制按钮复制的是原文。"""

    def setUp(self) -> None:
        try:
            self.window = chat.ChatWindow()
        except tk.TclError as error:   # 没有显示器（CI）就跳过
            raise unittest.SkipTest(f"没有可用的显示：{error}") from error

    def tearDown(self) -> None:
        try:
            self.window.close()
        except tk.TclError:
            pass

    def test_assistant_message_gets_a_copy_link_with_the_original_text(self) -> None:
        raw = "## 对比结果\n- **F1** 742 Hz"
        self.window.append("assistant", raw)
        registry = self.window._copy_registry
        self.assertTrue(registry, "回答后面应该有「⧉ 复制」")
        tag, stored = next(reversed(list(registry.items())))
        self.assertEqual(stored, raw)
        self.assertIn(tag, self.window.transcript.tag_names("end-2c"))
        # 真的点一下那个文本按钮（按坐标命中 tag，走 _on_copy_click）。
        # 取**最后**一个「⧉ 复制」（刚插进去的那条；开头那条欢迎语也有一个）。
        index = self.window.transcript.search("⧉ 复制", "1.0", stopindex="end")
        self.assertTrue(index)
        while True:
            following = self.window.transcript.search(
                "⧉ 复制", f"{index}+1c", stopindex="end"
            )
            if not following:
                break
            index = following
        self.window.transcript.see(index)
        self.window.root.update()
        box = self.window.transcript.bbox(index)
        self.assertIn(tag, self.window.transcript.tag_names(index))
        if box is None:   # 不在可视区里（窗口大小不同）时退化成直接复制
            self.window.copy_message(stored)
        else:
            self.window._on_copy_click(
                types.SimpleNamespace(x=box[0] + 2, y=box[1] + 2)
            )
        self.assertEqual(self.window.root.clipboard_get(), raw)

    def test_user_messages_are_not_copyable(self) -> None:
        before = len(self.window._copy_registry)
        self.window.append("user", "把这段和标准音比一下")
        self.assertEqual(len(self.window._copy_registry), before)

    def test_result_blocks_are_copyable_and_hinted_muted(self) -> None:
        before = len(self.window._copy_registry)
        self.window.append_lines("result", "基频 = 208.4 Hz")
        self.window.append_hint("例如：查一下 0.5 秒处的基频。")
        self.assertEqual(len(self.window._copy_registry), before + 1)
        self.assertEqual(
            self.window.transcript.tag_cget("failure_block", "background"),
            self.window.theme.color("dangerSoft"),
        )

    def test_message_block_colours_follow_the_theme(self) -> None:
        theme = self.window.theme
        self.assertEqual(
            self.window.transcript.tag_cget("assistant_block", "background"),
            theme.color("surface"),
        )
        theme.set_dark(True)
        self.assertEqual(
            self.window.transcript.tag_cget("assistant_block", "background"),
            theme.color("surface"),
        )
        self.assertEqual(
            self.window.transcript.cget("background"), theme.color("canvas")
        )


def tearDownModule():
    # Tk 解释器必须在主线程回收：否则后端线程的下一次 GC 会析构它，触发 Tcl_AsyncDelete。
    import gc

    gc.collect()

if __name__ == "__main__":
    unittest.main()
