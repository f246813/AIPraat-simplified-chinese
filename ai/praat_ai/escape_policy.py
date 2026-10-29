"""Application policy only; graph and model execution live in cloud_agent."""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any


class BranchExit(RuntimeError):
    pass


def note_guard(state: Any, kind: str, rule: str) -> None:
    """按稳定规则 ID 记录守卫动作到 state.metrics。guard_blocks 记录阻断，guard_repairs 记录自动修正；kind 使用 block 或 repair，rule 不包含完整错误理由。"""

    metrics = getattr(state, 'metrics', None)
    if not isinstance(metrics, dict):
        return
    bucket = metrics.setdefault('guard_blocks' if kind == 'block' else 'guard_repairs', {})
    bucket[rule] = bucket.get(rule, 0) + 1


@dataclass
class Budget:
    context_tokens: int = 32768
    #: 可见正文的额度（不含推理）。云端对话还会被 ``api.dialogue_max_tokens`` 再压一次。
    response_tokens: int = 1500
    request_limit: int = 12
    tool_limit: int = 20
    #: 思考档位要占掉的输出额度：云端把 reasoning token 计入 ``max_tokens``，
    #: 所以「正文额度 + 推理预留」才是这次请求真正的输出上限
    #: （见 :func:`praat_ai.dialogue_policy.reasoning_reserve`）。
    reasoning_tokens: int = 0

    @property
    def output_tokens(self) -> int:
        """这次请求真正的 ``max_tokens``：正文额度 + 推理预留。"""

        return self.response_tokens + self.reasoning_tokens

    @property
    def reserve(self) -> int:
        return self.output_tokens + max(512, min(2048, self.context_tokens // 10)) + 512

    def should_close(self, input_tokens: int) -> bool:
        return self.context_tokens - input_tokens < self.reserve


@dataclass
class Evidence:
    tool: str
    arguments: dict[str, Any]
    rows: list[str]
    source: str
    kind: str = 'measurement'
    acoustic: dict[str, Any] = field(default_factory=dict)

    @property
    def signature(self) -> str:
        return json.dumps([self.tool, self.arguments, self.source], ensure_ascii=False, sort_keys=True)


@dataclass
class AnalysisState:
    goal: str
    context_text: str
    mode: str = 'L0'
    evidence: list[Evidence] = field(default_factory=list)
    failures: dict[str, int] = field(default_factory=dict)
    attempts: list[dict[str, Any]] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    report: str = ''
    coverage: list[dict[str, Any]] = field(default_factory=list)
    deliveries: list[str] = field(default_factory=list)
    status: str = 'running'
    reason: str = ''
    requests: int = 0
    planning_rounds: int = 0
    tool_attempts: int = 0
    stagnant: int = 0
    last_progress: int = 0
    direction: str = ''
    visible_prompt: str = ''
    continuation: bool = False
    continuation_mode: str = ''
    can_continue: bool = False
    audio_sent: bool = False
    audio_received: bool = False
    audio_report_completed: bool = False
    audio_observations: list[dict[str, Any]] = field(default_factory=list)
    previous_report: str = ''
    previous_coverage: list[dict[str, Any]] = field(default_factory=list)
    dialogue_context: list[dict[str, str]] = field(default_factory=list)
    budget: Budget = field(default_factory=Budget)
    metrics: dict[str, Any] = field(default_factory=dict)
    phase_context: Any = None
    phase_contexts: dict[str, Any] = field(default_factory=dict)
    historical_evidence: list[dict] = field(default_factory=list)
    material_metadata: dict[str, Any] = field(default_factory=dict)
    session_id: str = ''
    task_id: str = ''
    context_epoch: int = 0
    workflow: Any = None  # None keeps the archived Tk workflow compatible.
    workflow_override: str = ''
    #: 本轮路由判定（router.classify 的对话分派值：'' / concept / advice / conversation）；
    #: 一轮只判一次，供对话分派与 entry_mode 共用。
    router_dialogue: str = ''
    results: list[dict] = field(default_factory=list)
    measurement_content: str = ''
    on_results: Any = field(default=None, repr=False)

    def __post_init__(self):
        if not self.deliveries:
            # Stable original delivery labels, passed verbatim to report validation.
            import re
            self.deliveries = [s.strip() for s in re.split(r'[\n；;]+', self.goal) if s.strip()] or [self.goal]

    def event(self, mode: str, reason: str) -> None:
        self.mode = mode
        self.events.append({'mode': mode, 'reason': reason})

    def failed(self, problem: str, reason: str, *, permanent: bool = False) -> None:
        self.failures[problem] = self.failures.get(problem, 0) + 1
        repair_limit = self.workflow.l1_repair_limit if self.workflow is not None else 1
        if permanent or self.failures[problem] > repair_limit:
            self.reason = reason
            raise BranchExit(reason)
        self.event('L1', reason + '；允许一次有区别的纠正')

    def add_evidence(self, evidence: Evidence) -> bool:
        if evidence.signature in {e.signature for e in self.evidence}:
            return False
        self.evidence.append(evidence)
        return True

    def finish_round(self) -> None:
        progress = len(self.evidence)
        self.stagnant = self.stagnant + 1 if progress == self.last_progress else 0
        self.last_progress = progress
        if self.stagnant >= 2:
            raise BranchExit('连续两轮没有新增有效证据，停止当前执行分支')

    def continued(self, direction: str, *, mode: str = '') -> AnalysisState:
        if not mode:
            import re
            mode = 'L4' if '一般' in direction else ('L3' if re.search(r'(直接.*(?:音频|录音|听)|原始(?:音频|录音)|听感)', direction) else 'L2')
        if mode not in {'L2', 'L3', 'L4'}:
            raise ValueError('继续分析需要明确的 L2/L3/L4 模式')
        state = AnalysisState(self.goal, self.context_text, evidence=copy.deepcopy(self.evidence),
                              failures=dict(self.failures), deliveries=list(self.deliveries),
                              dialogue_context=copy.deepcopy(self.dialogue_context),
                              audio_observations=copy.deepcopy(self.audio_observations),
                              previous_report=self.report, previous_coverage=copy.deepcopy(self.coverage),
                              reason=self.reason, continuation_mode=mode,
                              budget=copy.deepcopy(self.budget), direction=direction, continuation=True,
                              workflow=self.workflow, workflow_override=mode,
                              attempts=copy.deepcopy(self.attempts), material_metadata=copy.deepcopy(self.material_metadata))
        state.visible_prompt = ('继续分析原始任务：' + self.goal + '\n沿用原目标材料和成功测量，重点：'
                                + direction + '。仅补充必要分析，交代新增证据与缺口。')
        state.events = [{'mode':'continue', 'reason':state.visible_prompt}]
        return state

    def fallback_report(self) -> str:
        from . import delivery_text
        rows = [f'原始目标：{self.goal}', '本轮仅交付阶段记录，目标分析尚未完成。']
        if self.evidence:
            rows.append('已取得的证据：')
            rows.extend(f'- {e.tool}（来源：{e.source}）：' + '；'.join(e.rows) for e in self.evidence)
        self.metrics.setdefault('reason_detail', self.reason)
        reason = delivery_text.user_reason(self.reason, has_measurements=bool(self.evidence))
        rows.append('缺口与影响：' + reason
                    + '现有记录不能据此确定录音的具体发音缺陷。')
        rows.append('可继续：补充目标解释；精确数值需要相应测量，听感需要实际音频输入。')
        self.coverage = [{'item':item, 'status':'partial' if self.evidence else 'missing',
                          'explanation':'模型报告未完成，已保存现有证据与诊断'} for item in self.deliveries]
        return '\n\n'.join(rows)
