"""自绘控件：进度条数值语义、按钮状态、卡片/转圈的生命周期、输入框外壳。

这些是 ttk 的替代品，所以**必须**保持 ttk 那套对外接口（``cget("value")`` 是
0–100、``cget("state")`` 等）——回归脚本按这些读，见 guide.md §8.16。
"""

import unittest

import tkinter as tk
from tkinter import ttk

from praat_ai import ui_theme, ui_widgets


def make_root() -> tk.Tk:
    try:
        root = tk.Tk()
    except tk.TclError as error:   # 没有显示器（CI）就跳过这些用例
        raise unittest.SkipTest(f"没有可用的显示：{error}") from error
    return root


_SHARED_ROOT: tk.Tk | None = None


def shared_root() -> tk.Tk:
    """整个测试模块共用一个 Tk 解释器。

    每个用例各建各的根窗口时，Tk 在**同一个进程**里反复销毁/重建解释器，ttk 的
    ``<<ThemeChanged>>`` 广播会打到已经销毁的窗口上，往 stderr 吐一串吓人的
    Tcl 报错（测试结论不受影响，但输出很难看）。共用一个根就没有这个问题。
    """

    global _SHARED_ROOT
    if _SHARED_ROOT is None or not _SHARED_ROOT.winfo_exists():
        _SHARED_ROOT = make_root()
        _SHARED_ROOT.withdraw()
    return _SHARED_ROOT


def canvas_image_pixel(canvas: tk.Canvas, item: int, x: int, y: int) -> tuple[int, ...]:
    image_name = canvas.itemcget(item, "image")
    return tuple(canvas.tk.call(image_name, "get", x, y))


class WidgetTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.root = shared_root()
        self.theme = ui_theme.Theme(self.root, dark=False, scale=1.0)
        self._widgets: list = []

    def track(self, widget):
        """登记控件，tearDown 里先销毁控件再销毁窗口（顺序反了会报 TclError）。"""

        self._widgets.append(widget)
        return widget

    def tearDown(self) -> None:
        for widget in self._widgets:
            try:
                widget.destroy()
            except tk.TclError:
                pass


class RoundedProgressBarTests(WidgetTestCase):
    def make(self) -> ui_widgets.RoundedProgressBar:
        return self.track(ui_widgets.RoundedProgressBar(self.root, self.theme))

    def test_ttk_value_semantics(self) -> None:
        bar = self.make()
        self.assertEqual(bar.cget("value"), 0.0)
        self.assertEqual(bar.cget("maximum"), 100.0)
        bar.configure(value=30)
        self.assertEqual(float(bar.cget("value")), 30.0)

    def test_values_are_clamped(self) -> None:
        bar = self.make()
        bar.configure(value=150)
        self.assertEqual(bar.cget("value"), 100.0)
        bar.configure(value=-5)
        self.assertEqual(bar.cget("value"), 0.0)
        bar.configure(value="不是数字")
        self.assertEqual(bar.cget("value"), 0.0)

    def test_indeterminate_mode_is_ignored_gracefully(self) -> None:
        bar = self.make()
        bar.configure(mode="determinate", length=200, indeterminate=True)
        bar.stop_indeterminate()
        self.assertFalse(bar._indeterminate)

    def test_destroy_cancels_the_animation(self) -> None:
        bar = self.make()
        bar.configure(value=50)
        self.assertIsNotNone(bar._after_id)
        bar.destroy()
        self.assertIsNone(bar._after_id)


