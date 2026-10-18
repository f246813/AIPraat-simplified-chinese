"""轻量遥测：可聚合的指标 + 裁剪后的调试快照。

设计见 ``ai/docs/backend-workflow-redesign.zh-CN.md`` 第 9 节（P5）。要解决两件事：

1. ``state.metrics`` 现在混装四类东西——执行事实、性能、守卫计数、整份被拒报告。
   被拒报告全文随 evidence 表落盘（真机事故那条记录里就有），既占空间也没人读；
2. 规则命中只能靠手工扫 SQLite 才能汇总（这次统计就是这么做的）。

这里的口径：**telemetry 只放能聚合的数字/短枚举**，原文一概不进；
需要原文时去任务目录或 debug 快照里找。
"""
from __future__ import annotations

import json

#: 调试快照的体积上限（字节，按 UTF-8 估算）。超了就按下面的顺序继续丢。
DEBUG_LIMIT = 2048
#: 请求耗时最多保留最近这么多条。
TIMING_LIMIT = 24
#: 单个字符串字段的截断长度。
TEXT_LIMIT = 240

#: 只在内存里有意义、不随任务落盘的键（整份草稿/覆盖项）。
_HEAVY = ('rejected_report', 'rejected_coverage', 'unverified_draft')

#: 体积超标时依次丢弃的键（先丢最不影响排查的）。
_TRIM_ORDER = ('workflow_routes', 'phases', 'request_timings', 'audit', 'router')


def collect(metrics: dict | None, *, status: str = '') -> dict:
    """把一次任务的 metrics 折成一行遥测（纯数字 + 短枚举，可直接聚合）。"""

    metrics = metrics or {}
    audit = metrics.get('audit') or {}
    router = metrics.get('router') or {}
    blocks = metrics.get('guard_blocks') or {}
    repairs = metrics.get('guard_repairs') or {}
    return {
        'status': str(status or metrics.get('status') or ''),
        'requests': int(metrics.get('requests') or 0),
        'input_tokens': int(metrics.get('input_tokens') or 0),
        'output_tokens': int(metrics.get('output_tokens') or 0),
        'wall_seconds': _round(metrics.get('total_seconds')),
        'first_text_seconds': _round(metrics.get('first_text_seconds')),
        'l2_seconds': _round(metrics.get('l2_seconds')),
        'report_verified': bool(metrics.get('report_verified')),
        'finish_state': str(metrics.get('finish_state') or ''),
        'reason_class': str(metrics.get('reason_class') or ''),
        'masked': int(audit.get('masked_count') or 0),
        'audit_rounds': int(audit.get('rounds') or 0),
        'derived_accepted': int(metrics.get('report_derived_accepted') or 0),
        'general_assumed': int(metrics.get('report_general_value_assumed') or 0),
        'blocked': int(sum(blocks.values())),
        'repaired': int(sum(repairs.values())),
        'router_source': str(router.get('source') or ''),
        'router_intent': str(router.get('intent') or ''),
        'router_confidence': _round(router.get('confidence')),
    }


def counters(metrics: dict | None) -> list[dict]:
    """把守卫/审计计数摊平成 ``[{rule, kind, count}]``，供入库聚合。"""

    metrics = metrics or {}
    rows: list[dict] = []
    for kind, bucket in (('block', 'guard_blocks'), ('repair', 'guard_repairs')):
        for rule, count in (metrics.get(bucket) or {}).items():
            rows.append({'rule': str(rule), 'kind': kind, 'count': int(count)})
    for rule in ('report_derived_accepted', 'report_general_value_assumed'):
        if metrics.get(rule):
            rows.append({'rule': rule, 'kind': 'repair', 'count': int(metrics[rule])})
    return rows


def payload(metrics: dict | None, *, status: str = '') -> dict:
    """落库用的完整遥测行：可聚合指标 + 规则命中计数（``summarize`` 直接吃它）。"""

    row = collect(metrics, status=status)
    row['counters'] = {item['rule']: item['count'] for item in counters(metrics)}
    row['guard_blocks'] = dict((metrics or {}).get('guard_blocks') or {})
    return row


def slim(metrics: dict | None, *, limit: int = DEBUG_LIMIT) -> dict:
    """给落盘的调试快照：丢掉整份草稿、截断长文本、超限继续丢次要字段。"""

    source = dict(metrics or {})
    for key in _HEAVY:
        value = source.pop(key, None)
        if value:
            source[f'{key}_dropped'] = len(value) if isinstance(value, (str, list)) else 1
    for key, value in list(source.items()):
        if isinstance(value, str) and len(value) > TEXT_LIMIT:
            source[key] = value[:TEXT_LIMIT] + '…'
    timings = source.get('request_timings')
    if isinstance(timings, list) and len(timings) > TIMING_LIMIT:
        source['request_timings'] = timings[-TIMING_LIMIT:]
        source['request_timings_dropped'] = len(timings) - TIMING_LIMIT
    for key in _TRIM_ORDER:
        if _size(source) <= limit:
            break
        if key in source:
            source.pop(key)
            source[f'{key}_dropped'] = 1
    if _size(source) > limit:
        # 还超就只留短标量：字符串再截一刀，列表/字典换成计数，然后按顺序丢键。
        trimmed: dict = {}
        for key, value in source.items():
            if isinstance(value, str):
                trimmed[key] = value[:64] + ('…' if len(value) > 64 else '')
            elif isinstance(value, (int, float, bool)) or value is None:
                trimmed[key] = value
            elif isinstance(value, (list, dict)):
                trimmed[key] = f'[已裁剪 {len(value)} 项]'
        for key in list(trimmed):
            if _size(trimmed) <= limit:
                break
            trimmed.pop(key)
            trimmed['truncated_keys'] = trimmed.get('truncated_keys', 0) + 1
        source = trimmed
    return source


def summarize(rows) -> dict:
    """多任务聚合：给「守卫命中率」「验证通过率」这类问题一个直接答案。"""

    rows = [row for row in rows if isinstance(row, dict)]
    total = len(rows)
    if not total:
        return {'runs': 0}
    rules: dict[str, int] = {}
    blocks: dict[str, int] = {}
    for row in rows:
        for rule, count in (row.get('counters') or {}).items():
            rules[rule] = rules.get(rule, 0) + int(count)
        for rule, count in (row.get('guard_blocks') or {}).items():
            blocks[rule] = blocks.get(rule, 0) + int(count)
    return {
        'runs': total,
        'verified': sum(1 for row in rows if row.get('report_verified')),
        'verified_rate': round(sum(1 for row in rows if row.get('report_verified')) / total, 3),
        'requests_avg': round(sum(row.get('requests') or 0 for row in rows) / total, 2),
        'wall_seconds_avg': round(sum(row.get('wall_seconds') or 0 for row in rows) / total, 1),
        'masked_total': sum(int(row.get('masked') or 0) for row in rows),
        'blocked_total': sum(int(row.get('blocked') or 0) for row in rows),
        'rules': dict(sorted(rules.items(), key=lambda item: -item[1])),
        'blocks': dict(sorted(blocks.items(), key=lambda item: -item[1])),
    }


def _round(value) -> float:
    try:
        return round(float(value or 0), 3)
    except (TypeError, ValueError):
        return 0.0


def _size(value) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False).encode('utf-8'))
    except (TypeError, ValueError):
        return 0


__all__ = ['DEBUG_LIMIT', 'TIMING_LIMIT', 'TEXT_LIMIT', 'collect', 'counters', 'payload',
           'slim', 'summarize']
