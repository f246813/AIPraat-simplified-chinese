"""Separate documented audio capability, test observations and user settings."""
from __future__ import annotations
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Mapping

from . import model_capabilities as caps


def request_identity(base_url, model, api_key):
    values = [str(base_url or '').strip().rstrip('/'), str(model or '').strip(), str(api_key or '').strip(), caps.ADAPTER_VERSION]
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode('utf-8')).hexdigest()


def status_code(detail, explicit=None):
    if isinstance(explicit, int) and not isinstance(explicit, bool) and 100 <= explicit <= 599:
        return explicit
    match = re.search(r'(?i)(?:status[_ ]code\s*[:=]\s*|HTTP\s+)(\d{3})\b', detail)
    return int(match[1]) if match and 100 <= int(match[1]) <= 599 else None


def test_record(base_url, model, api_key, result, *, sequence=None):
    reason = caps.safe_error(RuntimeError(str(result.get('reason', ''))), api_key)
    return {
        'identity': request_identity(base_url, model, api_key),
        'status': result.get('status', 'unverified'),
        'reason': reason,
        'status_code': status_code(reason, result.get('status_code')),
        'tested_at': datetime.now(timezone.utc).isoformat(),
        #: 这次观测是在配置文件第几次音频写入之后做出的（时间并列时用来定先后）。
        'sequence': int(sequence if sequence is not None else result.get('sequence', 0) or 0),
    }


#: 音频设置的写序号键（见 config.ApiConfig.audio_setting_sequence）。
SEQUENCE_KEY = 'audio_setting_sequence'


def _record_sequence(record) -> int:
    if not isinstance(record, Mapping):
        return 0
    try:
        return int(record.get('sequence', 0) or 0)
    except (TypeError, ValueError):
        return 0


def observation_sequence(payload) -> int:
    """这份配置（或界面上的值）里已经记下的音频设置序号。"""

    if not isinstance(payload, Mapping):
        return 0
    try:
        stored = int(payload.get(SEQUENCE_KEY, 0) or 0)
    except (TypeError, ValueError):
        stored = 0
    return max(stored, _record_sequence(payload.get('audio_test_result')))


def next_sequence(payload) -> int:
    """下一次音频写入要用的序号：严格大于这份配置里已记录的。"""

    return observation_sequence(payload) + 1


def observed_later(previous, incoming) -> bool:
    """``previous`` 这次观测是否严格晚于 ``incoming``？

    Windows 的 ``datetime.now`` 粒度约 15 ms：同一个时刻写下的两次观测时间会**并列**，
    而「谁更晚」正是音频设置覆盖规则的唯一依据。并列时改比写序号（大的一方晚）；
    两边都没有序号时按「不更晚」处理——宁可保留已存的纠正，也不要在无从判断时放开。
    """

    previous_at = observation_time((previous or {}).get('tested_at'))
    incoming_at = observation_time((incoming or {}).get('tested_at'))
    if previous_at != incoming_at:
        return previous_at > incoming_at
    return _record_sequence(previous) > _record_sequence(incoming)


def edited_after(edited_at, edited_sequence, previous) -> bool:
    """用户的手动修改是不是发生在 ``previous`` 这次观测**之后**？

    时间并列时看「这次编辑有没有看到那次写入」：``edited_sequence`` 是编辑发生时配置里
    的序号，大于等于观测的序号才算在后。这样「先改后纠正」与「先纠正后改」在同一刻也
    能分出先后，用户的选择不会被随机覆盖。
    """

    edited = observation_time(edited_at)
    stored = observation_time((previous or {}).get('tested_at'))
    if edited != stored:
        return edited > stored
    try:
        seen = int(edited_sequence or 0)
    except (TypeError, ValueError):
        seen = 0
    return seen >= _record_sequence(previous)


def matching_record(record, base_url, model, api_key):
    if not isinstance(record, dict) or record.get('status') not in {'verified','unsupported','unverified','cancelled'}:
        return {}
    if record.get('identity') != request_identity(base_url, model, api_key):
        return {}
    clean = test_record(base_url, model, api_key, record)
    clean['tested_at'] = str(record.get('tested_at', ''))
    return clean


def observation_time(value):
    try:
        moment = datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return moment.replace(tzinfo=timezone.utc) if moment.tzinfo is None else moment.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return datetime.min.replace(tzinfo=timezone.utc)


def failure_summary(result):
    detail = str(result.get('reason', '')).casefold()
    code = result.get('status_code')
    if 'timeout' in detail or 'timed out' in detail or '超时' in detail:
        return '请求超时'
    if code in {401, 403}:
        return '鉴权或权限错误'
    if code == 429:
        return '请求频率受限'
    if code is not None and code >= 500:
        return '服务端错误'
    if 'url' in detail:
        return '资源 URL 或请求参数错误'
    if '特征校验未通过' in detail:
        return '基础音频特征校验未通过'
    if code in {400, 404, 422}:
        return '接口拒绝请求；详见错误详情'
    if any(word in detail for word in ('connection', 'connect', '连接')):
        return '网络连接失败'
    return '请求或结果校验失败；详见错误详情'


def audio_hint(base_url, model, enabled, result=None, diagnostic=None):
    preset = caps.audio_preset(base_url, model)
    support = {'supported':'支持直接音频输入', 'unsupported':'不支持直接音频输入', 'unknown':'直接音频输入能力未知'}[preset.support]
    if not preset.source:
        basis = '未收录'
    elif preset.documented:
        basis = '官方文档'
    else:
        support = '按名称推定' + support
        basis = '网关未验证'
    lines = []
    if diagnostic and diagnostic.get('status') in {'unsupported','unverified'}:
        code = diagnostic.get('status_code')
        name = ' '.join(model.split())[:100] or '未填写'
        lines.append(f"status code: {code if code is not None else '未返回'} · model: {name}")
    lines.append(f'模型预设：{support}（{basis}）')
    status = (result or {}).get('status')
    if status == 'verified':
        text = '基础音频验证通过'
    elif status == 'unsupported':
        text = '接口明确不支持音频输入'
    elif status == 'unverified':
        text = '未能验证——' + failure_summary(result)
    elif status == 'cancelled':
        text = '已取消'
    elif status == 'running':
        text = '正在验证（仅使用随机合成短音频）'
    else:
        text = '尚未验证'
    lines.extend(['音频实测：' + text, '当前设置：直接音频输入' + ('已启用' if enabled else '已关闭')])
    return '\n'.join(lines)