class RoundedButtonTests(WidgetTestCase):
    def make(self, **kwargs) -> tuple[ui_widgets.RoundedButton, list[str]]:
        calls: list[str] = []
        return (
            self.track(
                ui_widgets.RoundedButton(
                    self.root, self.theme, "发送", lambda: calls.append("click"), **kwargs
                )
            ),
            calls,
        )

    def test_ttk_style_api(self) -> None:
        button, calls = self.make()
        self.assertEqual(button.cget("state"), "normal")
        self.assertEqual(button.cget("text"), "发送")
        button.invoke()
        self.assertEqual(calls, ["click"])

    def test_disabled_button_never_fires(self) -> None:
        button, calls = self.make()
        button.configure(state="disabled")
        self.assertEqual(button.cget("state"), "disabled")
        button.invoke()
        button._on_press()
        button._on_release()
        self.assertEqual(calls, [])
        button.configure(state="normal")
        button.invoke()
        self.assertEqual(calls, ["click"])

    def test_text_can_be_changed(self) -> None:
        button, _calls = self.make()
        button.configure(text="已复制")
        self.assertEqual(button.cget("text"), "已复制")

    def test_outlined_hover_and_pressed_states_use_distinct_colors(self) -> None:
        button, _calls = self.make(kind="outlined")
        normal = button._palette(self.theme)
        button._hover = True
        hover = button._palette(self.theme)
        button._pressed = True
        pressed = button._palette(self.theme)

        self.assertEqual(normal, ("#FFFFFF", "#3B71CA", "#3B71CA"))
        self.assertEqual(hover, ("#E8F0FE", "#3567B5", "#3567B5"))
        self.assertEqual(pressed, ("#FBFBFB", "#3B71CA", "#3B71CA"))

    def test_press_and_release_inside_the_button_clicks(self) -> None:
        button, calls = self.make()
        button.configure(width=80, height=32)
        button.update_idletasks()
        button._on_press()
        button._on_release()   # 不给事件 = 视为在按钮内抬起
        self.assertEqual(calls, ["click"])

    def test_release_outside_the_button_does_not_click(self) -> None:
        button, calls = self.make()

        class _Event:
            x = 9999
            y = 9999

        button._on_press()
        button._on_release(_Event())
        self.assertEqual(calls, [])


class CardTests(WidgetTestCase):
    def test_card_has_a_body_and_unregisters_itself(self) -> None:
        card = ui_widgets.Card(self.root, self.theme)
        card.pack()
        self.assertIsInstance(card.body, tk.Frame)
        self.assertEqual(self.theme.listener_count(), 1)
        card.destroy()
        self.assertEqual(self.theme.listener_count(), 0)

    def test_card_redraws_on_theme_change(self) -> None:
        card = ui_widgets.Card(self.root, self.theme)
        card.pack(fill="both", expand=True)
        self.root.update_idletasks()
        self.assertEqual(
            card.canvas.cget("background"), ui_theme.LIGHT["canvas"]
        )
        self.theme.set_dark(True)
        self.assertEqual(card.canvas.cget("background"), ui_theme.DARK["canvas"])
        card.destroy()


