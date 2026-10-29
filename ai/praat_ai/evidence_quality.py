"""Evidence-side F0 screening/provenance; never a physiological diagnosis.

A returned finite value is a tracker candidate, not proof of correct periodicity.
The tool's screening receipt is interpreted conservatively. Legacy results and
existing Pitch objects without waveform checks remain unverified; low Hz alone
is not an error. Diagnostic counters/thresholds are not measured F0 quantities.
"""
from __future__ import annotations

import math
import re
from typing import Any

from .tools import parse_object_context

from .pitch_quality import PULSE_PREFIX, QUALITY_PREFIX

_PITCH = re.compile(r'基频|\bF0\b|pitch', re.I)

#: 基频与脉冲的回执都是“质量事实行”：里面的阈值/帧数/点数不是实测值。
QUALITY_PREFIXES = (QUALITY_PREFIX, PULSE_PREFIX)


def is_quality_row(row: str) -> bool:
    return str(row).lstrip().startswith(QUALITY_PREFIXES)

def is_pitch_evidence(evidence: Any) -> bool:
    """基频/脉冲这类“周期性”证据；``measure`` 只有出现基频行才算。"""

    tool = str(getattr(evidence, 'tool', ''))
    return tool in {'pitch', 'pitch_statistics', 'pitch_peak_latency', 'pulses'} or (
        tool == 'measure' and any(_PITCH.search(row) for row in getattr(evidence, 'rows', [])))

def source_object(evidence: Any):
    rows = parse_object_context(getattr(evidence, 'source', '') or '')
    requested = getattr(evidence, 'arguments', {}).get('object')
    if requested is not None:
        return next((r for r in rows if str(requested) in {str(r.id), r.name, f'{r.class_name} {r.name}'}), None)
    selected = [r for r in rows if r.selected]
    return selected[0] if len(selected) == 1 else None


def pitch_configuration(evidence: Any) -> tuple:
    """Unknown existing-Pitch extraction settings must not inherit Sound defaults."""
    args = getattr(evidence, 'arguments', {}) or {}
    acoustic = getattr(evidence, 'acoustic', {}) or {}
    method = (acoustic.get('method') or {}).get('name')
    source = source_object(evidence)
    derived = source is not None and source.class_name == 'Sound'
    def bound(name, default):
        value = args.get(name, default if derived else None)
        try:
            value = float(value)
            return value if math.isfinite(value) and value > 0 else None
        except (TypeError, ValueError):
            return None
    # Existing Pitch queries cannot change the object's extraction settings.
    if source is not None and source.class_name == 'Pitch':
        return (source.id, 'Pitch', None, None, method)
    return (source.id if source is not None else args.get('object'),
            source.class_name if source is not None else 'unknown',
            bound('pitch_floor', 75), bound('pitch_ceiling', 600), method)


def pitch_quality(evidence: Any) -> dict:
    rows = list(getattr(evidence, 'rows', []) or [])
    receipts = [row.strip() for row in rows if is_quality_row(row)]
    # One suspect receipt wins over any screened receipt in a multi-point call.
    status = 'unverified'
    if any('待复核' in row for row in receipts):
        status = 'suspect'
    elif receipts and all('已筛查' in row for row in receipts):
        status = 'screened'
    return dict(status=status, configuration=list(pitch_configuration(evidence)),
                diagnostics=receipts[:8])


def can_interpret_pitch(evidence: Any) -> bool:
    return is_pitch_evidence(evidence) and pitch_quality(evidence)['status'] == 'screened'


def compatible_pitch_configurations(evidences) -> bool:
    configurations = {pitch_configuration(item) for item in evidences if is_pitch_evidence(item)}
    return bool(configurations) and len(configurations) == 1


def report_quality_context(state: Any) -> dict:
    records = [(f'E{i}', item) for i, item in enumerate(getattr(state, 'evidence', []) or [], 1)
               if is_pitch_evidence(item)]
    if not records:
        return {}
    facts = {eid: pitch_quality(item) for eid, item in records}
    groups = {}
    for eid, item in records:
        config = pitch_configuration(item)
        groups.setdefault(config[:2], {}).setdefault(config[2:], []).append(eid)
    mixed = [list(eid for ids in configs.values() for eid in ids)
             for configs in groups.values() if len(configs) > 1]
    return dict(pitch_quality=facts, incompatible_pitch_settings=mixed,
                pitch_quality_guidance=(
                    '已筛查只是声学筛查通过，不是正确F0或重音诊断认证；未核验/待复核只可报告为候选，不能据此判断语调或发声问题。'
                    '不同提取频带的同名指标须分别归属，不能混作一条轨迹；不能凭平均绝对斜率或首尾相近断言整段平淡。'))
