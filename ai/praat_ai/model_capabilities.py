"""Explicit audio input facts, routing declarations and compare-and-save correction.

Never infer audio understanding from transcription/TTS or an SDK profile default.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

ADAPTER_VERSION = 'openai-chat-audio-v1'
DOCUMENTED_AT = '2026-10-02'


@dataclass(frozen=True)
class AudioCapability:
    enabled: bool = False
    support: str = 'unknown'
    reason: str = '未收录，默认关闭；可手动启用或验证'
    source: str = ''
    stream: bool = False
    availability: str = 'unknown'
    documented: bool = False


_PRESETS = {
    'qwen3-omni-flash': ('qwen', True),
    'qwen3.8-omni-flash': ('qwen', True),
    'qwen3.5-omni-plus': ('qwen', True),
    'qwen3.7-flash': ('qwen', False),
    'gemini-3.8-flash': ('google', True),
    'gemini-2.5-flash': ('google', True),
    'gemini-2.5-pro': ('google', True),
    'gpt-audio': ('openai', True),
    'gpt-audio-mini': ('openai', True),
    'gpt-4o-audio-preview': ('openai', True),
}
_SOURCES = {
    'qwen': 'https://help.aliyun.com/zh/model-studio/qwen-omni',
    'google': 'https://ai.google.dev/gemini-api/docs/openai',
    'openai': 'https://developers.openai.com/api/docs/guides/audio',
}


def audio_preset(base_url: str, model: str) -> AudioCapability:
    entry = _PRESETS.get(model.strip().casefold())
    if entry is None:
        return AudioCapability()
    supplier, enabled = entry
    host = (urlsplit(base_url).hostname or '').casefold()
    official = (
        (supplier == 'qwen' and (host == 'dashscope.aliyuncs.com' or host.endswith('.maas.aliyuncs.com') or host == 'dashscope-intl.aliyuncs.com'))
        or (supplier == 'google' and host == 'generativelanguage.googleapis.com')
        or (supplier == 'openai' and host == 'api.openai.com')
    )
    return AudioCapability(enabled, 'supported' if enabled else 'unsupported',
                           '模型预设，尚未实测' if official else '按名称推定，网关未验证',
                           'https://help.aliyun.com/zh/model-studio/qwen3-7-flash' if model.strip().casefold() == 'qwen3.7-flash' else _SOURCES[supplier], supplier == 'qwen' and enabled, documented=official)


def path_identity(api) -> str:
    value = [api.base_url.strip().rstrip('/'), api.model.strip(), ADAPTER_VERSION]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def config_identity(api) -> str:
    account = hashlib.sha256(str(api.api_key).encode()).hexdigest()
    value = [path_identity(api), account, api.audio_input_enabled, api.audio_input_source, api.audio_input_reason]
    return hashlib.sha256(json.dumps(value).encode()).hexdigest()


def initialize_audio(api) -> None:
    identity = path_identity(api)
    if api.audio_input_enabled is None or (api.audio_input_path and api.audio_input_path != identity):
        preset = audio_preset(api.base_url, api.model)
        api.audio_input_enabled = preset.enabled
        api.audio_input_source = 'preset' if preset.source else 'unknown'
        api.audio_input_reason = preset.reason
        api.audio_verified_at = ''
    api.audio_input_path = identity


def is_audio_unsupported(status: int | None, detail: str) -> bool:
    if status not in {400, 404, 422}:
        return False
    text = detail.casefold()
    # Field/encoding/format failures do not establish capability absence.
    if re.search(r'(format|encoding|size|duration|too large|invalid.*(?:data|base64)|格式|大小|时长)', text):
        return False
    # Output/TTS capability cannot establish whether the model accepts input.
    # Require an explicit input rejection when both directions are mentioned.
    output = r'\b(?:output(?:ting|s)?|generat(?:e|ing|ion)|produc(?:e|ing|tion)|synthesi[sz](?:e|ing)|synthesis|tts)\b|text.to.(?:speech|audio)|输出|生成|合成'
    input_term = r'(?:audio[ _-]*input|input[ _-]*audio|音频输入)'
    input_rejection = (input_term + r'[\s:\"\x27]*(?:modality\s+)?(?:is\s+|are\s+)?(?:not supported|unsupported|不支持)'
                       + r'|(?:does not support|not support|unsupported|不支持)\s*(?:the\s+|raw\s+|direct\s+|原始|直接)?'
                       + input_term)
    if re.search(output, text) and not re.search(input_rejection, text):
        return False
    return bool(re.search(
        r'(?:model|endpoint|模型|端点).{0,100}(?:does not support|not support|unsupported|不支持).{0,50}(?:audio|音频)'
        r'|(?:audio input|audio modality|音频输入).{0,60}(?:not supported|unsupported|不支持)'
        r'|(?:supported modalities|only supports).{0,30}(?:text|image).{0,30}(?:audio)', text))


def correct_audio_setting(path: Path, expected_identity: str, reason: str) -> bool:
    from .config import load_config
    from .service_lock import service_transition_lock
    from datetime import datetime, timezone
    from . import api_diagnostics
    path = Path(path)
    with service_transition_lock(Path(str(path) + '.lock')):
        config = load_config(path)
        if config_identity(config.api) != expected_identity:
            return False
        payload = json.loads(path.read_text(encoding='utf-8')) if path.exists() else config.to_dict()
        from . import api_diagnostics
        # 序号在写入时递增：并列的时间戳也能分出「谁更晚」（见 ApiConfig.audio_setting_sequence）。
        sequence = api_diagnostics.next_sequence(payload)
        payload[api_diagnostics.SEQUENCE_KEY] = sequence
        payload.setdefault('api', {}).update({
            'audio_input_enabled': False, 'audio_input_source': 'corrected',
            'audio_input_reason': reason, 'audio_input_path': path_identity(config.api),
            'audio_verified_at': '', 'audio_corrected_at': datetime.now(timezone.utc).isoformat(),
            'audio_test_result': api_diagnostics.test_record(config.api.base_url, config.api.model, config.api.api_key,
                {'status':'unsupported','reason':reason}, sequence=sequence),
        })
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                             prefix=path.name + '.', suffix='.tmp', delete=False) as handle:
                temporary = Path(handle.name)
                json.dump(payload, handle, ensure_ascii=False, indent=2)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return True


def safe_error(error: BaseException, api_key: str = '') -> str:
    text = str(error)
    if api_key and api_key != 'EMPTY':
        text = text.replace(api_key, '[凭据已隐藏]')
    text = re.sub(r'(?i)(Bearer\s+)[\w.\-]+', r'\1[隐藏]', text)
    return text[:3000]
