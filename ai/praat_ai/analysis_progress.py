"""分析阶段的进度小窗 + 「停止」按钮。

对话窗口里点「应用预设 / API 配置」用的是 :class:`progress_popup.MiniProgress`；而纠音**分析**
这一步此前只有标准输出里的进度，从提交到出报告之间既没有可视反馈，也不能中止。这里复用同一套
设计语言（卡片 + 圆角进度条 + 百分比 + 主题，见 guide.md §8.1），只多一个「停止」按钮：

- 分析跑在 worker 线程，进度回调经 ``root.after`` 回主线程刷新（Tk 不是线程安全的）；
- 点「停止」置位 ``cancel``；``run_tutor`` 已把取消贯穿到分析/对齐层（阶段 2/4），
  测量已经完成时它仍会写出报告、只是不再生成 AI 解释——所以这里只如实回报「已取消」，
  不谎称「什么也没做」；
- 关窗键在分析窗里等同点「停止」（没传 ``on_stop`` 的小窗仍是「不给中途关闭」）；
- 拿不到 Tk（没有 tkinter / 没有显示 / ``--no-gui``）时**退回同步执行**，行为与没有这一层时一致。

Praat 菜单那一档（``control.run_analysis``）走的是 Praat 自带进度窗（``PRAAT_PROGRESS``），
窗口属于 Praat，那里没有停止按钮，见 guide.md §8.1 的说明。
"""

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
    """在「简约小窗 + 停止按钮」里跑 ``work(cancel, report)``，返回 ``(outputs, cancelled)``。

    ``enabled=False``（``--no-gui``）或拿不到 GUI 时不建窗口，直接同步执行，``report`` 传 None：
    这样命令行/无人值守的行为与以前逐字节一致。
    """

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
