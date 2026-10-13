"""Allowlisted, lossless settings merge and per-task request configuration."""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
from dataclasses import asdict, fields
from pathlib import Path
from urllib.parse import urlsplit

from .config import ApiConfig, QwenConfig, load_config, apply_api_to_qwen
from .model_capabilities import initialize_audio
from .service_lock import service_transition_lock
from .workflow_policy import WorkflowPolicy

PREFERENCES = dict(theme='system', font_size=15, send_key='enter', smooth_stream=True, sidebar_width=272, conversation_width=850, sidebar_sort='recent', sidebar_grouping='project', sidebar_retained_time_groups=[])
# Window partition sizes are layout preferences, not model parameters: they are
# clamped here so a hand-edited config cannot produce an unusable shell.
PREFERENCE_RANGES = {'font_size': (12, 24), 'sidebar_width': (240, 520), 'conversation_width': (560, 4000)}
API_EDITABLE = {f.name for f in fields(ApiConfig)} - {'audio_test_result', 'audio_verified_at', 'verified_at', 'audio_corrected_at', 'audio_input_source', 'audio_input_reason', 'audio_input_path'}
LOCAL_EDITABLE = {f.name for f in fields(QwenConfig)}


def derived_token_mode(section) -> str:
    """界面看到的 ``token_mode``：文件里没写时按 ``limit_tokens`` 推出来的那个。

    和 :meth:`SettingsService.get` 用同一条公式（``dataclass`` 或普通 ``dict`` 都吃）。
    这个值是**派生**的：界面把它回显出来，用户并没有做过这个选择。
    """

    def read(key):
        return section.get(key) if isinstance(section, dict) else getattr(section, key, None)

    explicit = read('token_mode')
    return str(explicit) if explicit else ('manual' if read('limit_tokens') else 'provider')


def assert_endpoint(url, *, allow_cloud=False):
    parsed = urlsplit(str(url))
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('请输入不含凭据的 HTTP(S) API 地址')
    if not allow_cloud and parsed.hostname.lower() not in {'127.0.0.1', 'localhost', '::1'}:
        raise PermissionError('真实云端请求尚未单独授权；当前启动未启用 --allow-cloud')


def validate_section(value):
    value = copy.deepcopy(value)
    if 'token_mode' in value and value['token_mode'] not in {'auto', 'manual', 'provider', None}:
        raise ValueError('token 模式无效')
    for name, lo, hi in [('max_context_tokens', 1024, 2000000), ('plan_max_tokens', 128, 200000),
                         ('dialogue_max_tokens', 128, 200000), ('request_timeout_sec', 1, 3600),
                         ('plan_temperature', 0, 2), ('top_p', 0, 1), ('presence_penalty', -2, 2)]:
        if name in value and value[name] is not None:
            number = float(value[name])
            if not lo <= number <= hi:
                raise ValueError(f'{name} 必须在 {lo}–{hi} 范围内')
            value[name] = int(number) if name.endswith('tokens') or name.endswith('_sec') else number
    if value.get('thinking_level', 'auto') not in {'auto', 'off', 'low', 'medium', 'high'}:
        raise ValueError('思考档位无效')
    return value


