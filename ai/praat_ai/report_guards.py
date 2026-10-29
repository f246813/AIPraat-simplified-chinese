"""Deterministic checks at the report boundary; no extra model request.

Evidence IDs follow state.evidence order. ``E1.q3`` names a parsed quantity,
not an arbitrary number in a prompt. Calculations use a small arithmetic AST
and are rendered by this module. These checks bind numeric claims and a set of
explicit prosody claims to their sources; they do not prove free-text semantics.
"""
from __future__ import annotations

import ast
import copy
import math
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic_ai import ModelRetry

from .escape_policy import note_guard
from .tools import SOUND_CLASSES, parse_object_context


#: 算不出来的推导式在正文里换成这个：不把内部语法漏给用户，也不因为一句推导丢掉整份报告。
from . import evidence_quality, interpretation_policy, evidence_boundaries

INTERPRETATION_NOTE = '现有证据不足以支持部分发音判断，相关断言已省略。'

UNVERIFIED_CALC = '[推导未核对]'
#: 守卫没核上的数值，在保留的正文里换成这个占位符：正文能留，没测过的数字不能当实测。
#: 云端恢复稿（``cloud_agent.rescued_report``）与本机最终交付（``chat``）共用这一个标记。
UNVERIFIED_NUMBER = '[未通过核对的数值]'
UNVERIFIED_NOTE = '（部分数值未能从测量结果中确认，已标出；依赖这些数值的判断暂不能作为结论。）'

#: 正文里「这个数没核上」的标记：缺口标出来可以照常交付，但不能对外说全部核对通过。
GAP_MARKERS = (UNVERIFIED_NUMBER, UNVERIFIED_CALC)


def _gap_markers(text: str) -> int:
    """正文里明确标出的数值缺口有几处。"""

    body = str(text or '')
    return sum(body.count(marker) for marker in GAP_MARKERS)


def _note_gap(state: Any, count: int) -> None:
    """记下这份正文里有几处数值缺口（``report_gap``）。

    ``report_verified`` 的语义是「核过的都对上了」，不是「有一处没核上也说全部通过」。
    缺口已经就地标成 :data:`UNVERIFIED_NUMBER`、正文照常交付，但核对状态必须如实为
    False：``guard_delivered_text`` 依 ``report_gap`` 返回 ``rejected_masked``，
    ``audit`` 里另记 ``gap``（``audit_report`` 收尾只重写 ``verified`` 那几项，
    这个键不会被抹掉）。
    """

    if count <= 0:
        return
    metrics = getattr(state, 'metrics', None)
    if not isinstance(metrics, dict):
        return
    metrics['report_gap'] = int(metrics.get('report_gap') or 0) + int(count)
    audit = metrics.get('audit')
    if isinstance(audit, dict):
        audit['gap'] = int(audit.get('gap') or 0) + int(count)


class CalcUnavailable(Exception):
    """推导式的语法、量纲或引用无法核对；调用方标注该推导为未核对，不因此阻断整份报告。"""


def _block(state: Any, rule: str, message: str) -> None:
    """结构性守卫的唯一出口：调用方按 ``rule`` 决定重试还是就地处置。

    ``rule`` 挂到异常上（``ModelRetry.rule``）：``report_audit`` 据此区分
    「结构性错误（可以重试一次）」与「数值没对上（就地遮蔽，不许重写整份报告）」。
    """

    note_guard(state, 'block', rule)
    error = ModelRetry(message)
    error.rule = rule
    raise error


_N = (r"[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d*)?|\.\d+)"
      r"(?:[eE][-+]?\d+)?")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])" + _N + r"(?![A-Za-z0-9_.])")
_UNIT = re.compile(
    r"\s*(kHz|Hz(?:\s*/\s*(?:s|秒))?|半音\s*/\s*(?:s|秒)|"
    r"milliseconds?|seconds?|msec|sec|ms|s|毫秒|秒|dB|Pa|%|％|倍|半音|八度|"
    r"赫兹|帕|分贝|(?:个)?(?:有效)?帧|frames?)(?![A-Za-z])",
    re.I,
)
_RANGE = re.compile(
    r"(?P<a>" + _N + r")\s*(?P<ua>ms|s|毫秒|秒)?\s*[–—~～至到-]\s*"
    r"(?P<b>" + _N + r")\s*(?P<ub>milliseconds?|seconds?|msec|sec|ms|s|毫秒|秒)(?![A-Za-z])",
    re.I,
)
_VALUE_RANGE = re.compile(
    r'(?P<a>' + _N + r')\s*(?P<ua>kHz|Hz|dB|%|％)?\s*[–—~～至到-]\s*'
    r'(?P<b>' + _N + r')\s*(?P<ub>kHz|Hz|dB|%|％)(?![A-Za-z])', re.I)
#: ``[0.446194, 0.66158] 秒``：模型很常这样写编辑器圈选。它和「0.446194–0.66158 秒」
#: 括号对与短横线是同一范围的两种写法；不识别括号对会把正确的范围当成孤立数字。
_RANGE_PAIR = re.compile(
    r'[\[［(（]\s*(?P<a>' + _N + r')\s*[,，、]\s*(?P<b>' + _N + r')\s*[\]］)）]\s*'
    r'(?P<u>milliseconds?|seconds?|msec|sec|ms|s|毫秒|秒)?', re.I)


def _range_parts(text, tool=''):
    parts = {span: (role, unit) for match in _RANGE.finditer(text)
             for span, role, unit in ((match.span('a'), 'range.start', match['ua'] or match['ub']),
                                     (match.span('b'), 'range.end', match['ub']))}
    # Frequency/level bands inherit the trailing unit, but are not time scopes
    # or individual pitch measurements. Keep both bounds typed for validation.
    for match in _VALUE_RANGE.finditer(text):
        prefix = text[:match.start()]
        category = ''
        if not re.search(r'频带|频段|通带|高频带|band', prefix[-60:], re.I):
            for label in reversed(re.split(r'[，,；;：:=＝]', prefix)):
                role = _role(label, tool)
                candidate = role.rsplit('.', 1)[0]
                if candidate in {'pitch', 'intensity'} or candidate.startswith('formant.'):
                    category = candidate
                    break
        extraction = bool(re.search(r'(?:提取|分析|搜索|设置|参数).{0,12}(?:范围|频带|上下限)', prefix[-60:]))
        start_role, end_role = (('analysis.pitch_floor', 'analysis.pitch_ceiling') if extraction
                               else (category + '.min', category + '.max') if category
                                else ('value_range.start', 'value_range.end'))
        parts[match.span('a')] = (start_role, match['ua'] or match['ub'])
        parts[match.span('b')] = (end_role, match['ub'])
    # ``[a, b] 秒`` 与 ``a–b 秒`` 是同一个范围；括号外带单位时两个端点都算这个单位。
    for match in _RANGE_PAIR.finditer(text):
        unit = match['u'] or ''
        parts[match.span('a')] = ('range.start', unit)
        parts[match.span('b')] = ('range.end', unit)
    return parts


_CITATION = re.compile(r"\[E(\d+)\]")
_OBJECT_IDENTIFIER = re.compile(
    r'(?:(?:对象|编号)\s*[:：]?\s*|(?<![A-Za-z0-9_])(?:object|id)\s*[:：]?\s*|#\s*)(\d+)(?![\d.])', re.I)
_CALC = re.compile(r"\{\{calc:([^{}|]+)\|([^{}|]+)\}\}")
_REF = re.compile(r"\b(E\d+)\.q([1-9]\d*)\b")
#: 结构化出口：``{{q:E2.q3}}`` 由程序按证据第 N 个量原样渲染（值与单位都来自证据）。
#: 模型不必再自己抄数字——抄错、换小数位、换算单位这些失分点从源头消失。
#: 序号上限 6 位：``{{q:E1.q999…}}`` 这种超长序号不能进 ``int()``（Python 3.11+
#: 对超过 4300 位的整数串直接抛 ``ValueError``，一份报告会崩在守卫里而不是被标缺口）。
_QUANTITY = re.compile(r"\{\{q:(E\d+)\.q([1-9]\d{0,5})\}\}")
#: 数值模板的开头：判定正文里还有没有没处理的模板。
_QUANTITY_TEMPLATE = re.compile(r"\{\{\s*q\s*:")
#: 有 ``}}`` 收尾、但不在登记表里的数值模板。典型写法 ``{{q:E3.r1}}``：模型把
#: ``quantity_refs`` 里的 ``r{证据行号}:{量名}`` 当成序号抄了下来。
_QUANTITY_DELIMITED = re.compile(r"\{\{\s*q\s*:[^{}\n]*?\}\}")
#: 被截断的数值模板（``{{q:E3.q1``）：只吃模板字符，后面紧跟的中文正文要留下。
_QUANTITY_TRUNCATED = re.compile(r"\{\{\s*q\s*:[A-Za-z0-9_.:#|<>/\s-]*")
#: 兜底：模板里夹了中文/换行之类的非常规写法，同样整段去掉，绝不把内部语法漏给用户。
_QUANTITY_ANY = re.compile(r"\{\{\s*q\s*:[^{}\n]*\}?\}?")
#: 分句：句号/分号/全角逗号/半角逗号（**数字之间的逗号不算分隔**：``220,000``、``[0.446, 0.66158]``
#: 里的逗号属于数值本身，切开会把千分位读成两个数）。偏移保持绝对，遮蔽仍按原文定位。
_CLAUSE_SPLIT = re.compile(r'(?:[^；;。，\n]|,\s*(?=[-+]?\d))+')
_GENERAL = re.compile(r"一般知识|一般原理|概念说明|概念解释|通用知识|示例[（(]非本录音[）)]|general knowledge", re.I)
_SPECIFIC = re.compile(r"本(?:段|次|录音|对象|材料)|这(?:段|个|次|份)|该(?:段|录音|对象)|你(?:的|这)|当前|实测|测得|测量结果")
_ADVICE = re.compile(r'^\s*(?:[-*]\s*)?(?:建议|下一步|请|可以尝试|可尝试|练习时|参数设置)')
_TONE_CATEGORY = r'第?[一二三四1-4]声(?!音|源|道|学|明|调)|阴平|阳平|上声|去声|轻声'
_PROSODY = re.compile(
    _TONE_CATEGORY + r'|'
    r"平板型|头高型|中高型|尾高型|平板调|升调|降调|上升|下降|升高|降低|"
    r"(?:几乎|近似|接近)(?:是)?一条(?:平)?直线|"
    r"(?:语调|音调|音高|基频).{0,12}(?:平淡|单调|平坦|平稳|起伏小|变化小)|"
    r"(?:平淡|单调|平坦|平稳)的?(?:语调|音调|音高)|"
    r"\b(?:heiban|atamadaka|rising|falling|flat intonation)\b", re.I,
)
_INABILITY = re.compile(r"不能|无法|不可(?:据此|判断|确定)|未能|尚不能|不(?:代表|足以|等于)|不足|缺少|缺乏|尚无|未测|还需|需要先|必须先")


@dataclass(frozen=True)
class Quantity:
    evidence: str
    index: int
    row: int
    value: float
    text: str
    unit: str
    role: str
    start: int
    end: int

    @property
    def ref(self) -> str:
        return f'{self.evidence}.q{self.index}'


@dataclass
class Record:
    id: str
    evidence: Any
    quantities: list[Quantity]
    target: str | None
    target_name: str
    interval: tuple[float, float] | None


