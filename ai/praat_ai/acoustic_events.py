"""Typed acoustic candidates, validation, derived measures and TextGrid layers."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any


RECEIPT = '@aipraat-acoustic-v1\t'
FIELDS = ('scope_start', 'scope_end', 'source_start', 'source_end', 'burst',
          'pre_start', 'pre_end', 'pre_f0', 'post_start', 'vowel', 'truncated', 'separated',
          'burst_rise', 'high_band', 'closure_start')


@dataclass
class AcousticEvent:
    id: str
    kind: str
    time: float | None
    status: str = 'candidate'
    method: str = ''
    reason: str = ''


@dataclass
class AcousticInterval:
    id: str
    kind: str
    start: float
    end: float
    status: str = 'candidate'
    method: str = ''


@dataclass
class DerivedMeasure:
    name: str
    value: float | None
    unit: str
    inputs: list[str]
    formula: str
    status: str
    reason: str = ''


@dataclass
class AcousticAnalysis:
    source: dict[str, Any]
    scope: tuple[float, float]
    events: list[AcousticEvent] = field(default_factory=list)
    intervals: list[AcousticInterval] = field(default_factory=list)
    measures: list[DerivedMeasure] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    schema: str = 'acoustic-analysis-v1'
    kind: str = 'vot'
    method: dict[str, Any] = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def analyse_vot(values: dict[str, Any], arguments: dict, source: dict) -> AcousticAnalysis:
    """Validate point/interval relationships before subtracting event times.

    Automatic acoustic boundaries stay provisional. A gap in prevoicing does
    not reverse its sign; association with a stop closure is a separate check.
    """
    lo, hi = _number(values.get('scope_start')), _number(values.get('scope_end'))
    if lo is None or hi is None or lo >= hi:
        raise ValueError('声学事件缺少有效的绝对时间范围')
    source_lo, source_hi = _number(values.get('source_start')), _number(values.get('source_end'))
    if source_lo is None or source_hi is None or not source_lo <= lo < hi <= source_hi:
        raise ValueError('分析范围超出声音绝对时间域')
    analysis = AcousticAnalysis(source, (lo, hi))
    manual = 'burst' in arguments and ('voicing' in arguments or 'onset' in arguments)
    if manual:
        burst = _number(values.get('burst'))
        onset = _number(arguments.get('voicing', arguments.get('onset')))
        observed = _number(values.get('pre_start' if onset is not None and burst is not None and onset < burst else 'post_start'))
        supplied_burst = _number(arguments['burst'])
        if (None in (burst, supplied_burst, observed, onset)
                or not math.isclose(burst, supplied_burst, abs_tol=5.1e-10, rel_tol=0)
                or not math.isclose(observed, onset, abs_tol=5.1e-10, rel_tol=0)):
            raise ValueError('人工地标回执与请求不一致')
    method = 'manual' if manual else 'filtered_autocorrelation'
    for key, kind in (('burst', 'release_burst'), ('pre_start', 'prevoicing_onset'),
                      ('post_start', 'post_release_periodicity'), ('vowel', 'clear_vowel_onset')):
        time = _number(values.get(key))
        if time is not None and time < 0:
            time = None
        event = AcousticEvent(key, kind, time, method=method)
        if key == 'vowel':
            event.method = 'manual'
        if time is None:
            event.status, event.reason = 'unavailable', '没有该事件地标'
        elif not lo <= time <= hi:
            event.status, event.reason = 'rejected', '事件不在分析范围内'
        elif manual or key == 'vowel':
            event.status = 'validated'
        if key == 'burst':
            event.method = 'manual' if manual else 'high_band_release_transient'
            if not manual and not bool(_number(values.get('high_band'))):
                event.status, event.reason = 'uncertain', '全频能量上升不能单独证明 release burst'
            elif not manual and (_number(values.get('burst_rise')) or 0) < float(arguments.get('burst_db', 6)):
                event.status, event.reason = 'rejected', '释放瞬态能量上升证据不足'
        analysis.events.append(event)
    by_id = {event.id: event for event in analysis.events}
    burst, pre, post, vowel = (by_id[k] for k in ('burst', 'pre_start', 'post_start', 'vowel'))
    usable = lambda event: event.time is not None and event.status in {'candidate', 'validated'}
    closure = _number(arguments.get('closure_start', values.get('closure_start')))
    if closure is not None and closure >= 0 and usable(burst):
        if lo <= closure < burst.time:
            analysis.intervals.append(AcousticInterval('closure', 'stop_closure', closure, burst.time,
                                                       'validated', 'manual'))
        else:
            analysis.checks.append(dict(rule='closure.domain', status='rejected', reason='闭塞起点应在选区内并早于 burst'))
            closure = None
    else:
        closure = None
    if usable(pre):
        if usable(burst) and pre.time >= burst.time:
            pre.status, pre.reason = 'rejected', '预浊起点没有早于除阻'
        elif bool(_number(values.get('truncated'))):
            pre.status, pre.reason = 'uncertain', '起声在选区左端已存在，真正起点被截断'
        elif closure is not None and pre.time < closure:
            pre.status, pre.reason = 'uncertain', '周期性在目标闭塞前已存在，需区分前音延续与闭塞发声'
        elif not manual and closure is None:
            pre.reason = '周期性候选尚未确认属于目标塞音闭塞段'
            if bool(_number(values.get('separated'))):
                pre.reason += '；发声在除阻前已中断，间断本身不能决定 VOT 符号'
        end = _number(values.get('pre_end'))
        f0 = _number(values.get('pre_f0'))
        if not manual and (end is None or f0 is None or f0 <= 0 or end - pre.time < max(.02, 3 / f0) - 1e-8):
            pre.status, pre.reason = 'uncertain', '爆破前周期段持续性证据不足'
        if end is not None and pre.time < end <= hi:
            analysis.intervals.append(AcousticInterval('prevoicing', 'closure_voicing_candidate',
                                                       pre.time, end, pre.status, method))
    if usable(post) and usable(burst) and post.time < burst.time:
        post.status, post.reason = 'rejected', '爆破后周期性时刻早于 burst'
    if usable(vowel) and usable(burst) and vowel.time < burst.time:
        vowel.status, vowel.reason = 'rejected', '清晰元音起始早于 burst'
    onset = pre if pre.time is not None else post
    reason = ''
    if not usable(burst):
        reason = burst.reason
    elif not usable(onset):
        reason = onset.reason
    status = 'unavailable' if reason else ('validated' if manual else 'provisional')
    analysis.measures.append(DerivedMeasure('vot_ms', None if reason else (onset.time - burst.time) * 1000,
        'ms', [onset.id, burst.id], '(voicing_onset - release_burst) * 1000', status, reason))
    for event, name in ((post, 'burst_to_periodicity_ms'), (vowel, 'burst_to_clear_vowel_ms')):
        valid = usable(burst) and usable(event)
        analysis.measures.append(DerivedMeasure(name, (event.time - burst.time) * 1000 if valid else None,
            'ms', [event.id, burst.id], f'({event.kind} - release_burst) * 1000',
            'validated' if valid and manual else 'provisional' if valid else 'unavailable',
            '' if valid else event.reason or burst.reason))
    analysis.checks.extend(dict(rule='event.domain_and_role', event=e.id, status=e.status, reason=e.reason)
                           for e in analysis.events)
    return analysis


def consume_vot_receipt(rows: list[str], arguments: dict, source: dict):
    receipts = [row for row in rows if row.startswith(RECEIPT)]
    if not receipts:
        return None
    if len(receipts) != 1:
        raise ValueError('声学事件回执重复')
    parts = receipts[0][len(RECEIPT):].split('\t')
    if len(parts) != len(FIELDS):
        raise ValueError('声学事件回执字段不完整')
    return analyse_vot(dict(zip(FIELDS, parts)), arguments, source)


def result_rows(analysis: AcousticAnalysis) -> list[str]:
    """Render validated data; no model arithmetic or physiological diagnosis."""
    names = dict(burst='Release burst', pre_start='预浊起始候选',
                 post_start='爆破后稳定周期性候选', vowel='清晰元音起始')
    rows = [f'分析范围：{analysis.scope[0]:.6f}–{analysis.scope[1]:.6f} 秒']
    for event in analysis.events:
        value = f'{event.time:.6f} 秒' if event.time is not None else '未确定'
        rows.append(f'{names[event.id]}：{value}（{event.status}）' + (f'；{event.reason}' if event.reason else ''))
    for measure in analysis.measures:
        if measure.name == 'vot_ms':
            rows.append(f'VOT 估计值 = {measure.value / 1000:.6f} 秒（{measure.value:.3f} 毫秒，{measure.status}）'
                        if measure.value is not None else 'VOT 无法确定：' + measure.reason)
        elif measure.value is not None:
            label = '爆破后稳定起声候选间隔' if measure.name == 'burst_to_periodicity_ms' else 'Burst-to-clear-vowel interval'
            rows.append(f'{label} = {measure.value:.3f} 毫秒')
    rows.append('负 VOT 表示相关声带振动早于除阻；不能仅凭 VOT 判定音素身份、促音或发声生理原因。')
    return rows


def render_report(analysis: dict) -> str:
    measures = analysis.get('measures', [])
    vot = next((m for m in measures if m['name'] == 'vot_ms'), None)
    if not vot:
        return ''
    conclusion = ('VOT 无法确定：' + vot.get('reason', '') if vot['value'] is None else
                  f'VOT {"候选值" if vot["status"] != "validated" else "测量值"}：**{vot["value"]:.3f} ms**。')
    labels = dict(burst='Release burst', pre_start='预浊起始',
                  post_start='爆破后稳定周期性', vowel='清晰元音起始')
    statuses = dict(candidate='候选', validated='人工提供，时间关系已检查', uncertain='待复核',
                    unavailable='未确定', rejected='未通过验证', provisional='候选')
    table = ['| 事件 | 绝对时刻 | 状态 |', '| --- | --- | --- |']
    for event in analysis['events']:
        time = f'{event["time"]:.6f} s' if event['time'] is not None else '未确定'
        table.append(f'| {labels[event["id"]]} | {time} | {statuses[event["status"]]} |')
    if analysis.get('intervals'):
        table.extend(['', '| 持续区间 | 绝对时间范围 | 状态 |', '| --- | --- | --- |'])
        for interval in analysis['intervals']:
            label = '目标闭塞' if interval['id'] == 'closure' else '爆破前周期段'
            table.append(f'| {label} | {interval["start"]:.6f}–{interval["end"]:.6f} s | {statuses[interval["status"]]} |')
    calculation = []
    for measure in measures:
        if measure['value'] is not None:
            inputs = {e['id']: e['time'] for e in analysis['events']}
            first, second = (inputs[k] for k in measure['inputs'])
            label = dict(vot_ms='VOT', burst_to_periodicity_ms='爆破后稳定周期性间隔',
                         burst_to_clear_vowel_ms='爆破至清晰元音间隔')[measure['name']]
            calculation.append(f'- {label}：({first:.6f} − {second:.6f}) × 1000 = {measure["value"]:.3f} ms。')
    limitations = list(dict.fromkeys(e['reason'] for e in analysis['events'] if e.get('reason')))
    if vot['status'] != 'validated':
        limitations.append('自动地标仍需结合闭塞段与语图复核；周期性不等于清晰元音起始。')
    return '\n\n'.join(['## 测量结论\n\n' + conclusion,
                        '## 事件与区间\n\n' + '\n'.join(table),
                        '## 计算依据\n\n' + ('\n'.join(calculation) or '地标未通过验证，未计算 VOT。'),
                        '## 限制\n\n' + ('\n'.join('- ' + reason for reason in limitations) or '使用人工提供的地标。')])


def to_textgrid(analysis: AcousticAnalysis) -> str:
    """Keep instantaneous events in TextTier and durations in IntervalTier."""
    lo, hi = analysis.scope
    escape = lambda value: str(value).replace('"', '""').replace('\n', ' ')
    events = sorted((e for e in analysis.events if e.time is not None and lo <= e.time <= hi
                     and e.status != 'rejected'), key=lambda e: e.time)
    points = {}
    for event in events:
        points.setdefault(event.time, []).append(f'{event.kind} [{event.status}]')
    lines = ['File type = "ooTextFile"', 'Object class = "TextGrid"', '', f'xmin = {lo}', f'xmax = {hi}',
             'tiers? <exists>', f'size = {1 + len(analysis.intervals)}', 'item []:',
             '    item [1]:', '        class = "TextTier"', '        name = "Acoustic_events"',
             f'        xmin = {lo}', f'        xmax = {hi}', f'        points: size = {len(points)}']
    for i, (time, labels) in enumerate(points.items(), 1):
        lines.extend([f'        points [{i}]:', f'            number = {time}',
                      f'            mark = "{escape("; ".join(labels))}"'])
    for index, interval in enumerate(analysis.intervals, 2):
        segments = [(lo, interval.start, ''), (interval.start, interval.end, interval.kind), (interval.end, hi, '')]
        segments = [s for s in segments if s[0] < s[1]]
        lines.extend([f'    item [{index}]:', '        class = "IntervalTier"', f'        name = "{escape(interval.id)}"',
                      f'        xmin = {lo}', f'        xmax = {hi}', f'        intervals: size = {len(segments)}'])
        for i, (start, end, label) in enumerate(segments, 1):
            lines.extend([f'        intervals [{i}]:', f'            xmin = {start}', f'            xmax = {end}',
                          f'            text = "{escape(label)}"'])
    return '\n'.join(lines) + '\n'
