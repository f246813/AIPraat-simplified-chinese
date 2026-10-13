from __future__ import annotations

import json
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .workflow_policy import WorkflowPolicy


@dataclass(slots=True)
class QwenConfig:
    base_url: str = "http://127.0.0.1:8000/v1"
    model: str = "Qwen/Qwen3.5-0.8B"
    api_key: str = "EMPTY"
    #: 请求形状：``llama.cpp``（本地服务，可以带 ``chat_template_kwargs``）或
    #: ``api``（云端 OpenAI 兼容服务，不能带 llama.cpp 专有字段）。
    provider: str = "llama.cpp"
    request_timeout_sec: int = 60
    default_mode: str = "text"
    enable_thinking: bool = False
    #: 思考档位：``auto`` / ``off`` / ``low`` / ``medium`` / ``high``。
    #: 本地（llama.cpp）翻成 chat 模板的 ``enable_thinking``，云端翻成
    #: ``reasoning_effort``（服务端不认这个字段时会自动退回不带它，见 qwen.py）。
    thinking_level: str = "auto"
    #: ``strict``（默认，只许用工具结果里的数字）/ ``open``（云端大模型可以发挥
    #: 自己的语言学知识去解释、举例，测量数字仍然只能来自工具结果）。
    knowledge_mode: str = "strict"
    vision_when_requested: bool = True
    limit_tokens: bool | None = None
    max_context_tokens: int = 32768
    keep_alive_sec: int = 300
    # 生成参数：小模型和视觉模型对它们的敏感度不同，所以放进配置，
    # 由 server.presets[].qwen 按预设覆盖。
    plan_max_tokens: int = 4096
    plan_temperature: float = 0.1
    top_p: float = 0.8
    presence_penalty: float = 1.5
    audio_input_enabled: bool = False
    # None keeps the legacy frontend request semantics unchanged.
    token_mode: str | None = None

    def __post_init__(self) -> None:
        if self.limit_tokens is None:
            self.limit_tokens = self.provider != "api"


@dataclass(slots=True)
class ApiConfig:
    """云端（OpenAI 兼容）大模型：启用后前端不再依赖本地 llama-server。

    API key 存在 ``ai_config.json`` 里（这个文件在 .gitignore 里、不会进仓库），
    也可以只放在环境变量 ``PRAAT_AI_API_KEY`` 里，界面上的输入框留空即可。
    """

    enabled: bool = False
    #: 锁定云端时顶部模型菜单不能切换到本地；与当前是否正在使用 API 分开保存。
    locked: bool = False
    #: 显示用的名字（例如 DeepSeek / OpenAI），只影响界面和状态文案。
    label: str = ""
    base_url: str = ""
    model: str = ""
    api_key: str = ""
    request_timeout_sec: int = 120
    limit_tokens: bool = False
    max_context_tokens: int = 32768
    plan_max_tokens: int = 1500
    #: 对话正文默认上限为 2048；分析正文使用 plan_max_tokens，推理预留另计。
    dialogue_max_tokens: int = 2048
    plan_temperature: float = 0.1
    vision_when_requested: bool = False
    #: 思考档位（见 :class:`QwenConfig.thinking_level`）。云端大模型默认给「中」，
    #: 又慢又贵的那一档留给需要深想的测量规划。
    thinking_level: str = "medium"
    #: Only high can force extra reasoning on every model-backed conversation.
    force_deep_thinking: bool = False
    #: 云端模型可以发挥自己的语言学/语音学知识（默认开；本地小模型仍然是
    #: ``strict``，免得它拿想象出来的数字当测量结果）。
    use_world_knowledge: bool = True
    #: 启用 API 时要不要顺手停掉本机 llama-server（默认 True：腾出显存）。
    #: 关掉之后本机服务继续跑，切回本地模型时不用重新加载（见 guide.md §8.13）。
    #: 老配置里没有这个键时按 True 读，不写迁移。
    stop_local_service: bool = True
    #: 最近一次「测试连接」成功的时间（只用于显示，不参与逻辑）。
    verified_at: str = ""
    audio_input_enabled: bool | None = None
    audio_input_source: str = "unknown"
    audio_input_reason: str = ""
    audio_input_path: str = ""
    audio_verified_at: str = ""
    audio_corrected_at: str = ""
    audio_test_result: dict[str, Any] = field(default_factory=dict)
    token_mode: str | None = None
    top_p: float | None = None
    presence_penalty: float | None = None


