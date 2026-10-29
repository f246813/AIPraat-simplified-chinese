"""界面主题：对齐 TW-Elements（Tailwind + MDB，MIT）的设计令牌 + 跟随系统深浅色。"""

from __future__ import annotations

from typing import Callable

#: 浅色令牌。颜色取自 TW-Elements 构建产物里实测的值（primary #3B71CA 等）。
LIGHT: dict[str, str] = {
    "canvas": "#F5F5F5",
    "surface": "#FFFFFF",
    "surfaceAlt": "#FBFBFB",
    "border": "#E0E0E0",
    "divider": "#EEEEEE",
    "text": "#212529",
    "textMuted": "#6C757D",
    "primary": "#3B71CA",
    "primaryHover": "#3567B5",
    "primarySoft": "#E8F0FE",
    "onPrimary": "#FFFFFF",
    "success": "#14A44D",
    "successSoft": "#E6F4EA",
    "successText": "#0B6B33",
    "danger": "#DC4C64",
    "dangerSoft": "#FCE8EB",
    "warning": "#E4A11B",
    "warningText": "#9A6E00",
    "info": "#54B4D3",
    "codeBg": "#F8F9FA",
    "codeText": "#212529",
    "chipBg": "#F1F3F5",
    "shadowRing": "#E9ECEF",
}

#: 深色令牌：语义同名，底色换成 Tailwind neutral 深色，主色提亮以便在深底上看清。
DARK: dict[str, str] = {
    "canvas": "#121212",
    "surface": "#1E1E1E",
    "surfaceAlt": "#262626",
    "border": "#3A3A3A",
    "divider": "#2E2E2E",
    "text": "#E9ECEF",
    "textMuted": "#9AA0A6",
    "primary": "#6EA8FE",
    "primaryHover": "#8AB8FF",
    "primarySoft": "#22304A",
    "onPrimary": "#10151C",
    "success": "#3FB950",
    "successSoft": "#12261A",
    "successText": "#4ED164",
    "danger": "#F85149",
    "dangerSoft": "#2A1416",
    "warning": "#E3B341",
    "warningText": "#E3B341",
    "info": "#58C4DD",
    "codeBg": "#1B1B1B",
    "codeText": "#E9ECEF",
    "chipBg": "#2A2A2A",
    "shadowRing": "#2E2E2E",
}

#: 令牌名（两套必须完全相同，单测会对比）。
TOKEN_NAMES: tuple[str, ...] = tuple(LIGHT)

#: Windows 里「应用用浅色还是深色」这个开关的位置。
APPS_USE_LIGHT_KEY = (
    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
    "AppsUseLightTheme",
)

#: 字号（point）。正文/说明用微软雅黑，数据用等宽字体。
FONT_ROLES: dict[str, tuple[int, str]] = {
    "title": (11, "bold"),
    "heading": (11, "bold"),
    "body": (10, "normal"),
    "body_bold": (10, "bold"),
    "small": (9, "normal"),
    "small_bold": (9, "bold"),
    "chip": (9, "bold"),
    "data": (10, "normal"),
    "data_bold": (10, "bold"),
}

#: 优先字体，找不到就往后回落（本机有微软雅黑和 Consolas，没有 Roboto）。
UI_FAMILIES = ("Microsoft YaHei UI", "Segoe UI", "TkDefaultFont")
MONO_FAMILIES = ("Consolas", "Cascadia Mono", "Courier New", "TkFixedFont")


def system_is_dark() -> bool:
    """系统是不是深色（Windows ``AppsUseLightTheme == 0``）。

    非 Windows、键缺失、读失败都返回 ``False``（按浅色）——界面亮着总比看不清强。
    """

    try:
        import winreg

        path, name = APPS_USE_LIGHT_KEY
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except (ImportError, OSError, ValueError):
        return False
    try:
        return int(value) == 0
    except (TypeError, ValueError):
        return False


def contrast_ratio(foreground: str, background: str) -> float:
    """两色的对比度（WCAG 公式，1–21）。给单测守着「深色下也看得清」。"""

    def channel(value: str) -> float:
        text = value.lstrip("#")
        if len(text) == 3:
            text = "".join(item * 2 for item in text)
        if len(text) != 6:
            raise ValueError(f"不是合法的十六进制颜色：{value!r}")
        parts = [int(text[index : index + 2], 16) / 255.0 for index in (0, 2, 4)]
        converted = [
            item / 12.92 if item <= 0.03928 else ((item + 0.055) / 1.055) ** 2.4
            for item in parts
        ]
        return (
            0.2126 * converted[0] + 0.7152 * converted[1] + 0.0722 * converted[2]
        )

    first = channel(foreground)
    second = channel(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)


def scale_factor(window) -> float:
    """界面缩放：以 96 DPI 为 1.0，夹在 1.0–2.0 之间。

    Tk 的**字号**用 point，本来就会跟着 DPI 走；只有 Canvas 自绘的尺寸（圆角半径、
    内边距、进度条高度）需要我们手动乘这个系数。
    """

    try:
        per_inch = float(window.winfo_fpixels("1i"))
    except Exception:   # noqa: BLE001 - 拿不到就当 96 DPI
        return 1.0
    if per_inch <= 0:
        return 1.0
    return max(1.0, min(2.0, per_inch / 96.0))


