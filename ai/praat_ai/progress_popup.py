"""「简约小窗口 + 进度条」：加载/停止模型时给用户看的那个小窗。

两种入口用两套 UI，但数据是同一条进度流（0–1 的进度 + 一句中文说明）：

- Praat 菜单里点「启动前端 / 加载模型」→ 控制脚本把进度打到标准输出
  （``PRAAT_PROGRESS\\t<进度>\\t<说明>``）→ Praat 自己的进度小窗
  （`sys/praat_python.cpp` → `Melder_progress`，那个窗口属于 Praat，见 guide.md §8.1
  说明为什么不动它）；
- 对话窗口里点「应用预设 / API 配置」→ 本模块的 :class:`MiniProgress`。

界面按 TW-Elements 的卡片形态做：圆角卡片 + 圆角进度条 + 右侧百分比（等宽数字），
深浅色跟随系统。既有接口一个都没变（``bar.cget("value")`` 仍是 0–100，``configure``/``cget``
兼容层照旧）。

``on_stop`` 是给「分析阶段」用的可选第三档：传了回调才长出一个「停止」按钮，点一次置位一次
（按钮随即变成不可再点的「正在停止…」），关窗键也与它同义；不传就与以前完全一样。
收尾用 :meth:`MiniProgress.complete`（推到 100% 并禁掉按钮），让「分析已经结束」与
「用户要求停止」不再混淆。
"""

from __future__ import annotations

import time

from . import ui_theme, ui_widgets

#: 小窗最少显示多久：一次「应用预设」有时几百毫秒就完了，不兜一下用户只看到一闪。
MINIMUM_VISIBLE_SEC = 0.8