@dataclass(slots=True)
class ServerPreset:
    """一个可以直接切换的本地模型预设。

    ``id`` 是稳定标识（菜单和状态上报都用它）；``label`` 只用于显示。
    ``context_tokens`` / ``n_gpu_layers`` / ``threads`` 为 0 或 ``None`` 时按显存
    自动选择（见 :mod:`praat_ai.vram`），否则覆盖自动值。
    ``qwen`` 里的键会覆盖 ``qwen`` 配置节，用来给不同模型配生成参数
    （例如小模型关闭 thinking、加大 ``max_tokens``）。
    """

    id: str = ""
    label: str = ""
    model_path: str = ""
    mmproj_path: str = ""
    vision: bool = True
    context_tokens: int = 0
    n_gpu_layers: int | None = None
    threads: int | None = None
    qwen: dict[str, Any] = field(default_factory=dict)

    @property
    def display_name(self) -> str:
        return self.label or Path(self.model_path).name or self.id


@dataclass(slots=True)
class ServerConfig:
    llama_server: str = ""
    model_path: str = ""
    mmproj_path: str = ""
    mmproj_by_model: dict[str, str] = field(default_factory=dict)
    presets: list[ServerPreset] = field(default_factory=list)
    active_preset: str = ""
    host: str = "127.0.0.1"
    port: int = 8000
    n_gpu_layers: int = -1
    threads: int = 8
    parallel: int = 1
    auto_start: bool = False


@dataclass(slots=True)
class AnalysisConfig:
    default_error_threshold: float = 1.25
    maximum_errors_per_phone: int = 3


@dataclass(slots=True)
class MfaAlignmentConfig:
    enabled: bool = False
    executable: str = "mfa"
    conda_executable: str = ""
    conda_environment: str = ""
    dictionary_path: str = ""
    dictionary_paths: list[str] = field(default_factory=list)
    acoustic_model: str = ""
    acoustic_models: list[str] = field(default_factory=list)
    beam: int = 10
    retry_beam: int = 40


@dataclass(slots=True)
class Wav2Vec2AlignmentConfig:
    enabled: bool = False
    model: str = ""
    models: list[str] = field(default_factory=list)
    device: str = "cpu"
    blank_token_id: int = 0
    sample_rate: int = 16000
    do_phonemize: bool = False
    token_map: dict[str, str] = field(default_factory=dict)


@dataclass(slots=True)
class AlignmentConfig:
    backend: str = "auto"
    agreement_threshold_sec: float = 0.04
    minimum_confidence: float = 0.45
    mfa: MfaAlignmentConfig = field(default_factory=MfaAlignmentConfig)
    wav2vec2: Wav2Vec2AlignmentConfig = field(
        default_factory=Wav2Vec2AlignmentConfig
    )


@dataclass(slots=True)
class AudioDecoderConfig:
    ffmpeg: str = ""