def _unit(raw: str) -> tuple[str, float, tuple[int, int, str]]:
    """Frequency and time are independent dimensions; other units are named."""
    key = re.sub(r'\s+', '', raw).lower().replace('％', '%')
    key = {'赫兹': 'hz', '千赫': 'khz', '帕': 'pa', '分贝': 'db'}.get(key, key)
    if key in {'s', 'sec', 'second', 'seconds', '秒'}:
        return 's', 1.0, (0, 1, '')
    if key in {'ms', 'msec', 'millisecond', 'milliseconds', '毫秒'}:
        return 'ms', .001, (0, 1, '')
    if key in {'hz', 'khz'}:
        return 'kHz' if key == 'khz' else 'Hz', 1000.0 if key == 'khz' else 1.0, (1, 0, '')
    if key in {'pa'}:
        return 'Pa', 1.0, (0, 0, 'Pa')
    if key in {'hz/s', 'hz/秒'}:
        return 'Hz/s', 1.0, (1, -1, '')
    if key in {'半音/s', '半音/秒'}:
        return '半音/s', 1.0, (0, -1, '半音')
    if key in {'帧', '有效帧', '个帧', '个有效帧', 'frame', 'frames'}:
        return '帧', 1.0, (0, 0, 'frames')
    if key in {'', '%', '倍'}:
        # Percent expressions explicitly multiply by 100, as ordinary formulas do.
        return key, 1.0, (0, 0, '')
    return raw.strip(), 1.0, (0, 0, key)


def _precision(text: str) -> float:
    mantissa, _, exponent = text.lower().partition('e')
    decimals = len(mantissa.split('.')[1]) if '.' in mantissa else 0
    # 指数只用来算容差，量级够了就行：``220e999999999`` / 5000 位的指数串
    # 直接进 ``10 ** n`` 会 ``OverflowError``（``int()`` 本身还会撞上 4300 位的
    # 整数串上限），整份报告就崩在守卫里。按 ±40 位夹住，行为对正常数字不变。
    power = int(exponent.lstrip('+-')[:6] or 0) * (-1 if exponent.startswith('-') else 1)
    power = max(-40, min(40, power))
    return .5 * 10 ** (power - decimals)


#: 全角数字/句点/逗号：模型偶尔会写 ``８１．５５８``，以前整段核对直接跳过它。
_FULLWIDTH = str.maketrans('０１２３４５６７８９．，％', '0123456789.,%')

_CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '壹': 1, '二': 2, '两': 2, '贰': 2, '三': 3, '叁': 3,
              '四': 4, '肆': 4, '五': 5, '伍': 5, '六': 6, '陆': 6, '七': 7, '柒': 7,
              '八': 8, '捌': 8, '九': 9, '玖': 9}
_CN_UNITS = {'十': 10, '拾': 10, '百': 100, '佰': 100, '千': 1000, '仟': 1000}
_CN_NUMBER = re.compile(r'[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟点]{2,}')


def _chinese_number(text: str) -> float | None:
    """将支持的中文整数与小数转换为数值，无法解析时返回 None。"""

    if '点' in text:
        whole, _, fraction = text.partition('点')
        base = _chinese_number(whole) if whole else 0.0
        digits = [_CN_DIGITS.get(char) for char in fraction]
        if base is None or not digits or any(digit is None for digit in digits):
            return None
        return base + float('0.' + ''.join(str(digit) for digit in digits))
    total = 0
    section = 0
    digit = None
    for char in text:
        if char in _CN_DIGITS:
            digit = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            section += (digit if digit is not None else 1) * unit
            digit = None
        else:
            return None
    total += section + (digit or 0)
    return float(total)


def _normalize_claim(text: str) -> str:
    """把全角数字写法归一成 ASCII，其余照旧。"""

    return text.translate(_FULLWIDTH)


def _number_candidates(raw: str) -> list[str]:
    """返回数值文本的可能读法，兼容千分位和小数逗号；由测量证据决定可接受的读法。"""

    if ',' not in raw:
        return [raw]
    return [raw.replace(',', ''), raw.replace(',', '.')]


def _role(prefix: str, tool: str = '') -> str:
    """Recognized quantity labels, kept separate from the numeric matcher."""
    pieces = re.split(r'[，,；;：:=＝]', prefix)
    nearby = next((part for part in reversed(pieces) if part.strip()), '')[-60:]
    full = prefix[-160:]
    if re.search(r'有效(?:基频)?帧(?:数)?|有效\s*$', nearby):
        return 'analysis.accepted_frames'
    analysis_context = re.search(r'基频|提取|分析|搜索|设置|参数', full)
    if analysis_context and re.search(r'下限|floor', nearby, re.I):
        return 'analysis.pitch_floor'
    if analysis_context and re.search(r'上限|ceiling', nearby, re.I):
        return 'analysis.pitch_ceiling'
    formant = re.search(r'(?:\bF\s*([1-9])\b|第\s*([1-9])\s*共振峰|共振峰\s*([1-9]))', nearby, re.I)
    if not formant and 'formant' in tool:
        formant = re.search(r'(?:\bF\s*([1-9])\b|第\s*([1-9])\s*共振峰)', full, re.I)
    if formant:
        category = 'formant.' + next(x for x in formant.groups() if x)
    elif re.search(r'基频|音高|F0|pitch', nearby, re.I) or tool in {'pitch', 'pitch_statistics'}:
        category = 'pitch'
    elif re.search(r'强度|音强|响度|intensity', nearby, re.I) or 'intensity' in tool:
        category = 'intensity'
    elif re.search(r'VOT|嗓音起始|发声起始', nearby, re.I) or tool == 'vot':
        category = 'vot'
    elif re.search(r'时长|duration', nearby, re.I) or tool == 'duration':
        return 'duration'
    elif re.search(r'斜率', nearby) and re.search(r'基频|音高', full):
        category = 'pitch'
    else:
        category = ''
    if re.search(r'原始平均|raw\s*mean', nearby, re.I):
        metric = 'raw_mean'
    elif re.search(r'峰值延迟|peak latency', nearby, re.I):
        metric = 'peak_latency'
    elif re.search(r'中位|median', nearby, re.I):
        metric = 'median'
    elif re.search(r'标准差|standard deviation|\bsd\b', nearby, re.I):
        metric = 'sd'
    elif re.search(r'斜率|slope', nearby, re.I):
        metric = ('slope_octave_free' if '倍频跳变' in nearby else
                  'slope_absolute' if '绝对' in nearby else 'slope')
    elif re.search(r'起点|起始基频|start', nearby, re.I):
        metric = 'start'
    elif re.search(r'终点|结束基频|end', nearby, re.I):
        metric = 'end'
    elif re.search(r'最高点|峰值.{0,5}时|最大值.{0,5}时', nearby):
        return (category or 'pitch') + '.max_time'
    elif re.search(r'最低|最小|minimum|\bmin\b', nearby, re.I):
        metric = 'min'
    elif re.search(r'最高|最大|峰值|maximum|\bmax\b', nearby, re.I):
        metric = 'max'
    elif re.search(r'平均|均值|mean|average', nearby, re.I):
        metric = 'mean'
    else:
        metric = 'value'
    if category:
        return category + '.' + metric
    if metric != 'value':
        return metric
    label = re.sub(r'[^A-Za-z\u3400-\u9fff_]', '', nearby)[-20:]
    return label or 'value'


def _quantities(eid: str, evidence: Any) -> list[Quantity]:
    result: list[Quantity] = []
    acoustic = getattr(evidence, 'acoustic', {}) or {}
    if acoustic.get('schema') == 'pronunciation-measurements-v2':
        for row, measure in enumerate(acoustic.get('measurements', []), 1):
            value = measure.get('value')
            if value is not None and math.isfinite(float(value)):
                result.append(Quantity(eid, len(result) + 1, row, float(value),
                                       f'{float(value):.9g}', measure['unit'], measure['role'], 0, 0))
        return result
    if acoustic.get('schema') == 'acoustic-analysis-v1':
        def add(value, unit, role, row=1):
            if value is not None and math.isfinite(float(value)):
                result.append(Quantity(eid, len(result) + 1, row, float(value),
                                       f'{float(value):.9g}', unit, role, 0, 0))
        bounds = acoustic.get('scope', [])
        if len(bounds) == 2:
            add(bounds[0], 's', 'range.start')
            add(bounds[1], 's', 'range.end')
            add(bounds[1] - bounds[0], 's', 'duration')
        kind = acoustic.get('kind', 'vot')
        for index, event in enumerate(acoustic.get('events', []), 2):
            if event.get('status') in {'candidate', 'validated'}:
                role = ({'f0_peak_time':'pitch.max_time', 'f0_start_time':'pitch.start_time',
                         'f0_end_time':'pitch.end_time'}.get(event['kind'], 'pitch.query_time')) if kind == 'pitch' else 'vot.' + event['kind']
                add(event.get('time'), 's', role, index)
        for measure in acoustic.get('measures', []):
            role = measure['name'] if kind == 'pitch' else 'vot.value' if measure['name'] == 'vot_ms' else 'vot.' + measure['name']
            role = {'pitch.minimum': 'pitch.min', 'pitch.maximum': 'pitch.max'}.get(role, role)
            add(measure.get('value'), measure['unit'], role)
            if measure['name'] == 'vot_ms' and measure.get('value') is not None:
                add(measure['value'] / 1000, 's', role)
        if kind == 'pitch':
            method = acoustic.get('method') or {}
            add(method.get('floor_hz'), 'Hz', 'analysis.pitch_floor')
            add(method.get('ceiling_hz'), 'Hz', 'analysis.pitch_ceiling')
            screening = next((c for c in acoustic.get('checks', [])
                              if c.get('rule') == 'pitch.screening'), {})
            add(screening.get('accepted'), '帧', 'analysis.accepted_frames')
        if kind == 'vot' or evidence.tool != 'measure':
            return result
    quality = evidence_quality.pitch_quality(evidence)
    for row_number, row in enumerate(evidence.rows, 1):
        if evidence_quality.is_quality_row(row):
            continue  # Screening thresholds/counts are not measured F0 values.
        if acoustic.get('kind') == 'pitch':
            from .pitch_events import covered_row
            if covered_row(row,acoustic):
                continue
        range_parts = _range_parts(row, evidence.tool)
        for match in _NUMBER.finditer(row):
            if re.match(r'\s*共振峰', row[match.end():]) or re.search(r'第\s*$', row[:match.start()]):
                continue
            unit_match = _UNIT.match(row, match.end())
            unit = unit_match[1] if unit_match else ''
            role = _role(row[:match.start()], evidence.tool)
            if role == 'pitch.slope' and evidence.tool == 'measure':
                # 单项 measure 的正式参数可补全省略的量名；两种斜率均不提供方向。
                parameters = (evidence.arguments.get('parameter')
                              or evidence.arguments.get('parameters') or [])
                if isinstance(parameters, str):
                    parameters = [piece for piece in re.split(r'[,，\s]+', parameters) if piece]
                if set(parameters) == {'pitch_slope_octave_free'}:
                    role = 'pitch.slope_octave_free'
                elif set(parameters) == {'pitch_slope'}:
                    role = 'pitch.slope_absolute'
            if match.span() in range_parts:
                role, unit = range_parts[match.span()]
            elif not unit and not re.search(r'[=:＝]\s*$', row[:match.start()]):
                continue
            # Undefined rows contain range/time parameters, never a measured value.
            if re.search(r'无法计算|undefined|没有可用的数据', row, re.I) and not role.startswith('range.'):
                continue
            if role.startswith('pitch.') and quality['status'] == 'suspect':
                continue
            value = float(match[0])
            if not math.isfinite(value):
                continue
            result.append(Quantity(eid, len(result) + 1, row_number, value, match[0], unit,
                                   role, match.start(), unit_match.end() if unit_match else match.end()))
    return result


def _target(evidence: Any) -> tuple[str | None, str]:
    rows = parse_object_context(evidence.source or '')
    requested = evidence.arguments.get('object')
    selected = [row for row in rows if row.selected]
    for row in rows:
        if requested is not None and str(requested) in {str(row.id), row.name, f'{row.class_name} {row.name}'}:
            return str(row.id), row.name
    if requested is not None:
        return str(requested), ''
    if len(selected) == 1:
        return str(selected[0].id), selected[0].name
    return None, ''