class MiniProgress:
    """挂在对话窗口上的迷你进度窗（卡片 + 一句话 + 一根进度条 + 百分比）。

    传了 ``on_stop`` 才多一个「停止」按钮（见 :meth:`_request_stop`），关窗键也只在那种情况下
    才有意义（等同点停止）；收尾用 :meth:`complete`，它会把按钮禁掉，免得收尾阶段那一下点击
    被后读的代码当成「本次被取消」。
    """

    def __init__(
        self,
        root,
        *,
        title: str = "Praat AI",
        message: str = "正在加载模型…",
        on_stop=None,
        stop_label: str = "停止",
    ):
        import tkinter as tk

        self._tk = tk
        self.root = root
        self.shown_at = time.monotonic()
        self.on_stop = on_stop
        self.stop_label = stop_label
        self.stop_button = None
        self._stop_requested = False
        self._completed = False
        self.theme = ui_theme.Theme(root)
        self.window = tk.Toplevel(root)
        self.window.title(title)
        self.window.resizable(False, False)
        # 简约：默认不给中途关闭；有「停止」这一档时，关窗键就是点停止（见 _on_close）。
        self.window.protocol("WM_DELETE_WINDOW", self._on_close)
        self.window.configure(background=self.theme.color("canvas"))
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass

        self.card = ui_widgets.Card(
            self.window, self.theme, padding=(16, 14, 16, 14), radius=12
        )
        self.card.pack(fill="both", expand=True)
        body = self.card.body

        head = tk.Frame(body, background=self.theme.color("surface"))
        head.pack(fill="x")
        self.dot = tk.Canvas(
            head,
            width=self.theme.pad(10),
            height=self.theme.pad(10),
            bd=0,
            highlightthickness=0,
            background=self.theme.color("surface"),
        )
        self.dot.pack(side="left", padx=(0, self.theme.pad(8)))
        self.message = tk.StringVar(value=message)
        self.title_label = tk.Label(
            head,
            textvariable=self.message,
            anchor="w",
            bd=0,
            highlightthickness=0,
            background=self.theme.color("surface"),
            foreground=self.theme.color("text"),
            font=self.theme.font("body_bold"),
        )
        self.title_label.pack(side="left", fill="x", expand=True)

        row = tk.Frame(body, background=self.theme.color("surface"))
        row.pack(fill="x", pady=(self.theme.pad(10), 0))
        self.bar = ui_widgets.RoundedProgressBar(
            row, self.theme, height=8, radius=4, length=240
        )
        self.bar.pack(side="left", fill="x", expand=True)
        self.percent_text = tk.StringVar(value="0%")
        self.percent = tk.Label(
            row,
            textvariable=self.percent_text,
            width=5,
            anchor="e",
            bd=0,
            highlightthickness=0,
            background=self.theme.color("surface"),
            foreground=self.theme.color("textMuted"),
            font=self.theme.font("data"),
        )
        self.percent.pack(side="left", padx=(self.theme.pad(8), 0))

        # 只有要「停止」这一档时才多一行按钮；不传 on_stop 的小窗与以前逐像素一致。
        self.footer = None
        if on_stop is not None:
            self.footer = tk.Frame(body, background=self.theme.color("surface"))
            self.footer.pack(fill="x", pady=(self.theme.pad(12), 0))
            self.stop_button = ui_widgets.RoundedButton(
                self.footer,
                self.theme,
                stop_label,
                self._request_stop,
                kind="outlined",
                height=30,
            )
            self.stop_button.pack(side="right")

        self.theme.add_listener(self._apply_theme)
        self._draw_dot()
        self._place_near_parent()
        try:
            self.window.update_idletasks()
        except Exception:   # noqa: BLE001
            pass

    # ------------------------------------------------------------------ 绘制

    def _draw_dot(self, *, done: bool = False) -> None:
        theme = self.theme
        self.dot.configure(background=theme.color("surface"))
        self.dot.delete("dot")
        size = theme.pad(10)
        color = theme.color("success" if done else "primary")
        self.dot.create_oval(0, 0, size, size, fill=color, outline="")

    def _apply_theme(self, theme=None) -> None:
        theme = theme or self.theme
        surface = theme.color("surface")
        self.window.configure(background=theme.color("canvas"))
        for widget in (self.title_label, self.percent, self.dot):
            widget.configure(background=surface)
        self.title_label.configure(foreground=theme.color("text"))
        self.percent.configure(foreground=theme.color("textMuted"))
        for frame in (self.card.body, self.title_label.master, self.bar.master, self.footer):
            if frame is not None:
                frame.configure(background=surface)
        self._draw_dot(done=float(self.bar.cget("value")) >= 100.0)

    def _place_near_parent(self) -> None:
        """显示在对话窗口中间偏上一点（不遮住输入框）。"""

        try:
            self.window.update_idletasks()
            parent = self.root.winfo_rootx(), self.root.winfo_rooty()
            size = self.root.winfo_width(), self.root.winfo_height()
            width = self.window.winfo_width()
            height = self.window.winfo_height()
            x = parent[0] + max(0, (size[0] - width) // 2)
            y = parent[1] + max(0, (size[1] - height) // 3)
            self.window.geometry(f"+{x}+{y}")
        except Exception:   # noqa: BLE001 - 位置算不出来就用系统默认位置
            pass

    # ------------------------------------------------------------------ 更新

    def update(self, fraction: float, message: str | None = None) -> None:
        """更新进度条（0–1）和文案。"""

        try:
            value = max(0.0, min(1.0, float(fraction))) * 100.0
            self.bar.configure(value=value)
            self.percent_text.set(f"{value:.0f}%")
            if message:
                self.message.set(message)
            self._draw_dot(done=value >= 100.0)
            self.window.update_idletasks()
        except Exception:   # noqa: BLE001 - 窗口已经被关掉时忽略
            pass

    def complete(self, message: str | None = None) -> None:
        """分析已经结束：进度推到 100%，并让「停止」按钮不再可点。

        收尾还有最短显示时间，用户可能在这一小段里再点一下停止；那一下不能被当成
        「本次被取消」——取消状态由 :func:`analysis_progress.run_with_stop` 在分析结束的
        那一刻记下，这里只负责别让按钮继续接受点击。
        """

        self.update(1.0, message)
        self._completed = True
        try:
            if self.stop_button is not None:
                self.stop_button.configure(state="disabled")
        except Exception:   # noqa: BLE001 - 窗口已经关掉时忽略
            pass

    def _on_close(self) -> None:
        """关窗键：有「停止」这一档就等同点停止，否则维持「简约：不给中途关闭」。"""

        if self.on_stop is not None:
            self._request_stop()
    def close(self) -> None:
        try:
            self.theme.remove_listener(self._apply_theme)
        except Exception:   # noqa: BLE001
            pass
        try:
            self.window.destroy()
        except Exception:   # noqa: BLE001
            pass

    def remaining_minimum_seconds(self) -> float:
        """还要等多久才允许关闭（不够 MINIMUM_VISIBLE_SEC 就返回剩下的秒数）。"""

        elapsed = time.monotonic() - self.shown_at
        return max(0.0, MINIMUM_VISIBLE_SEC - elapsed)

    # ------------------------------------------------------------------ 停止

    def _request_stop(self) -> None:
        """点「停止」：回调只发一次，按钮立刻变成不可再点的「正在停止…」。

        真正的中止由回调负责（分析阶段传的是 ``cancel.set``）：这里只表达「用户要停」，
        不假装已经停下——已完成的测量结果由上层照常交付。
        """

        if self._completed:
            return      # 分析已经结束：结束之后的那一下点击不算「要求停止」
        if self._stop_requested:
            return
        self._stop_requested = True
        try:
            if self.stop_button is not None:
                self.stop_button.configure(state="disabled", text="正在停止…")
            self.message.set("正在停止…")
            self.window.update_idletasks()
        except Exception:   # noqa: BLE001 - 窗口已经关掉时忽略
            pass
        if self.on_stop is not None:
            self.on_stop()

    @property
    def stop_requested(self) -> bool:
        """用户是否点过「停止」（用于测试和上层判断文案）。"""

        return self._stop_requested