class FieldCardTests(WidgetTestCase):
    def test_wrapped_entry_does_not_draw_a_second_border(self) -> None:
        style = ttk.Style(self.root)
        previous_theme = style.theme_use()
        try:
            style.theme_use("clam")
            for dark, expected_surface in (
                (False, "#FFFFFF"),
                (True, "#1E1E1E"),
            ):
                self.theme.set_dark(dark)
                ui_widgets.configure_ttk(style, self.theme)
                shell = ui_widgets.FieldCard(self.root, self.theme)
                entry = ttk.Entry(shell)
                shell.attach(entry)
                self.assertEqual(entry.cget("style"), "Field.TEntry")
                for option in (
                    "background",
                    "bordercolor",
                    "lightcolor",
                    "darkcolor",
                ):
                    self.assertEqual(
                        style.lookup("Field.TEntry", option), expected_surface
                    )
                    self.assertEqual(
                        style.lookup(
                            "Field.TEntry", option, state=("focus",)
                        ),
                        expected_surface,
                    )
                shell.destroy()
        finally:
            style.theme_use(previous_theme)

    def test_wrapped_entry_focus_is_shown_on_the_rounded_shell(self) -> None:
        style = ttk.Style(self.root)
        previous_theme = style.theme_use()
        previous_geometry = self.root.geometry()
        style.theme_use("clam")
        ui_widgets.configure_ttk(style, self.theme)
        shell = ui_widgets.FieldCard(self.root, self.theme, width=180, height=40)
        shell.pack()
        entry = ttk.Entry(shell)
        shell.attach(entry)
        self.root.geometry("240x80+0+0")
        try:
            self.root.attributes("-alpha", 0.0)
        except tk.TclError:
            pass
        self.root.deiconify()
        try:
            self.root.update()
            item = shell.canvas.find_withtag("field")[0]
            normal_edge = canvas_image_pixel(
                shell.canvas, item, shell.winfo_width() // 2, 0
            )
            self.assertLess(
                sum(abs(value - expected) for value, expected in zip(normal_edge, (224, 224, 224))),
                60,
            )
            entry.event_generate("<FocusIn>")
            self.root.update()

            item = shell.canvas.find_withtag("field")[0]
            focused_edge = canvas_image_pixel(
                shell.canvas, item, shell.winfo_width() // 2, 0
            )
            self.assertLess(
                sum(abs(value - expected) for value, expected in zip(focused_edge, (59, 113, 202))),
                60,
            )
            entry.event_generate("<FocusOut>")
            self.root.update()
            item = shell.canvas.find_withtag("field")[0]
            blurred_edge = canvas_image_pixel(
                shell.canvas, item, shell.winfo_width() // 2, 0
            )
            self.assertLess(
                sum(abs(value - expected) for value, expected in zip(blurred_edge, (224, 224, 224))),
                60,
            )
        finally:
            shell.destroy()
            self.root.withdraw()
            style.theme_use(previous_theme)
            self.root.geometry(previous_geometry)
            try:
                self.root.attributes("-alpha", 1.0)
            except tk.TclError:
                pass

    def test_it_wraps_a_real_ttk_entry(self) -> None:
        """``key_entry.cget("show")`` 这些是回归脚本读的，不能被换成自绘控件。"""

        shell = ui_widgets.FieldCard(self.root, self.theme)
        shell.pack(fill="x")
        entry = ttk.Entry(shell, show="•")
        shell.attach(entry)
        self.assertIs(shell.attach(entry), entry)
        self.assertEqual(entry.cget("show"), "•")
        self.assertEqual(self.theme.listener_count(), 1)
        shell.destroy()
        self.assertEqual(self.theme.listener_count(), 0)


class SpinnerTests(WidgetTestCase):
    def test_start_and_stop(self) -> None:
        spinner = ui_widgets.Spinner(self.root, self.theme, background="canvas")
        spinner.pack()
        spinner.start()
        self.assertTrue(spinner._running)
        spinner.stop()
        self.assertFalse(spinner._running)
        self.assertIsNone(spinner._after_id)

    def test_destroy_stops_it(self) -> None:
        spinner = ui_widgets.Spinner(self.root, self.theme, background="canvas")
        spinner.pack()
        spinner.start()
        spinner.destroy()
        self.assertFalse(spinner._running)
        self.assertEqual(self.theme.listener_count(), 0)


class ChipAndSnackbarTests(WidgetTestCase):
    def test_chip_kind_switches_palette(self) -> None:
        chip = ui_widgets.Chip(self.root, self.theme, text="服务未响应", kind="neutral")
        chip.pack()
        self.assertEqual(chip.label.cget("foreground"), ui_theme.LIGHT["textMuted"])
        chip.set_kind("danger")
        self.assertEqual(chip.label.cget("foreground"), ui_theme.LIGHT["danger"])
        self.assertEqual(chip.set_kind("胡说"), "neutral")   # 认不出来的按中性
        chip.destroy()

    def test_snackbar_follows_the_theme(self) -> None:
        snack = ui_widgets.Snackbar(self.root, self.theme, text="配置已经更新")
        snack.pack(fill="x")
        snack.set_kind("primary")
        self.assertEqual(snack.label.cget("background"), ui_theme.LIGHT["primarySoft"])
        self.theme.set_dark(True)
        self.assertEqual(snack.label.cget("background"), ui_theme.DARK["primarySoft"])
        snack.destroy()
        self.assertEqual(self.theme.listener_count(), 0)



def tearDownModule():
    # 模块级共用 root 必须在主线程销毁并回收：否则后端线程的下一次 GC 会析构它，
    # 触发 Tcl_AsyncDelete（async handler 是在主线程注册的）。
    global _SHARED_ROOT
    import gc

    root, _SHARED_ROOT = _SHARED_ROOT, None
    if root is not None:
        try:
            root.destroy()
        except tk.TclError:
            pass
    gc.collect()

if __name__ == "__main__":
    unittest.main()