def _records(state: Any) -> dict[str, Record]:
    records: dict[str, Record] = {}
    for number, evidence in enumerate(state.evidence, 1):
        eid = f'E{number}'
        quantities = _quantities(eid, evidence) if evidence.kind == 'measurement' else []
        target, name = _target(evidence)
        starts = [q for q in quantities if q.role == 'range.start']
        ends = [q for q in quantities if q.role == 'range.end']
        interval = None
        if starts and ends:
            interval = (starts[0].value * _unit(starts[0].unit)[1], ends[0].value * _unit(ends[0].unit)[1])
        elif 'from' in evidence.arguments and 'to' in evidence.arguments:
            try:
                interval = (float(evidence.arguments['from']), float(evidence.arguments['to']))
            except (ValueError, TypeError):
                pass
        records[eid] = Record(eid, evidence, quantities, target, name, interval)
    return records


#: 拒绝理由里那处没核上的数值：「数值“0.551 秒”未匹配…」。
_REJECTED_VALUE = re.compile(r'数值“([^”]{0,60})”')


def rejected_values(reason: str) -> list[str]:
    """从守卫的拒绝理由里取出没核上的数值。

    保留模型草稿时用得到：正文可以留，但这几个没通过核对的数字不能当实测给用户看。
    """

    return [item.strip() for item in _REJECTED_VALUE.findall(str(reason or '')) if item.strip()]


def report_context_data(state: Any) -> dict[str, Any]:
    """Compact IDs and row/role indexes, with no second copy of source values."""

    records = _records(state)
    if not records:
        return {}
    data: dict[str, Any] = {'evidence_ids': list(records)}
    refs = {eid: [f'q{index}:{q.role}' for index, q in enumerate(record.quantities, 1)]
            for eid, record in records.items() if record.quantities}
    if refs:
        data['quantity_refs'] = refs
    return data


#: 数值纪律：提示词里唯一一处硬约束（守卫的判定口径与它一一对应）。
#: 真机 10 轮（v1）的遮蔽记录里，模型犯的其实不是「价值观」问题，而是四条**行文**问题：
#: ①把两个选区的数放进同一句（守卫按本句选区核对，另一段的数就没了归属）；
#: ②抄证据时省小数位或单位（``96.000 Hz`` 写成 ``96``）；
#: ③自己算跨度/时长（「不到 1 秒」）；
#: ④把典型值写成具体数字（``1900 Hz``）。所以 v2 把这四条写成可执行的行文纪律。
#: 长度要克制：证据分支进 32k 上下文无压力，但无证据分支会被
#: ``test_cloud_escape`` / ``test_staircase_review_fixes`` 的 2048/3072 紧预算卡住，
#: 所以那边只用最短三句（见 ``_NUMERIC_DISCIPLINE_COMPACT``）。
_NUMERIC_DISCIPLINE = (
    '\n数值纪律（硬约束，全部要遵守）：'
    '①正文里每个数字都必须能落到某条证据的同一对象、范围、量名，没有对应证据就不写这个数；'
    '②不做单位换算：证据写“秒”就写“秒”，写“Hz”就写“Hz”，不要自行换成毫秒或千赫兹；'
    '③工具已返回的均值、斜率等实测量直接引用quantity_refs对应的q模板或照抄结果，不能重新计算。'
    '只有新增推导（如两对象差值、比例、时长差）使用calc，'
    '输入必须是真实quantity_refs中的引用，不能把测量数值当常数填进calc。'
    '不要自己写“不到 1 秒”“约 4 秒”“高出 30%”这类自己算好的结论，正文里也不要出现算式或运算过程；'
    '④一行只说一个选区的一个量：两个选区的数不要放进同一句比较，'
    '否则按本行选区核对时另一个数没有归属，会被遮蔽；'
    '⑤照抄证据要连小数位和单位一起抄：证据是 96.000 Hz 就写 96.000 Hz，不要写成 96 或 96.0；'
    '⑥calc 渲染出的结果不要再用文字重复一遍；'
    '⑦典型范围与常识阈值写在标注（一般知识）的句子里，只给定性说法，不给具体数字。')

#: 无证据时用最短形式：这时没有可换算、可推算的实测值，长篇纪律只是白占上下文
#: （见上面的长度说明），三句足以覆盖「不写无据数值 / 不换算 / 不自算」。
_NUMERIC_DISCIPLINE_COMPACT = (
    '\n数值纪律：没有对应证据就不写；不做单位换算；算出来的数一律写成{{calc:…}}由程序算。')


def report_guard_instructions(state: Any) -> str:
    # 一般知识与录音测量分别约束；对照材料的要求由具体测量任务确定。
    if not state.evidence:
        return ('\n没有测量时不填录音数值或下音调结论；一般知识明确标注，缺证据就说明。'
                + _NUMERIC_DISCIPLINE_COMPACT)
    if not state.evidence:
        return ('\n没有测量时不填录音数值或下音调结论；一般知识明确标注，缺证据就说明。'
                + _NUMERIC_DISCIPLINE_COMPACT)
    return ('\n量化陈述逐行引[E编号]，须同对象、范围、量名、单位；一般知识明确标注。'
            'quantity_refs直接列出q序号：如q2写{{q:E1.q2}}，不是证据行号；程序按证据原样渲染数值和单位。'
            '推导用{{calc:E1.q1/E2.q1*100|%}}，仅真实输入和四则，程序写算式结果。'
            '多个引证连写成[E1][E2]（不要写成[E1, E2]），否则引证认不出来、要多跑一次补引证。'
            '具体录音音调结论须同目标且有定义值的走向证据，平均值不足。'
            '逐字引用、选区范围、一般知识的写法与例子见 skill=notation。'
            + _NUMERIC_DISCIPLINE)


def _same_value(value: float, text: str, unit: str, quantity: Quantity) -> bool:
    _, scale, dimension = _unit(unit)
    _, source_scale, source_dimension = _unit(quantity.unit)
    # 范围端点允许省略单位：``[0.446194, 0.66158]`` 没写「秒」，两个端点仍然指同一段时间，
    # 而且这里仍然要求数值本身在容差内相等，不会因此放过别的量。
    unitless_range = unit == '' and quantity.role.startswith('range.')
    if dimension != source_dimension and not unitless_range:
        return False
    # Omitted units do not silently turn Hz into dimensionless numbers.
    if unit == '' and quantity.unit != '' and not unitless_range:
        return False
    if source_dimension == (0, 0, 'frames'):
        return value == quantity.value
    tolerance = _precision(text) * scale + _precision(quantity.text) * source_scale + 1e-10
    return abs(value * scale - quantity.value * source_scale) <= tolerance


def _roles_match(claim: str, source: str) -> bool:
    if claim.startswith('range.'):
        return claim == source
    if claim == source:
        return True
    if claim in {'mean', 'min', 'max', 'median', 'sd', 'start', 'end', 'slope', 'slope_absolute', 'slope_octave_free'}:
        return source.endswith('.' + claim)
    # A generic pitch summary may omit the word "mean", but may not become F1.
    if claim == 'pitch.value':
        return source in {'pitch.value', 'pitch.mean'}
    if claim.startswith('formant.') and claim.endswith('.value'):
        return source in {claim, claim.replace('.value', '.mean')}
    # 「最高频率出现在 0.446 秒」：声明侧只说「最高」，证据侧是 max_time。量纲由
    # _same_value 另外核对（秒 vs Hz），所以这里放行不会让频率值冒充时刻。
    if claim.endswith(('.max', '.min')) and source == claim + '_time':
        return True
    return False


#: 本身就算量名的角色（不在这个集合里、又不带类别的，只能当自由文本标签）。
_NAMED_ROLES = {'mean', 'min', 'max', 'median', 'sd', 'start', 'end',
                'slope', 'slope_absolute', 'slope_octave_free', 'duration', 'range.start', 'range.end'}


def _free_label(role: str) -> bool:
    """这个角色名只是解读数字出处的自由标签（「爆破」「浊音起始」），不是量名吗？

    证据侧的角色是按工具名和邻近词推出来的（``vot`` 行的每个数都被打成
    ``vot.value``），声明侧却是从模型自己的句子里取标签。两边的标签天然不会一字不差，
    所以自由标签不能直接比字符串；只有真的点名了量名（基频/共振峰/强度/VOT/时长…）
    才按量名核对。
    """

    if not role or role == 'value':
        return True
    if '.' in role:
        return False
    return role not in _NAMED_ROLES


def _explicit_targets(state: Any, text: str, records: dict[str, Record]) -> set[str]:
    """这段文字里**点名**的对象（``对象 2``、``#3``、证据里的对象名），不含默认选中目标。

    行里点了名就只认点名的对象；没点名才轮到默认选中目标或原目标授权（见 :func:`_line_targets`
    与 :func:`_authorized_targets`）。
    """

    explicit = set(_OBJECT_IDENTIFIER.findall(text))
    known = {str(row.id) for row in parse_object_context(state.context_text)}
    known.update(record.target for record in records.values() if record.target is not None)
    if explicit - known:
        _block(state, 'report.object_unknown', '报告引用了快照和证据中不存在的对象编号。')
    for record in records.values():
        if record.target and len(record.target_name) >= 2 and record.target_name in text:
            explicit.add(record.target)
    return explicit


def _line_targets(state: Any, text: str, records: dict[str, Record]) -> set[str]:
    explicit = _explicit_targets(state, text, records)
    if explicit:
        return explicit
    cited = {records[f'E{eid}'].target for eid in _CITATION.findall(text)
             if f'E{eid}' in records and records[f'E{eid}'].target is not None}
    if cited and not any(row.selected and row.class_name in SOUND_CLASSES
                         for row in parse_object_context(state.context_text)):
        return cited  # Evidence retains its Sound even when Praat selected a derived object.
    return {str(row.id) for row in parse_object_context(state.context_text) if row.selected}


def _authorized_targets(state: Any, records: dict[str, Record]) -> set[str]:
    """原目标里**明确点名**、而且已有证据确认的对象（「分别测量对象 1 和对象 2，再告诉我两者差值」）。

    跨对象比较/差值/比例只对用户自己点名的对象开放：目标里没提到的对象永远不在这个集合里，
    所以「两者差值」夹带第三个对象仍然被拦（见 ``report.calc_other_target``）。
    """

    goal = str(getattr(state, 'goal', '') or '')
    if not goal:
        return set()
    known = {str(row.id) for row in parse_object_context(state.context_text)}
    known.update(record.target for record in records.values() if record.target is not None)
    named = {record.target for record in records.values()
             if record.target and len(record.target_name) >= 2 and record.target_name in goal}
    return (set(_OBJECT_IDENTIFIER.findall(goal)) | named) & known


def _general(text: str, in_general: bool, records: dict[str, Record]) -> bool:
    marked = in_general or bool(_GENERAL.search(text))
    names_target = any(len(r.target_name) >= 2 and r.target_name in text for r in records.values())
    return marked and not _SPECIFIC.search(text) and not names_target and not _CITATION.search(text)


def _whole_ranges(state: Any, records: dict[str, Record]) -> dict[str, tuple[float, float]]:
    from .chat import _whole_recording_request
    if not _whole_recording_request(state.goal):
        return {}
    result = {}
    for record in records.values():
        durations = [q for q in record.quantities if q.role == 'duration' and _unit(q.unit)[2] == (0, 1, '')]
        if record.target is not None and durations:
            result[record.target] = (0, durations[0].value * _unit(durations[0].unit)[1])
    return result


