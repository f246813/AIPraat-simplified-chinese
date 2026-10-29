"""两条界面路径共用的执行事实、尝试记录与证据账本。

本模块统一投递状态、记录字段及证据判定；Praat I/O、进程锁与结果文件由执行器管理。"""
from __future__ import annotations

from dataclasses import asdict, dataclass

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
    if getattr(step, 'acoustic', None):
        record['acoustic'] = step.acoustic
    if guard_key:
        record['guard_key'] = guard_key
    if proposal_signature:
        record['proposal_signature'] = proposal_signature
    return record


def valid_evidence(step: Any, *, mutating: bool, rows_valid: bool | None = None) -> bool:
    """判定可用证据；目标指标缺测时仍保留独立有效的事件和测量。"""

    if not getattr(step, 'results', None):
        return False
    acoustic = getattr(step, 'acoustic', {}) or {}
    partial = acoustic.get('schema') == 'acoustic-analysis-v1' and (
        any(m.get('value') is not None for m in acoustic.get('measures', [])) or
        any(e.get('time') is not None and e.get('status') in {'candidate','validated'}
            for e in acoustic.get('events', [])))
    if not getattr(step, 'ok', False) and not partial:
        return False
    if partial:
        return True
    if (getattr(step, 'ok', False) and acoustic.get('schema') == 'acoustic-analysis-v1'
            and acoustic.get('kind') == 'pitch' and acoustic.get('measures')
            and all(m.get('value') is None and m.get('reason') for m in acoustic['measures'])):
        return True  # Missing F0 is an observed outcome with a scope and reason.
    if mutating:
        return True
    return True if rows_valid is None else bool(rows_valid)


def evidence_row(step: Any, context_text: str, *, mutating: bool = False) -> Any:
    """把调用中的有效结果变成账本里的一条证据（与云端 ``state.evidence`` 同一形状）。"""

    from .escape_policy import Evidence

    return Evidence(step.tool, dict(getattr(step, 'arguments', {}) or {}),
                    list(getattr(step, 'results', []) or []), context_text,
                    'operation' if mutating else 'measurement',
                    acoustic=dict(getattr(step, 'acoustic', {}) or {}))




#: 循环判定的三种结果。
CONTINUE, BUDGET, STAGNANT = 'continue', 'budget', 'stagnant'


@dataclass(frozen=True)
class LoopSignals:
    """一轮之后「还能不能继续」需要的全部信号（两条路径用同一组）。"""

    requests: int = 0
    request_limit: int = 0
    tool_attempts: int = 0
    tool_limit: int = 0
    produced_evidence: bool = True
    duplicate_action: bool = False


def loop_decision(signals: LoopSignals) -> str:
    """预算耗尽返回 BUDGET，没有新证据或动作重复返回 STAGNANT，否则返回 CONTINUE。"""

    if signals.request_limit and signals.requests >= signals.request_limit:
        return BUDGET
    if signals.tool_limit and signals.tool_attempts >= signals.tool_limit:
        return BUDGET
    if signals.duplicate_action or not signals.produced_evidence:
        return STAGNANT
    return CONTINUE


def signals_from_state(state: Any, *, produced_evidence: bool = True,
                       duplicate_action: bool = False) -> LoopSignals:
    """从 ``AnalysisState`` 取预算与进度，补齐两条路径都能用的信号。"""

    budget = getattr(state, 'budget', None)
    return LoopSignals(
        requests=int(getattr(state, 'requests', 0) or 0),
        request_limit=int(getattr(budget, 'request_limit', 0) or 0),
        tool_attempts=int(getattr(state, 'tool_attempts', 0) or 0),
        tool_limit=int(getattr(budget, 'tool_limit', 0) or 0),
        produced_evidence=produced_evidence,
        duplicate_action=duplicate_action)
def evidence_from_steps(context_text: str, steps, *, is_mutating=None):
    """本机（Tk）路径用：把步骤中的有效结果折成证据列表，去重规则与云端一致。"""

    from .escape_policy import AnalysisState

    state = AnalysisState(goal='', context_text=str(context_text or ''))
    for step in steps or []:
        mutating = bool(is_mutating(step.tool)) if is_mutating else False
        if not valid_evidence(step, mutating=mutating):
            continue
        state.add_evidence(evidence_row(step, state.context_text, mutating=mutating))
    return state


def evidence_dicts(state: Any) -> list[dict]:
    return [asdict(item) for item in getattr(state, 'evidence', []) or []]


__all__ = ['BUDGET', 'CONTINUE', 'STAGNANT', 'UNCONFIRMED', 'LoopSignals', 'attempt_record',
           'attempt_status', 'delivery_fact', 'evidence_dicts', 'evidence_from_steps',
           'evidence_row', 'loop_decision', 'signals_from_state', 'valid_evidence']
