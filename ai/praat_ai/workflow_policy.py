"""Host-owned workflow preferences; no model classification or new execution ledger."""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, fields
from pathlib import Path

from . import tools


@dataclass(frozen=True)
class WorkflowPolicy:
    schema_version: int = 1
    preset: str = 'balanced'
    l0_enabled: bool = True
    l1_repair_limit: int = 1
    l2_mode: str = 'on_demand'
    l2_detail: str = 'brief'
    #: L2 的软时限：只决定「还能不能再发一次请求」，绝不中途取消已经在跑的请求，
    #: 也绝不因为超时丢弃已生成的正文（见 finish_locally 的三态）。
    l2_soft_deadline_sec: int = 60
    #: 路由：off 只用正则提示 + 默认完整分析；auto 让模型在对正则不确定的轮次里分类。
    router_mode: str = 'off'
    l3_mode: str = 'manual'
    l4_enabled: bool = True

    @classmethod
    def validate(cls, raw=None):
        if raw is None:
            return cls()
        if not isinstance(raw, dict):
            raise ValueError('工作流程必须是对象')
        value = asdict(cls())
        value.update({f.name: raw[f.name] for f in fields(cls) if f.name in raw})
        for key in ('l0_enabled', 'l4_enabled'):
            if type(value[key]) is not bool:
                raise ValueError(f'工作流程 {key} 必须是布尔值')
        for key, lo, hi in (('schema_version', 1, 1), ('l1_repair_limit', 0, 1),
                            ('l2_soft_deadline_sec', 1, 3600)):
            if type(value[key]) is not int or not lo <= value[key] <= hi:
                raise ValueError(f'工作流程 {key} 必须是 {lo}–{hi} 的整数')
        for key, choices in dict(preset={'fast','balanced','deep','chat','custom'},
                                  l2_mode={'off','on_demand','always'},
                                  l2_detail={'brief','full'}, l3_mode={'off','manual','auto'},
                                  router_mode={'off','auto'}).items():
            if not isinstance(value[key], str) or value[key] not in choices:
                raise ValueError(f'工作流程 {key} 无效')
        policy = cls(**value)
        if policy.preset != 'custom' and policy != cls.for_preset(policy.preset):
            value['preset'] = 'custom'
            policy = cls(**value)
        return policy

    @classmethod
    def for_preset(cls, preset):
        changes = {
            'fast': dict(l1_repair_limit=0, l2_mode='off'),
            'balanced': {},
            'deep': dict(l2_mode='always', l2_detail='full', l2_soft_deadline_sec=120,
                         l3_mode='auto'),
            'chat': dict(l0_enabled=False, l1_repair_limit=0, l2_mode='off', l3_mode='off'),
        }
        return cls(preset=preset, **changes[preset])


def wants_explanation(goal):
    from .dialogue_policy import advice_request
    return advice_request(goal) or bool(re.search(r'解释|分析|比较|对比|判断|诊断|评估|综合|explain|analy[sz]e|compare|diagnos', goal, re.I))


def explicit_action(goal):
    """Only clear one-shot requests override stage preferences."""
    if re.fullmatch(r'(?:请)?(?:本次)?解释(?:这些|已有|上述|刚才的)?(?:测量)?结果[。！？?!]*', goal.strip()):
        return 'L2'
    # 自然说法的追问：同时出现「解释/说明」与「刚才/上述/这个结果」时，
    # 与封闭写法含义相同，应进入 L2。同一句还要求重新测量或换对象时不接管，
    # 那属于新任务而不是解释已有结果。
    if (re.search(r'解释|说明|讲讲|什么意思|怎么看', goal)
            and re.search(r'刚才|上述|之前|上一个|本次|这次|的?结果|这些(?:数|测量|数值)', goal)
            and not re.search(r'再测|重新测|重测|计算|读取|打开|删除|导出|标注|对比|比较|'
                              r'当前|选中|对象\s*\d|\d+\s*(?:到|至)\s*\d+|分析原始', goal)):
        return 'L2'
    if re.search(r'(?:本次分析原材料|直接听|听一下|原始(?:音频|录音).{0,10}分析|分析原始(?:音频|录音))', goal):
        return 'L3'
    return ''