def _identifier_number(cleaned: str, match: re.Match[str], targets: set[str]) -> bool:
    """这个数字是编号/标签而不是量值吗（Markdown 序号、对象编号、共振峰序号）。"""

    # _NUMBER accepts a trailing decimal dot, so Markdown "1." is matched
    # _NUMBER accepts a trailing decimal dot, so Markdown "1." is matched
    # including its punctuation. Identify the marker from the line instead.
    # 允许 Markdown 强调包裹（``**1.**``）与句中编号（``。2. xxx``）：遮蔽列表序号是纯破坏。
    ordinal = re.match(r'^\s*(?:#{1,6}\s*)?(?:\*{1,3}|_{1,3})?\s*(\d{1,2})[.)、](?=\s)', cleaned)
    if ordinal and match.start() == ordinal.start(1) and match.end() <= ordinal.end():
        return True
    if re.match(r'^\s*(?:\*{1,3}|_{1,3})?\s*\d{1,2}[.)、]\s', cleaned) is None:
        inline = re.search(r'[。；;：:]\s*(\d{1,2})[.)、](?=\s)', cleaned)
        if inline and inline.start(1) == match.start():
            return True
    if re.match(r'\s*(?:\*{1,3}|_{1,3})?\s*[`\'"]?$', cleaned[match.end():]) and match[0].endswith('.'):
        return True
    if any(item.span(1) == match.span() and item[1] in targets
           for item in _OBJECT_IDENTIFIER.finditer(cleaned)):
        return True
    if re.match(r'\s*共振峰', cleaned[match.end():]) or re.search(r'第\s*$', cleaned[:match.start()]):
        return True
    return False


#: 句内明确标注的「一般知识」。标注文字本身常含「非本次测量」这类词，不能拿它当
#: 实测主张的证据——否则提示词里承诺的「一般知识明确标注」这条出口永远走不通。
_MARKED_GENERAL = re.compile(
    r'[（(][^）)]{0,24}(?:一般知识|通用知识|常见范围|general knowledge)[^）)]{0,24}[）)]|'
    r'(?:一般知识|通用知识|general knowledge)\s*[:：]', re.I)


def _marked_general(clause: str) -> bool:
    if not _MARKED_GENERAL.search(clause):
        return False
    probe = _MARKED_GENERAL.sub('', clause)
    return not _SPECIFIC.search(probe) and not _CITATION.search(probe)


def _verbatim_line(line: str) -> bool:
    """这一行是在**抄工具输出**，不是在给录音下断言。

    代码块里 ``mean_pitch = 81.558``、赋值式的 ``pitch = 220.0`` 都属于逐字引用：省略单位
    是正常的，前提是数值和某条证据**唯一**对得上（见 :func:`_numeric_claim_sources`）。
    这和「平均基频是 220。」那种不带单位的实测主张不同，后者仍然要拦。
    """

    stripped = line.strip()
    if stripped.startswith('```'):
        return True
    return bool(re.match(r'^\s*(?:[-*]\s*)?[A-Za-z_][A-Za-z0-9_.]*\s*[:=]\s*[-+]?[\d.]', stripped))


def _same_numeric_value(value: float, text: str, quantity: Quantity) -> bool:
    """忽略单位是否写出，只比数值（逐字引用用）。"""

    _, source_scale, _ = _unit(quantity.unit)
    return abs(value - quantity.value * source_scale) <= _precision(text) * source_scale + 1e-10


def _numeric_claim_sources(state: Any, line: str, records: dict[str, Record], *, verbatim: bool = False):
    """核对行中数值并返回命中证据 ID。返回 (sources, failure)，失败含规则、消息及需要遮蔽的数值/单位跨度。调用方决定阻断或补引用，共用同一匹配引擎。"""

    cites = [f'E{x}' for x in _CITATION.findall(line)]
    for eid in cites:
        if eid not in records:
            # 正常路径到不了这里：_strip_unknown_citations 会在验证前清掉并计数。
            return [], ('report.evidence_unknown', f'不存在证据[{eid}]；请引用现有证据或说明缺测量。')
    targets = _line_targets(state, line, records)
    sources: list[str] = []
    # 判定单元降到**分句**（逗号也切）：一句话里比较两个选区的写法很常见
    # （「后半段（0.6–0.892 秒）的平均值 85.333 Hz 低于前半段 96.000 Hz」），
    # 整句共用一个选区范围会把后半个数判成「没有归属」。切到分句后，规则一条没变：
    # 每个分句仍按自己出现的选区、引证、量名核对，编造或错归属照样被拦。
    for clause_match in _CLAUSE_SPLIT.finditer(line):
        clause = clause_match[0]
        if _marked_general(clause):
            continue
        clause_cites = [f'E{x}' for x in _CITATION.findall(clause)] or cites
        # 漏写引证时按「同对象的所有证据」核对，命中的证据由调用方补进正文：引证是守卫
        # 自己的记账方式，不该要求模型逐行照抄格式，更不该因为漏写就判成编造数据。
        available = [records[eid] for eid in clause_cites] if clause_cites else list(records.values())
        if targets:
            available = [r for r in available if r.target in targets]
        ranges = list(_RANGE.finditer(clause))
        range_parts = _range_parts(clause)
        supported_range = False
        range_scope = None
        if len(ranges) == 1:
            match = ranges[0]
            start = float(match['a']) * _unit(match['ua'] or match['ub'])[1]
            end = float(match['b']) * _unit(match['ub'])[1]
            available = [record for record in available if _supports_range(record, start, end, match)]
            # 范围本身已被证据确认（含 object_info/duration 的 0–duration）时，
            # 端点不再要求各自对应一条 range.start/end 量。
            supported_range = bool(available)
            range_scope = (start, end, match)
            if not supported_range:
                # 整段范围都没有证据支持：把这一段范围整体当作未核对的值，端点不逐个遮
                # （逐个遮会把 "0.446–0.892 秒" 拆成三个占位符，正文直接读不懂）。
                span_start, span_end = ranges[0].start('a'), ranges[0].end()
                return sources, ('report.number_unmatched',
                                 f'数值“{clause[span_start:span_end]}”未匹配同对象/范围的测量。'
                                 '逐行引证；缺测量就说明。',
                                 (clause_match.start() + span_start, clause_match.start() + span_end))
        cleaned = _CITATION.sub(lambda m: ' ' * len(m[0]), clause)
        cleaned = _normalize_claim(cleaned)
        for match in _NUMBER.finditer(cleaned):
            if _identifier_number(cleaned, match, targets):
                continue
            unit_match = _UNIT.match(cleaned, match.end())
            unit = unit_match[1] if unit_match else ''
            role = _role(cleaned[:match.start()])
            if match.span() in range_parts:
                role, unit = range_parts[match.span()]
            if supported_range and match.span() in range_parts:
                continue
            # 声明侧没点名量名（只有「爆破」「浊音起始」这类出处标签）时按「同对象 +
            # 同范围 + 同量纲 + 同数值」核对；点名了量名的仍走严格规则（F1 不能冒充
            # 平均基频）。_free_label 见上。
            hit, value = [], None
            for candidate in _number_candidates(match[0]):
                value = float(candidate)
                hit = [record for record in available
                       if any((_roles_match(role, q.role) or _free_label(role))
                              and _same_value(value, candidate, unit, q) for q in record.quantities)]
                if hit:
                    break
            if not hit:
                # 同对象、同量名、同数值但引证漏写/写错另一条：这不是编造数据，补上正确
                # 引证即可（「漏写引证不该判成编造」的既有口径，见上方注释）。
                available_ids = {record.id for record in available}
                siblings = [record for record in records.values()
                            if record.id not in available_ids and (not targets or record.target in targets)
                            and (range_scope is None or _supports_range(record, *range_scope))]
                for candidate in _number_candidates(match[0]):
                    value = float(candidate)
                    hit = [record for record in siblings
                           if any((_roles_match(role, q.role) or _free_label(role))
                                  and _same_value(value, candidate, unit, q) for q in record.quantities)]
                    if hit:
                        state.metrics['report.sibling_citation'] = state.metrics.get('report.sibling_citation', 0) + 1
                        break
            if not hit and verbatim and unit == '':
                # 逐字引用可以不带单位，但必须唯一对上一条证据，否则仍然按未核对处理。
                candidates = {(record.id, q.ref) for record in available for q in record.quantities
                              if _same_numeric_value(value, match[0], q)}
                if len({ref for _, ref in candidates}) == 1:
                    hit = [record for record in available
                           if any((record.id, q.ref) in candidates for q in record.quantities)]
            phrase = bool(_DERIVED_PHRASE.search(cleaned[max(0, match.start() - 24):match.start()]))
            if not hit and _derived_match(value, match[0], unit, role, phrase, available, targets):
                # 透明推导：由本句引用范围内的证据四则算得，不再当编造拦下。
                state.metrics['report_derived_accepted'] = state.metrics.get('report_derived_accepted', 0) + 1
                continue
            if not hit and _general_claim(clause):
                # 典型范围属于一般知识，不是本次测量；记一次供遥测观察，不阻断。
                state.metrics['report_general_value_assumed'] = state.metrics.get('report_general_value_assumed', 0) + 1
                continue
            if not hit:
                return sources, ('report.number_unmatched',
                                 f'数值“{match[0]} {unit}”未匹配同对象/范围/量名的测量。逐行引证；推导用calc占位式，缺测量就说明。',
                                 (clause_match.start() + match.start(),
                                  clause_match.start() + (unit_match.end() if unit_match else match.end())))
            for record in hit:
                if record.id not in sources:
                    sources.append(record.id)
        # 中文数字：写成「二百五十赫兹」的编造数值以前完全躲过核对。
        for match in _CN_NUMBER.finditer(cleaned):
            unit_match = _UNIT.match(cleaned, match.end())
            if not unit_match:
                continue            # 只有跟着单位才当数值主张，普通中文里的「三方面」不算
            value = _chinese_number(match[0])
            if value is None:
                continue
            unit = unit_match[1]
            role = _role(cleaned[:match.start()])
            hit = [record for record in available
                   if any((_roles_match(role, q.role) or _free_label(role))
                          and _same_value(value, f'{value:g}', unit, q) for q in record.quantities)]
            if not hit:
                return sources, ('report.number_unmatched',
                                 f'数值“{match[0]} {unit}”未匹配同对象/范围/量名的测量。'
                                 '请用阿拉伯数字并逐行引证；推导用calc占位式。',
                                 (clause_match.start() + match.start(),
                                  clause_match.start() + unit_match.end()))
            for record in hit:
                if record.id not in sources:
                    sources.append(record.id)
    return sources, None


def _supports_range(record: Record, start: float, end: float, match: re.Match[str]) -> bool:
    """这条证据能支持 ``start–end`` 这个范围吗。

    ``object_info``/``duration`` 的整对象时长可支持 ``0–duration`` 范围，
    其余记录按 ``record.interval`` 与数值精度核对。
    """

    tolerance_start = _precision(match['a']) * _unit(match['ua'] or match['ub'])[1] + .0005
    tolerance_end = _precision(match['b']) * _unit(match['ub'])[1] + .0005
    if record.evidence.tool in {'object_info', 'duration'} and abs(start) <= .001:
        durations = [q for q in record.quantities
                     if q.role == 'duration' and _unit(q.unit)[2] == (0, 1, '')]
        if durations:
            return abs(durations[0].value * _unit(durations[0].unit)[1] - end) <= tolerance_end
    if record.interval is None:
        return False
    return (abs(record.interval[0] - start) <= tolerance_start
            and abs(record.interval[1] - end) <= tolerance_end)


#: 「通常 85–180 Hz」这类典型范围不是本次测量：无引证时可以出现。
_GENERAL_HINT = re.compile(r'常见|典型|通常|一般来说|一般而言|大约|约\s*\d|文献|教科书|经验上')


def _general_hint(text: str) -> bool:
    return bool(_GENERAL_HINT.search(text))


def _derived_role_ok(role: str) -> bool:
    """点名了量名的位置不许用推导值顶上（平均基频不能拿最小值+最大值的均值冒充）。"""

    if not role:
        return True
    if role.startswith(('pitch.', 'formant.', 'intensity.', 'vot.', 'harmonicity.', 'range.')):
        return False
    return role not in _NAMED_ROLES


