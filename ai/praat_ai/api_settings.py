"""API provider presets and configuration helpers.

Modern menus and standalone entry points open the assistant Model page.
The Tk dialog class remains only for the explicitly selected legacy chat UI.
Credentials and settings continue to use the shared ai_config.json configuration.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Mapping

from . import qwen, ui_theme, ui_widgets, ui_windows, model_capabilities, api_diagnostics
from .config import AppConfig, load_config, normalize_thinking_level, default_config_path


#: 常见服务商的默认地址和示例模型；用户也可以自己改地址接别的网关。
#: Gemini 使用 Google 的 ``/v1beta/openai`` 兼容接口，其余服务商使用各自的地址。
PROVIDERS: tuple[dict[str, str], ...] = (
    {
        "label": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "model": "deepseek-chat",
        "hint": "deepseek-chat / deepseek-reasoner",
    },
    {
        "label": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "model": "gpt-4o-mini",
        "hint": "gpt-4o-mini / gpt-4o",
    },
    {
        "label": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "model": "gemini-3.8-flash",
        "hint": "使用 Google AI Studio API Key；可按需更换 Gemini 模型",
    },
    {
        "label": "阿里云百炼（通义千问）",
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-max",
        "hint": "qwen-max / qwen-plus",
    },
    {
        "label": "智谱 GLM",
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "model": "glm-4-plus",
        "hint": "glm-4-plus / glm-4-flash",
    },
    {
        "label": "月之暗面 Kimi",
        "base_url": "https://api.moonshot.cn/v1",
        "model": "moonshot-v1-8k",
        "hint": "moonshot-v1-8k / moonshot-v1-128k",
    },
    {
        "label": "硅基流动 SiliconFlow",
        "base_url": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-72B-Instruct",
        "hint": "可用的开源大模型很多，key 在官网申请",
    },
    {
        "label": "自定义 / 本机网关",
        "base_url": "http://127.0.0.1:8000/v1",
        "model": "local-model",
        "hint": "Ollama、LM Studio、vLLM 这类本机 OpenAI 兼容服务",
    },
)

#: 默认值（新建配置时用）。
DEFAULTS: dict[str, Any] = {
    "enabled": False,
    "locked": False,
    "label": "",
    "base_url": "",
    "model": "",
    "api_key": "",
    "request_timeout_sec": 120,
    "limit_tokens": False,
    "max_context_tokens": 32768,
    "plan_max_tokens": 1500,
    "dialogue_max_tokens": 2048,
    "local_limit_tokens": True,
    "local_max_context_tokens": 32768,
    "local_plan_max_tokens": 4096,
    "plan_temperature": 0.1,
    "vision_when_requested": False,
    "thinking_level": "medium",
    "force_deep_thinking": False,
    "use_world_knowledge": True,
    "stop_local_service": True,
}

#: 「思考档位」下拉框的显示文字 → 配置里的值（见 config.THINKING_LEVELS）。
THINKING_CHOICES: tuple[tuple[str, str], ...] = (
    ("自动（服务端默认）", "auto"),
    ("关闭（最快）", "off"),
    ("低", "low"),
    ("中（推荐）", "medium"),
    ("高（最慢、最深）", "high"),
)


def thinking_choice_label(level: str) -> str:
    """把配置里的思考档位翻成下拉框里的显示文字。"""

    wanted = normalize_thinking_level(level)
    for label, value in THINKING_CHOICES:
        if value == wanted:
            return label
    return THINKING_CHOICES[0][0]


def settings_from_config(config: AppConfig) -> dict[str, Any]:
    """把当前配置读成一份「窗口里的值」（用来预填界面）。"""

    api = config.api
    local = config.local_qwen or config.qwen
    values = dict(DEFAULTS)
    values.update(
        {
            "enabled": bool(api.enabled),
            "locked": bool(api.locked),
            "label": api.label,
            "base_url": api.base_url,
            "model": api.model,
            "api_key": api.api_key,
            "request_timeout_sec": api.request_timeout_sec,
            "limit_tokens": bool(api.limit_tokens),
            "max_context_tokens": api.max_context_tokens,
            "plan_max_tokens": api.plan_max_tokens,
            "dialogue_max_tokens": api.dialogue_max_tokens,
            "local_limit_tokens": bool(local.limit_tokens),
            "local_max_context_tokens": local.max_context_tokens,
            "local_plan_max_tokens": local.plan_max_tokens,
            "plan_temperature": api.plan_temperature,
            "vision_when_requested": bool(api.vision_when_requested),
            "thinking_level": api.thinking_level,
            "force_deep_thinking": bool(api.force_deep_thinking),
            "use_world_knowledge": bool(api.use_world_knowledge),
            "stop_local_service": bool(api.stop_local_service),
            "audio_input_enabled": bool(api.audio_input_enabled),
            "audio_input_source": api.audio_input_source,
            "audio_input_reason": api.audio_input_reason,
            "audio_input_path": api.audio_input_path,
            "audio_verified_at": api.audio_verified_at,
            "audio_test_result": api_diagnostics.matching_record(api.audio_test_result, api.base_url, api.model, api.api_key),
            api_diagnostics.SEQUENCE_KEY: int(getattr(api, 'audio_setting_sequence', 0) or 0),
        }
    )
    return values


def _as_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        number = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def _as_float(value: Any, default: float, minimum: float, maximum: float) -> float:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, number))


def normalize_settings(values: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """整理界面上的值，返回 ``(干净的值, 错误列表)``（错误是中文，直接显示给用户）。"""

    enabled = bool(values.get("enabled"))
    base_url = str(values.get("base_url", "") or "").strip().rstrip("/")
    model = str(values.get("model", "") or "").strip()
    errors: list[str] = []
    if enabled and not base_url:
        errors.append("启用 API 时必须填 API 地址（Base URL）。")
    if enabled and not model:
        errors.append("启用 API 时必须填模型名。")
    if base_url and not base_url.casefold().startswith(("http://", "https://")):
        errors.append("API 地址要以 http:// 或 https:// 开头。")
    if errors:
        enabled = False
    clean = {
        "enabled": enabled,
        "locked": bool(values.get("locked", False)),
        "label": str(values.get("label", "") or "").strip(),
        "base_url": base_url,
        "model": model,
        "api_key": str(values.get("api_key", "") or "").strip(),
        "request_timeout_sec": _as_int(
            values.get("request_timeout_sec"), 120, 10, 600
        ),
        "limit_tokens": bool(values.get("limit_tokens", False)),
        "max_context_tokens": _as_int(
            values.get("max_context_tokens"), 32768, 2048, 1_000_000
        ),
        "plan_max_tokens": _as_int(values.get("plan_max_tokens"), 1500, 128, 32768),
        "dialogue_max_tokens": _as_int(values.get("dialogue_max_tokens"), 2048, 256, 32768),
        "local_limit_tokens": bool(values.get("local_limit_tokens", True)),
        "local_max_context_tokens": _as_int(values.get("local_max_context_tokens"), 32768, 2048, 1_000_000),
        "local_plan_max_tokens": _as_int(values.get("local_plan_max_tokens"), 4096, 128, 32768),
        "plan_temperature": _as_float(
            values.get("plan_temperature"), 0.1, 0.0, 2.0
        ),
        "vision_when_requested": bool(values.get("vision_when_requested")),
        "thinking_level": normalize_thinking_level(values.get("thinking_level")),
        "force_deep_thinking": bool(values.get('force_deep_thinking', False)),
        "use_world_knowledge": bool(values.get("use_world_knowledge", True)),
        # 老配置没有这个键 → True（= 进 API 就停本机服务，腾显存）。
        "stop_local_service": bool(values.get("stop_local_service", True)),
        "audio_input_enabled": bool(values.get("audio_input_enabled", False)),
        "audio_input_source": str(values.get("audio_input_source", "unknown")),
        "audio_input_reason": str(values.get("audio_input_reason", "")),
        "audio_input_path": str(values.get("audio_input_path", "")),
        "audio_verified_at": str(values.get("audio_verified_at", "")),
        "audio_test_result": api_diagnostics.matching_record(values.get('audio_test_result'), base_url, model, str(values.get('api_key', '') or '').strip()),
        # 音频设置的写序号：这次保存要严格大于配置里与界面上已经出现的（时间并列时定先后）。
        api_diagnostics.SEQUENCE_KEY: max(api_diagnostics.next_sequence(values),
                                          api_diagnostics.next_sequence({'audio_test_result': values.get('audio_test_result')})),
    }
    return clean, errors


def save_settings(
    values: Mapping[str, Any],
    config_path: str | Path | None = None,
    *,
    verified: bool = False,
    audio_setting_edited_at: str | None = None,
    audio_setting_edited_sequence: int | None = None,
) -> dict[str, Any]:
    """写入 ``api`` 配置节（校验不过就抛 ``ValueError``）。

    ``verified=True``（刚点过「测试连接」并成功）会记下时间，窗口和状态里能显示
    「最近验证」；只影响显示，不参与任何逻辑。
    ``audio_setting_edited_at`` 只用于处理窗口与运行时纠正的先后顺序，不写入配置。
    """

    from . import control   # 延迟导入：control 也 import 本模块，避免循环

    def prepare_changes():
        existing = settings_from_config(load_config(config_path))
        updated = dict(existing)
        updated.update(values)
        updated['audio_test_result'] = api_diagnostics.matching_record(updated.get('audio_test_result'), str(updated['base_url']), str(updated['model']), str(updated['api_key']))
        previous_test = api_diagnostics.matching_record(existing['audio_test_result'], str(updated['base_url']), str(updated['model']), str(updated['api_key']))
        incoming_test = updated['audio_test_result']
        edited_at = audio_setting_edited_at if audio_setting_edited_at is not None else values.get('audio_setting_edited_at')
        edited_sequence = (audio_setting_edited_sequence if audio_setting_edited_sequence is not None
                           else values.get('audio_setting_edited_sequence'))
        # 本次保存自己的写序号：严格大于配置里与界面上已经出现的序号。
        updated[api_diagnostics.SEQUENCE_KEY] = max(
            api_diagnostics.next_sequence(existing), api_diagnostics.next_sequence(values))
        # Clearing a stale dialog's result must not erase a later runtime rejection.
        # An explicit manual edit made after that observation still takes effect.
        # 时间戳并列时（Windows 时钟粒度约 15 ms）由写序号决定先后，见 api_diagnostics。
        if (previous_test
                and api_diagnostics.observed_later(previous_test, incoming_test)
                and not api_diagnostics.edited_after(edited_at, edited_sequence, previous_test)):
            updated['audio_test_result'] = previous_test
            for key in ('audio_input_enabled','audio_input_source','audio_input_reason','audio_input_path','audio_verified_at'):
                updated[key] = existing[key]
            updated[api_diagnostics.SEQUENCE_KEY] = existing[api_diagnostics.SEQUENCE_KEY]
        if updated.get('api_key') != existing.get('api_key'):
            updated['audio_verified_at'] = ''
        if (str(updated.get('base_url', '')).rstrip('/'), updated.get('model')) != (existing['base_url'].rstrip('/'), existing['model']):
            preset = model_capabilities.audio_preset(str(updated.get('base_url', '')), str(updated.get('model', '')))
            if 'audio_input_enabled' not in values:
                updated['audio_input_enabled'] = preset.enabled
                updated['audio_input_source'] = 'preset' if preset.source else 'unknown'
                updated['audio_input_reason'] = preset.reason
            updated['audio_input_path'] = ''
            updated['audio_verified_at'] = ''
        if updated["locked"] or (
            bool(updated["stop_local_service"])
            and not existing["stop_local_service"]
            and not existing["enabled"]
        ):
            updated["enabled"] = True
        if (
            updated["enabled"]
            and "base_url" not in values
            and "model" not in values
            and not str(updated["base_url"]).strip()
            and not str(updated["model"]).strip()
        ):
            provider = PROVIDERS[0]
            for key in ("label", "base_url", "model"):
                if not str(updated[key]).strip():
                    updated[key] = provider[key]
        clean, errors = normalize_settings(updated)
        if errors:
            raise ValueError("；".join(errors))
        if verified:
            import datetime
    
            clean["verified_at"] = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
        else:
            # 改了地址/模型/key 就作废上一次的验证时间。
            clean["verified_at"] = ""
        # 思考档位是两条路共用的（本地翻成 enable_thinking、云端翻成 reasoning_effort），
        # 所以同时写进 ``qwen`` 节；其余字段只属于 ``api`` 节。
        local = {key: clean.pop(key) for key in ("local_limit_tokens", "local_max_context_tokens", "local_plan_max_tokens")}
        return (
            {
                "api": clean,
                "qwen": {
                    "thinking_level": clean["thinking_level"],
                    "limit_tokens": local["local_limit_tokens"],
                    "max_context_tokens": local["local_max_context_tokens"],
                    "plan_max_tokens": local["local_plan_max_tokens"],
                },
            }
        )
    # Read, derive and write settings under the same cross-process writer lock.
    return control.update_config(prepare_changes, config_path)


class ApiSettingsDialog:
    """「API 配置」小窗口（挂在对话窗口上；也用 :func:`run_standalone` 独立打开）。"""

    def __init__(
        self,
        parent,
        *,
        config: AppConfig | None = None,
        config_path: str | Path | None = None,
        on_saved: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        import tkinter as tk
        from tkinter import messagebox, ttk

        self._tk = tk
        self._messagebox = messagebox
        self.config_path = config_path
        self.on_saved = on_saved
        self.values = settings_from_config(config or load_config(config_path))
        self.verified = bool(
            (config or load_config(config_path)).api.verified_at
        )

        self.window = tk.Toplevel(parent) if parent is not None else ui_windows.create_root()
        self.window.title("API 配置")
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Destroy>", self._on_destroy, add="+")
        self.window.resizable(False, False)
        try:
            self.window.attributes("-topmost", True)
        except tk.TclError:
            pass

        # 主题：TW-Elements 令牌 + 跟随系统深浅色（开窗时定一次，窗口是短命的）。
        self.theme = ui_theme.Theme(self.window)
        theme = self.theme
        self.window.configure(background=theme.color("canvas"))
        style = ttk.Style(self.window)
        if "clam" in style.theme_names() and style.theme_use() != "clam":
            style.theme_use("clam")
        ui_widgets.configure_ttk(style, theme)

        viewport = tk.Frame(self.window, background=theme.color('canvas'))
        viewport.pack(fill='both', expand=True)
        self.canvas = tk.Canvas(viewport, background=theme.color('canvas'), highlightthickness=0, borderwidth=0)
        self.canvas.pack(side='left', fill='both', expand=True)
        scroll = ttk.Scrollbar(viewport, orient='vertical', command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scroll.set)
        frame = tk.Frame(self.canvas, background=theme.color("canvas"))
        self.canvas.create_window(14, 14, window=frame, anchor='nw')
        def fit_settings(_event=None):
            height, width = frame.winfo_reqheight()+28, frame.winfo_reqwidth()+28
            available = max(240, self.window.winfo_screenheight()-110)
            self.canvas.configure(scrollregion=(0, 0, width, height), width=width, height=min(height, available))
            if height > available:
                scroll.pack(side='right', fill='y')
            else:
                scroll.pack_forget()
                self.canvas.yview_moveto(0)
        frame.bind('<Configure>', fit_settings)
        self.window.bind('<MouseWheel>', lambda event:self.canvas.yview_scroll(-int(event.delta/120), 'units'), add='+')
        frame.columnconfigure(0, weight=1)

        def field_label(parent, text: str, *, muted: bool = False, role: str = "body"):
            return tk.Label(
                parent,
                text=text,
                anchor="w",
                background=theme.color("surface"),
                foreground=theme.color("textMuted" if muted else "text"),
                font=theme.font("small" if muted else role),
            )

        def field_entry(parent, variable, *, width: int = 44, show: str | None = None):
            """圆角外壳 + **真正的 ttk.Entry**（key_entry 这些属性名不能换掉）。"""

            shell = ui_widgets.FieldCard(parent, theme, background="surface")
            entry = ttk.Entry(shell, textvariable=variable, width=width, font=theme.font("body"))
            if show is not None:
                entry.configure(show=show)
            shell.attach(entry)
            return shell, entry

        # ---------------------------------------------------------- 连接
        connect = ui_widgets.Card(frame, theme, padding=(14, 12, 14, 12))
        connect.grid(row=0, column=0, sticky="ew")
        connect_body = connect.body
        connect_body.columnconfigure(1, weight=1)

        self.enabled = tk.BooleanVar(value=bool(self.values["enabled"]))
        self.locked = tk.BooleanVar(value=bool(self.values["locked"]))
        ttk.Checkbutton(
            connect_body,
            text="锁定云端 API（顶部不能切换到本地模型）",
            variable=self.locked,
            command=self._on_lock_changed,
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))

        field_label(connect_body, "服务商").grid(row=1, column=0, sticky="w", pady=4)
        self.provider = tk.StringVar(value=self._provider_for(self.values))
        self.provider_box = ttk.Combobox(
            connect_body,
            textvariable=self.provider,
            values=[item["label"] for item in PROVIDERS],
            width=32,
            font=theme.font("body"),
        )
        self.provider_box.grid(row=1, column=1, columnspan=2, sticky="ew", pady=4)
        self.provider_box.bind("<<ComboboxSelected>>", self._on_provider)

        field_label(connect_body, "API 地址").grid(row=2, column=0, sticky="w", pady=4)
        self.base_url = tk.StringVar(value=self.values["base_url"])
        shell, _entry = field_entry(connect_body, self.base_url, width=46)
        shell.grid(row=2, column=1, columnspan=2, sticky="ew", pady=4)

        field_label(connect_body, "模型名").grid(row=3, column=0, sticky="w", pady=4)
        self.model = tk.StringVar(value=self.values["model"])
        shell, _entry = field_entry(connect_body, self.model, width=46)
        shell.grid(row=3, column=1, columnspan=2, sticky="ew", pady=4)

        field_label(connect_body, "API Key").grid(row=4, column=0, sticky="w", pady=4)
        self.api_key = tk.StringVar(value=self.values["api_key"])
        shell, self.key_entry = field_entry(
            connect_body, self.api_key, width=32, show="•"
        )
        shell.grid(row=4, column=1, sticky="ew", pady=4)
        self.show_key = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            connect_body, text="显示", variable=self.show_key, command=self._toggle_key
        ).grid(row=4, column=2, sticky="w", padx=(8, 0))

        field_label(connect_body, "超时(秒)").grid(row=5, column=0, sticky="w", pady=4)
        self.timeout = tk.StringVar(value=str(self.values["request_timeout_sec"]))
        shell, _entry = field_entry(connect_body, self.timeout, width=8)
        shell.grid(row=5, column=1, sticky="w", pady=4)

        self.audio_enabled = tk.BooleanVar(value=self.values['audio_input_enabled'])
        self.audio_source = self.values['audio_input_source']
        self.audio_reason = self.values['audio_input_reason']
        self.audio_identity = (self.base_url.get(), self.model.get(), self.api_key.get())
        self.audio_test_result = self.values['audio_test_result']
        self._diagnostic_result = self.audio_test_result
        self._audio_dirty = False
        self._audio_result_dirty = False
        self._audio_setting_edited_at = ''
        #: 手动修改发生时配置里已经写下的音频写序号（时间并列时判先后的依据）。
        self._audio_setting_edited_sequence = 0
        self._audio_edit_revision = 0
        self._audio_probe_cancel = threading.Event()
        ttk.Checkbutton(connect_body,
                        text='启用直接音频输入（允许发送原始音频）',
                        variable=self.audio_enabled, command=self._on_audio_manual).grid(
                            row=8, column=0, columnspan=3, sticky='w', pady=(8, 0))
        self.audio_hint = tk.StringVar(value=self._audio_hint_text())
        field_label(connect_body, '').grid(row=9, column=0)
        self.audio_hint_label = tk.Label(connect_body, textvariable=self.audio_hint, wraplength=600, justify='left', anchor='w',
                 background=theme.color('surface'), foreground=theme.color('textMuted'),
                 font=theme.font('small'))
        self.audio_hint_label.grid(row=9, column=0, columnspan=3, sticky='w')
        self.base_url.trace_add('write', self._on_audio_path)
        self.model.trace_add('write', self._on_audio_path)
        self.api_key.trace_add('write', self._on_audio_path)
        self.audio_test_button = ui_widgets.RoundedButton(connect_body, theme, '验证音频能力（可选）', self.test_audio, kind='text')
        self.audio_test_button.grid(row=10, column=0, columnspan=3, sticky='w')
        self._audio_poll_id = self.window.after(1000, self._refresh_audio_correction)

        # 进入 API 模式时要不要顺手停掉本机 llama-server：默认停（腾显存），
        # 但切回本地模型时要重新加载几十秒，所以给一个「别停」的选项。
        self.stop_local_service = tk.BooleanVar(
            value=bool(self.values["stop_local_service"])
        )
        ttk.Checkbutton(
            connect_body,
            text=(
                "启用 API 时顺手停掉本机模型服务（省显存；取消勾选则切回本地时"
                "不用重新加载）"
            ),
            variable=self.stop_local_service,
            command=self._on_stop_changed,
        ).grid(row=6, column=0, columnspan=3, sticky="w", pady=(6, 0))

        # 允许云端模型发挥自己的语言学知识（本地小模型永远不让，免得编数字）。
        self.world_knowledge = tk.BooleanVar(
            value=bool(self.values["use_world_knowledge"])
        )
        ttk.Checkbutton(
            connect_body,
            text="允许云端 API 用自己的知识解释、举例（测量数字仍只来自工具结果）",
            variable=self.world_knowledge,
        ).grid(row=7, column=0, columnspan=3, sticky="w", pady=(4, 0))

        self.show_advanced = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            frame, text="高级设置：上下文、token 与思考策略", variable=self.show_advanced,
            command=self._toggle_advanced,
        ).grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.advanced = ui_widgets.Card(frame, theme, padding=(14, 12, 14, 12))
        self.advanced.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        advanced_body = self.advanced.body
        advanced_body.columnconfigure(1, weight=1)

        self.local_limit_tokens = tk.BooleanVar(value=bool(self.values["local_limit_tokens"]))
        ttk.Checkbutton(
            advanced_body, text="对本地前端启用限制", variable=self.local_limit_tokens,
        ).grid(row=0, column=0, columnspan=2, sticky="w")
        field_label(advanced_body, "本地上下文 token").grid(row=1, column=0, sticky="w", pady=4)
        self.local_context_tokens = tk.StringVar(value=str(self.values["local_max_context_tokens"]))
        shell, _entry = field_entry(advanced_body, self.local_context_tokens, width=10)
        shell.grid(row=1, column=1, sticky="w", pady=4)
        field_label(advanced_body, "本地最大回复 token").grid(row=2, column=0, sticky="w", pady=4)
        self.local_plan_tokens = tk.StringVar(value=str(self.values["local_plan_max_tokens"]))
        shell, _entry = field_entry(advanced_body, self.local_plan_tokens, width=10)
        shell.grid(row=2, column=1, sticky="w", pady=4)

        self.limit_tokens = tk.BooleanVar(value=bool(self.values["limit_tokens"]))
        ttk.Checkbutton(
            advanced_body, text="对云端 API 启用限制", variable=self.limit_tokens,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))
        field_label(advanced_body, "云端上下文 token").grid(row=4, column=0, sticky="w", pady=4)
        self.context_tokens = tk.StringVar(value=str(self.values["max_context_tokens"]))
        shell, _entry = field_entry(advanced_body, self.context_tokens, width=10)
        shell.grid(row=4, column=1, sticky="w", pady=4)
        field_label(advanced_body, "云端最大回复 token").grid(row=5, column=0, sticky="w", pady=4)
        self.plan_tokens = tk.StringVar(value=str(self.values["plan_max_tokens"]))
        shell, _entry = field_entry(advanced_body, self.plan_tokens, width=10)
        shell.grid(row=5, column=1, sticky="w", pady=4)
        field_label(advanced_body, "云端对话最大回复 token").grid(row=6, column=0, sticky="w", pady=4)
        self.dialogue_tokens = tk.StringVar(value=str(self.values["dialogue_max_tokens"]))
        shell, _entry = field_entry(advanced_body, self.dialogue_tokens, width=10)
        shell.grid(row=6, column=1, sticky="w", pady=4)
        limits_note = field_label(
            advanced_body,
            '不勾选「对云端 API 启用限制」时，云端上下文按 131072、分析类回复按 8192 兜底；'
            '「云端对话最大回复 token」始终生效，但不会超过「云端最大回复 token」的预算。'
            '思考强度还会额外留出推理额度（低/中/高 = 1024/4096/8192），所以「最大回复 Token」'
            '只管正文长度。',
            muted=True, role='small')
        limits_note.configure(wraplength=600, justify='left')
        limits_note.grid(row=7, column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.force_deep_thinking = tk.BooleanVar(value=self.values['force_deep_thinking'])
        ttk.Checkbutton(
            advanced_body, text='最高思考强度下，强制每次对话进行深度思考',
            variable=self.force_deep_thinking,
        ).grid(row=8, column=0, columnspan=2, sticky='w', pady=(10, 0))
        note = field_label(advanced_body, '仅最高档生效；关闭时寒暄和能力介绍可快速回复，复杂分析仍使用所选强度。', muted=True, role='small')
        note.configure(wraplength=600, justify='left')
        note.grid(row=9, column=0, columnspan=2, sticky='w', pady=(4, 0))
        self.advanced.grid_remove()

        ui_widgets.Snackbar(
            frame,
            theme,
            text=(
                "填完点「测试连接」确认；key 只存在 ai_config.json（已 gitignore），"
                "也可以用环境变量 PRAAT_AI_API_KEY。"
            ),
            kind="neutral",
            background="canvas",
            wraplength=520,
        ).grid(row=3, column=0, sticky="ew", pady=(10, 0))

        buttons = tk.Frame(frame, background=theme.color("canvas"))
        buttons.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        buttons.columnconfigure(0, weight=1)
        self.status = tk.StringVar(value=self._verified_text())
        self.status_snack = ui_widgets.Snackbar(
            buttons,
            theme,
            textvariable=self.status,
            kind="neutral",
            background="canvas",
            wraplength=300,
        )
        self.status_snack.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.details_button = ui_widgets.RoundedButton(buttons, theme, '查看详情', self.show_diagnostic_details, kind='text')
        self.details_button.grid(row=1, column=0, sticky='w')
        self.test_button = ui_widgets.RoundedButton(
            buttons, theme, "测试连接", self.test_connection, kind="outlined"
        )
        self.test_button.grid(row=1, column=1, padx=(8, 0), pady=(6,0))
        ui_widgets.RoundedButton(buttons, theme, "保存", self.save, kind="filled").grid(
            row=1, column=2, padx=(8, 0), pady=(6,0)
        )
        ui_widgets.RoundedButton(buttons, theme, "取消", self.close, kind="text").grid(
            row=1, column=3, padx=(8, 0), pady=(6,0)
        )
        self._update_audio_display()

    # ---------------------------------------------------------------- 交互

    def _toggle_advanced(self) -> None:
        if self.show_advanced.get():
            self.advanced.grid()
        else:
            self.advanced.grid_remove()

    def _provider_for(self, values: Mapping[str, Any]) -> str:
        for item in PROVIDERS:
            if item["base_url"] == values.get("base_url"):
                return item["label"]
        return str(values.get("label") or "")

    def _verified_text(self) -> str:
        if self.verified:
            return "上次文字连接测试成功（仅文字请求）。"
        return "尚未测试文字连接。"

    def _toggle_key(self) -> None:
        self.key_entry.configure(show="" if self.show_key.get() else "•")

    def _select_fallback_provider(self) -> None:
        if not self.base_url.get().strip() and not self.model.get().strip():
            self.provider.set(PROVIDERS[0]["label"])
            self._on_provider()

    def _on_lock_changed(self) -> None:
        if self.locked.get():
            self.enabled.set(True)
            self._select_fallback_provider()

    def _on_stop_changed(self) -> None:
        if self.stop_local_service.get() and not self.enabled.get():
            self.enabled.set(True)
            self._select_fallback_provider()

    def _on_provider(self, _event: object = None) -> None:
        label = self.provider.get().strip()
        for item in PROVIDERS:
            if item["label"] == label:
                previous_url = self.base_url.get().strip().rstrip("/")
                self.base_url.set(item["base_url"])
                if previous_url != item["base_url"] or not self.model.get().strip() or self.model.get().strip() in {
                    other["model"] for other in PROVIDERS
                }:
                    self.model.set(item["model"])
                self.values["label"] = label
                self.status.set(item.get("hint", ""))
                break

    def collect(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled.get(),
            "locked": self.locked.get(),
            "label": self.provider.get().strip(),
            "base_url": self.base_url.get(),
            "model": self.model.get(),
            "api_key": self.api_key.get(),
            "request_timeout_sec": self.timeout.get(),
            "limit_tokens": self.limit_tokens.get(),
            "max_context_tokens": self.context_tokens.get(),
            "plan_max_tokens": self.plan_tokens.get(),
            "dialogue_max_tokens": self.dialogue_tokens.get(),
            "local_limit_tokens": self.local_limit_tokens.get(),
            "local_max_context_tokens": self.local_context_tokens.get(),
            "local_plan_max_tokens": self.local_plan_tokens.get(),
            # 窗口里没有这几项的控件：原样带回，别让一次保存把它们抹掉。
            "plan_temperature": self.values.get("plan_temperature"),
            "vision_when_requested": self.values.get("vision_when_requested"),
            "thinking_level": self.values.get("thinking_level"),
            "force_deep_thinking": self.force_deep_thinking.get(),
            "use_world_knowledge": self.world_knowledge.get(),
            "stop_local_service": self.stop_local_service.get(),
            'audio_input_enabled':self.audio_enabled.get(),
            'audio_input_source':self.audio_source,
            'audio_input_reason':self.audio_reason,
            'audio_input_path':self.values.get('audio_input_path', '') if self.audio_identity[:2] == (self.values['base_url'], self.values['model']) else '',
            'audio_verified_at':self.values.get('audio_verified_at', ''),
            'audio_test_result':self.audio_test_result,
            'audio_setting_edited_at':self._audio_setting_edited_at,
            'audio_setting_edited_sequence':self._audio_setting_edited_sequence,
        }

    def _audio_hint_text(self):
        return api_diagnostics.audio_hint(self.base_url.get(), self.model.get(), self.audio_enabled.get(),
                                          self.audio_test_result, self._diagnostic_result)

    def _update_audio_display(self):
        self.audio_hint.set(self._audio_hint_text())
        if self._diagnostic_result.get('status') in {'unsupported','unverified'}:
            self.details_button.grid()
        else:
            self.details_button.grid_remove()

    def show_diagnostic_details(self):
        import tkinter as tk
        from tkinter import ttk
        result = self._diagnostic_result
        if result.get('status') not in {'unsupported','unverified'}:
            return
        dialog = tk.Toplevel(self.window)
        dialog.title('API 测试详情')
        dialog.geometry('680x360')
        dialog.geometry('680x360')
        frame = ttk.Frame(dialog, padding=10); frame.pack(fill='both', expand=True)
        body = tk.Text(frame, wrap='word', font=self.theme.font('small'))
        scroll = ttk.Scrollbar(frame, orient='vertical', command=body.yview)
        body.configure(yscrollcommand=scroll.set)
        body.pack(side='left',fill='both',expand=True); scroll.pack(side='right',fill='y')
        header = self._audio_hint_text()
        detail = model_capabilities.safe_error(RuntimeError(str(result.get('reason', ''))), self.api_key.get())
        body.insert('1.0', header+'\n\n完整错误详情：\n'+detail)
        body.configure(state='disabled')


    def _stored_config_payload(self) -> dict:
        """配置文件里当前写下的原始内容（读不到就当空的：宁可保守，也不要凭猜测定序）。"""

        try:
            return json.loads(Path(self.config_path).read_text(encoding='utf-8'))
        except (OSError, ValueError, TypeError):
            return {}
    def _on_audio_manual(self):
        from datetime import datetime, timezone
        self._audio_setting_edited_at = datetime.now(timezone.utc).isoformat()
        # 记下「这次修改看到了几次音频写入」：与运行时纠正的时间戳并列时用它分先后。
        self._audio_setting_edited_sequence = api_diagnostics.observation_sequence(self._stored_config_payload())
        self._audio_edit_revision += 1
        self._audio_dirty = True
        self.audio_source, self.audio_reason = 'manual', '用户声明；尚未实测'
        self.values['audio_verified_at'] = ''
        self.audio_test_result = {}
        self._diagnostic_result = {}
        self._update_audio_display()

    def _on_audio_path(self, *_args):
        identity = (self.base_url.get(), self.model.get(), self.api_key.get())
        if identity == self.audio_identity:
            return
        path_changed = identity[:2] != self.audio_identity[:2]
        self.audio_identity = identity
        self._audio_edit_revision += 1
        self._audio_dirty = True
        if path_changed:
            preset = model_capabilities.audio_preset(*identity[:2])
            self.audio_enabled.set(preset.enabled)
            self.audio_source = 'preset' if preset.source else 'unknown'
            self.audio_reason = preset.reason
        self.values['audio_verified_at'] = ''
        self.audio_test_result = {}
        self._diagnostic_result = {}
        self.verified = False
        self._update_audio_display()
        self.status.set('设置已改变，请重新测试。')

    def _refresh_audio_correction(self):
        try:
            config = load_config(self.config_path)
            api = config.api
            if (not self._audio_dirty and (self.base_url.get().rstrip('/'), self.model.get(), self.api_key.get())
                    == (api.base_url.rstrip('/'), api.model, api.api_key) and api.audio_input_source == 'corrected'
                    and self.audio_test_result.get('status') != 'running'):
                self.audio_enabled.set(False)
                self.audio_source, self.audio_reason = api.audio_input_source, api.audio_input_reason
                self.values.update({'audio_input_path':api.audio_input_path, 'audio_verified_at':api.audio_verified_at})
                observed = api_diagnostics.matching_record(api.audio_test_result, api.base_url, api.model, api.api_key)
                if not observed:
                    observed = api_diagnostics.test_record(api.base_url, api.model, api.api_key,
                        {'status':'unsupported','reason':api.audio_input_reason})
                    observed['tested_at'] = api.audio_corrected_at
                if (not self.audio_test_result or api_diagnostics.observation_time(observed['tested_at'])
                        > api_diagnostics.observation_time(self.audio_test_result.get('tested_at'))):
                    self.audio_test_result = observed
                    self._diagnostic_result = observed
                self._update_audio_display()
        except (OSError, ValueError):
            pass
        self._audio_poll_id = self.window.after(1000, self._refresh_audio_correction)

    def test_audio(self):
        from copy import deepcopy
        from .config import apply_api_to_qwen
        from .audio_probe import probe_audio
        values, errors = normalize_settings(self.collect())
        if errors or not values['base_url'] or not values['model']:
            self.status.set('音频验证需要完整的模型名和 API 地址')
            return
        config = deepcopy(load_config(self.config_path))
        for key in ('base_url','model','api_key','request_timeout_sec','thinking_level'):
            setattr(config.api, key, values[key])
        config.api.enabled = True
        apply_api_to_qwen(config)
        original = (values['base_url'].rstrip('/'),values['model'],values['api_key'])
        original_revision = self._audio_edit_revision
        # 这次验证「看到」的音频写序号：纠正发生在验证期间时，靠它判谁更晚。
        observed_sequence = api_diagnostics.observation_sequence(self._stored_config_payload())
        saved_identity = model_capabilities.config_identity(load_config(self.config_path).api)
        self.audio_test_button.configure(state='disabled')
        self._audio_probe_cancel.clear()
        self.audio_test_result = {'status':'running'}
        self._diagnostic_result = {}
        self._update_audio_display()
        self.status.set('正在用本地随机合成短音频验证，不使用用户录音…')
        def worker():
            try:
                result = probe_audio(config, Path(self.config_path).parent / 'runtime' / 'tasks' if self.config_path else
                                     Path(__file__).resolve().parents[1] / 'runtime' / 'tasks', cancel=self._audio_probe_cancel)
                observed = api_diagnostics.test_record(*original, result, sequence=observed_sequence)
                if result['status'] == 'unsupported' and not self._audio_probe_cancel.is_set():
                    # Only update a saved matching path. Unsaved new choices are
                    # reflected in this dialog and persisted when Save is clicked.
                    saved = load_config(self.config_path)
                    if (self._audio_edit_revision == original_revision and
                            (saved.api.base_url.rstrip('/'),saved.api.model,saved.api.api_key) == original):
                        model_capabilities.correct_audio_setting(Path(self.config_path) if self.config_path else
                            default_config_path(), saved_identity, result['reason'])
            except (OSError, ValueError) as error:
                result = {'status':'unverified', 'reason':model_capabilities.safe_error(error, values['api_key'])}
                observed = api_diagnostics.test_record(*original, result, sequence=observed_sequence)
            def finish():
                self.audio_test_button.configure(state='normal')
                if (self._audio_edit_revision != original_revision or
                        (self.base_url.get().rstrip('/'),self.model.get(),self.api_key.get()) != original):
                    self.status.set('配置已改变，旧路径验证结果未应用到新设置')
                    return
                self.audio_test_result = observed
                self._diagnostic_result = self.audio_test_result
                if result['status'] == 'verified':
                    self.audio_enabled.set(True)
                    self.audio_source, self.audio_reason = 'manual', observed['reason']
                    self.values['audio_verified_at'] = observed['tested_at']
                elif result['status'] == 'unsupported':
                    self.audio_enabled.set(False)
                    self.audio_source, self.audio_reason = 'corrected', observed['reason']
                    self.values['audio_verified_at'] = ''
                else:
                    self.values['audio_verified_at'] = ''
                if result['status'] in {'verified','unsupported'}:
                    self._audio_dirty = True
                self._audio_result_dirty = True
                self._update_audio_display()
                self.status.set('音频实测：'+self.audio_hint.get().split('音频实测：',1)[1].split('\n',1)[0])
                self.status_snack.set_kind({'verified':'success','unsupported':'danger','unverified':'warning','cancelled':'neutral'}.get(result['status'],'neutral'))
            try:
                self.window.after(0, finish)
            except Exception:
                pass
        # Finish cancellation and owned temporary cleanup even if Tk exits.
        threading.Thread(target=worker, daemon=False).start()

    def test_connection(self) -> None:
        values, errors = normalize_settings(self.collect())
        if errors:
            self.status.set("；".join(errors))
            self.status_snack.set_kind("danger")
            return
        if not values["base_url"] or not values["model"]:
            self.status.set("先填 API 地址和模型名。")
            self.status_snack.set_kind("warning")
            return
        self.test_button.configure(state="disabled")
        self.status.set("正在测试连接…")
        self.status_snack.set_kind("primary")
        original = api_diagnostics.request_identity(values['base_url'],values['model'],values['api_key'])

        def worker() -> None:
            ok, detail = qwen.probe_api(
                base_url=values["base_url"],
                api_key=values["api_key"],
                model=values["model"],
                timeout=values["request_timeout_sec"],
            )

            def finish() -> None:
                self.test_button.configure(state="normal")
                if api_diagnostics.request_identity(self.base_url.get(),self.model.get(),self.api_key.get()) != original:
                    self.status.set('设置已改变，旧文字连接结果未应用。')
                    return
                self._diagnostic_result = {} if ok else api_diagnostics.test_record(values['base_url'],values['model'],values['api_key'],
                    {'status':'unverified','reason':detail})
                self._update_audio_display()
                self.status.set('文字连接成功（仅文字请求）。' if ok else '文字连接失败：'+api_diagnostics.failure_summary(self._diagnostic_result))
                self.status_snack.set_kind("success" if ok else "danger")
                self.verified = bool(ok)
                if ok:
                    self.enabled.set(True)
                    self.locked.set(True)

            try:
                self.window.after(0, finish)
            except Exception:   # noqa: BLE001 - 窗口已关就什么都不做
                pass

        threading.Thread(target=worker, daemon=True).start()

    def save(self) -> None:
        values, errors = normalize_settings(self.collect())
        if errors:
            self._messagebox.showwarning("API 配置", "；".join(errors), parent=self.window)
            return
        try:
            if not getattr(self, '_audio_dirty', False):
                values = {key:value for key,value in values.items() if not key.startswith("audio_")
                          or (key == 'audio_test_result' and getattr(self, '_audio_result_dirty', False))}
            save_settings(values, self.config_path, verified=self.verified,
                audio_setting_edited_at=getattr(self, '_audio_setting_edited_at', ''),
                audio_setting_edited_sequence=getattr(self, '_audio_setting_edited_sequence', 0))
        except (ValueError, OSError) as error:
            self._messagebox.showerror("API 配置", f"保存失败：{error}", parent=self.window)
            return
        if self.on_saved is not None:
            try:
                self.on_saved(values)
            except Exception as error:   # noqa: BLE001 - keep the saved config, report failed application
                self._messagebox.showerror(
                    "API 配置", f"配置已保存，但切换服务失败：{error}", parent=self.window
                )
                return
        self.close()

    def _on_destroy(self, event):
        if event.widget is self.window:
            self._audio_probe_cancel.set()

    def close(self) -> None:
        self._audio_probe_cancel.set()
        try:
            self.window.after_cancel(self._audio_poll_id)
            self.window.destroy()
        except Exception:   # noqa: BLE001
            pass


def run_standalone(config_path: str | Path | None = None) -> int:
    """Compatibility entry; the standalone API window has been removed."""
    from start_api_settings import main
    return main(config_path)
