"""两条 UI 路径共用的执行核心：投递事实、尝试记录、证据账本。

背景（见 ``ai/docs/backend-workflow-redesign.zh-CN.md`` 第 7 节）：前端路径
（``modern_execution``）与 Tk 路径（``chat``）各自实现了"一次工具调用之后要记什么"：
投递四态怎么判、attempts 里放哪些字段、哪些结果才算证据。两份实现各修各的，
结果就是"守卫只在新路径生效"这类问题。

这里只抽**与界面无关的事实**，不搬 Praat I/O（那部分仍留在 ``modern_execution``，
因为它深度依赖进程/锁/结果文件）。两条路径都从这一处取同一套判定。
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from . import delivery

#: 传输层异常/未确认时的兜底理由（用户看不到，只进 attempts 与 metrics）。
UNCONFIRMED = 'completion not confirmed'


def delivery_fact(*, ok: bool, facts: list[str], note: str = '') -> str:
    """把一次发送的结果折成 ``delivery`` 的四态之一。

    ``facts`` 是发送方主动声明的投递事实（``DELIVERED`` / ``NOT_DELIVERED``）；
    没有声明又失败时只能记"状态不明"，不能猜成"没投递"。
    """

    if ok:
        return delivery.DELIVERED
    if facts and facts[-1] == delivery.NOT_DELIVERED:
        return delivery.NOT_DELIVERED
    return delivery.EXECUTION_UNKNOWN


def attempt_status(*, ok: bool, execution: str, has_script: bool = True) -> str:
    """attempts 里的 status 文案：成功 / 未执行 / 状态不明 / 执行后失败。"""

    if ok:
        return 'success'
    if execution == delivery.EXECUTION_UNKNOWN:
        return 'unknown'
    if execution in {delivery.NOT_DELIVERED, delivery.EXECUTION_BLOCKED} or not has_script:
        return 'not_executed'
    return 'failed'


def attempt_record(step: Any, *, status: str | None = None, execution: str = '',
                   guard_key: str = '', proposal_signature: str = '') -> dict:
    """一次工具调用的账本记录：两条路径写进 state.attempts 的都是这个形状。"""

    resolved = status or attempt_status(ok=bool(step.ok), execution=execution,
                                        has_script=bool(getattr(step, 'script', '')))
    record = {'tool': step.tool, 'arguments': dict(getattr(step, 'arguments', {}) or {}),
              'status': resolved, 'execution': execution,
              'reason': getattr(step, 'observation', '') or '',
              'script': getattr(step, 'script', '') or '',
              'rows': list(getattr(step, 'results', []) or [])}
    if guard_key:
        record['guard_key'] = guard_key
    if proposal_signature:
        record['proposal_signature'] = proposal_signature
    return record


def valid_evidence(step: Any, *, mutating: bool, rows_valid: bool | None = None) -> bool:
    """这次调用的结果算不算证据：操作类只要成功，测量类还要求有有效结果行。"""

    if not getattr(step, 'ok', False) or not getattr(step, 'results', None):
        return False
    if mutating:
        return True
    return True if rows_valid is None else bool(rows_valid)


def evidence_row(step: Any, context_text: str, *, mutating: bool = False) -> Any:
    """把一次成功调用变成账本里的一条证据（与云端 ``state.evidence`` 同一形状）。"""

    from .escape_policy import Evidence

    return Evidence(step.tool, dict(getattr(step, 'arguments', {}) or {}),
                    list(getattr(step, 'results', []) or []), context_text,
                    'operation' if mutating else 'measurement')


def evidence_from_steps(context_text: str, steps, *, is_mutating=None):
    """本机（Tk）路径用：把一串成功步骤折成证据列表，去重规则与云端一致。"""

    from .escape_policy import AnalysisState

    state = AnalysisState(goal='', context_text=str(context_text or ''))
    for step in steps or []:
        if not getattr(step, 'ok', False) or not getattr(step, 'results', None):
            continue
        mutating = bool(is_mutating(step.tool)) if is_mutating else False
        state.add_evidence(evidence_row(step, state.context_text, mutating=mutating))
    return state


def evidence_dicts(state: Any) -> list[dict]:
    return [asdict(item) for item in getattr(state, 'evidence', []) or []]


__all__ = ['UNCONFIRMED', 'attempt_record', 'attempt_status', 'delivery_fact',
           'evidence_dicts', 'evidence_from_steps', 'evidence_row', 'valid_evidence']