#: 明确在说「差、比值、占比」的位置：这些数天然是推导，不看量名。
_DERIVED_PHRASE = re.compile(r'相差|差值|之差|差异|高出|低于|多了|少了|增加|减少|下降|上升|'
                             r'占比|百分比|比例|变化量|幅度')


def _derived_values(scoped: list[Record], targets: set[str]) -> list[tuple[float, str]]:
    """同一批证据里两条同量纲实测值能四则得到的数（差值/和/均值/比例）。

    报告的「透明推导」是提示词鼓励的写法，但这些数没有独立的 ``[En]``，过去一律被判成
    编造。这里只承认**由本句引用范围内的证据算得**的组合，不放松「凭空数字」。
    """

    quantities = [q for record in scoped
                  if (not targets or record.target in targets)
                  for q in record.quantities if math.isfinite(q.value)]
    pool: list[tuple[float, str]] = []
    seen: set[tuple[float, str]] = set()
    for index, left in enumerate(quantities):
        _, left_scale, left_dimension = _unit(left.unit)
        left_value = left.value * left_scale
        for right in quantities[index + 1:]:
            _, right_scale, right_dimension = _unit(right.unit)
            if left_dimension != right_dimension:
                continue
            right_value = right.value * right_scale
            unit = left.unit or right.unit
            candidates = [(left_value - right_value, unit), (right_value - left_value, unit),
                          (left_value + right_value, unit), ((left_value + right_value) / 2, unit)]
            if left_dimension == (0, 0, ''):
                if right_value:
                    candidates.append((left_value / right_value * 100, '%'))
                if left_value:
                    candidates.append((right_value / left_value * 100, '%'))
            for value, candidate_unit in candidates:
                key = (round(value, 9), _unit(candidate_unit)[0])
                if key in seen:
                    continue
                seen.add(key)
                pool.append((value, candidate_unit))
                if len(pool) >= 3000:
                    return pool
    return pool


def _derived_match(value, text: str, unit: str, role: str, phrase: bool,
                   scoped, targets) -> bool:
    """这个数是不是由本句引用范围内的实测值四则算出来的（透明推导）。

    ``phrase`` 表示这句话在说「相差/占比/变化量」这类推导表述，此时不看量名。
    """

    if value is None or not (_derived_role_ok(role) or phrase):
        return False
    claim_key, claim_scale, claim_dimension = _unit(unit)
    dimensionless = claim_key in {'', '%', '倍'}
    for candidate_value, candidate_unit in _derived_values(scoped, targets):
        _, candidate_scale, candidate_dimension = _unit(candidate_unit)
        if dimensionless:
            if candidate_dimension != (0, 0, ''):
                continue
        elif claim_dimension != candidate_dimension:
            continue
        tolerance = _precision(text) * claim_scale + 1e-9
        if abs(value * claim_scale - candidate_value * candidate_scale) <= max(tolerance, 1e-6):
            return True
    return False


def _target_durations(records: dict[str, Record]) -> dict[str, float]:
    durations: dict[str, float] = {}
    for record in records.values():
        for quantity in record.quantities:
            if quantity.role == 'duration' and _unit(quantity.unit)[2] == (0, 1, ''):
                durations.setdefault(record.target, quantity.value * _unit(quantity.unit)[1])
    return durations


def _check_scope_line(state: Any, line: str, records: dict[str, Record],
                      notes: dict[str, list[str]] | None = None) -> str:
    """Correct scope claims; whole-object statistics cannot use segment evidence.

    这条规则拦的是措辞与引用范围不匹配，不是数值本身；凡是区间确实覆盖整对象、
    引用了 object_info/duration、或显式声明 _whole_object 的，都不加标注。
    """

    if not re.search(r'整段|全段|整个(?:声音|录音|音频|对象)|whole|entire', line, re.I):
        return line
    if re.search(r'中点|时刻|秒处|采样点', line) or _general_hint(line):
        return line
    cites = [f'E{x}' for x in _CITATION.findall(line)]
    chosen = [records[eid] for eid in cites if eid in records]
    if not chosen:
        return line
    statistic = bool(re.search(r'平均|均值|中位|最[高低大小]|标准差|mean|average|median|minimum|maximum|stdev', line, re.I))
    durations = _target_durations(records)
    whole_ranges = _whole_ranges(state, records)
    spans: list[tuple[float, float]] = []
    for record in chosen:
        if record.evidence.tool in {'object_info', 'duration'}:
            if statistic:
                continue
            return line
        arguments = getattr(record.evidence, 'arguments', None) or {}
        if arguments.get('_whole_object'):
            return line
        if record.interval is None:
            continue
        duration = durations.get(record.target)
        if duration is not None and abs(record.interval[0]) <= .001 and abs(record.interval[1] - duration) <= .001:
            return line
        required = whole_ranges.get(record.target)
        if required is not None and (record.interval[0] <= required[0] + .001
                                     and record.interval[1] >= required[1] - .001):
            return line
        spans.append(record.interval)
    if not spans:
        return line
    start = min(span[0] for span in spans)
    end = max(span[1] for span in spans)
    if statistic:
        _block(state, 'report.range_whole_partial',
               f'整段统计量引用的证据仅覆盖 {start:g}–{end:g} 秒；不能作为整段统计结果。')
    note_guard(state, 'repair', 'report.range_whole_partial')
    if notes is not None:
        notes.setdefault('scope', []).append(f'{start:g}–{end:g} 秒')
        return line
    return line.rstrip() + f'（该句引用的是 {start:g}–{end:g} 秒的局部分段测量，不是整段值）'

def mask_rejected_numbers(text: str, values, marker: str, *, state=None) -> str:
    """Mask whole rejected quantities, never substrings of decimals/IDs/labels.

    With evidence, reuse the guard's failing span so an equal, valid quantity
    elsewhere survives. This only locates rejected values; it grants no pass.
    """
    rejected = {re.sub(r'\s+', '', str(value)) for value in values}
    if state is not None:
        state = copy.copy(state)
        state.metrics = {}  # Locating a rejected value must not recount guards.
    records = _records(state) if state is not None else None
    out = []
    in_general = False
    for line in text.splitlines(keepends=True):
        if records is not None:
            if re.match(r'^\s*#{1,6}\s', line):
                in_general = bool(_GENERAL.search(line))
            if (_general(line, in_general, records)
                    or (_ADVICE.search(line) and not _SPECIFIC.search(line) and not _CITATION.search(line))):
                out.append(line)
                continue
            while True:
                try:
                    _, failure = _numeric_claim_sources(state, line, records)
                except ModelRetry:
                    break  # Other gates do not authorize numeric replacement.
                if not failure or len(failure) != 3:
                    break
                start, end = failure[2]
                failed = {re.sub(r'\s+', '', value) for value in rejected_values(failure[1])}
                if re.sub(r'\s+', '', line[start:end]) not in rejected and not failed.intersection(rejected):
                    break
                line = line[:start] + marker + line[end:]
        else:
            # Standalone rescue/reason text has no evidence: exact lexical tokens.
            targets = set(_OBJECT_IDENTIFIER.findall(line))
            spans = []
            for match in _NUMBER.finditer(line):
                if _identifier_number(line, match, targets):
                    continue
                unit = _UNIT.match(line, match.end())
                end = unit.end() if unit else match.end()
                if re.sub(r'\s+', '', line[match.start():end]) in rejected:
                    spans.append((match.start(), end))
            for start, end in reversed(spans):
                line = line[:start] + marker + line[end:]
        out.append(line)
    return ''.join(out)


def _check_numeric_line(state: Any, line: str, records: dict[str, Record], *, verbatim: bool = False) -> None:
    _, failure = _numeric_claim_sources(state, line, records, verbatim=verbatim)
    if failure:
        _block(state, failure[0], failure[1])


def _trajectory(records: list[Record], directional: bool, whole_ranges: dict[str, tuple[float, float]],
                direction: int = 0) -> bool:
    # Scalar slopes/endpoints/means do not prove flatness or a lexical accent
    # category. A signed, screened comparison can support only its net change.
    if not directional:
        return False

    def covers(record, required):
        return required is None or (record.interval is not None and
                                   record.interval[0] <= required[0] + .001 and
                                   record.interval[1] >= required[1] - .001)

    def endpoint_covered(record, role, required):
        acoustic = getattr(record.evidence, 'acoustic', {}) or {}
        if acoustic.get('kind') != 'pitch':
            return True
        event = next((e for e in acoustic.get('events', [])
                      if e['kind'] == 'f0_' + role.split('.')[-1] + '_time'), None)
        if event is None or event.get('time') is None:
            return False
        required = required or record.interval
        if required is None:
            return False
        # A frame represents half a sampling step on either side of its centre.
        step = next((c.get('frame_step', 0) for c in acoustic.get('checks', [])
                     if c['rule'] == 'pitch.screening'), 0)
        return abs(event['time'] - required[0 if role == 'pitch.start' else 1]) <= step / 2 + 1e-8

    groups = {}
    for record in records:
        if (record.target is not None and record.evidence.kind == 'measurement'
                and evidence_quality.can_interpret_pitch(record.evidence)):
            key = (record.target, evidence_quality.pitch_configuration(record.evidence))
            groups.setdefault(key, []).append(record)
    for (target, _config), group in groups.items():
        required = whole_ranges.get(target)
        for record in group:
            if covers(record, required) and any(q.role == 'pitch.slope' and q.value * direction > 0
                    and _unit(q.unit)[2] in {(1, -1, ''), (0, -1, '半音')} for q in record.quantities):
                return True
        endpoints = [(r.interval, q.role, q.value) for r in group if covers(r, required)
                     for q in r.quantities if q.role in {'pitch.start', 'pitch.end'}
                     and _unit(q.unit)[2] == (1, 0, '') and endpoint_covered(r, q.role, required)]
        if any(a[0] == b[0] and a[1] == 'pitch.start' and b[1] == 'pitch.end'
               and (b[2] - a[2]) * direction > 0 for a in endpoints for b in endpoints):
            return True
        segments = [(r, q.value) for r in group if r.evidence.tool == 'pitch_statistics' and r.interval
                    for q in r.quantities if q.role == 'pitch.mean' and _unit(q.unit)[2] == (1, 0, '')]
        if any(a.interval[1] <= b.interval[0] and a.interval[0] < a.interval[1] and b.interval[0] < b.interval[1]
               and (bv - av) * direction > 0
               and (required is None or (a.interval[0] <= required[0] + .001 and b.interval[1] >= required[1] - .001
                                        and b.interval[0] - a.interval[1] <= .001))
               for a, av in segments for b, bv in segments if a is not b):
            return True
    return False


def _prosody_problems(state, line, records):
    if not _PROSODY.search(line):
        return []
    cites = [f'E{x}' for x in _CITATION.findall(line)]
    targets = _line_targets(state, line, records)
    applicable = [records[eid] for eid in cites if eid in records and
                  (not targets or records[eid].target in targets)]
    problems = []
    for clause in re.split(r'[，,；;。！？!?]|(?:但|然而|不过)', line):
        # 去掉「- **不足**：」这类项目符号标签：标签里的「不足」不是否定词，
        # 否则「**不足**：未出现预期的尾音下降」会被当成诚实表述放过。
        clause = re.sub(r'^\s*[-*]?\s*\*\*[^*]{0,12}\*\*\s*[:：]?\s*', '', clause)
        for claim in _PROSODY.finditer(clause):
            if re.fullmatch(r'上升|下降|升高|降低|rising|falling', claim[0], re.I) and not re.search(
                    r'音高|基频|语调|音调|声调|调型|尾音|音尾|词尾|句尾|末尾|重音|\bF0\b|pitch|intonation|'
                    r'tone|accent', clause, re.I):
                continue
            if _INABILITY.search(clause[max(0, claim.start() - 45):claim.start()]):
                continue
            if re.fullmatch(_TONE_CATEGORY, claim[0]):
                problems.append(clause.strip())
                continue
            if (re.search(r'[ぁ-ゖァ-ヺ]|音素|音拍|元音|辅音|摩拉|モーラ', clause)
                    and evidence_boundaries.boundary_status(state)['status'] == 'none'):
                problems.append(clause.strip())
                continue
            direction = (1 if re.search(r'上升|升高|升调|rising', claim[0], re.I) else
                         -1 if re.search(r'下降|降低|降调|falling', claim[0], re.I) else 0)
            if not _trajectory(applicable, bool(direction), _whole_ranges(state, records), direction):
                problems.append(clause.strip())
    return list(dict.fromkeys(p for p in problems if p))