@dataclass(slots=True)
class AppConfig:
    audio: AudioDecoderConfig = field(default_factory=AudioDecoderConfig)
    qwen: QwenConfig = field(default_factory=QwenConfig)
    server: ServerConfig = field(default_factory=ServerConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    analysis: AnalysisConfig = field(default_factory=AnalysisConfig)
    alignment: AlignmentConfig = field(default_factory=AlignmentConfig)
    local_qwen: QwenConfig | None = field(default=None, repr=False)
    workflow: WorkflowPolicy | None = field(default=None, repr=False)

    def to_dict(self) -> dict[str, Any]:
        values = asdict(self)
        values.pop("local_qwen", None)
        values.pop("workflow", None)
        return values


def default_config_path() -> Path:
    # PRAAT_AI_CONFIG_PATH 可替换整个配置文件，供独立配置启动使用。
    override = os.getenv("PRAAT_AI_CONFIG_PATH", "").strip()
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[1] / "ai_config.json"


def _merge_dataclass(instance: Any, values: dict[str, Any]) -> None:
    for key, value in values.items():
        if hasattr(instance, key):
            setattr(instance, key, value)


def preset_from_raw(value: dict[str, Any], index: int = 0) -> ServerPreset:
    """Build a :class:`ServerPreset` from one JSON entry (tolerant to typos)."""

    model_path = str(value.get("model_path", "") or "")
    preset = ServerPreset(
        id=str(value.get("id", "") or "").strip(),
        label=str(value.get("label", "") or value.get("name", "") or "").strip(),
        model_path=model_path,
        mmproj_path=str(value.get("mmproj_path", "") or ""),
    )
    if not preset.id:
        preset.id = Path(model_path).name or f"preset-{index + 1}"
    if "vision" in value:
        preset.vision = bool(value.get("vision"))
    for key in ("context_tokens", "n_gpu_layers", "threads"):
        if value.get(key) not in (None, ""):
            try:
                setattr(preset, key, int(value[key]))
            except (TypeError, ValueError):
                pass
    qwen = value.get("qwen")
    if isinstance(qwen, dict):
        preset.qwen = dict(qwen)
    return preset


def presets_from_raw(value: Any) -> list[ServerPreset]:
    if isinstance(value, dict):
        # 兼容 {id: {...}} 写法：把键当成 id。
        items = []
        for key, entry in value.items():
            if isinstance(entry, dict):
                merged = dict(entry)
                merged.setdefault("id", key)
                items.append(merged)
        return [preset_from_raw(entry, index) for index, entry in enumerate(items)]
    if isinstance(value, list):
        return [
            preset_from_raw(entry, index)
            for index, entry in enumerate(value)
            if isinstance(entry, dict)
        ]
    return []


def api_is_active(config: AppConfig) -> bool:
    """API 模式是不是真的能用（启用了、而且地址和模型都填了）。"""

    return bool(
        config.api.enabled
        and config.api.base_url.strip()
        and config.api.model.strip()
    )


#: 思考档位的合法取值。``auto`` 表示「不指定」，交给服务端/模型自己的默认。
THINKING_LEVELS = ("auto", "off", "low", "medium", "high")

#: 界面上认的写法（中英文、数字都收）。
_THINKING_ALIASES = {
    "": "auto",
    "auto": "auto",
    "default": "auto",
    "自动": "auto",
    "off": "off",
    "none": "off",
    "no": "off",
    "关闭": "off",
    "0": "off",
    "low": "low",
    "低": "low",
    "1": "low",
    "medium": "medium",
    "mid": "medium",
    "中": "medium",
    "2": "medium",
    "high": "high",
    "高": "high",
    "3": "high",
}


def normalize_thinking_level(value: Any) -> str:
    """把配置/界面里的思考档位收成 :data:`THINKING_LEVELS` 里的一个。"""

    return _THINKING_ALIASES.get(str(value or "").strip().casefold(), "auto")


def apply_api_to_qwen(config: AppConfig) -> None:
    """把 API 节的设置搬进 ``qwen``（只在 API 模式可用时）。

    对话链路里到处是 ``config.qwen``（客户端、状态栏、token 预算），所以搬进这一节
    之后**不用改别的地方**就能切到云端模型；本地 llama-server 的配置原样留着，
    关掉 API 就能回去用。
    """

    if not api_is_active(config):
        return
    api = config.api
    config.qwen.base_url = api.base_url.strip().rstrip("/")
    config.qwen.model = api.model.strip()
    config.qwen.api_key = api.api_key or "EMPTY"
    config.qwen.provider = "api"
    config.qwen.request_timeout_sec = int(api.request_timeout_sec)
    config.qwen.max_context_tokens = int(api.max_context_tokens)
    config.qwen.plan_max_tokens = int(api.plan_max_tokens)
    config.qwen.limit_tokens = bool(api.limit_tokens)
    config.qwen.plan_temperature = (None if api.token_mode is not None and api.plan_temperature is None
                                    else float(api.plan_temperature))
    config.qwen.token_mode = api.token_mode
    if api.token_mode is not None:
        config.qwen.top_p = api.top_p
        config.qwen.presence_penalty = api.presence_penalty
    config.qwen.vision_when_requested = bool(api.vision_when_requested)
    config.qwen.audio_input_enabled = bool(api.audio_input_enabled)
    # 云端模型的思考档位走 reasoning_effort（见 qwen.thinking_request_fields），
    # 不用 llama.cpp 的 chat 模板开关（那是本地服务专有的）。
    config.qwen.thinking_level = normalize_thinking_level(api.thinking_level)
    config.qwen.enable_thinking = config.qwen.thinking_level not in {"off", "auto"}
    # 云端大模型可以用自己的知识解释（默认开）；本地小模型不让它发挥，免得编数字。
    config.qwen.knowledge_mode = "open" if api.use_world_knowledge else "strict"
    # API 模式下不许悄悄拉起本地 llama-server。
    config.server.auto_start = False


def load_config(path: str | Path | None = None) -> AppConfig:
    config = AppConfig()
    candidate = Path(path) if path else default_config_path()
    raw = {}
    if candidate.is_file():
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        _merge_dataclass(config.audio, raw.get("audio", {}))
        local_values = raw.get("qwen") or {}
        _merge_dataclass(config.qwen, local_values)
        # Upgrade the old shipped pair only; preserve individually tuned limits.
        if ("limit_tokens" not in local_values
                and local_values.get("max_context_tokens") == 8192
                and local_values.get("plan_max_tokens") == 700):
            config.qwen.max_context_tokens = 32768
            config.qwen.plan_max_tokens = 4096
        server_values = dict(raw.get("server", {}))
        presets_value = server_values.pop("presets", None)
        _merge_dataclass(config.server, server_values)
        if presets_value is not None:
            config.server.presets = presets_from_raw(presets_value)
        _merge_dataclass(config.analysis, raw.get("analysis", {}))
        api_values = raw.get("api", {})
        _merge_dataclass(config.api, api_values)
        # 旧版「使用云端 API」兼作锁定开关；首次读取时沿用其选择。
        if "locked" not in api_values:
            config.api.locked = bool(config.api.enabled)
        alignment_values = raw.get("alignment", {})
        _merge_dataclass(
            config.alignment,
            {
                key: value
                for key, value in alignment_values.items()
                if key not in {"mfa", "wav2vec2"}
            },
        )
        _merge_dataclass(
            config.alignment.mfa,
            alignment_values.get("mfa", {}),
        )
        _merge_dataclass(
            config.alignment.wav2vec2,
            alignment_values.get("wav2vec2", {}),
        )

    # API 节的环境变量覆盖（key 不外放时用得上；地址/模型也留了口子）。
    config.api.api_key = os.getenv("PRAAT_AI_API_KEY", config.api.api_key)
    config.api.base_url = os.getenv("PRAAT_AI_API_BASE_URL", config.api.base_url)
    config.api.model = os.getenv("PRAAT_AI_API_MODEL", config.api.model)

    config.qwen.base_url = os.getenv("PRAAT_AI_QWEN_BASE_URL", config.qwen.base_url)
    config.qwen.model = os.getenv("PRAAT_AI_QWEN_MODEL", config.qwen.model)
    config.qwen.api_key = os.getenv("PRAAT_AI_QWEN_API_KEY", config.qwen.api_key)
    config.qwen.default_mode = os.getenv("PRAAT_AI_QWEN_MODE", config.qwen.default_mode)

    env_server = os.getenv("PRAAT_AI_LLAMA_SERVER")
    env_model = os.getenv("PRAAT_AI_QWEN_MODEL_PATH")
    env_mmproj = os.getenv("PRAAT_AI_QWEN_MMPROJ_PATH")
    if env_server:
        config.server.llama_server = env_server
    if env_model:
        config.server.model_path = env_model
    if env_mmproj:
        config.server.mmproj_path = env_mmproj

    # Preserve an existing Anki decoder path when a model/settings save first
    # serializes the shared audio section. An explicit empty value means auto.
    if "ffmpeg" not in raw.get("audio", {}):
        legacy = candidate.parent / "anki_config.json"
        if legacy.is_file():
            config.audio.ffmpeg = str(json.loads(legacy.read_text(encoding="utf-8-sig")).get("ffmpeg", ""))
    from .model_capabilities import initialize_audio
    initialize_audio(config.api)
    config.local_qwen = deepcopy(config.qwen)
    apply_api_to_qwen(config)

    return config