class Theme:
    """当前主题：令牌 + 缩放 + 字体；``add_listener`` 的东西在换主题时被通知。

    控件在 ``__init__`` 里 ``add_listener(self.apply_theme)``，窗口级的东西（ttk
    style、文本 tag）由窗口自己注册一个回调，这样系统一换深色就整体重刷。
    """

    def __init__(self, root, *, dark: bool | None = None, scale: float | None = None):
        self.root = root
        self.dark = system_is_dark() if dark is None else bool(dark)
        self.scale = scale_factor(root) if scale is None else float(scale)
        self._families: dict[str, str] | None = None
        self._listeners: list[Callable[["Theme"], None]] = []

    # ---------------------------------------------------------------- 令牌

    @property
    def tokens(self) -> dict[str, str]:
        return DARK if self.dark else LIGHT

    def color(self, name: str) -> str:
        try:
            return self.tokens[name]
        except KeyError as error:
            raise KeyError(f"主题里没有这个令牌：{name}") from error

    def pad(self, value: int) -> int:
        """自绘尺寸（与 DPI 有关）。字号不要用这个，用 point 让 Tk 自己缩放。"""

        return max(1, int(round(value * self.scale)))

    def radius(self, value: int) -> int:
        return max(0, int(round(value * self.scale)))

    # ---------------------------------------------------------------- 字体

    def _resolve_families(self) -> dict[str, str]:
        if self._families is not None:
            return self._families
        available: set[str] = set()
        try:
            import tkinter.font as tkfont

            available = set(tkfont.families(self.root))
        except Exception:   # noqa: BLE001 - 拿不到字体列表就走默认
            available = set()

        def pick(candidates: tuple[str, ...]) -> str:
            for name in candidates:
                if name in available:
                    return name
            return candidates[-1]

        self._families = {
            "ui": pick(UI_FAMILIES),
            "mono": pick(MONO_FAMILIES),
        }
        return self._families

    def font(self, role: str = "body") -> tuple:
        """``("Microsoft YaHei UI", 10, "bold")`` 这样的元组，直接喂给 Tk。"""

        size, weight = FONT_ROLES.get(role, FONT_ROLES["body"])
        family = self._resolve_families()["mono" if role.startswith("data") else "ui"]
        return (family, size, weight) if weight != "normal" else (family, size)

    def family(self, kind: str = "ui") -> str:
        return self._resolve_families()[kind]

    # ------------------------------------------------------------ 监听/刷新

    def add_listener(self, callback: Callable[["Theme"], None]) -> None:
        self._listeners.append(callback)
        try:
            callback(self)
        except Exception:   # noqa: BLE001 - 初次上色失败不该拦住建窗口
            pass

    def remove_listener(self, callback: Callable[["Theme"], None]) -> None:
        try:
            self._listeners.remove(callback)
        except ValueError:
            pass

    def listener_count(self) -> int:
        """还挂在主题上的控件数（自绘控件销毁后必须掉回 0，单测用它守着）。"""

        return len(self._listeners)

    def set_dark(self, dark: bool) -> bool:
        """切换深浅色；真变了才通知（返回是否真的变了）。"""

        dark = bool(dark)
        if dark == self.dark:
            return False
        self.dark = dark
        self.refresh()
        return True

    def refresh(self) -> None:
        """重新给所有注册过的控件上色；已经销毁的自己会从名单里掉出去。"""

        for callback in list(self._listeners):
            try:
                callback(self)
            except Exception:   # noqa: BLE001 - 控件已经没了
                self.remove_listener(callback)


class ThemeWatcher:
    """盯着系统深色开关：变了就回调（默认 5 秒看一次）。

    只做 ``window.after`` + ``after_cancel``，不碰别的线程；``stop()`` 之后不会再排
    回调——窗口关掉之后还剩一个定时回调去碰 Tk，会直接报错。
    """

    def __init__(
        self,
        window,
        on_change: Callable[[bool], None],
        *,
        interval_ms: int = 5000,
        probe: Callable[[], bool] = system_is_dark,
    ) -> None:
        self.window = window
        self.on_change = on_change
        self.interval_ms = max(1000, int(interval_ms))
        self.probe = probe
        self.current = bool(probe())
        self._after_id: str | None = None
        self.stopped = False

    def start(self) -> None:
        if self.stopped or self._after_id is not None:
            return
        try:
            self._after_id = self.window.after(self.interval_ms, self._tick)
        except Exception:   # noqa: BLE001 - 窗口已经没了
            self.stopped = True

    def stop(self) -> None:
        self.stopped = True
        if self._after_id is None:
            return
        try:
            self.window.after_cancel(self._after_id)
        except Exception:   # noqa: BLE001
            pass
        self._after_id = None

    def _tick(self) -> None:
        self._after_id = None
        if self.stopped:
            return
        try:
            dark = bool(self.probe())
        except Exception:   # noqa: BLE001 - 探测失败就当下次再说
            dark = self.current
        if dark != self.current:
            self.current = dark
            try:
                self.on_change(dark)
            except Exception:   # noqa: BLE001 - 回调失败不影响继续盯
                pass
        self.start()