def _check_prosody_line(state: Any, line: str, records: dict[str, Record], notes=None) -> str:
    if not _prosody_problems(state, line, records):
        return line
    state.metrics['prosody_unverified'] = state.metrics.get('prosody_unverified', 0) + 1
    note_guard(state, 'repair', 'report.prosody_unverified')
    if notes is not None:
        notes.setdefault('prosody', []).append('1')
        return line
    return line.rstrip() + '（该音调/走向结论缺少同目标的走向证据，未核对）'

def _clean_residue(line: str) -> str:
    """去掉删除分句留下的空括号、重复标点与孤立粗体标记；只剩符号时返回空串。"""

    body = re.sub(r'[（(]\s*[）)]', '', line)
    body = re.sub(r'([，,；;])\s*(?=[，,；;。！？!?])', '', body)
    body = re.sub(r'(^[\s>*\-]*)[，,；;、]+', r'\1', body)
    body = re.sub(r'[ \t]+([。！？!?])', r'\1', body)
    if body.count('**') % 2:
        body = body.replace('**', '')
    return '' if re.fullmatch(r'[\s#>*\-_.。；;，,、:：()（）]*', body) else body.rstrip()


def sanitize_prosody_claims(state, text):
    """Delete pitch assertions that no screened trajectory supports.

    Headings and bold sub-headings go as a whole (a half sentence title is worse
    than no title); body lines lose only the offending clause, then residue such
    as ``（）``/``，，``/unbalanced ``**`` is cleaned so the user never sees edits.
    """

    if state is None or not hasattr(state, 'evidence'):
        return str(text), []
    records, out, removed, in_general = _records(state), [], [], False
    for line in str(text).splitlines():
        stripped = line.strip()
        # Markdown 标题与整行加粗的小标题都当标题：删半句标题比删整行更糟。
        heading = bool(re.match(r'^\s*#{1,6}\s', line)) or bool(
            re.fullmatch(r'\*\*[^*]+\*\*', stripped))
        if heading:
            in_general = bool(_GENERAL.search(line))
        issues = [] if _general(line, in_general, records) else _prosody_problems(state, line, records)
        if issues:
            removed.extend(issues)
            # 没有实测数字的行整行删掉：分句手术后剩下的半句（「标准日语中。然而，」）
            # 比直接不给更糟；数字本身在结构化结果里仍然保留。
            if heading or not re.search(r'\d', line):
                continue
            for clause in issues:
                line = line.replace(clause, '')
            line = _clean_residue(line)
            if not line.strip():
                continue
        if line.strip():
            out.append(line)
    body = '\n'.join(out)
    if removed:
        state.metrics['prosody_claims_removed'] = state.metrics.get('prosody_claims_removed', 0) + len(removed)
        if INTERPRETATION_NOTE not in body:
            body = body.rstrip() + '\n\n' + INTERPRETATION_NOTE
    return body, removed
    return body, removed


def sanitize_interpretation_claims(state, text):
    body, issues = interpretation_policy.sanitize_interpretation(state, text)
    if issues:
        for item in issues:
            note_guard(state, 'repair', item['rule'])
        if INTERPRETATION_NOTE not in body:
            body = body.rstrip() + '\n\n' + INTERPRETATION_NOTE
    return sanitize_prosody_claims(state, body)[0]


def _dim_operation(left: tuple[int, int, str], right: tuple[int, int, str], divide: bool = False) -> tuple[int, int, str]:
    if left[2] and right[2] and left[2] != right[2]:
        raise CalcUnavailable('calc输入单位不相容。')
    if divide:
        named = '' if left[2] == right[2] else (left[2] if not right[2] else f'{left[2]}/{right[2]}')
    else:
        named = f'{left[2]}*{right[2]}' if left[2] and right[2] else (left[2] or right[2])
    return (left[0] - right[0], left[1] - right[1], named) if divide else (left[0] + right[0], left[1] + right[1], named)


def _calculate(match: re.Match[str], records: dict[str, Record]) -> str:
    expression, requested_unit = match[1].strip(), match[2].strip()
    all_quantities = {q.ref: q for record in records.values() for q in record.quantities}
    used: dict[str, Quantity] = {}

    def replace(reference: re.Match[str]) -> str:
        ref = reference[0]
        if ref not in all_quantities:
            raise CalcUnavailable(f'calc输入{ref}不存在或未定义。')
        name = ref.replace('.', '_')
        used[name] = all_quantities[ref]
        return name

    if len(expression) > 300:
        raise CalcUnavailable('calc算式过长。')
    parsed_expression = _REF.sub(replace, expression)
    if not used:
        raise CalcUnavailable('calc必须引用真实证据量，不能只写常数。')
    try:
        tree = ast.parse(parsed_expression, mode='eval')
    except (SyntaxError, ValueError) as exc:
        raise CalcUnavailable('calc只接受证据量与四则算式。') from exc
    if len(list(ast.walk(tree))) > 80:
        raise CalcUnavailable('calc算式过长。')

    def visit(node: ast.AST) -> tuple[float, tuple[int, int, str]]:
        if isinstance(node, ast.Expression):
            return visit(node.body)
        if isinstance(node, ast.Name) and node.id in used:
            quantity = used[node.id]
            _, scale, dimension = _unit(quantity.unit)
            return quantity.value * scale, dimension
        if isinstance(node, ast.Constant) and type(node.value) in {int, float} and node.value in {0, 1, 2, 10, 100, 1000}:
            return float(node.value), (0, 0, '')
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            value, dimension = visit(node.operand)
            return (-value if isinstance(node.op, ast.USub) else value), dimension
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
            left, ld = visit(node.left)
            right, rd = visit(node.right)
            if isinstance(node.op, (ast.Add, ast.Sub)):
                if ld != rd:
                    raise CalcUnavailable('calc加减输入单位不相容。')
                return (left + right if isinstance(node.op, ast.Add) else left - right), ld
            if isinstance(node.op, ast.Div):
                if right == 0:
                    raise CalcUnavailable('calc不能除以零。')
                return left / right, _dim_operation(ld, rd, True)
            return left * right, _dim_operation(ld, rd)
        raise CalcUnavailable('calc只接受已有证据量、换算/平均用常数与四则运算。')

    value, dimension = visit(tree)
    unit, scale, expected_dimension = _unit(requested_unit)
    if expected_dimension != dimension or not math.isfinite(value):
        raise CalcUnavailable('calc输出单位与输入/算式维度不符。')
    # A dimensionless result cannot be assigned an invented, unknown unit.
    if requested_unit and not _UNIT.fullmatch(requested_unit):
        raise CalcUnavailable('calc输出单位不受支持。')
    value /= scale
    substituted = _REF.sub(lambda r: f'({all_quantities[r[0]].text} {all_quantities[r[0]].unit})', expression)
    citations = ' '.join(f'[{eid}]' for eid in dict.fromkeys(q.evidence for q in used.values()))
    return f'{substituted} = {value:.6g} {unit} {citations}'.strip()


#: 算不出来的推导用一个私有标记占位，再把整小句删掉：绝不把内部占位式给用户看。
_CALC_FAILED = '\ue002'
#: 「某一小句」= 两个标点之间的片段（分句、换行都算边界）。
_CLAUSE_ANY = r'[^，,；;。！？!?\n]*{marker}[^，,；;。！？!?\n]*'


def _drop_marker_clauses(state: Any, text: str, marker: str) -> str:
    """删掉带 ``marker`` 的那一小句；同一行的空档顺手收干净。"""

    if marker not in text:
        return text
    pattern = re.compile(_CLAUSE_ANY.format(marker=re.escape(marker)))
    lines = []
    for line in pattern.sub('', text).splitlines():
        lines.append(_clean_residue(line))
    return '\n'.join(lines)


def _settle_calc_templates(state: Any, text: str,
                           records: dict[str, Record] | None = None,
                           *, keep_citations: bool = False) -> str:
    """渲染证据四则运算模板；无法核对时删除相应小句，保留其他有效正文。"""

    if '{{calc:' not in text:
        return text
    if records is None:
        try:
            records = _records(state) if getattr(state, 'evidence', None) else {}
        except (AttributeError, TypeError):
            records = {}

    def render(match: re.Match[str]) -> str:
        try:
            rendered = _calculate(match, records)
        except CalcUnavailable:
            note_guard(state, 'repair', 'report.calc_unverified')
            return _CALC_FAILED
        if not keep_citations:
            rendered = re.sub(r'\s*\[E\d+\]', '', rendered)
        receipt = dict(equation=rendered, value=rendered.rsplit(' = ', 1)[-1])
        receipts = state.metrics.setdefault('calculation_receipts', [])
        if receipt not in receipts:
            receipts.append(receipt)
        note_guard(state, 'repair', 'report.derived_accepted')
        return rendered

    text = _CALC.sub(render, text)
    if '{{calc:' in text:
        # 语法残缺、_CALC 没匹配上的占位式：同样删句，不给用户看内部语法。
        note_guard(state, 'repair', 'report.calc_malformed')
        text = _drop_marker_clauses(state, text.replace('{{calc:', _CALC_FAILED), _CALC_FAILED)
    return _drop_marker_clauses(state, text, _CALC_FAILED)


#: 模型自己算的近似值：紧跟在程序渲染结果后面时是重复（数值纪律 ⑥）。
_APPROX_PAREN = re.compile(r'[（(]\s*约?\s*(-?\d+(?:\.\d+)?)\s*(%|Hz|dB|秒|毫秒|ms|Hz/s)?\s*[）)]')


def _drop_duplicate_approx(state: Any, text: str) -> str:
    """渲染出的结果后面那份「（约 74%）」是重复，去掉。"""

    def replace(match: re.Match[str]) -> str:
        value = abs(float(match[1]))
        # 只跟「程序刚渲染出的结果」比：前面紧邻的 `= 74.3806`。别用整句数字去比，
        # 否则 `(1.025063 秒)` 这种算式里的输入也会被当成重复删掉。
        before = text[max(0, match.start() - 40):match.start()]
        rendered = [abs(float(item)) for item in re.findall(r'=\s*(\d+(?:\.\d+)?)', before)]
        if any(abs(number - value) <= max(number * 0.015, 1e-6) for number in rendered):
            note_guard(state, 'repair', 'report.duplicate_value_collapsed')
            return ''
        return match[0]

    return _APPROX_PAREN.sub(replace, text)


def drop_unverified_calc(state: Any, text: str) -> str:
    """两条出口共用的收尾：把算不出来的推导整小句删掉，不把内部标记交给用户。"""

    text = _settle_calc_templates(state, text) if '{{calc:' in text else text
    text = _drop_marker_clauses(state, text, UNVERIFIED_CALC)
    return _drop_marker_clauses(state, text, _CALC_FAILED)


def _strip_unknown_citations(state: Any, text: str, records: dict[str, Record]) -> str:
    """清掉不存在的证据编号（``[E99]``），只计数不阻断：编号写错不等于数字是编的。"""

    def replace(match: re.Match[str]) -> str:
        if f'E{match.group(1)}' in records:
            return match[0]
        note_guard(state, 'repair', 'report.citation_unknown')
        return ''

    return _CITATION.sub(replace, text)


