"""通过 Praat sendpraat 协议写消息文件并发送 WM_APP，不主动激活窗口。Message.txt 全局共享，必须串行投递；超时或取消后清空待处理消息。consume_statement 先将消息换为空操作，避免排队通知重复执行脚本。完全信任标记用于脚本写文件，这不是文件系统沙箱。命令行 --send 通道可能恢复或激活 Praat 子窗口。"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


WINDOW_MODE = "window"
ARGV_MODE = "argv"
SEND_MODE_ENV = "PRAAT_AI_SEND_MODE"

#: ``ctypes.wintypes`` 没有这个类型别名，自己按 Win32 原型补一个
#: （``BOOL CALLBACK EnumWindowsProc (HWND, LPARAM)``）。
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

#: Praat 的接收端在 ``motifEmulator.cpp`` 里同时处理 ``WM_USER`` 和 ``WM_APP``。
WM_APP = 0x8000
WM_USER = 0x0400

#: ``ShowWindow`` 的常用取值（验证脚本把窗口收进任务栏时用）。
SW_MINIMIZE = 6

#: Praat 给所有顶层窗口注册的窗口类都长这样：``PraatChildWindow1 Praat`` /
#: ``PraatShell1 Praat``（数字是 PRAAT_WINDOW_CLASS_NUMBER）。用前缀匹配，
#: 免得跟着 PRAAT_WINDOW_CLASS_NUMBER 或程序名的变化一起改。
PRAAT_WINDOW_CLASS_PREFIX = "praat"

#: 对象窗口一直在，拿它当投递目标最稳（关掉编辑器、Info 窗口都不影响）。
PRAAT_OBJECTS_TITLE = "Praat Objects"

#: 消息文件开头的完全信任标记；``praat_executeScript_noGUI()`` 靠这一行判断。
FULL_TRUST_MARKER = "\n# --FULL-TRUST\n"

#: 自消费之后写回消息文件的内容（除了注释什么都不做）。
CONSUMED_NOTE = "# praat-ai: 这条消息已经消费过"


@dataclass(frozen=True)
class WindowInfo:
    """一个顶层窗口（只用到投递和验证需要的字段）。"""

    handle: int
    process_id: int
    class_name: str
    title: str
    visible: bool
    minimized: bool

    @property
    def is_praat_window(self) -> bool:
        return self.class_name.casefold().startswith(PRAAT_WINDOW_CLASS_PREFIX)

    @property
    def state(self) -> tuple[bool, bool]:
        """窗口的可见/最小化状态；窗口被激活或还原时这个会变。"""

        return (self.visible, self.minimized)


def send_mode() -> str:
    """投递方式：默认 ``window``（自己发 WM_APP，不动窗口）。

    - ``window``：本模块的投递方式，不激活任何 Praat 窗口（默认）。
    - ``argv``：退回 ``Praat.exe --send``（会激活一个 Praat 子窗口，只用于排障）。

    用环境变量 ``PRAAT_AI_SEND_MODE`` 切换，取值 ``window`` / ``argv``。
    非 Windows 上没有 ``%APPDATA%\\Praat\\Message.txt`` + ``WM_APP`` 这套东西，
    直接走 ``argv``（macOS/Linux 上 ``--send`` 靠 pid 文件发信号，本来也不激活窗口）。
    """

    if os.name != "nt":
        return ARGV_MODE
    value = os.getenv(SEND_MODE_ENV, "").strip().casefold()
    if value in {"argv", "send", "praat.exe", "sendpraat"}:
        return ARGV_MODE
    return WINDOW_MODE


def message_file_path() -> Path | None:
    """Praat 读消息的文件：Windows 上是 ``%APPDATA%\\Praat\\Message.txt``。

    与 ``melder_files.cpp`` 的 ``Melder_preferencesFolder7()`` 对应
    （``Melder_getHomeDir() + "\\AppData\\Roaming\\" + 程序名``）。查不到位置时
    返回 ``None``，调用方应该退回 ``--send``。
    """

    if os.name != "nt":
        return None
    appdata = os.getenv("APPDATA", "").strip()
    if appdata:
        return Path(appdata) / "Praat" / "Message.txt"
    home = os.getenv("USERPROFILE", "").strip()
    if home:
        return Path(home) / "AppData" / "Roaming" / "Praat" / "Message.txt"
    return None


def _quote(value: str) -> str:
    """Praat 字符串里的双引号写成两个双引号，反斜杠一律换成斜杠。"""

    return value.replace("\\", "/").replace('"', '""')


def _handle_value(handle) -> int:
    """窗口句柄统一成整数：回调里拿到的是 ``int``，API 返回值是 ``c_void_p``。"""

    if isinstance(handle, int):
        return handle
    value = getattr(handle, "value", None)
    return int(value or 0)


def consume_statement(message_path: Path | None = None) -> str:
    """返回「把消息文件换成空操作」的那几句 Praat 语句（消息自消费）。

    为什么必须这么做：``sys/praat.cpp`` 的 ``cb_userMessage()`` 每收到一条
    ``WM_APP`` 都会 ``MelderFile_exists`` + 重新 ``praat_executeScript_noGUI()``
    那个 ``Message.txt``，而 ``praat_executeScript_noGUI()``（``sys/praat_script.cpp``）
    读完就执行、**不删**消息文件。于是排队中的每条 ``WM_APP`` 都会拿*当前*的
    文件内容跑一遍：上一条超时还排在队里时，它醒来执行的就是用户新发的指令，
    接着那条指令自己的 ``WM_APP`` 又执行一次——「删除」执行两遍就是这么来的。

    投递的消息**第一句**就把文件覆写成空操作，队列里剩下的消息醒来只会读到这个
    占位内容。用 ``writeFileLine`` 覆写（会新建/截断），再用 ``appendFileLine``
    补两行，正好和 :func:`noop_message()` 的内容一致；文件位置在脚本目录之外，
    所以依赖消息里那行 ``# --FULL-TRUST``。

    找不到消息文件位置（非 Windows）时返回空字符串，调用方原样投递。
    """

    path = message_path or message_file_path()
    if path is None:
        return ""
    target = _quote(str(path))
    return (
        f'writeFileLine: "{target}", ""\n'
        f'appendFileLine: "{target}", "# --FULL-TRUST"\n'
        f'appendFileLine: "{target}", "{CONSUMED_NOTE}"\n'
    )


def build_message(directory: Path, script: Path, *, consume: bool = True) -> str:
    """拼出 ``--send`` 会写进``Message.txt`` 的那段文本。

    真实内容长这样（实测 ``%APPDATA%\\Praat\\Message.txt``）::

        <空行>
        # --FULL-TRUST
        writeFileLine: "…/Message.txt", ""
        appendFileLine: "…/Message.txt", "# --FULL-TRUST"
        appendFileLine: "…/Message.txt", "# praat-ai: 这条消息已经消费过"
        setWorkingDirectory: "D:/praat/praat-simplified-chinese/ai"
        runScript: "D:/…/ai/runtime/chat_command.praat"

    前三行是 :func:`consume_statement()` 加的自消费语句（见那里的说明）；
    ``consume=False`` 时和 ``--send`` 自己写的内容一致，只用于对照和排障。

    Praat 读文件时会先 ``Melder_killReturns_inplace()`` 去掉 ``\\r``，所以
    ``\\n`` / ``\\r\\n`` 都行，这里统一写 ``\\n``。
    """

    preamble = consume_statement() if consume else ""
    return (
        FULL_TRUST_MARKER
        + preamble
        + f'setWorkingDirectory: "{_quote(str(directory))}"\n'
        + f'runScript: "{_quote(str(script))}"\n'
    )


def noop_message() -> str:
    """超时之后用来顶掉待执行消息的空脚本（只有注释，什么都不做）。"""

    return FULL_TRUST_MARKER + "# praat-ai: 上一条指令已超时取消，这条消息不做事\n"


def _user32():
    if os.name != "nt":
        return None
    try:
        user32 = ctypes.windll.user32
    except OSError:   # pragma: no cover - 只在异常环境里发生
        return None
    user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND,
        ctypes.POINTER(wintypes.DWORD),
    ]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.IsIconic.argtypes = [wintypes.HWND]
    user32.IsIconic.restype = wintypes.BOOL
    user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.ShowWindow.restype = wintypes.BOOL
    user32.PostMessageW.argtypes = [
        wintypes.HWND,
        ctypes.c_uint,
        wintypes.WPARAM,
        wintypes.LPARAM,
    ]
    user32.PostMessageW.restype = wintypes.BOOL
    user32.GetForegroundWindow.restype = wintypes.HWND
    return user32


def list_windows() -> list[WindowInfo]:
    """按 z 序（最上面的在前）列出所有顶层窗口。非 Windows 返回空表。"""

    user32 = _user32()
    if user32 is None:
        return []
    windows: list[WindowInfo] = []

    @WNDENUMPROC
    def visit(handle, _param):   # type: ignore[no-untyped-def]
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        class_buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, class_buffer, 256)
        length = user32.GetWindowTextLengthW(handle)
        title_buffer = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(handle, title_buffer, length + 1)
        windows.append(
            WindowInfo(
                handle=_handle_value(handle),
                process_id=int(owner.value),
                class_name=class_buffer.value,
                title=title_buffer.value,
                visible=bool(user32.IsWindowVisible(handle)),
                minimized=bool(user32.IsIconic(handle)),
            )
        )
        return True

    user32.EnumWindows(visit, 0)
    return windows


def praat_windows() -> list[WindowInfo]:
    """所有 Praat 的顶层窗口（按 z 序）。"""

    return [window for window in list_windows() if window.is_praat_window]


def choose_window(
    windows: list[WindowInfo], process_id: int | None = None
) -> WindowInfo | None:
    """挑一个投递目标：默认挑窗口号最大的那个 Praat（和 ``--send`` 的目标一致）。

    同一个进程里优先用对象窗口：它一直开着，不会因为用户关掉编辑器或 Info
    窗口而消失。
    """

    if not windows:
        return None
    if process_id is None:
        process_id = max(window.process_id for window in windows)
    candidates = [window for window in windows if window.process_id == process_id]
    if not candidates:
        return None
    for window in candidates:
        if window.title == PRAAT_OBJECTS_TITLE:
            return window
    return candidates[0]


def deliver(
    directory: Path,
    script: Path,
    *,
    process_id: int | None = None,
) -> tuple[bool, str]:
    """将 script 交给运行中的 Praat，返回投递是否成功及说明；调用方通过 chat_state.txt 等待执行结果。"""

    user32 = _user32()
    if user32 is None:
        return False, "当前系统不支持这种投递方式（非 Windows）。"
    path = message_file_path()
    if path is None:
        return False, "找不到 Praat 的消息文件位置（%APPDATA% 不可用）。"
    window = choose_window(praat_windows(), process_id)
    if window is None:
        return False, "没有找到正在运行的 Praat 窗口，请先打开 Praat。"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            build_message(directory, script), encoding="utf-8", newline=""
        )
    except OSError as error:
        return False, f"无法写入 Praat 消息文件（{path}）：{error}"
    if not user32.PostMessageW(window.handle, WM_APP, 0, 0):
        return False, "给 Praat 发消息失败（窗口可能刚被关掉），请重试一次。"
    return True, ""


def command_lock():
    """Hold across delivery AND acknowledgement, shared by Anki and AI."""
    from .service_lock import service_transition_lock
    path = message_file_path()
    directory = path.parent if path else Path(__file__).resolve().parents[1] / 'runtime'
    return service_transition_lock(directory / 'aipraat-command.lock', timeout=40)


def cancel_pending(directory: Path | None = None) -> bool:
    """超时之后把消息文件换成空脚本，免得它被 Praat 稍后当成新指令执行。

    超时意味着 ``WM_APP`` 还排在 Praat 的消息队列里；如果不管它，前端下一条
    指令会覆盖 ``Message.txt``，那条老消息醒来时执行的就成了**别人的脚本**。
    """

    path = message_file_path()
    if path is None or not path.is_file():
        return False
    try:
        path.write_text(noop_message(), encoding="utf-8", newline="")
    except OSError:
        return False
    return True


def foreground_window_title() -> str:
    """当前前台窗口的标题（验证脚本用来确认前端没有被 Praat 抢走焦点）。"""

    user32 = _user32()
    if user32 is None:
        return ""
    handle = user32.GetForegroundWindow()
    if not handle:
        return ""
    length = user32.GetWindowTextLengthW(handle)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(handle, buffer, length + 1)
    return buffer.value


def window_state_map() -> dict[tuple[int, str], tuple[bool, bool]]:
    """``(进程号, 标题) -> (可见, 最小化)``，用来前后对比窗口有没有被动过。"""

    return {
        (window.process_id, window.title): window.state
        for window in praat_windows()
    }


def minimize_window(handle: int) -> bool:
    """把窗口收进任务栏；验证脚本用它复现用户看到的那种「Praat 窗口都最小化」。"""

    user32 = _user32()
    if user32 is None:
        return False
    return bool(user32.ShowWindow(handle, SW_MINIMIZE))
