"""自绘的圆角控件：把 TW-Elements 的组件形态画在 Tk 的 Canvas 上。"""

from __future__ import annotations

from math import ceil, floor
import tkinter as tk

from PIL import Image, ImageDraw, ImageTk


_ROUNDED_SUPERSAMPLE = 4


# --------------------------------------------------------------------------- #
# 基础：圆角形状
# --------------------------------------------------------------------------- #


def _tk_rgb(canvas: tk.Canvas, color: str) -> tuple[int, int, int, int]:
    red, green, blue = canvas.winfo_rgb(color)
    return red // 257, green // 257, blue // 257, 255


def _draw_rounded_image(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: int,
    fill: str | None,
    *,
    outline: str | None = None,
    width: int = 1,
    tags: tuple[str, ...] = (),
) -> list[int]:
    """绘制坐标范围 ``[x1, x2) × [y1, y2)``，四边使用相同边界规则。"""

    left, top = floor(x1), floor(y1)
    right, bottom = ceil(x2), ceil(y2)
    image_width, image_height = right - left, bottom - top
    if image_width <= 0 or image_height <= 0 or (fill is None and not outline):
        return []

    scale = _ROUNDED_SUPERSAMPLE
    pixel_width, pixel_height = image_width * scale, image_height * scale
    radius_px = min(int(radius), image_width // 2, image_height // 2) * scale
    raster = Image.new("RGBA", (pixel_width, pixel_height), (0, 0, 0, 0))
    ImageDraw.Draw(raster).rounded_rectangle(
        (0, 0, pixel_width - 1, pixel_height - 1),
        radius=max(0, radius_px),
        fill=_tk_rgb(canvas, fill) if fill is not None else None,
        outline=_tk_rgb(canvas, outline) if outline else None,
        width=max(1, int(width)) * scale if outline else 1,
    )
    raster = raster.resize(
        (image_width, image_height), Image.Resampling.LANCZOS
    )
    photo = ImageTk.PhotoImage(raster, master=canvas)
    item = canvas.create_image(left, top, image=photo, anchor="nw", tags=tags)

    # Tk keeps only the image name. Retain Python references for live Canvas items,
    # and discard stale ones after a widget's next redraw.
    photos = getattr(canvas, "_praat_rounded_photos", None)
    if photos is None:
        photos = {}
        canvas._praat_rounded_photos = photos
    for stale in tuple(photos):
        if not canvas.type(stale):
            del photos[stale]
    photos[item] = photo
    return [item]


def rounded_rect(
    canvas: tk.Canvas,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    radius: int,
    fill: str | None,
    *,
    outline: str | None = None,
    width: int = 1,
    tags: tuple[str, ...] = (),
) -> list[int]:
    """用 Pillow 高分辨率光栅统一绘制圆角填充和描边，文字仍由 Tk 绘制。"""

    return _draw_rounded_image(
        canvas,
        x1,
        y1,
        x2,
        y2,
        radius,
        fill,
        outline=outline if width > 0 else None,
        width=width,
        tags=tags,
    )


# --------------------------------------------------------------------------- #
# ttk 统一配色（原生控件也要跟着深浅色）
# --------------------------------------------------------------------------- #


def configure_ttk(style, theme) -> None:
    """把剩下的 ttk 控件（输入框、下拉框、滚动条、勾选框）刷成当前主题。

    基础主题用 ``clam``：``vista`` 主题的输入框/下拉框由系统主题引擎画，颜色根本
    改不动，深色模式下会留一片白底。
    """

    surface = theme.color("surface")
    text = theme.color("text")
    muted = theme.color("textMuted")
    border = theme.color("border")
    canvas_color = theme.color("canvas")
    primary = theme.color("primary")
    soft = theme.color("primarySoft")

    style.configure("TFrame", background=canvas_color)
    style.configure("TLabel", background=canvas_color, foreground=text, font=theme.font("body"))
    style.configure(
        "Card.TFrame", background=surface, borderwidth=0, relief="flat"
    )
    style.configure(
        "Card.TLabel", background=surface, foreground=text, font=theme.font("body")
    )
    style.configure(
        "Muted.TLabel", background=canvas_color, foreground=muted, font=theme.font("small")
    )
    style.configure(
        "CardMuted.TLabel", background=surface, foreground=muted, font=theme.font("small")
    )
    style.configure(
        "Title.TLabel", background=canvas_color, foreground=text, font=theme.font("title")
    )

    style.configure(
        "TEntry",
        fieldbackground=surface,
        foreground=text,
        insertcolor=text,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        padding=theme.pad(4),
    )
    style.map(
        "TEntry",
        fieldbackground=[("disabled", theme.color("chipBg"))],
        foreground=[("disabled", muted)],
        bordercolor=[("focus", primary)],
    )
    # FieldCard draws the rounded outline itself, so its ttk.Entry child must
    # not add clam's rectangular border on top of it.
    style.configure(
        "Field.TEntry",
        # ttk's native Entry window keeps these corner pixels outside its field
        # element; set the window background too so they match the shell.
        background=surface,
        fieldbackground=surface,
        bordercolor=surface,
        lightcolor=surface,
        darkcolor=surface,
        padding=theme.pad(4),
    )
    style.map(
        "Field.TEntry",
        bordercolor=[("focus", surface)],
        lightcolor=[("focus", surface)],
        darkcolor=[("focus", surface)],
    )
    style.configure(
        "TCombobox",
        fieldbackground=surface,
        background=surface,
        foreground=text,
        arrowcolor=text,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        selectbackground=soft,
        selectforeground=text,
        padding=theme.pad(3),
    )
    style.map(
        "TCombobox",
        fieldbackground=[("readonly", surface), ("disabled", theme.color("chipBg"))],
        foreground=[("disabled", muted)],
        background=[("active", surface)],
        bordercolor=[("focus", primary)],
    )
    style.configure(
        "TCheckbutton",
        background=surface,
        foreground=text,
        focuscolor=surface,
        indicatorcolor=surface,
        bordercolor=border,
        lightcolor=border,
        darkcolor=border,
        font=theme.font("body"),
    )
    style.map(
        "TCheckbutton",
        background=[("active", surface)],
        foreground=[("disabled", muted)],
        indicatorcolor=[("selected", primary), ("!selected", surface)],
    )
    style.configure(
        "Vertical.TScrollbar",
        background=theme.color("chipBg"),
        troughcolor=canvas_color,
        bordercolor=canvas_color,
        arrowcolor=muted,
        relief="flat",
    )
    style.map(
        "Vertical.TScrollbar",
        background=[("active", border), ("pressed", primary)],
    )


# --------------------------------------------------------------------------- #
# 控件
# --------------------------------------------------------------------------- #


class _Themed:
    """共同套路：把 ``apply_theme`` 注册进主题，销毁时摘掉。"""

    _theme = None

    def _bind_theme(self, theme) -> None:
        self._theme = theme
        theme.add_listener(self.apply_theme)
        self.bind("<Destroy>", self._unbind_theme, add="+")

    def _unbind_theme(self, event=None) -> None:   # noqa: ARG002 - Tk 回调
        if event is not None and getattr(event, "widget", None) is not self:
            return   # 子控件销毁也会冒泡到父控件
        if self._theme is not None:
            self._theme.remove_listener(self.apply_theme)
            self._theme = None

    def apply_theme(self, theme=None) -> None:   # 由子类实现
        raise NotImplementedError


class Card(_Themed, tk.Frame):
    """圆角卡片：背景画在一张 ``place`` 的 Canvas 上，内容放 :attr:`body`。

    高度由内容决定（Canvas 用 ``place`` 不参与尺寸计算），所以卡片能"包着"内容。
    """

    def __init__(
        self,
        parent,
        theme,
        *,
        padding: tuple[int, int, int, int] = (14, 12, 14, 12),
        radius: int = 10,
        surface: str = "surface",
        background: str = "canvas",
        **kwargs,
    ) -> None:
        super().__init__(parent, bd=0, highlightthickness=0, **kwargs)
        self._padding = padding
        self._radius = radius
        self._surface = surface
        self._background = background
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.body = tk.Frame(self, bd=0, highlightthickness=0)
        left, top, right, bottom = padding
        self.body.pack(
            fill="both",
            expand=True,
            padx=(theme.pad(left), theme.pad(right)),
            pady=(theme.pad(top), theme.pad(bottom)),
        )
        self.body.lift()   # 内容压在圆角背景之上（Canvas 是后画的背景层）
        self.bind("<Configure>", self._redraw)
        self._bind_theme(theme)

    def apply_theme(self, theme=None) -> None:
        theme = theme or self._theme
        if theme is None:
            return
        self.configure(background=theme.color(self._background))
        self.body.configure(background=theme.color(self._surface))
        self._redraw()

    def _redraw(self, _event=None) -> None:
        theme = self._theme
        if theme is None:
            return
        self.canvas.configure(background=theme.color(self._background))
        self.canvas.delete("card")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        rounded_rect(
            self.canvas,
            0,
            0,
            width,
            height,
            theme.radius(self._radius),
            theme.color(self._surface),
            outline=theme.color("shadowRing"),
            width=max(1, theme.pad(1)),
            tags=("card",),
        )


class RoundedButton(_Themed, tk.Canvas):
    """圆角按钮：``filled`` / ``outlined`` / ``text`` 三档，支持 hover/pressed/禁用。"""

    KINDS = ("filled", "outlined", "text")

    def __init__(
        self,
        parent,
        theme,
        text: str,
        command=None,
        *,
        kind: str = "filled",
        height: int = 32,
        width: int | None = None,
        radius: int = 6,
        font: str = "body_bold",
        padx: int = 14,
        background: str = "canvas",
        **kwargs,
    ) -> None:
        super().__init__(
            parent, bd=0, highlightthickness=0, takefocus=1, height=theme.pad(height), **kwargs
        )
        self._text = text
        self._command = command
        self._kind = kind if kind in self.KINDS else "filled"
        self._font_role = font
        self._padx = padx
        self._radius = radius
        self._height = height
        self._explicit_width = width
        self._background = background
        self._state = "normal"
        self._hover = False
        self._pressed = False
        self._focused = False
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<FocusIn>", self._on_focus_in)
        self.bind("<FocusOut>", self._on_focus_out)
        self.bind("<Return>", self._on_key)
        self.bind("<space>", self._on_key)
        self.bind("<Configure>", lambda _event: self._redraw())
        self._bind_theme(theme)

    # ------------------------------------------------------------ ttk 兼容层

    def cget(self, key):
        if key == "state":
            return self._state
        if key == "text":
            return self._text
        return super().cget(key)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)
        options: dict = {}
        if isinstance(cnf, dict):
            options.update(cnf)
        options.update(kwargs)
        local = {}
        for key in ("state", "text", "command", "kind", "background"):
            if key in options:
                local[key] = options.pop(key)
        if "width" in options:
            self._explicit_width = int(options.pop("width"))
        if "height" in options:
            self._height = int(options.pop("height"))
            if self._theme is not None:
                super().configure(height=self._theme.pad(self._height))
        if options:
            super().configure(**options)
        if "state" in local:
            self._state = "disabled" if str(local["state"]) == "disabled" else "normal"
            try:
                super().configure(cursor="arrow" if self._state == "disabled" else "hand2")
            except tk.TclError:
                pass
        if "text" in local:
            self._text = str(local["text"])
        if "command" in local:
            self._command = local["command"]
        if "kind" in local:
            self._kind = str(local["kind"])
        if "background" in local:
            self._background = str(local["background"])
        self._redraw()
        return None

    config = configure

    # ---------------------------------------------------------------- 主题

    def apply_theme(self, theme=None) -> None:
        theme = theme or self._theme
        if theme is None:
            return
        super().configure(background=theme.color(self._background))
        super().configure(height=theme.pad(self._height))
        if self._explicit_width is not None:
            super().configure(width=theme.pad(self._explicit_width))
        else:
            super().configure(width=self._text_width(theme) + 2 * theme.pad(self._padx))
        self._redraw()

    def _text_width(self, theme) -> int:
        try:
            import tkinter.font as tkfont

            return int(tkfont.Font(root=self, font=theme.font(self._font_role)).measure(self._text))
        except Exception:   # noqa: BLE001 - 量不出来就按字数估
            return max(24, len(self._text) * 9)

    # ---------------------------------------------------------------- 绘制

    def _palette(self, theme) -> tuple[str, str, str | None]:
        """返回 ``(底色, 文字色, 边线色)``。"""

        disabled = self._state == "disabled"
        if self._kind == "filled":
            if disabled:
                return theme.color("chipBg"), theme.color("textMuted"), None
            if self._pressed:
                return theme.color("primarySoft"), theme.color("primary"), None
            base = theme.color("primaryHover") if self._hover else theme.color("primary")
            return base, theme.color("onPrimary"), None
        if self._kind == "outlined":
            if disabled:
                return theme.color("surface"), theme.color("textMuted"), theme.color("border")
            if self._pressed:
                return theme.color("surfaceAlt"), theme.color("primary"), theme.color("primary")
            if self._hover:
                return theme.color("primarySoft"), theme.color("primaryHover"), theme.color("primaryHover")
            return theme.color("surface"), theme.color("primary"), theme.color("primary")
        # text：无底，hover 时给一层浅底
        if disabled:
            return theme.color(self._background), theme.color("textMuted"), None
        if self._pressed:
            return theme.color("surfaceAlt"), theme.color("primary"), None
        fill = theme.color("primarySoft") if self._hover else theme.color(self._background)
        return fill, theme.color("primary"), None

    def _redraw(self) -> None:
        theme = self._theme
        if theme is None:
            return
        self.delete("btn")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        fill, foreground, outline = self._palette(theme)
        radius = theme.radius(self._radius)
        line_width = max(1, theme.pad(1))
        inset = line_width if outline else 0
        if self._focused and self._state == "normal" and outline:
            # Outlined buttons already reserve an outer pixel. Draw the focus ring
            # there first so the normal border is painted on top and stays intact.
            rounded_rect(
                self,
                0,
                0,
                width,
                height,
                radius,
                None,
                outline=theme.color("primarySoft"),
                width=line_width,
                tags=("btn",),
            )
        rounded_rect(
            self,
            inset,
            inset,
            width - inset,
            height - inset,
            radius,
            fill,
            outline=outline,
            width=line_width,
            tags=("btn",),
        )
        if self._focused and self._state == "normal" and not outline:
            rounded_rect(
                self,
                0,
                0,
                width,
                height,
                radius,
                None,
                outline=theme.color("primarySoft"),
                width=line_width,
                tags=("btn",),
            )
        self.create_text(
            width // 2,
            height // 2,
            text=self._text,
            fill=foreground,
            font=theme.font(self._font_role),
            tags=("btn",),
        )

    # ---------------------------------------------------------------- 事件

    def _on_enter(self, _event=None) -> None:
        self._hover = True
        self._redraw()

    def _on_leave(self, _event=None) -> None:
        self._hover = False
        self._pressed = False
        self._redraw()

    def _on_press(self, _event=None) -> None:
        if self._state == "disabled":
            return
        self._pressed = True
        try:
            self.focus_set()
        except tk.TclError:
            pass
        self._redraw()

    def _on_release(self, event=None) -> None:
        if self._state == "disabled" or not self._pressed:
            self._pressed = False
            self._redraw()
            return
        self._pressed = False
        self._redraw()
        inside = True
        if event is not None:
            inside = 0 <= event.x <= self.winfo_width() and 0 <= event.y <= self.winfo_height()
        if inside and self._command is not None:
            self._command()

    def _on_key(self, _event=None) -> str:
        if self._state != "disabled" and self._command is not None:
            self._command()
        return "break"

    def _on_focus_in(self, _event=None) -> None:
        self._focused = True
        self._redraw()

    def _on_focus_out(self, _event=None) -> None:
        self._focused = False
        self._redraw()

    def invoke(self) -> None:
        """和 ttk.Button 同名的方法（脚本里偶尔会调）。"""

        if self._state != "disabled" and self._command is not None:
            self._command()