def _fill_citations(state: Any, text: str, records: dict[str, Record]) -> str:
    """为数值已匹配但未写引用的行补证据引用；未匹配数值留给验证阶段处理。"""

    out: list[str] = []
    in_general = False
    for line in text.splitlines():
        heading = re.match(r'^\s*#{1,6}\s', line)
        if heading:
            in_general = bool(_GENERAL.search(line))
        # 标题/一般知识/建议行不补引证：前者补了会改变标题文本，后两者的数字本来就不是
        # 本次测量。数字真编了，验证阶段照样拦。
        if (heading or _CITATION.search(line) or not _NUMBER.search(line) or in_general
                or _ADVICE.search(line) or _general(line, in_general, records)):
            out.append(line)
            continue
        sources, failure = _numeric_claim_sources(state, line, records)
        if failure or not sources:
            out.append(line)
            continue
        out.append(line + ' ' + ' '.join(f'[{eid}]' for eid in sources))
        note_guard(state, 'repair', 'report.citation_filled')
    return '\n'.join(out)

#: 标注类规则的正文末尾说明（同一轮里只说一次，不再逐行插括号）。
_NOTE_TEXT = {
    'prosody': '音调/走向的结论缺少同目标的走向证据，正文只按实测作保守表述。',
    'scope': '写着「整段」的句子引用的是局部分段测量，范围以句中区间为准。',
}

#: 正文里的算式：「(a 单位)/(b 单位)*100 = 结果」。只收拾算式、不碰结果——
#: 数值本身在核对阶段已经过一遍，这里纯粹是排版；命中数量记进 report.formula_collapsed。
_FORMULA = re.compile(
    r'(?:[（(]?\s*[-+]?\d+(?:\.\d+)?\s*(?:Hz|kHz|dB|Pa|%|秒|毫秒|ms|s)?\s*[)）]?\s*'
    r'(?:[*/×÷]\s*[（(]?\s*[-+]?\d+(?:\.\d+)?\s*(?:Hz|kHz|dB|Pa|%|秒|毫秒|ms|s)?\s*[)）]?\s*){1,6})'
    r'=\s*(?=[-+]?\d)')


def _annotation_note(notes: dict[str, list[str]]) -> str:
    """把本轮触发过的标注合成一句话（同类只说一次，但保留具体区间）。"""

    parts = []
    if notes.get('prosody'):
        parts.append(_NOTE_TEXT['prosody'])
    if notes.get('scope'):
        spans = list(dict.fromkeys(notes['scope']))[:3]
        parts.append('写着「整段」的句子引用的是 ' + '、'.join(spans) + ' 的局部分段测量，范围以句中区间为准。')
    return '（' + '；'.join(parts) + '）' if parts else ''


def _collapse_formulas(text: str, state: Any = None) -> str:
    """去掉正文里的算式，只留结果（``(a/b)*100 = 103.042 %`` → ``103.042 %``）。

    这里只负责排版，数值核对由报告判定阶段处理。
    """

    collapsed, count = _FORMULA.subn('', text)
    if count and state is not None:
        for _ in range(count):
            note_guard(state, 'repair', 'report.formula_collapsed')
    return collapsed


_DUPLICATE_VALUE = re.compile(
    r'[（(]\s*(?P<left>[-+]?\d+(?:\.\d+)?\s*(?:Hz/s|Hz|dB|Pa|%|秒|毫秒|ms|s))'
    r'\s*[）)]\s*[=＝]\s*(?P<right>[-+]?\d+(?:\.\d+)?'
    r'\s*(?:Hz/s|Hz|dB|Pa|%|秒|毫秒|ms|s)?)')


def _collapse_duplicate_values(state: Any, text: str) -> str:
    """把「（值 单位）= 同值」压成一个值；两边不相等时原样留着交给核对阶段。"""

    def replace(match: re.Match[str]) -> str:
        left, right = match.group('left').strip(), match.group('right').strip()
        left_value = float(re.match(r'[-+]?\d+(?:\.\d+)?', left).group(0))
        right_value = float(re.match(r'[-+]?\d+(?:\.\d+)?', right).group(0))
        if abs(left_value - right_value) > 1e-9:
            return match.group(0)
        note_guard(state, 'repair', 'report.duplicate_value_collapsed')
        return left

    return _DUPLICATE_VALUE.sub(replace, text)


def _settle_quantity_templates(state: Any, text: str,
                               records: dict[str, Record] | None = None) -> str:
    """仅渲染 quantity_refs 中登记的 q:E#.q# 模板。数值、单位和精度来自证据；未知、畸形或截断的引用记录缺口，不猜行号与序号映射。"""

    if _QUANTITY_TEMPLATE.search(text) is None:
        return text
    if records is None:
        try:
            records = _records(state) if getattr(state, 'evidence', None) else {}
        except (AttributeError, TypeError):
            # 证据形状不认识（恢复稿也可能只拿到 dict）：不渲染，一律标缺口更安全。
            records = {}

    def render(match: re.Match[str]) -> str:
        """``{{q:E2.q3}}`` → 证据里那个量的原样写法（含单位）。"""

        record = records.get(match[1])
        index = int(match[2]) - 1
        if record is None or index < 0 or index >= len(record.quantities):
            note_guard(state, 'repair', 'report.quantity_unverified')
            return UNVERIFIED_NUMBER
        quantity = record.quantities[index]
        note_guard(state, 'repair', 'report.quantity_rendered')
        # 逐字渲染证据的写法：单位、小数位都不经模型的手。
        return f'{quantity.text} {quantity.unit}'.strip()

    text = _QUANTITY.sub(render, text)

    def drop(_match: re.Match[str]) -> str:
        return UNVERIFIED_NUMBER

    text, unregistered = _QUANTITY_DELIMITED.subn(drop, text)
    text, truncated = _QUANTITY_TRUNCATED.subn(drop, text)
    if _QUANTITY_TEMPLATE.search(text):
        # 模板里夹了中文/换行之类的非常规写法：整段去掉。宁可多标一个缺口，
        # 也不能让 ``{{q:`` 这类内部语法漏到用户面前。
        text, blunt = _QUANTITY_ANY.subn(drop, text)
        truncated += blunt
    for _ in range(unregistered):
        note_guard(state, 'repair', 'report.quantity_unverified')
    for _ in range(truncated):
        note_guard(state, 'repair', 'report.quantity_malformed')
    return text


def _validate_text(state: Any, text: str, records: dict[str, Record], *, fill_citations: bool = True) -> str:
    """核对正文里的数值/推导。

    ``fill_citations=False``：只核对、不给数值补 ``[E1]`` 引证。本机最终交付发的是
    普通聊天正文，没有引证记号，补进去会改动用户看到的答复。
    """
    replacements: list[str] = []
    # 标注类规则（音调走向缺证据 / 「整段」引用局部区间）不再逐行插括号：真机一轮里同一句
    # 提示重复了 3 次，正文读起来像没写完。这里只记「出现过什么」，最后在正文末尾说一句。
    notes: dict[str, list[str]] = {}
    # 缺口计数按「这次调用新增了几个标记」算：上一轮已经标过的标记不重复记账。
    gaps_at_entry = _gap_markers(text)

    if '\ue000' in text or '\ue001' in text:
        # 模型不可能自己产出这两个内部标记；清掉继续，不拿它中断报告。
        text = text.replace('\ue000', '').replace('\ue001', '')
        note_guard(state, 'repair', 'report.internal_marker')
    text = _strip_unknown_citations(state, text, records)
    text = _strip_unknown_citations(state, text, records)
    if fill_citations:
        text = _fill_citations(state, text, records)

    # 数值模板只有一份实现：``{{q:E#.q#}}`` 逐字渲染，未登记/写错/截断的标缺口。
    text = _settle_quantity_templates(state, text, records)

    def calculate(match: re.Match[str]) -> str:
        line_start = text.rfind('\n', 0, match.start()) + 1
        line_end = text.find('\n', match.end())
        source_line = text[line_start:line_end if line_end >= 0 else len(text)]
        explicit = _explicit_targets(state, source_line, records)
        targets = explicit or _line_targets(state, source_line, records)
        referenced_targets = {records[eid].target for eid, _ in _REF.findall(match[1]) if eid in records}
        comparison = re.search(r'差值|相差|差异|比值|比例|比率|倍|比较|对比|difference|ratio|compar',
                               source_line + '\n' + str(getattr(state, 'goal', '')), re.I)
        if not explicit and comparison and len(referenced_targets) > 1:
            # Only a real, goal-authorized comparison may widen the implicit
            # selected-object scope. A lone E2 value is not a comparison.
            targets = targets | _authorized_targets(state, records)
        for reference in _REF.findall(match[1]):
            record = records.get(reference[0])
            if record and targets and record.target not in targets:
                _block(state, 'report.calc_other_target',
                       'calc引用了其他对象的量；须注明实际对象，不能冒充目标录音。')
        claim_prefix = text[line_start:match.start()]
        claim_role = _role(claim_prefix)
        category = claim_role.split('.')[0]
        if category in {'pitch', 'formant', 'intensity', 'vot'}:
            quantities = {q.ref: q for record in records.values() for q in record.quantities}
            inputs = [quantities.get(f'{eid}.q{index}') for eid, index in _REF.findall(match[1])]
            # VOT 占录音时长的百分比需要两个不同量名的时间量；分母不是冒充 VOT。
            # 对象检查仍在上面，四则与输出量纲仍由 _calculate 核对。
            vot_percentage = (category == 'vot' and match[2].strip() == '%'
                              and bool(re.search(r'占比|百分比|比例', claim_prefix))
                              and any(q and q.role.split('.')[0] == 'vot' for q in inputs))
            for reference in _REF.findall(match[1]):
                q = quantities.get(f'{reference[0]}.q{reference[1]}')
                if vot_percentage and q and q.role == 'duration':
                    continue
                if q and not q.role.startswith('range.') and q.role.split('.')[0] != category:
                    _block(state, 'report.calc_quantity_mismatch',
                           'calc输入量名与声称的测量不相符。')
        try:
            rendered = _calculate(match, records)
        except CalcUnavailable:
            # 算不出来只说明这一处推导没核对，不说明报告在编造测量：标注即可。
            note_guard(state, 'repair', 'report.calc_unverified')
            return UNVERIFIED_CALC
        replacements.append(rendered)
        # Internal token contains no numbers and is never accepted from model text.
        return '\ue000' + chr(0xE100 + len(replacements) - 1) + '\ue001'

    masked = _CALC.sub(calculate, text)
    if '{{calc:' in masked:
        # 语法残缺、_CALC 没匹配上的占位式：同样只标注，不阻断。
        note_guard(state, 'repair', 'report.calc_malformed')
        masked = re.sub(r'\{\{calc:[^{}]*\}\}', UNVERIFIED_CALC, masked)
        if '{{calc:' in masked:
            masked = masked.replace('{{calc:', UNVERIFIED_CALC)
    in_general = False
    in_fence = False
    for line in masked.splitlines():
        if re.match(r'^\s*#{1,6}\s', line):
            in_general = bool(_GENERAL.search(line))
        if line.strip().startswith('```'):
            in_fence = not in_fence
            continue
        if _general(line, in_general, records):
            continue
        # Suggested future parameters/counts are not claims of measurements.
        if _ADVICE.search(line) and not _SPECIFIC.search(line) and not _CITATION.search(line):
            checked = _check_prosody_line(state, line, records, notes)
            if checked != line:
                masked = masked.replace(line, checked)
            continue
            continue
        # Calculation citations remain visible to prosody validation, while its
        # deterministic numbers bypass a second check against raw measurements.
        evidence_line = line
        for index, rendered in enumerate(replacements):
            token = '\ue000' + chr(0xE100 + index) + '\ue001'
            evidence_line = evidence_line.replace(token, ' '.join(m[0] for m in _CITATION.finditer(rendered)))
        _check_numeric_line(state, evidence_line, records, verbatim=in_fence or _verbatim_line(line))
        checked = _check_prosody_line(state, evidence_line, records, notes)
        checked = _check_scope_line(state, checked, records, notes)
        if checked != evidence_line:
            # 只把追加的标注接回被遮蔽的那一行：该行的 calc 占位式已被引证替换，
            # 整行替换会丢掉程序计算的结果。
            masked = masked.replace(line, line.rstrip() + checked[len(evidence_line):])
    # 先收掉模型自己写的算式（此时程序渲染的 calc 结果还是占位记号，不会被误伤），
    # 再把程序渲染的结果放回去：透明推导的 `算式 = 结果` 是有意展示的，不能一起收拾掉。
    masked = _collapse_formulas(masked, state)
    for index, rendered in enumerate(replacements):
        masked = masked.replace('\ue000' + chr(0xE100 + index) + '\ue001', rendered)
    if notes:
        sentence = _annotation_note(notes)
        if sentence not in masked:
            masked = masked.rstrip() + '\n\n' + sentence
    # 这次调用新增的数值缺口（模板没登记、calc 算不出、换算维度不符…）：正文照常交付，
    # 但「全部核对通过」这句话从现在起不能再说（见 :func:`_note_gap`）。
    _note_gap(state, _gap_markers(masked) - gaps_at_entry)
    masked = sanitize_interpretation_claims(state, _collapse_duplicate_values(state, masked))
    return masked