def measurement_contract(goal):
    """A closed, inspectable set. Compound/diagnostic goals never qualify."""
    text = goal.strip().rstrip('。！？?!')
    text = re.sub(r'(?:，?并|，?然后)解释(?:测量)?结果$', '', text)
    current = r'(?:当前|选中)(?:声音|音频)'
    target = rf'(?:{current}|(?:Sound|Pitch|Intensity)\s+\d+|对象\s*\d+)'
    prefix = r'(?:请|帮我)?(?:测量|查询|读取|告诉我)?'
    for tool, quantity, unit in (
        ('duration', r'(?:总)?(?:时长|长度)', '秒'),
        ('pitch_statistics', r'(?:平均基频|基频统计|平均F0)', 'Hz'),
        ('intensity_statistics', r'(?:平均强度|强度统计)', 'dB'),
    ):
        if re.fullmatch(prefix + r'\s*' + target + r'\s*(?:的)?\s*' + quantity, text, re.I):
            return dict(tool=tool, unit=unit)
    from .dialogue_policy import direct_measurement
    direct = direct_measurement(text)
    # This proves completion after the registered VOT tool ran; it does not
    # create a local keyword-to-tool fast path or invent burst/voicing bounds.
    if re.fullmatch(r'(?:请|帮我)?(?:计算|测量)\s*'
                    r'(?:这段|选中(?:的)?(?:这段|这一段|片段)|圈选(?:的)?(?:这段|这一段|片段)|Sound\s+\d+)'
                    r'\s*(?:的)?\s*VOT', text, re.I):
        return dict(tool='vot', unit='秒')
    return dict(tool=direct['tool'], unit='秒') if direct else None


def valid_measurement_rows(tool, rows):
    """Known templates can succeed while reporting an undefined statistic."""
    patterns = {'duration':r'(?:总)?时长\s*=\s*(-?\d+(?:\.\d+)?)\s*(?:秒|s\b)',
                'pitch_statistics':r'平均\s*(-?\d+(?:\.\d+)?)\s*Hz',
                'intensity_statistics':r'平均\s*(-?\d+(?:\.\d+)?)\s*dB',
                'vot':r'VOT\s*(?:估计值)?\s*=\s*(-?\d+(?:\.\d+)?)\s*秒'}
    if tool not in patterns:
        return bool(rows)
    match = re.search(patterns[tool], '\n'.join(rows))
    return bool(match and math.isfinite(float(match[1]))
                and (tool != 'duration' or float(match[1]) >= 0)
                and (tool != 'pitch_statistics' or float(match[1]) > 0))


def measurement_complete(state, *, calls=None):
    """Prove quantity, original object, range, successful delivery and valid output."""
    contract = measurement_contract(state.goal)
    if not contract or state.reason or state.continuation or len(state.deliveries) != 1:
        return False
    if calls is not None and (len(calls) != 1 or calls[0].tool_name != contract['tool']):
        return False
    from .tool_guards import StrictContext
    try:
        original = StrictContext(tools.parse_object_context(state.context_text), Path('result'), Path('state'))
        ref = re.search(r'(?:Sound|Pitch|Intensity|对象)\s*(\d+)', state.goal, re.I)
        if contract['tool'] == 'vot' and not ref:
            candidates = [r for r in original.objects if r.class_name in tools.SOUND_CLASSES and r.selected]
            if not candidates:
                candidates = [r for r in original.objects if r.class_name in tools.SOUND_CLASSES and r.selection]
            if len(candidates) != 1:
                return False
            target = candidates[0]
        else:
            target = original.resolve_object(int(ref[1]) if ref else None)
        for evidence in state.evidence:
            if evidence.tool != contract['tool'] or evidence.kind != 'measurement':
                continue
            source = StrictContext(tools.parse_object_context(evidence.source), Path('result'), Path('state'))
            actual = source.resolve_object(evidence.arguments.get('object'))
            if (target.id, target.class_name, target.name, target.selection) != (actual.id, actual.class_name, actual.name, actual.selection):
                continue
            if not any(a.get('status') == 'success' and a.get('tool') == evidence.tool
                       and a.get('arguments') == evidence.arguments
                       and a.get('execution', '') not in {'unknown','blocked','not_delivered'} for a in state.attempts):
                continue
            if contract['tool'] != 'duration':
                if any(k in evidence.arguments for k in ('pitch_floor','pitch_ceiling')):
                    continue
                supplied = (evidence.arguments.get('from'), evidence.arguments.get('to'))
                if supplied != (None, None) and supplied != actual.selection:
                    continue
            row = '\n'.join(evidence.rows)
            label = {'duration':r'(?:总)?时长\s*=\s*', 'pitch_statistics':r'平均\s*',
                     'intensity_statistics':r'平均\s*', 'vot':r'VOT\s*(?:估计值)?\s*=\s*'}[contract['tool']]
            unit = r'(?:秒|s\b)' if contract['tool'] == 'duration' else contract['unit']
            match = re.search(label + r'(-?\d+(?:\.\d+)?)\s*' + unit, row)
            if (match and math.isfinite(float(match[1]))
                    and (contract['tool'] != 'duration' or float(match[1]) >= 0)
                    and (contract['tool'] != 'pitch_statistics' or float(match[1]) > 0)):
                return True
    except tools.ToolError:
        pass
    return False