class RoundedProgressBar(_Themed, tk.Canvas):
    """圆角进度条。``value`` 仍是 ttk 那套 **0–100**（回归脚本按这个读）。"""

    def __init__(
        self,
        parent,
        theme,
        *,
        height: int = 8,
        radius: int = 4,
        length: int = 280,
        background: str = "surface",
        track: str = "shadowRing",
        fill: str = "primary",
        **kwargs,
    ) -> None:
        super().__init__(
            parent, bd=0, highlightthickness=0, width=length, height=theme.pad(height), **kwargs
        )
        self._value = 0.0        # 目标值：cget("value") 读的就是它
        self._drawn = 0.0        # 动画当前值
        self._height = height
        self._radius = radius
        self._background = background
        self._track_token = track
        self._fill_token = fill
        self._indeterminate = False
        self._phase = 0.0
        self._after_id: str | None = None
        self.bind("<Configure>", lambda _event: self._redraw())
        self._bind_theme(theme)

    # ------------------------------------------------------------ ttk 兼容层

    def cget(self, key):
        if key == "value":
            return float(self._value)
        if key == "maximum":
            return 100.0
        return super().cget(key)

    def configure(self, cnf=None, **kwargs):
        if isinstance(cnf, str):
            return super().configure(cnf, **kwargs)
        options: dict = {}
        if isinstance(cnf, dict):
            options.update(cnf)
        options.update(kwargs)
        if "value" in options:
            self.set_value(options.pop("value"))
        self._indeterminate = bool(options.pop("indeterminate", self._indeterminate))
        for key in ("mode", "maximum", "length"):
            options.pop(key, None)   # ttk 时代的参数，忽略即可
        if options:
            super().configure(**options)
        if self._indeterminate:
            self._start_animation()
        else:
            self._redraw()
        return None

    config = configure

    # ---------------------------------------------------------------- 数值

    def set_value(self, value: float) -> None:
        """设置目标值（0–100，越界夹紧）。真正画出来的是平滑过渡后的值。"""

        try:
            number = float(value)
        except (TypeError, ValueError):
            number = 0.0
        self._value = max(0.0, min(100.0, number))
        if self._indeterminate:
            self._start_animation()
            return
        self._start_animation()

    def start_indeterminate(self) -> None:
        self._indeterminate = True
        self._start_animation()

    def stop_indeterminate(self) -> None:
        self._indeterminate = False
        self._start_animation()

    # ---------------------------------------------------------------- 主题

    def apply_theme(self, theme=None) -> None:
        theme = theme or self._theme
        if theme is None:
            return
        super().configure(background=theme.color(self._background))
        super().configure(height=theme.pad(self._height))
        self._redraw()

    # ---------------------------------------------------------------- 动画

    def _start_animation(self) -> None:
        if self._after_id is not None or self._theme is None:
            return
        try:
            self._after_id = self.after(16, self._step)
        except tk.TclError:
            self._after_id = None

    def _step(self) -> None:
        self._after_id = None
        if self._theme is None:
            return
        changed = False
        if self._indeterminate:
            self._phase = (self._phase + 3.0) % 100.0
            changed = True
        elif abs(self._drawn - self._value) > 0.5:
            self._drawn += (self._value - self._drawn) * 0.35
            changed = True
        else:
            self._drawn = self._value
        self._redraw()
        if changed or self._indeterminate:
            self._start_animation()

    # ---------------------------------------------------------------- 绘制

    def _redraw(self) -> None:
        theme = self._theme
        if theme is None:
            return
        self.delete("bar")
        width = self.winfo_width()
        height = self.winfo_height()
        if width <= 1 or height <= 1:
            return
        radius = theme.radius(self._radius)
        rounded_rect(
            self,
            0,
            0,
            width,
            height,
            radius,
            theme.color(self._track_token),
            tags=("bar",),
        )
        fill_token = self._fill_token
        if self._indeterminate:
            segment = max(theme.pad(24), width // 4)
            start = int((width + segment) * (self._phase / 100.0)) - segment
            left = max(0, start)
            right = min(width, start + segment)
            if right - left <= 1:
                return
        else:
            left = 0
            right = int(width * (self._drawn / 100.0))
            if right <= 1:
                return
        rounded_rect(
            self,
            left,
            0,
            right,
            height,
            min(radius, max(0, (right - left) // 2)),
            theme.color(fill_token),
            tags=("bar",),
        )

    def destroy(self) -> None:
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        super().destroy()


class Chip(_Themed, tk.Frame):
    """状态胶囊：一行小字 + 圆角底色（按语义取色）。"""

    KINDS = {
        "neutral": ("chipBg", "textMuted"),
        "primary": ("primarySoft", "primary"),
        # success/warning 直接用会压不住浅底（对比度 < 3:1），所以用它们的"文字版"。
        "success": ("successSoft", "successText"),
        "danger": ("dangerSoft", "danger"),
        "warning": ("chipBg", "warningText"),
        "info": ("primarySoft", "info"),
    }

    def __init__(
        self,
        parent,
        theme,
        *,
        text: str = "",
        textvariable=None,
        kind: str = "neutral",
        background: str = "canvas",
        padx: int = 10,
        pady: int = 3,
        radius: int = 10,
        **kwargs,
    ) -> None:
        super().__init__(parent, bd=0, highlightthickness=0, **kwargs)
        self._kind = kind
        self._background = background
        self._radius = radius
        self._pad = (padx, pady)
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.label = tk.Label(
            self,
            text=text,
            textvariable=textvariable,
            bd=0,
            highlightthickness=0,
            font=theme.font("chip"),
        )
        self.label.pack(padx=theme.pad(padx), pady=theme.pad(pady))
        self.label.lift()   # 文字压在圆角底色之上
        self.bind("<Configure>", self._redraw)
        self._bind_theme(theme)

    def set_kind(self, kind: str) -> str:
        if kind not in self.KINDS:
            kind = "neutral"
        if kind != self._kind:
            self._kind = kind
            self.apply_theme()
        return self._kind

    def apply_theme(self, theme=None) -> None:
        theme = theme or self._theme
        if theme is None:
            return
        fill_token, fg_token = self.KINDS.get(self._kind, self.KINDS["neutral"])
        self.configure(background=theme.color(self._background))
        self.label.configure(
            background=theme.color(fill_token),
            foreground=theme.color(fg_token),
            font=theme.font("chip"),
        )
        self.label.pack_configure(
            padx=theme.pad(self._pad[0]), pady=theme.pad(self._pad[1])
        )
        self._redraw()

    def _redraw(self, _event=None) -> None:
        theme = self._theme
        if theme is None:
            return
        self.canvas.configure(background=theme.color(self._background))
        self.canvas.delete("chip")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        fill_token, _fg = self.KINDS.get(self._kind, self.KINDS["neutral"])
        rounded_rect(
            self.canvas,
            0,
            0,
            width,
            height,
            theme.radius(self._radius),
            theme.color(fill_token),
            tags=("chip",),
        )


class Snackbar(_Themed, tk.Frame):
    """提示条：浅色底、左侧强调色与折行文本。"""

    KINDS = {
        "neutral": ("surfaceAlt", "textMuted", "border"),
        "primary": ("primarySoft", "text", "primary"),
        "success": ("successSoft", "text", "success"),
        "danger": ("dangerSoft", "text", "danger"),
        "warning": ("surfaceAlt", "text", "warning"),
    }

    def __init__(
        self,
        parent,
        theme,
        *,
        text: str = "",
        textvariable=None,
        kind: str = "neutral",
        background: str = "canvas",
        wraplength: int = 520,
        padx: int = 12,
        pady: int = 8,
        radius: int = 8,
        **kwargs,
    ) -> None:
        super().__init__(parent, bd=0, highlightthickness=0, **kwargs)
        self._kind = kind
        self._background = background
        self._radius = radius
        self._pad = (padx, pady)
        self._wraplength = wraplength
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.label = tk.Label(
            self,
            text=text,
            textvariable=textvariable,
            bd=0,
            highlightthickness=0,
            justify="left",
            anchor="w",
            font=theme.font("small"),
        )
        self.label.pack(fill="both", expand=True, padx=theme.pad(padx), pady=theme.pad(pady))
        self.label.lift()   # 文字压在圆角底色之上
        self.bind("<Configure>", self._redraw)
        self._bind_theme(theme)

    def set_kind(self, kind: str) -> str:
        if kind not in self.KINDS:
            kind = "neutral"
        if kind != self._kind:
            self._kind = kind
            self.apply_theme()
        return self._kind

    def apply_theme(self, theme=None) -> None:
        theme = theme or self._theme
        if theme is None:
            return
        fill_token, fg_token, _accent = self.KINDS.get(self._kind, self.KINDS["neutral"])
        self.configure(background=theme.color(self._background))
        self.label.configure(
            background=theme.color(fill_token),
            foreground=theme.color(fg_token),
            font=theme.font("small"),
            wraplength=max(120, int(self._wraplength * theme.scale)),
        )
        self.label.pack_configure(
            padx=(theme.pad(self._pad[0]) + theme.pad(4), theme.pad(self._pad[0])),
            pady=theme.pad(self._pad[1]),
        )
        self._redraw()

    def _redraw(self, _event=None) -> None:
        theme = self._theme
        if theme is None:
            return
        self.canvas.configure(background=theme.color(self._background))
        self.canvas.delete("snack")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        fill_token, _fg, accent_token = self.KINDS.get(self._kind, self.KINDS["neutral"])
        radius = theme.radius(self._radius)
        rounded_rect(
            self.canvas,
            0,
            0,
            width,
            height,
            radius,
            theme.color(fill_token),
            outline=theme.color("shadowRing"),
            width=max(1, theme.pad(1)),
            tags=("snack",),
        )
        pad = theme.pad(self._pad[1])
        rounded_rect(
            self.canvas,
            theme.pad(self._pad[0]),
            pad,
            theme.pad(self._pad[0]) + max(2, theme.pad(3)),
            max(pad + 2, height - pad),
            theme.pad(1),
            theme.color(accent_token),
            tags=("snack",),
        )


class FieldCard(_Themed, tk.Frame):
    """圆角输入框外壳：里面放**真正的** ttk.Entry（属性名不能被换掉）。

    输入框要用这个壳当父容器创建：``field = FieldCard(frame, theme)``，
    ``entry = ttk.Entry(field, ...)``，再 ``field.attach(entry)``。
    跨父容器 ``pack`` 在 Tk 里要靠 ``in_=``，不划算。
    """

    def __init__(
        self,
        parent,
        theme,
        *,
        background: str = "surface",
        padding: tuple[int, int] = (8, 5),
        radius: int = 6,
        **kwargs,
    ) -> None:
        super().__init__(parent, bd=0, highlightthickness=0, **kwargs)
        self._background = background
        self._radius = radius
        self._padding = padding
        self._focused = False
        self.canvas = tk.Canvas(self, bd=0, highlightthickness=0)
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1)
        self.field = None
        self.bind("<Configure>", self._redraw)
        self._bind_theme(theme)

    def attach(self, widget):
        """把输入框放进壳里（必须先把它创建在这个 FieldCard 下面）。"""

        theme = self._theme
        is_new_child = widget is not self.field
        self.field = widget
        if is_new_child and widget.winfo_class() == "TEntry":
            widget.configure(style="Field.TEntry")
            widget.bind("<FocusIn>", self._on_field_focus, add="+")
            widget.bind("<FocusOut>", self._on_field_blur, add="+")
        widget.pack(
            fill="both",
            expand=True,
            padx=theme.pad(self._padding[0]) if theme else 6,
            pady=theme.pad(self._padding[1]) if theme else 4,
        )
        widget.lift()
        return widget

    def _on_field_focus(self, _event=None) -> None:
        self._focused = True
        self._redraw()

    def _on_field_blur(self, _event=None) -> None:
        self._focused = False
        self._redraw()

    def apply_theme(self, theme=None) -> None:
        theme = theme or self._theme
        if theme is None:
            return
        self.configure(background=theme.color(self._background))
        if self.field is not None:
            self.field.pack_configure(
                padx=theme.pad(self._padding[0]), pady=theme.pad(self._padding[1])
            )
        self._redraw()

    def _redraw(self, _event=None) -> None:
        theme = self._theme
        if theme is None:
            return
        self.canvas.configure(background=theme.color(self._background))
        self.canvas.delete("field")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        rounded_rect(
            self.canvas,
            0,
            0,
            width,
            height,
            theme.radius(self._radius),
            theme.color("surface"),
            outline=theme.color("primary" if self._focused else "border"),
            width=max(1, theme.pad(1)),
            tags=("field",),
        )


class Spinner(_Themed, tk.Canvas):
    """转圈的小圈：不确定进度时用（比如「正在读取模型文件」）。"""

    def __init__(self, parent, theme, *, size: int = 16, background: str = "surface", **kwargs) -> None:
        super().__init__(
            parent,
            bd=0,
            highlightthickness=0,
            width=theme.pad(size),
            height=theme.pad(size),
            **kwargs,
        )
        self._size = size
        self._background = background
        self._angle = 0
        self._after_id: str | None = None
        self._running = False
        self.bind("<Configure>", lambda _event: self._redraw())
        self._bind_theme(theme)

    def apply_theme(self, theme=None) -> None:
        theme = theme or self._theme
        if theme is None:
            return
        super().configure(background=theme.color(self._background))
        super().configure(width=theme.pad(self._size), height=theme.pad(self._size))
        self._redraw()

    def start(self) -> None:
        if self._running or self._theme is None:
            return
        self._running = True
        self._tick()

    def stop(self) -> None:
        self._running = False
        if self._after_id is not None:
            try:
                self.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None

    def _tick(self) -> None:
        self._after_id = None
        if not self._running or self._theme is None:
            return
        self._angle = (self._angle + 30) % 360
        self._redraw()
        try:
            self._after_id = self.after(60, self._tick)
        except tk.TclError:
            self._after_id = None
            self._running = False

    def _redraw(self) -> None:
        theme = self._theme
        if theme is None:
            return
        self.delete("spin")
        width, height = self.winfo_width(), self.winfo_height()
        if width <= 1 or height <= 1:
            return
        margin = max(1, theme.pad(2))
        self.create_arc(
            margin,
            margin,
            width - margin,
            height - margin,
            start=self._angle,
            extent=280,
            style="arc",
            outline=theme.color("primary"),
            width=max(2, theme.pad(2)),
            tags=("spin",),
        )

    def destroy(self) -> None:
        self.stop()
        super().destroy()


__all__ = [
    "Card",
    "Chip",
    "FieldCard",
    "RoundedButton",
    "RoundedProgressBar",
    "Snackbar",
    "Spinner",
    "configure_ttk",
    "rounded_rect",
]
