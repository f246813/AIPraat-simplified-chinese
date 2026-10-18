"""报告审计：数值没对上就地遮蔽，绝不重写整份回答。

设计见 ``ai/docs/backend-workflow-redesign.zh-CN.md`` 第 4 节（P0，B 方案）。
``report_guards`` 负责确定性判定，本模块只负责**处置**：

- **结构性错误**（编造对象、用「没有音频输入」搪塞、用户点名对象缺失未说明、
  跨对象冒充量名）→ 交给上层重试一次，因为那是"说了不该说的话"；
- **数值没对上** → 只遮蔽那一个数值并记账，继续交付其余正文。

这样一次回答不会因为几个数字被整份退回，也不会为了几个数字再整包重发一遍证据
（真机事故：15 秒的 L2 时限里，一次重写就等于整份回答被丢弃）。
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import ModelRetry

from . import report_guards
from .escape_policy import note_guard

#: 结构性规则：拦的是说法与对象，不是数字精度，仍允许一次重试。
STRUCTURAL_RULES = frozenset({
    'report.object_unknown',
    'report.input_claim_conflict',
    'report.missing_object_unmentioned',
    'report.calc_other_target',
    'report.calc_quantity_mismatch',
})

#: 数值规则：可靠遮蔽解决。
NUMERIC_RULES = frozenset({'report.number_unmatched'})


@dataclass
class _Draft:
    """把正文塞进 ``validate_report`` 用的最小报告对象（无 pydantic）。"""

    analysis: str
    coverage: list[Any] = field(default_factory=list)


@dataclass
class AuditSummary:
    rounds: int = 0
    masked: list[str] = field(default_factory=list)
    rules: dict[str, int] = field(default_factory=dict)
    verified: bool = True
    exhausted: bool = False

    def record(self, state: Any) -> None:
        metrics = getattr(state, 'metrics', None)
        if not isinstance(metrics, dict):
            return
        audit = metrics.setdefault('audit', {})
        audit['rounds'] = self.rounds
        audit['masked'] = list(self.masked)
        audit['masked_count'] = len(self.masked)
        audit['verified'] = self.verified
        audit['exhausted'] = self.exhausted
        for rule, count in self.rules.items():
            audit[rule] = audit.get(rule, 0) + count


def _mask_entry(entry: Any, values: list[str], state: Any) -> Any:
    explanation = str(getattr(entry, 'explanation', '') or '')
    masked = report_guards.mask_rejected_numbers(
        explanation, values, report_guards.UNVERIFIED_NUMBER, state=state)
    if masked == explanation:
        return entry
    if hasattr(entry, 'model_copy'):
        return entry.model_copy(update={'explanation': masked})
    return entry


def _rebuild(report: Any, validated: Any) -> Any:
    analysis = str(getattr(validated, 'analysis', '') or '')
    coverage = list(getattr(validated, 'coverage', []) or [])
    if hasattr(report, 'model_copy'):
        return report.model_copy(update={'analysis': analysis, 'coverage': coverage})
    report.analysis = analysis
    report.coverage = coverage
    return report



def _unblock(state: Any, rule: str) -> None:
    """审计把这次「阻断」改成就地遮蔽了，计数也要跟着改，别把遮蔽记成拦截。"""

    metrics = getattr(state, 'metrics', None)
    if not isinstance(metrics, dict):
        return
    blocks = metrics.get('guard_blocks') or {}
    count = blocks.get(rule, 0)
    if count <= 0:
        return
    if count == 1:
        blocks.pop(rule, None)
    else:
        blocks[rule] = count - 1

def _retry_feedback(found: list[str]) -> str:
    """把「这些数对不上证据」变成模型能照着改的一句话（只描述改法，不重发证据）。"""

    listed = '、'.join(found[:6])
    return ('以下数值在测量证据里找不到同对象、同范围、同量名的对应值：' + listed
            + '。请只改这些句子，其余已核对通过的内容原样保留：'
            '一行只说一个选区的一个量；数值照抄证据（含小数位与单位）并在同一行用 [E编号] 引证；'
            '算出来的数（差值、比例、均值、跨度、时长）写成 {{calc:…}} 由程序渲染；'
            '典型值或常识范围只给定性说法，不写具体数字。')

def _retry_used(state: Any) -> bool:
    """这份报告已经让模型改过一版了吗（标志挂在共享的 metrics 上，跨重试可见）。"""

    metrics = getattr(state, 'metrics', None)
    return bool(isinstance(metrics, dict) and metrics.get('audit_numeric_retry'))


def _mark_retry(state: Any) -> None:
    metrics = getattr(state, 'metrics', None)
    if isinstance(metrics, dict):
        metrics['audit_numeric_retry'] = int(metrics.get('audit_numeric_retry') or 0) + 1


def audit_report(state: Any, report: Any, *, fill_citations: bool = True,
                 max_rounds: int = 12, allow_numeric_retry: bool = False) -> Any:
    """审计一份报告：数值问题就地遮蔽，结构性问题上抛 ``ModelRetry``。

    ``allow_numeric_retry=True`` 时（云端报告阶段：本来就有一次 output 重试额度），
    第一个对不上的数值先抛 ``ModelRetry`` 让模型**只改那几句**；同一份报告只给一次机会
    （标志写进 ``state.metrics['audit_numeric_retry']``，重试后的那一版照旧就地遮蔽）。
    判定规则与遮蔽语义都没改，只是把 B 方案当初为了省一次大请求而砍掉的「改一次」补回来。

    返回值与入参同一类型（``AnalysisReport`` 或替身对象），正文可能含
    ``[未通过核对的数值]`` 占位符；调用方据 ``state.metrics['audit']`` 判断这次交付
    是否"完全核对通过"。
    """

    analysis = str(getattr(report, 'analysis', '') or '')
    coverage = list(getattr(report, 'coverage', []) or [])
    summary = AuditSummary()
    for _ in range(max_rounds):
        summary.rounds += 1
        draft = _Draft(analysis=analysis, coverage=copy.copy(coverage))
        try:
            validated = report_guards.validate_report(state, draft, fill_citations=fill_citations)
        except ModelRetry as error:
            rule = str(getattr(error, 'rule', '') or 'unknown')
            summary.rules[rule] = summary.rules.get(rule, 0) + 1
            found = [value for value in report_guards.rejected_values(str(error))
                     if value not in summary.masked]
            if rule not in NUMERIC_RULES:
                # 结构性错误（编造对象、跨对象冒充、"没有音频输入"搪塞）：交给上层重试一次。
                summary.verified = False
                summary.record(state)
                raise
            if not found:
                # 同一个数遮过一遍还在被点名（写法不同/中文数字）：收尾，不再整份重写。
                summary.exhausted = True
                break
            if allow_numeric_retry and not _retry_used(state):
                # 先给模型一次只改这几句的机会（同一份报告只给一次）。这一次不算「阻断」：
                # 报告没有因此被退回，只是被要求改那几句。
                _mark_retry(state)
                _unblock(state, rule)
                summary.verified = False
                summary.record(state)
                note_guard(state, 'repair', 'report.number_retried')
                raise ModelRetry(_retry_feedback(found))
            summary.masked.extend(found)
            summary.verified = False
            _unblock(state, rule)
            analysis = report_guards.mask_rejected_numbers(
                analysis, found, report_guards.UNVERIFIED_NUMBER, state=state)
            coverage = [_mask_entry(entry, found, state) for entry in coverage]
            note_guard(state, 'repair', 'report.number_masked')
            continue
        summary.record(state)
        return _rebuild(report, validated)
    # 轮数用完仍没核干净：正文保留，问题数值已全部遮蔽，标注在正文里。
    summary.verified = False
    summary.exhausted = True
    note_guard(state, 'repair', 'report.audit_exhausted')
    summary.record(state)
    return _rebuild(report, _Draft(analysis=analysis, coverage=coverage))


__all__ = ['AuditSummary', 'NUMERIC_RULES', 'STRUCTURAL_RULES', 'audit_report']