#: 「没有音频输入所以测不了」不成立：搜索与测量在 Praat 对象上进行，
#: 不需要把音频发给模型。已经取得测量行时更不能据此收尾。
_NO_INPUT_CLAIM = re.compile(
    r'未提供音频|没有收到音频|未收到任何音频|没有音频输入|未提供任何音频|无音频输入|'
    r'audio_input\s*(?:为|=|是|is)\s*`?false|无法执行(?:任何)?测量|无法进行(?:任何)?测量|'
    r'无法完成(?:任何)?测量', re.I)

#: 目标上下文里关于「用户提到的对象不存在」的提示（cloud_workflow.target_context 写的）。
_MISSING_OBJECT_NOTE = re.compile(r'^#\s*用户提到但这些对象不在当前 Praat 对象列表里：(.+?)；', re.M)
#: 回答里必须出现的「不存在」判定词（只说了一遍名字不算交代）。
_MISSING_DENIAL = re.compile(r'不存在|找不到|未找到|没有找到|不在(?:当前|对象)|没有这个对象|'
                             r'未加载|没有该对象|does not exist|not found', re.I)


def validate_report(state: Any, report: Any, *, fill_citations: bool = True) -> Any:
    """Return a report with deterministic calculations, or request SDK retry.

    This works with AnalysisReport without importing cloud_agent (and creating
    a cycle). Coverage explanations share the report boundary; user delivery
    labels are identifiers and are not reinterpreted as measurement claims.
    """
    records = _records(state)
    analysis_text = str(getattr(report, 'analysis', '') or '')
    measured = [eid for eid, record in records.items() if record.quantities]
    context_text = str(getattr(state, 'context_text', '') or '')
    has_sound = any(row.class_name in SOUND_CLASSES for row in parse_object_context(context_text))
    # 已经有测量、或者对象列表里明明有声音对象时，不能用「没有音频输入」当不做测量的理由。
    if (measured or has_sound) and _NO_INPUT_CLAIM.search(analysis_text) and not _CITATION.search(analysis_text):
        reason = ('本轮已经有测量证据（' + '、'.join(f'[{eid}]' for eid in measured) + '）；'
                  if measured else '对象列表里有可测量的声音对象；')
        _block(state, 'report.input_claim_conflict',
               reason + '测量由 Praat 对对象执行，不需要把音频发给模型，'
               '所以「没有音频输入」或「无法测量」在这里不成立。')
    # 用户点名的对象不存在时，回答必须明确说明，不能用「没有音频输入」搪塞。
    for match in _MISSING_OBJECT_NOTE.finditer(context_text):
        names = [piece.strip() for piece in re.split(r'[、,，]', match[1]) if piece.strip()]
        if not names:
            continue
        named = any(name.strip('「」') in analysis_text for name in names)
        denied = bool(_MISSING_DENIAL.search(analysis_text))
        if not (named and denied):
            _block(state, 'report.missing_object_unmentioned',
                   '用户提到的对象（' + '、'.join(names) + '）不在当前 Praat 对象列表里；'
                   '回答里必须点名说明它不存在（找不到/未找到/不在列表），'
                   '不能改用别的对象，也不能用「没有音频输入」代替。')
    analysis = _validate_text(state, report.analysis, records, fill_citations=fill_citations)
    coverage = []
    for entry in report.coverage:
        explanation = _validate_text(state, entry.explanation, records, fill_citations=fill_citations)
        coverage.append(entry.model_copy(update={'explanation': explanation})
                        if hasattr(entry, 'model_copy') else entry)
    if hasattr(report, 'model_copy'):
        return report.model_copy(update={'analysis': analysis, 'coverage': coverage})
    report.analysis = analysis
    return report


@dataclass
class _TextDraft:
    """把一段普通正文塞进 ``validate_report`` 用的最小报告对象（无引证、无覆盖项）。"""

    analysis: str
    coverage: list[Any] = field(default_factory=list)

def _guard_note(text: str, detail: str, values=()) -> str:
    """被守卫拒绝、又只能靠遮蔽修复时，在末尾补一句**人话**（不带规则名与内部语法）。

    旧文案会把守卫的原始理由整段贴给用户（「数值“96 Hz”未匹配同对象/范围/量名的测量。
    逐行引证；推导用calc占位式…」），既不解决问题也读不懂。原因只留一句说明。
    """

    note = UNVERIFIED_NOTE
    if note in text:
        return text
    return text.rstrip() + '\n\n' + note


def guard_delivered_text(state: Any, text: str, *, values=(), verify: bool = True,
                         fill_citations: bool = False) -> tuple[str, str]:
    """两条交付路径共用的守卫入口：云端恢复稿与本机最终交付都经这一个函数。

    ``verify=True`` 时委托 :func:`report_audit.audit_report`——遮蔽循环只有一份实现：
    数值没对上就就地遮蔽并继续核对，只有结构性错误才放弃（标注，不假装通过）。

    返回 ``(可交付文本, 状态)``：``verified``（全部对上）、``rejected_masked``
    （有数值被遮蔽/标注/缺口）、``masked_only``（调用方已知道要遮哪些值）。
    被遮蔽不等于通过核对，状态必须如实往上带：正文里有 :data:`GAP_MARKERS` 标记时
    一律 ``rejected_masked``，不说 ``verified``。

    两条出口都要处理数值模板：``verify=False``（云端恢复稿）没有重跑诊断的机会，
    但 ``{{q:E3.r1}}`` 这类未登记模板同样不能漏给用户——已登记的照常渲染，其余标缺口。
    """

    text = str(text or '')
    pending = list(dict.fromkeys(value for value in values if value))
    if not verify:
        gaps = _gap_markers(text)
        text = _collapse_duplicate_values(state, _settle_quantity_templates(state, text))
        # calc 也要在这里渲染：这条路以前一个占位式都不处理，用户看到的是
        # `所选区间占时长的 [推导未核对]（约 74%）`。
        text = _settle_calc_templates(state, text)
        text = _drop_duplicate_approx(state, text)
        _note_gap(state, _gap_markers(text) - gaps)
        if pending:
            text = mask_rejected_numbers(text, pending, UNVERIFIED_NUMBER, state=state)
        text = sanitize_interpretation_claims(state, text)
        if pending or _gap_markers(text) or INTERPRETATION_NOTE in text:
            return text, 'rejected_masked'
        return text, 'masked_only'
    from .report_audit import audit_report

    probe = copy.copy(state)
    probe.metrics = {}
    try:
        audited = audit_report(probe, _TextDraft(analysis=text), fill_citations=fill_citations)
    except ModelRetry as error:
        # 与数值无关的规则（对象、声称等）遮蔽不了：留一句人话，不伪装成通过。
        # 但解释边界仍要过一遍——这条早退以前把整段原文直接交出去，连带
        # 「发声位置靠后、气息支撑不足」这类生理归因一起漏给了用户。
        _merge_counters(state, probe.metrics)
        safe = _settle_calc_templates(state, _collapse_duplicate_values(state, text))
        safe = _drop_duplicate_approx(state, safe)
        if getattr(state, 'evidence', None):
            safe = sanitize_interpretation_claims(state, safe)
        return _guard_note(safe, str(error), pending), 'rejected_masked'
    _merge_counters(state, probe.metrics)
    verified = bool((probe.metrics.get('audit') or {}).get('verified', True))
    # 缺口是「这个数没核上」的事实，不是核对通过的证明：``audit`` 的 verified 只看
    # 「有没有对不上的数」，模板缺口走的是另一条路，这里必须自己把它算进来。
    gaps = int(probe.metrics.get('report_gap') or 0)
    body = str(getattr(audited, 'analysis', text) or '')
    body = _collapse_duplicate_values(state, body)
    # audit 不渲染 calc：占位式（以及它标成 [推导未核对] 的那种）在这里一并处理——
    # 能算的按证据算出结果，算不出来的整小句删掉，内部标记不给用户看。
    unverified_calc = int((state.metrics.get('guard_repairs') or {}).get('report.calc_unverified') or 0)
    body = _settle_calc_templates(state, body)
    body = _drop_marker_clauses(state, body, UNVERIFIED_CALC)
    body = _drop_duplicate_approx(state, body)
    if int((state.metrics.get('guard_repairs') or {}).get('report.calc_unverified') or 0) > unverified_calc:
        verified = False
    # 「基频偏低说明发声位置靠后、气息支撑不足」原样交给了用户，而云端恢复稿那条路
    # 会中和它。两条出口必须用同一个 sanitize（云端恢复稿见上面 verify=False 分支）。
    if getattr(state, 'evidence', None):
        body = sanitize_interpretation_claims(state, body)
        if INTERPRETATION_NOTE in body:
            verified = False
    if verified and not gaps:
        return body, 'verified'
    if not _gap_markers(body) and INTERPRETATION_NOTE not in body:
        body = _guard_note(body, '', pending)
    return body, 'rejected_masked'


def _merge_counters(state: Any, metrics: dict) -> None:
    """把探测副本里的守卫计数并回主 state（本机交付也要进遥测）。"""

    target = getattr(state, 'metrics', None)
    if not isinstance(target, dict) or not isinstance(metrics, dict):
        return
    for bucket in ('guard_blocks', 'guard_repairs'):
        for rule, count in (metrics.get(bucket) or {}).items():
            slot = target.setdefault(bucket, {})
            slot[rule] = slot.get(rule, 0) + count
    for rule in ('report_derived_accepted', 'report_general_value_assumed', 'report_gap'):
        if metrics.get(rule):
            target[rule] = target.get(rule, 0) + metrics[rule]
    if metrics.get('audit'):
        target['audit'] = dict(metrics['audit'])
    for receipt in metrics.get('calculation_receipts', []):
        items = target.setdefault('calculation_receipts', [])
        if receipt not in items:
            items.append(receipt)


def _general_claim(clause: str) -> bool:
    """这句话是不是在讲典型范围（一般知识），而不是声称本次测到的值。

    引证可能落在前一句末尾（``平均基频 999 Hz。[E1] 参考材料：…通常 85–180 Hz``），
    所以判断时先剥掉句首的引证，再看有没有「通常/常见/文献…」这类措辞。
    """

    text = re.sub(r'^\s*(?:\[E\d+\]\s*)+', '', clause)
    if _CITATION.search(text):
        return False
    return _general_hint(text)