class SettingsService:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.guard = threading.RLock()

    def raw(self):
        return json.loads(self.path.read_text(encoding='utf-8')) if self.path.is_file() else {}

    def get(self):
        with self.guard:
            config, raw = load_config(self.path), self.raw()
            local = config.local_qwen or config.qwen
            api, local = asdict(config.api), asdict(local)
            for section in (api, local):
                section['has_api_key'] = bool(section.pop('api_key', '') not in {'', 'EMPTY'})
                section['api_key'] = ''
                section['token_mode'] = derived_token_mode(section)
            # Diagnostic responses could contain credentials; never expose raw records.
            api.pop('audio_test_result', None)
            return dict(api=api, local=local, server=dict(model_path=config.server.model_path), preferences={**PREFERENCES, **raw.get('modern', {}).get('preferences', {})},
                        analysis=asdict(config.analysis), alignment=asdict(config.alignment),
                        workflow=asdict(WorkflowPolicy.validate(raw.get("modern", {}).get("workflow"))))

    @staticmethod
    def merge_section(base, incoming, allowed, *, derived_token_mode=None):
        result = dict(base)
        incoming = validate_section(incoming)
        # ``token_mode`` 是给界面看的派生值（见 :func:`derived_token_mode`）。界面整段保存
        # 会把同一个值原样送回，一旦落盘，所有按 ``token_mode is not None`` 分支的读者
        # （老 Tk 窗口 :mod:`praat_ai.cloud_workflow`、本地模型的 payload）都会换一条路，
        # 而用户并没有做过这个选择。等于派生值的回显不算选择，不写。
        if ('token_mode' in incoming and base.get('token_mode') is None
                and derived_token_mode is not None
                and incoming['token_mode'] == derived_token_mode):
            incoming = {key: value for key, value in incoming.items() if key != 'token_mode'}
        for name in allowed:
            if name in incoming and name != 'api_key':
                if isinstance(result.get(name), bool) and not isinstance(incoming[name], bool):
                    raise ValueError(name + ' 必须是布尔值')
                result[name] = incoming[name]
        if incoming.get('clear_api_key'):
            result['api_key'] = ''
        elif incoming.get('api_key'):
            result['api_key'] = str(incoming['api_key'])
        return result

    @staticmethod
    def local_model_update(config, settings):
        incoming = settings.get('server', {})
        if not isinstance(incoming, dict):
            raise ValueError('本地模型路径设置无效')
        value = incoming.get('model_path', config.server.model_path)
        if not isinstance(value, str):
            raise ValueError('本地模型路径必须是文本')
        value = value.strip().strip('"')
        if value == config.server.model_path:
            return None
        if value and (Path(value).suffix.lower() != '.gguf' or not Path(value).is_file()):
            raise ValueError('请选择存在的 GGUF 本地模型文件')
        from .presets import find_preset
        preset = find_preset(config, value)
        return dict(model_path=value, active_preset=preset.id if preset else '')

    def candidate(self, settings=None):
        with self.guard:
            cfg = copy.deepcopy(load_config(self.path))
            if settings:
                for name, obj, allowed in [('api', cfg.api, API_EDITABLE), ('local', cfg.local_qwen, LOCAL_EDITABLE)]:
                    merged = self.merge_section(asdict(obj), settings.get(name, {}), allowed,
                                                derived_token_mode=derived_token_mode(obj))
                    for key, value in merged.items():
                        setattr(obj, key, value)
                model_update = self.local_model_update(cfg, settings)
                if model_update:
                    for key, value in model_update.items():
                        setattr(cfg.server, key, value)
                    if model_update['model_path']:
                        cfg.local_qwen.model = Path(model_update['model_path']).name
            raw_workflow = self.raw().get("modern", {}).get("workflow", {})
            incoming_workflow = (settings or {}).get('workflow', {})
            if not isinstance(incoming_workflow, dict):
                raise ValueError('工作流程必须是对象')
            cfg.workflow = WorkflowPolicy.validate({**raw_workflow, **incoming_workflow})
            cfg.qwen = copy.deepcopy(cfg.local_qwen)
            if cfg.api.locked and not cfg.api.enabled:
                raise ValueError('云端锁定时不能切换本地模型；请先解除锁定')
            initialize_audio(cfg.api)
            apply_api_to_qwen(cfg)
            return cfg

    def save(self, settings):
        with self.guard, service_transition_lock(Path(str(self.path) + '.lock')):
            raw = self.raw()
            effective = load_config(self.path)
            local_effective = effective.local_qwen or effective.qwen
            # Merge raw sections, not env-overridden effective config (do not persist env secrets).
            for name, dest, allowed, section in [('api', 'api', API_EDITABLE, effective.api),
                                                 ('local', 'qwen', LOCAL_EDITABLE, local_effective)]:
                raw[dest] = self.merge_section(raw.get(dest, {}), settings.get(name, {}), allowed,
                                               derived_token_mode=derived_token_mode(section))
            model_update = self.local_model_update(effective, settings)
            if model_update:
                raw.setdefault('server', {}).update(model_update)
                if model_update['model_path']:
                    raw['qwen']['model'] = Path(model_update['model_path']).name
            if raw['api'].get('locked') and raw['api'].get('enabled') is False:
                raise ValueError('云端锁定时不能切换本地模型')
            for name in ('analysis', 'alignment'):
                if name in settings:
                    base = asdict(getattr(load_config(self.path), name))
                    def merge_known(original, source, schema):
                        result = dict(original)
                        for key, value in source.items():
                            if key in schema:
                                result[key] = merge_known(result.get(key, {}), value, schema[key]) if isinstance(schema[key], dict) else value
                        return result
                    raw[name] = merge_known(raw.get(name, {}), settings[name], base)
            prefs = {**PREFERENCES, **raw.get('modern', {}).get('preferences', {}), **settings.get('preferences', {})}
            if prefs['theme'] not in {'system', 'light', 'dark'} or prefs['send_key'] not in {'enter', 'ctrl-enter'}:
                raise ValueError('外观或快捷键设置无效')
            if not isinstance(prefs['sidebar_sort'], str) or prefs['sidebar_sort'] not in {'recent', 'oldest', 'name', 'created'}:
                raise ValueError('会话排序设置无效')
            if not isinstance(prefs['sidebar_grouping'], str) or prefs['sidebar_grouping'] not in {'project','connection','list'}:
                raise ValueError('侧边栏整理设置无效')
            for name, (low, high) in PREFERENCE_RANGES.items():
                try:
                    number = int(round(float(prefs[name])))
                except (TypeError, ValueError):
                    number = PREFERENCES[name]
                prefs[name] = min(high, max(low, number))
            if 'workflow' in settings:
                workflow = settings['workflow']
                if not isinstance(workflow, dict):
                    raise ValueError('工作流程必须是对象')
                existing = raw.get('modern', {}).get('workflow', {})
                effective_workflow = WorkflowPolicy.validate({**existing, **workflow})
                raw.setdefault('modern', {})['workflow'] = {**existing, **asdict(effective_workflow)}
            raw.setdefault('modern', {})['preferences'] = {k: prefs[k] for k in PREFERENCES}
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = None
            try:
                with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=self.path.parent, prefix=self.path.name + '.', suffix='.tmp', delete=False) as handle:
                    temporary = Path(handle.name)
                    json.dump(raw, handle, ensure_ascii=False, indent=2)
                os.replace(temporary, self.path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return self.get()