def measurement_scope(tool, args, row):
    """Requested tool scope; editor selection is only a default, never a limit."""
    if tool == 'duration':
        return dict(kind='whole_object')
    if args.get('time', args.get('times')) not in (None, ''):
        return dict(kind='query_points', requested_times=[float(t) for t in tools._query_times(args, fallback='0')])
    interval = (args.get('from', args.get('start')), args.get('to', args.get('end')))
    if args.get('_whole_object'):
        interval = (None, None)
    elif interval == (None, None) and row.selection:
        interval = row.selection
    return dict(kind='whole_object') if interval == (None,None) else dict(kind='bound_range',start=interval[0],end=interval[1])


def publish_results(state):
    """Projection of the existing ledger, emitted before report/network work."""
    results = []
    for index, attempt in enumerate(state.attempts):
        evidence_index = next((i for i, e in enumerate(state.evidence)
                               if e.tool == attempt.get('tool') and e.arguments == attempt.get('arguments')), None)
        evidence = state.evidence[evidence_index] if evidence_index is not None else None
        quantities = [dict(name=match[1], value=float(match[2]), unit=match[3])
                      for row in attempt.get('rows', []) for match in
                      re.finditer(r'((?:总)?时长|平均|最高|最低|基频|强度)\s*(?:=|：|:)?\s*(-?\d+(?:\.\d+)?)\s*(秒|Hz|dB|s\b)', row)]
        source_text = evidence.source if evidence else state.context_text
        target = None
        from .tool_guards import StrictContext
        try:
            context = StrictContext(tools.parse_object_context(source_text), Path('result'), Path('state'))
            row = context.resolve_object(attempt.get('arguments', {}).get('object'))
            target = dict(id=row.id, class_name=row.class_name, name=row.name)
            scope = measurement_scope(attempt.get('tool'), attempt.get('arguments', {}), row)
        except tools.ToolError:
            scope = dict(kind='unresolved')
        results.append(dict(id=f'{state.task_id}:{index}', tool=attempt.get('tool'),
                            arguments=attempt.get('arguments', {}), rows=attempt.get('rows', []),
                            status=attempt.get('status'), execution=attempt.get('execution', ''),
                            result_valid=attempt.get('result_valid', evidence is not None),
                            source=source_text, object=target,
                            evidence_ref=evidence_index, quantities=quantities,
                            range=scope, reason=attempt.get('reason', '')))
    state.results = results
    state.measurement_content = '\n'.join(row for e in state.evidence for row in e.rows)
    if state.on_results:
        state.on_results(state)


def finish_locally(state):
    """没有可交付的模型正文时收尾：三态，并且永远不丢掉已经写好的正文。

    - ``complete``：量、对象、范围都证明为完成，且目标不需要额外解释；
    - ``partial_with_text``：已有解释正文（可能带遮蔽标注），只补缺口说明；
    - ``incomplete``：连正文都没有，只交付数据板和一句人话原因。
    """

    complete = measurement_complete(state)
    needs_explanation = wants_explanation(state.goal) or state.continuation_mode == 'L2'
    body = str(state.report or '').strip()
    measurement = state.measurement_content or ''
    gap = state.reason or ('目标解释尚未执行' if complete or state.continuation
                           else '现有工具结果尚未证明目标所需的量、对象与范围均已完成')
    if body and (not measurement or not body.startswith(measurement.strip())):
        # 已经有解释正文：保留它（含遮蔽标注），只追加缺口说明。
        state.status = 'complete' if complete and not needs_explanation and not state.reason else 'partial'
        if state.status != 'complete':
            state.report = body + '\n\n尚未完成：' + gap
        state.metrics['finish_state'] = 'partial_with_text' if state.status != 'complete' else 'complete'
    else:
        state.status = 'complete' if complete and not needs_explanation else 'partial'
        state.report = measurement
        if state.status != 'complete':
            state.report += ('\n\n' if state.report else '') + '尚未完成：' + gap
        state.metrics['finish_state'] = 'complete' if state.status == 'complete' else (
            'partial_with_text' if state.report.strip() else 'incomplete')
    state.coverage = [dict(item=item, status=state.status,
                           explanation='已保存工具结果' if complete else state.reason or '仍缺目标依据或解释')
                      for item in state.deliveries]
    state.metrics['route_reason'] = 'measurement_complete' if complete else 'workflow_stage_disabled'
    state.can_continue = bool(state.evidence)
    state.can_continue = bool(state.evidence)
