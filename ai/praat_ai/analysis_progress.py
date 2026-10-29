"""分析进度与停止入口。worker 线程执行分析，进度经 root.after 回主线程。停止或关窗置位取消事件；已完成测量按调用方策略保留。无 Tk 或禁用 GUI 时同步执行。Praat 菜单使用原生进度窗。"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

DEFAULT_TITLE = "AI 纠音 · 分析"
DEFAULT_MESSAGE = "正在做音素对齐与声学提取…"
DEFAULT_STOP_LABEL = "停止分析"


def _open_window(cancel: threading.Event, *, title: str, message: str, stop_label: str):
    """建一个真实 Tk 小窗；拿不到 Tk 时抛异常，由 :func:`run_with_stop` 退回同步执行。"""

    import tkinter as tk

    from .progress_popup import MiniProgress

    root = tk.Tk()
    root.withdraw()
    try:
        window = MiniProgress(
            root, title=title, message=message, on_stop=cancel.set, stop_label=stop_label
        )
    except Exception:
        root.destroy()
        raise
    return root, window


def run_with_stop(
    work: Callable[[threading.Event, Callable[[float, str], None] | None], Any],
    *,
    title: str = DEFAULT_TITLE,
    message: str = DEFAULT_MESSAGE,
    stop_label: str = DEFAULT_STOP_LABEL,
    enabled: bool = True,
    open_window: Callable[..., tuple[Any, Any]] | None = None,
) -> tuple[Any, bool]:
    """运行 work(cancel, report)，返回 (outputs, cancelled)。禁用 GUI 或无法创建窗口时同步执行，report 为 None。"""

    cancel = threading.Event()
    if not enabled:
        return work(cancel, None), False
    opener = open_window or _open_window
    try:
        root, window = opener(cancel, title=title, message=message, stop_label=stop_label)
    except Exception:   # noqa: BLE001 - 没有 GUI 就退回同步执行
        return work(cancel, None), False

    box: dict[str, Any] = {}

    def report(fraction: float, text: str) -> None:
        try:
            root.after(0, lambda: window.update(fraction, text))
        except Exception:   # noqa: BLE001 - 窗口已关闭时忽略这一帧
            pass

    def runner() -> None:
        try:
            box["outputs"] = work(cancel, report)
        except BaseException as error:   # noqa: BLE001 - 连取消异常也带回主线程由调用方决定怎么报
            box["error"] = error
        finally:
            # 取消状态要在**分析结束的那一刻**记下：收尾还有最短显示时间，用户可能在那之后
            # 又点一下「停止」（按钮已被 complete 禁掉），那一下不能把已完成的分析说成已取消。
            box["cancelled"] = cancel.is_set()
            try:
                root.after(0, finish)
            except Exception:   # noqa: BLE001 - 主循环已经退出时直接收尾
                teardown()

    def finish() -> None:
        message = "已取消" if box.get("cancelled") else None
        complete = getattr(window, "complete", None)
        if callable(complete):
            complete(message)      # 推到 100% + 禁掉「停止」（MiniProgress 的收尾入口）
        else:
            window.update(1.0, message)
        try:
            remaining = max(0.0, float(window.remaining_minimum_seconds()))
            root.after(int(remaining * 1000) + 20, teardown)
        except Exception:   # noqa: BLE001
            teardown()

    def teardown() -> None:
        try:
            window.close()
        finally:
            try:
                root.quit()
            except Exception:   # noqa: BLE001
                root.destroy()

    threading.Thread(target=runner, name="praat-ai-analysis", daemon=True).start()
    try:
        root.mainloop()
    finally:
        try:
            window.close()
        except Exception:   # noqa: BLE001
            pass
        try:
            root.destroy()
        except Exception:   # noqa: BLE001
            pass

    if "error" in box:
        raise box["error"]
    return box.get("outputs"), bool(box.get("cancelled"))
