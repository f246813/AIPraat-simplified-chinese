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
from dataclasses import dataclass
from typing import Any

from pydantic_ai import ModelRetry

from .escape_policy import note_guard
from .tools import SOUND_CLASSES, parse_object_context


#: 算不出来的推导式在正文里换成这个：不把内部语法漏给用户，也不因为一句推导丢掉整份报告。
UNVERIFIED_CALC = '[推导未核对]'


class CalcUnavailable(Exception):
    """推导式的语法、量纲或引用无法核对；调用方标注该推导为未核对，不因此阻断整份报告。"""


def _block(state: Any, rule: str, message: str) -> None:
    """守卫里唯一允许中断报告的出口：只留客观、漏过去会真的错的规则。"""

    note_guard(state, 'block', rule)
    raise ModelRetry(message)


_N = (r"[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d*)?|\.\d+)"
      r"(?:[eE][-+]?\d+)?")
_NUMBER = re.compile(r"(?<![A-Za-z0-9_.])" + _N + r"(?![A-Za-z0-9_.])")
_UNIT = re.compile(
    r"\s*(kHz|Hz(?:\s*/\s*(?:s|秒))?|半音\s*/\s*(?:s|秒)|"
    r"milliseconds?|seconds?|msec|sec|ms|s|毫秒|秒|dB|Pa|%|％|倍|半音|八度|"
    r"赫兹|帕|分贝)(?![A-Za-z])",
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
#: 是同一个范围，只是写法不同；不认它就会把一个**正确**的范围当成孤立数字
#: （2026-10-07 实测：报告因此连吃两次拒绝，正文里留下 ``[[未通过核对的数值], 0.66158]``）。
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
        start_role, end_role = ((category + '.min', category + '.max') if category
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
_GENERAL = re.compile(r"一般知识|一般原理|概念说明|概念解释|通用知识|示例[（(]非本录音[）)]|general knowledge", re.I)
_SPECIFIC = re.compile(r"本(?:段|次|录音|对象|材料)|这(?:段|个|次|份)|该(?:段|录音|对象)|你(?:的|这)|当前|实测|测得|测量结果")
_ADVICE = re.compile(r'^\s*(?:[-*]\s*)?(?:建议|下一步|请|可以尝试|可尝试|练习时|参数设置)')
_PROSODY = re.compile(
    r"平板型|头高型|中高型|尾高型|平板调|升调|降调|上升|下降|升高|降低|"
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
    if key in {'', '%', '倍'}:
        # Percent expressions explicitly multiply by 100, as ordinary formulas do.
        return key, 1.0, (0, 0, '')
    return raw.strip(), 1.0, (0, 0, key)


def _precision(text: str) -> float:
    mantissa, _, exponent = text.lower().partition('e')
    decimals = len(mantissa.split('.')[1]) if '.' in mantissa else 0
    return .5 * 10 ** ((int(exponent) if exponent else 0) - decimals)


#: 全角数字/句点/逗号：模型偶尔会写 ``８１．５５８``，以前整段核对直接跳过它。
_FULLWIDTH = str.maketrans('０１２３４５６７８９．，％', '0123456789.,%')

_CN_DIGITS = {'零': 0, '〇': 0, '一': 1, '壹': 1, '二': 2, '两': 2, '贰': 2, '三': 3, '叁': 3,
              '四': 4, '肆': 4, '五': 5, '伍': 5, '六': 6, '陆': 6, '七': 7, '柒': 7,
              '八': 8, '捌': 8, '九': 9, '玖': 9}
_CN_UNITS = {'十': 10, '拾': 10, '百': 100, '佰': 100, '千': 1000, '仟': 1000}
_CN_NUMBER = re.compile(r'[零〇一壹二两贰三叁四肆五伍六陆七柒八捌九玖十拾百佰千仟点]{2,}')


def _chinese_number(text: str) -> float | None:
    """``八十一`` → 81、``一点五`` → 1.5、``二百五十`` → 250；解析不了返回 ``None``。

    中文数字以前完全不核对（2026-10-07 实测：``平均基频约八十一点六赫兹`` 直接通过），
    编造的数字只要写成中文就能躲过守卫。这里只处理到千位和一位小数——报告里够用，
    超出的形式按解析不了处理（仍会被当成可疑数值拦下）。
    """

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
    """一个数值文本的可能读法：``81,558`` 既可能是千分位也可能是小数逗号。

    两种读法都试：只有能对上真实测量才会通过，所以放宽读法不会放过编造的数字
    （2026-10-07 实测：``平均基频 81,558 Hz`` 被拆成 81 + 558，整行判成未核对）。
    """

    if ',' not in raw:
        return [raw]
    return [raw.replace(',', ''), raw.replace(',', '.')]


def _role(prefix: str, tool: str = '') -> str:
    """Recognized quantity labels, kept separate from the numeric matcher."""
    pieces = re.split(r'[，,；;：:=＝]', prefix)
    nearby = next((part for part in reversed(pieces) if part.strip()), '')[-60:]
    full = prefix[-160:]
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
    elif re.search(r'中位|median', nearby, re.I):
        metric = 'median'
    elif re.search(r'标准差|standard deviation|\bsd\b', nearby, re.I):
        metric = 'sd'
    elif re.search(r'斜率|slope', nearby, re.I):
        metric = 'slope_absolute' if '绝对' in nearby or '倍频跳变' in nearby else 'slope'
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
    for row_number, row in enumerate(evidence.rows, 1):
        range_parts = _range_parts(row, evidence.tool)
        for match in _NUMBER.finditer(row):
            if re.match(r'\s*共振峰', row[match.end():]) or re.search(r'第\s*$', row[:match.start()]):
                continue
            unit_match = _UNIT.match(row, match.end())
            unit = unit_match[1] if unit_match else ''
            role = _role(row[:match.start()], evidence.tool)
            if role == 'pitch.slope' and evidence.tool == 'measure':
                parameters = evidence.arguments.get('parameters') or []
                if isinstance(parameters, str):
                    parameters = parameters.split(',')
                if set(parameters) & {'pitch_slope', 'pitch_slope_octave_free'}:
                    role = 'pitch.slope_absolute'
            if match.span() in range_parts:
                role, unit = range_parts[match.span()]
            elif not unit and not re.search(r'[=:＝]\s*$', row[:match.start()]):
                continue
            # Undefined rows contain range/time parameters, never a measured value.
            if re.search(r'无法计算|undefined|没有可用的数据', row, re.I) and not role.startswith('range.'):
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
    refs = {eid: [f'r{q.row}:{q.role}' for q in record.quantities]
            for eid, record in records.items() if record.quantities}
    if refs:
        data['quantity_refs'] = refs
    return data


def report_guard_instructions(state: Any) -> str:
    if not state.evidence:
        return '\n没有测量时不填录音数值或下音调结论；一般知识明确标注，缺证据就说明。'
    return ('\n量化陈述逐行引[E编号]，须同对象、范围、量名、单位；一般知识明确标注。'
            'quantity_refs第N项即E编号.qN。推导用{{calc:E1.q1/E2.q1*100|%}}，仅真实输入和四则，程序写算式结果。'
            '具体录音音调结论须同目标且有定义值的走向证据，平均值不足。'
            '逐字引用、单位换算、选区范围、一般知识的写法与例子见 skill=notation。')


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
    tolerance = _precision(text) * scale + _precision(quantity.text) * source_scale + 1e-10
    return abs(value * scale - quantity.value * source_scale) <= tolerance


def _roles_match(claim: str, source: str) -> bool:
    if claim.startswith('range.'):
        return claim == source
    if claim == source:
        return True
    if claim in {'mean', 'min', 'max', 'median', 'sd', 'start', 'end', 'slope', 'slope_absolute'}:
        return source.endswith('.' + claim)
    # A generic pitch summary may omit the word "mean", but may not become F1.
    if claim == 'pitch.value':
        return source in {'pitch.value', 'pitch.mean'}
    if claim.startswith('formant.') and claim.endswith('.value'):
        return source in {claim, claim.replace('.value', '.mean')}
    return False


#: 本身就算量名的角色（不在这个集合里、又不带类别的，只能当自由文本标签）。
_NAMED_ROLES = {'mean', 'min', 'max', 'median', 'sd', 'start', 'end',
                'slope', 'slope_absolute', 'duration', 'range.start', 'range.end'}


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


def _line_targets(state: Any, text: str, records: dict[str, Record]) -> set[str]:
    explicit = set(_OBJECT_IDENTIFIER.findall(text))
    known = {str(row.id) for row in parse_object_context(state.context_text)}
    known.update(record.target for record in records.values() if record.target is not None)
    if explicit - known:
        _block(state, 'report.object_unknown', '报告引用了快照和证据中不存在的对象编号。')
    for record in records.values():
        if record.target and len(record.target_name) >= 2 and record.target_name in text:
            explicit.add(record.target)
    if explicit:
        return explicit
    cited = {records[f'E{eid}'].target for eid in _CITATION.findall(text)
             if f'E{eid}' in records and records[f'E{eid}'].target is not None}
    if cited and not any(row.selected and row.class_name in SOUND_CLASSES
                         for row in parse_object_context(state.context_text)):
        return cited  # Evidence retains its Sound even when Praat selected a derived object.
    return {str(row.id) for row in parse_object_context(state.context_text) if row.selected}


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
    # including its punctuation. Identify the marker from the line instead.
    ordinal = re.match(r'^\s*(?:#{1,6}\s*)?(\d+)[.)、](?=\s)', cleaned)
    if ordinal and match.start() == ordinal.start(1) and match.end() <= ordinal.end():
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
    for clause_match in re.finditer(r'[^；;。\n]+', line):
        clause = clause_match[0]
        if _marked_general(clause):
            continue
        clause_cites = [f'E{x}' for x in _CITATION.findall(clause)] or cites
        # 漏写引证时按「同对象的所有证据」核对，命中的证据由调用方补进正文：引证是守卫
        # 自己的记账方式，不该要求模型逐行照抄格式，更不该因为漏写就判成编造数据。
        available = [records[eid] for eid in clause_cites] if clause_cites else list(records.values())
        if targets:
            available = [r for r in available if r.target in targets]
        if re.search(r'整段|全段|整个(?:声音|录音|音频|对象)|whole|entire', clause, re.I) and not re.search(r'中点|时刻|秒处|采样点', clause):
            whole_ranges = _whole_ranges(state, records)
            def full_scope(record):
                if record.evidence.tool in {'duration', 'object_info'}:
                    return True
                if record.evidence.tool in {'pitch', 'intensity', 'formant_frequency', 'formant_bandwidth'}:
                    return False
                required = whole_ranges.get(record.target)
                if required is not None:
                    return record.interval is not None and abs(record.interval[0] - required[0]) <= .001 and abs(record.interval[1] - required[1]) <= .001
                args = record.evidence.arguments
                return args.get('_whole_object') and not any(args.get(key) is not None for key in ('from', 'to', 'start', 'end'))
            available = [record for record in available if full_scope(record)]
            if not available and clause_cites and re.search(r'平均|均值|统计|数值|mean|average', clause, re.I):
                return sources, ('report.range_whole_partial', '整段量化陈述不能引用局部分段值；请保留真实范围或取得整段测量。')
        ranges = list(_RANGE.finditer(clause))
        range_parts = _range_parts(clause)
        if len(ranges) == 1:
            match = ranges[0]
            start = float(match['a']) * _unit(match['ua'] or match['ub'])[1]
            end = float(match['b']) * _unit(match['ub'])[1]
            available = [r for r in available if r.interval is not None and
                         abs(r.interval[0] - start) <= _precision(match['a']) * _unit(match['ua'] or match['ub'])[1] + .0005 and
                         abs(r.interval[1] - end) <= _precision(match['b']) * _unit(match['ub'])[1] + .0005]
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
            if not hit and verbatim and unit == '':
                # 逐字引用可以不带单位，但必须唯一对上一条证据，否则仍然按未核对处理。
                candidates = {(record.id, q.ref) for record in available for q in record.quantities
                              if _same_numeric_value(value, match[0], q)}
                if len({ref for _, ref in candidates}) == 1:
                    hit = [record for record in available
                           if any((record.id, q.ref) in candidates for q in record.quantities)]
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
                if re.sub(r'\s+', '', line[start:end]) not in rejected:
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


def _trajectory(records: list[Record], directional: bool, whole_ranges: dict[str, tuple[float, float]]) -> bool:
    def covers(record: Record, required: tuple[float, float] | None) -> bool:
        return required is None or (record.interval is not None and
                                   record.interval[0] <= required[0] + .001 and
                                   record.interval[1] >= required[1] - .001)

    by_target: dict[str, list[Record]] = {}
    for record in records:
        if record.target is not None and record.evidence.kind == 'measurement':
            by_target.setdefault(record.target, []).append(record)
    for target, group in by_target.items():
        required = whole_ranges.get(target)
        for record in group:
            slopes = [q for q in record.quantities if q.role in {'pitch.slope', 'pitch.slope_absolute'}
                      and _unit(q.unit)[2] in {(1, -1, ''), (0, -1, '半音')}]
            if covers(record, required) and any(q.role == 'pitch.slope' or not directional for q in slopes):
                return True
        endpoints = [(record.interval, q.role) for record in group for q in record.quantities
                     if covers(record, required) and q.role in {'pitch.start', 'pitch.end'}
                     and _unit(q.unit)[2] == (1, 0, '')]
        if any(start_range == end_range and start_role == 'pitch.start' and end_role == 'pitch.end'
               for start_range, start_role in endpoints for end_range, end_role in endpoints):
            return True
        segments = [record for record in group if record.evidence.tool == 'pitch_statistics'
                    and record.interval is not None and any(q.role == 'pitch.mean' and _unit(q.unit)[2] == (1, 0, '')
                                                            for q in record.quantities)]
        if any(a.interval[1] <= b.interval[0] and a.interval[0] < a.interval[1] and b.interval[0] < b.interval[1]
               and (required is None or (a.interval[0] <= required[0] + .001 and b.interval[1] >= required[1] - .001
                                        and b.interval[0] - a.interval[1] <= .001))
               for a in segments for b in segments if a is not b):
            return True
    return False


def _check_prosody_line(state: Any, line: str, records: dict[str, Record]) -> str:
    """音调/走向结论缺走向证据时**标注**这一行，不再拦下整份报告。

    这条规则拦的是「判断」而不是「数值」，2026-10-06 的文档就把它列为「本来该退回提示词、
    只是历史事故先留着观察」；现在有了真实触发率：用户历史 28 轮 + 真机 30 轮里
    ``report.prosody_untraced`` **一次都没触发**，而同期的数值规则触发了 7 次。
    按那条文档自己写的判据（触发率低 → 降为可见提醒），这里改成在结论旁边写明
    「缺少同目标的走向证据」，规则本身留在 skill=prosody 与报告指令里。
    """

    if not _PROSODY.search(line):
        return line
    cites = [f'E{x}' for x in _CITATION.findall(line)]
    targets = _line_targets(state, line, records)
    whole_ranges = _whole_ranges(state, records)
    annotated = False
    for clause in re.split(r'[，,；;。！？!?]|(?:但|然而|不过)', line):
        claims = list(_PROSODY.finditer(clause))
        if not claims:
            continue
        for claim in claims:
            if re.fullmatch(r'上升|下降|升高|降低|rising|falling', claim[0], re.I) and not re.search(
                    r'音高|基频|语调|音调|声调|\bF0\b|pitch|intonation|tone', clause, re.I):
                continue
            if _INABILITY.search(clause[max(0, claim.start() - 45):claim.start()]):
                continue
            # Explicitly attributed conceptual explanations are handled by caller.
            applicable = [records[eid] for eid in cites if eid in records and
                          (not targets or records[eid].target in targets)]
            directional = bool(re.search(r'上升|下降|升高|降低|升调|降调|rising|falling', claim[0], re.I))
            if not _trajectory(applicable, directional, whole_ranges):
                annotated = True
    if not annotated:
        return line
    state.metrics['prosody_unverified'] = state.metrics.get('prosody_unverified', 0) + 1
    note_guard(state, 'repair', 'report.prosody_unverified')
    return line.rstrip() + '（该音调/走向结论缺少同目标的走向证据，未核对）'


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


def _validate_text(state: Any, text: str, records: dict[str, Record]) -> str:
    replacements: list[str] = []

    if '\ue000' in text or '\ue001' in text:
        # 模型不可能自己产出这两个内部标记；清掉继续，不拿它中断报告。
        text = text.replace('\ue000', '').replace('\ue001', '')
        note_guard(state, 'repair', 'report.internal_marker')
    text = _strip_unknown_citations(state, text, records)
    text = _fill_citations(state, text, records)

    def calculate(match: re.Match[str]) -> str:
        line_start = text.rfind('\n', 0, match.start()) + 1
        line_end = text.find('\n', match.end())
        source_line = text[line_start:line_end if line_end >= 0 else len(text)]
        targets = _line_targets(state, source_line, records)
        for reference in _REF.findall(match[1]):
            record = records.get(reference[0])
            if record and targets and record.target not in targets:
                _block(state, 'report.calc_other_target',
                       'calc引用了其他对象的量；须注明实际对象，不能冒充目标录音。')
        claim_role = _role(text[line_start:match.start()])
        category = claim_role.split('.')[0]
        if category in {'pitch', 'formant', 'intensity', 'vot'}:
            quantities = {q.ref: q for record in records.values() for q in record.quantities}
            for reference in _REF.findall(match[1]):
                q = quantities.get(f'{reference[0]}.q{reference[1]}')
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
            checked = _check_prosody_line(state, line, records)
            if checked != line:
                masked = masked.replace(line, checked)
            continue
        # Calculation citations remain visible to prosody validation, while its
        # deterministic numbers bypass a second check against raw measurements.
        evidence_line = line
        for index, rendered in enumerate(replacements):
            token = '\ue000' + chr(0xE100 + index) + '\ue001'
            evidence_line = evidence_line.replace(token, ' '.join(m[0] for m in _CITATION.finditer(rendered)))
        _check_numeric_line(state, evidence_line, records, verbatim=in_fence or _verbatim_line(line))
        checked = _check_prosody_line(state, evidence_line, records)
        if checked != evidence_line:
            # 只把追加的标注接回 masked 的那一行：evidence_line 里的 calc 占位式已经被
            # 引证替换过，整行替换会把程序算出来的结果弄丢（2026-10-07 自查发现）。
            masked = masked.replace(line, line.rstrip() + checked[len(evidence_line):])
    for index, rendered in enumerate(replacements):
        masked = masked.replace('\ue000' + chr(0xE100 + index) + '\ue001', rendered)
    return masked


#: 「没有音频输入所以测不了」：测量是 Praat 对对象做的，不需要把音频发给模型。
#: 2026-10-07 实测：一轮里已经测到 10 行数据，报告仍以 ``audio_input=false，无法执行测量``
#: 收尾，而且因为不含任何数字，数值核对一句话都没拦——整份「零测量报告」照样算通过。
_NO_INPUT_CLAIM = re.compile(
    r'未提供音频|没有收到音频|未收到任何音频|没有音频输入|未提供任何音频|无音频输入|'
    r'audio_input\s*(?:为|=|是|is)\s*`?false|无法执行(?:任何)?测量|无法进行(?:任何)?测量|'
    r'无法完成(?:任何)?测量', re.I)

#: 目标上下文里关于「用户提到的对象不存在」的提示（cloud_workflow.target_context 写的）。
_MISSING_OBJECT_NOTE = re.compile(r'^#\s*用户提到但这些对象不在当前 Praat 对象列表里：(.+?)；', re.M)
#: 回答里必须出现的「不存在」判定词（只说了一遍名字不算交代）。
_MISSING_DENIAL = re.compile(r'不存在|找不到|未找到|没有找到|不在(?:当前|对象)|没有这个对象|'
                             r'未加载|没有该对象|does not exist|not found', re.I)


def validate_report(state: Any, report: Any) -> Any:
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
               '所以不能声称没有输入或无法测量。请执行测量并引用结果，'
               '或者说明工具到底为什么失败。')
    # 用户点名的对象不存在时，回答里必须说出来；不能拿「没有音频输入」搪塞过去
    # （2026-10-07 实测：对象名不存在被答成「未收到音频」）。
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
    analysis = _validate_text(state, report.analysis, records)
    coverage = []
    for entry in report.coverage:
        explanation = _validate_text(state, entry.explanation, records)
        coverage.append(entry.model_copy(update={'explanation': explanation})
                        if hasattr(entry, 'model_copy') else entry)
    if hasattr(report, 'model_copy'):
        return report.model_copy(update={'analysis': analysis, 'coverage': coverage})
    report.analysis = analysis
    return report
