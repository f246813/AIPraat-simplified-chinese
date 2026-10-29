"""音素边界证据检查。声学窗口、文件名、模型推测和一般知识不构成已取得的音素级对齐；声明须对应实际工具或人工标注。"""
from __future__ import annotations

import re
from typing import Any, Iterable

__all__ = ['ALIGNMENT_TOOLS', 'TEXTGRID_TOOLS', 'evidence_items', 'boundary_status',
           'report_boundary_context', 'tail_guidance', 'validate_alignment_claims']

#: 只有这些工具名成立的结果才算「对齐产物」。**未知工具一律不算**：``custom_script`` /
#: ``run_praat_script`` 之类可以产出任何文本，不能因为行里有时间就被认成对齐。
ALIGNMENT_TOOLS = frozenset({'alignment', 'forced_alignment', 'phone_alignment',
                             'mfa_alignment', 'align_phones'})
#: 报出 TextGrid 区间标签的工具：能给真实边界，但**标签来源未登记**（不等于对齐产物）。
TEXTGRID_TOOLS = frozenset({'textgrid_info'})

#: 来源字段。只有结果里真的写了来源，才叫「带 provenance」。
_PROVENANCE_KEYS = ('alignment_source', 'aligner', 'source', 'backend', 'method')
#: 行里的来源写法：``对齐来源：mfa`` / ``source=wav2vec2``。
#: 行里的来源写法：``对齐来源：mfa`` / ``source=wav2vec2`` / ``来源 mfa``。
_PROVENANCE_ROW = re.compile(r'(?:对齐来源|对齐方式|来源|source|aligner|backend)'
                             r'(?:[=:：]\s*|\s+)([A-Za-z0-9_.+\-]{2,40})', re.I)

#: TextGrid 区间/层的行形状（``tools._build_textgrid_info`` 的输出）：
#: ``第 1 层「phones」（区间层）``、``区间 1：0.123–0.456 秒，标签「o」``。
_TIER_ROW = re.compile(r'第\s*(\d+)\s*层[「"]([^」"]*)[」"]')
_INTERVAL_ROW = re.compile(r'(\d+(?:\.\d+)?)\s*[–—~\-]\s*(\d+(?:\.\d+)?)\s*(?:秒|s)\s*[，,]\s*'
                           r'标签[「"]([^」"]*)[」"]')
#: 像音素标签的短标签：≤3 个拉丁字符（IPA 常写成 a/aa/tS/sp）、假名、IPA 区段。
#: 更长的拉丁标签（``hello`` 这类词层标签）不算——词层边界不等于音素边界。
_PHONE_LABEL = re.compile(r'(?:[a-z]{1,3}|[ぁ-ゖァ-ヺー]{1,6}|[\u0250-\u02ffɑ-ɜ]{1,6})')
_PHONE_TIER = re.compile(r'音素|音位|母音|子音|phone|phoneme|align|对齐|ipa|segment', re.I)

#: 「这段报告在谈音素级的东西」——决定要不要加尾指导（不加就不占预算）。
_PHONE_SCOPE = re.compile(r'音素|音位|音节|发音|读音|纠音|口音|标准音|对照|对齐|元音|辅音|'
                          r'母音|子音|モーラ|音拍|mora|phoneme|phone|syllable|pronunciation|'
                          r'alignment|\bipa\b', re.I)

#: 时间值：``0.4 秒`` / ``120 ms`` / ``0.30s``。没有时间的句子（词典知识）不会命中。
_TIME_VALUE = re.compile(r'\d+(?:\.\d+)?\s*(?:ms(?![a-z])|毫秒|seconds?|sec(?![a-z])|s(?![a-z])|秒)', re.I)
#: 窗口词：模型不写具体秒数、只说「等分窗口/切成 4 段」时仍然是在指测量窗口。
_WINDOW_NOUN = re.compile(r'窗口|时窗|时间窗|区间|等分|均分|四等分|三等分|分段|选段')
#: 对应关系。刻意不收「是/为」这类系词：几乎每句都有，收进来等于宽泛误杀。
_MAPPING = re.compile(r'对应|相当于|即|就是|落在|位于|处在|归属|标注为|标为|判为|切分|划分为|'
                      r'归为|→|->|⇔|=')
#: 音素 token：假名、带分隔符的 IPA、以及点名的语音单位。裸拉丁字母（``Hz``、``F1``、
#: ``E1``）不算音素，所以 IPA 只在 ``/o/``、「o」这类分隔符里才认。
_PHONE_KANA = re.compile(r'[ぁ-ゖァ-ヺー]{1,8}')
_PHONE_DELIMITED_IPA = re.compile(r'[/「『“"（(]\s*([a-zɑ-ʯəɐɪʊɛɔæʌʃʒθðŋɲɹɾʔβɸˈˌː]{1,6})\s*'
                                  r'[/」』”"）)]')
_PHONE_WORD = re.compile(r'音素|音位|元音|辅音|母音|子音|モーラ|音拍|音节|phonemes?|phones?|'
                         r'vowel|consonant|mora|syllable', re.I)
#: 音素 token 里的泛称：它们本身不是某个音素，报错时不再引号重复一遍。
_META_PHONE = re.compile(r'^(?:音素|音位|phonemes?|phones?)$', re.I)
#: 明确否定：紧贴对应关系、或整句「不能把…」时不算命中。
_NEGATION = re.compile(r'不能|不可|无法|不应|不代表|不等于|不是|不算|不足以|没有|缺少|不做|不进行|'
                       r'无需|不必|尚无|未测|未对齐|未经对齐|未做|非对齐')
_HARD_DENIAL = re.compile(r'(?:不能|不可|不应|无法)(?:把它|把|将|据此|据此把)')
#: 情态弱化：允许一般语言学假设，避免把「可能对应」也拦下（已知代价：也能藏住弱化后的断言）。
_HEDGE = re.compile(r'可能|或许|大概|似乎|推测|猜测|不确定|尚未确认|是否|大约|约为|估计|假设')
#: 明确标注的一般知识/示例（不是本次录音的实测）。
_MARKED_GENERAL = re.compile(r'[（(][^）)]{0,24}(?:一般知识|通用知识|常见|示例|非本录音|'
                             r'general knowledge)[^）)]{0,24}[）)]|'
                             r'(?:一般知识|通用知识|general knowledge)\s*[:：]', re.I)
#: 分句：句末标点 + 换行 + 逗号（「0.1–0.3 秒对应おも，0.3–0.5 秒对应い」要切开逐段判断）。
_CLAUSE_SPLIT = re.compile(r'[。；;！？!?\n，,]')
#: 参考材料/标准音在证据里的显式标记；没有产出方时这一事实恒为「没有」。
_REFERENCE_KEYS = ('reference', 'reference_object', 'reference_material', 'reference_sound',
                   'standard', 'standard_object', 'target_reference')


def evidence_items(state: Any) -> list[Any]:
    """从 state（或直接给的证据序列）取出 Evidence 列表；不臆造字段。"""

    if state is None:
        return []
    items = getattr(state, 'evidence', state)
    if isinstance(items, (list, tuple)):
        return [item for item in items if item is not None]
    return []


def _rows(item: Any) -> list[str]:
    rows = getattr(item, 'rows', []) or []
    return [str(row) for row in rows]


def _arguments(item: Any) -> dict[str, Any]:
    arguments = getattr(item, 'arguments', None)
    return dict(arguments) if isinstance(arguments, dict) else {}


def _tool(item: Any) -> str:
    return str(getattr(item, 'tool', '') or '')


def _provenance(item: Any) -> str:
    """证据里写明的对齐来源；没写就是空串（不拿工具名充当来源）。"""

    arguments = _arguments(item)
    for key in _PROVENANCE_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:40]
    for row in _rows(item):
        match = _PROVENANCE_ROW.search(row)
        if match:
            return match.group(1)
    return ''




def boundary_status(state: Any) -> dict[str, Any]:
    """本轮有没有音素级边界证据，以及它属于哪一类（可序列化）。

    ``status``：
    ``alignment``——工具在 :data:`ALIGNMENT_TOOLS` 里，是真对齐产物；
    ``textgrid``——TextGrid 区间标签（边界是真的，标签来源未登记）；
    ``none``——两条都不是。``provenance`` 只来自证据自己写的来源字段。
    """

    items = evidence_items(state)
    alignment: list[str] = []
    provenance = ''
    textgrid: list[str] = []
    textgrid_provenance = ''
    for number, item in enumerate(items, 1):
        tool = _tool(item)
        has_span = any(re.search(r'\d+(?:\.\d+)?\s*[–—~-]\s*\d+(?:\.\d+)?\s*(?:秒|s\b)', row)
                       for row in _rows(item))
        if tool in ALIGNMENT_TOOLS and _provenance(item) and has_span:
            alignment.append(f'E{number}')
            provenance = provenance or _provenance(item)
        elif _is_textgrid_tool(tool) and _labelled_intervals(_rows(item)):
            textgrid.append(f'E{number}')
            textgrid_provenance = textgrid_provenance or _provenance(item)
    if alignment:
        return {'status': 'alignment', 'ids': alignment, 'provenance': provenance}
    if textgrid:
        # 标签来源没登记时 provenance 留空：TextGrid 给出边界，但不证明它来自对齐器。
        return {'status': 'textgrid', 'ids': textgrid, 'provenance': textgrid_provenance}
    return {'status': 'none', 'ids': [], 'provenance': ''}


def _measured_windows(items: list[Any], limit: int = 6) -> list[str]:
    """声学测量的时间窗（``E2 0.100–0.300 秒``）；只抄证据里的参数或结果行。"""

    windows: list[str] = []
    for number, item in enumerate(items, 1):
        tool = _tool(item)
        if tool in ALIGNMENT_TOOLS or _is_textgrid_tool(tool):
            continue  # 这些是边界，不是声学时间窗，不能混进同一份事实里。
        arguments = _arguments(item)
        start = arguments.get('from', arguments.get('start'))
        end = arguments.get('to', arguments.get('finish', arguments.get('end')))
        span = None
        try:
            if start is not None and end is not None:
                span = (float(start), float(end))
        except (TypeError, ValueError):
            span = None
        if span is None:
            for row in _rows(item):
                match = _INTERVAL_ROW.search(row) or re.search(
                    r'(\d+(?:\.\d+)?)\s*[–—~\-]\s*(\d+(?:\.\d+)?)\s*秒', row)
                if match:
                    span = (float(match.group(1)), float(match.group(2)))
                    break
        if span is None or span[1] <= span[0]:
            continue
        windows.append(f'E{number} {span[0]:g}–{span[1]:g} 秒')
        if len(windows) >= limit:
            break
    return windows


def _reference_ids(items: list[Any]) -> list[str]:
    """带显式参考/标准音标记的证据编号（当前没有工具产出该标记，所以通常为空）。"""

    found: list[str] = []
    for number, item in enumerate(items, 1):
        arguments = _arguments(item)
        if any(arguments.get(key) for key in _REFERENCE_KEYS):
            found.append(f'E{number}')
        elif str(getattr(item, 'kind', '') or '') in {'reference', 'comparison'}:
            found.append(f'E{number}')
    return found


def _is_textgrid_tool(tool: str) -> bool:
    """只有登记过的 TextGrid 工具才算「有区间标签」；未知工具产出的同样文本不算。"""

    return tool in TEXTGRID_TOOLS


def _labelled_intervals(rows: Iterable[str]) -> list[tuple[float, float, str]]:
    """TextGrid 行里的带标签区间；层名像音素层时短标签都算，否则只收像音素的标签。"""

    rows = list(rows)
    phone_tier = False
    intervals: list[tuple[float, float, str]] = []
    for row in rows:
        tier = _TIER_ROW.search(row)
        if tier:
            phone_tier = bool(_PHONE_TIER.search(tier.group(2)))
        match = _INTERVAL_ROW.search(row)
        if not match:
            continue
        label = match.group(3).strip()
        if not label:
            continue
        if not phone_tier or not label:
            continue
        intervals.append((float(match.group(1)), float(match.group(2)), label))
    return intervals

def _filename_guess(state: Any) -> dict[str, Any] | None:
    """``material_metadata.pronunciation_hint`` 里的文件名推测（只认 filename 来源）。"""

    metadata = getattr(state, 'material_metadata', None)
    if not isinstance(metadata, dict):
        return None
    hint = metadata.get('pronunciation_hint')
    if not isinstance(hint, dict):
        return None
    if str(hint.get('source') or '').strip().lower() != 'filename':
        return None
    candidate = str(hint.get('candidate') or '').strip()[:60]
    if not candidate:
        return None
    return {'candidate': candidate, 'source': 'filename',
            'status': str(hint.get('status') or '根据文件名推测；未验证实际录音内容')[:80]}


def _phone_scope(state: Any) -> bool:
    """这一轮是不是在问音素级的发音/对齐/标准音（决定要不要花 token 加尾指导）。"""

    goal = str(getattr(state, 'goal', '') or '')
    direction = str(getattr(state, 'direction', '') or '')
    return bool(_PHONE_SCOPE.search(goal + '\n' + direction))


def report_boundary_context(state: Any) -> dict[str, Any]:
    """本轮的边界事实＋一句尾指导；没有可说的就返回 ``{}``（不占缓存预算）。

    事实只来自真实字段：``state.evidence``（工具名/参数/结果行/来源）、
    ``state.material_metadata['pronunciation_hint']``、``state.goal`` / ``state.direction``。
    不读配置字符串——``alignment.mfa`` / ``wav2vec2`` 这类配置出现不等于已对齐。
    """

    items = evidence_items(state)
    boundary = boundary_status(items)
    guess = _filename_guess(state)
    references = _reference_ids(items)
    in_scope = _phone_scope(state)
    windows = _measured_windows(items)

    guidance: list[str] = []
    if boundary['status'] == 'alignment':
        provenance = boundary['provenance']
        guidance.append('本轮有音素对齐结果（' + '、'.join(boundary['ids'])
                        + ('，来源 ' + provenance if provenance else '，但来源未登记')
                        + '）；引用其边界时注明来源，未对齐的窗口仍不能当音素边界。')
    elif boundary['status'] == 'textgrid':
        guidance.append('本轮有 TextGrid 区间标签（' + '、'.join(boundary['ids'])
                        + '），可以直接引用标签与时间；但标签来源未登记，'
                        '不能说成对齐器输出或标准音对照。')
    elif in_scope:
        guidance.append('本轮没有音素级边界证据：声学测量的时间窗不等于音素/音节边界，'
                        '不能把声学时间窗指定为具体音素，也不能声称已完成音素对齐。')
    else:
        guidance.append('本轮没有音素级边界证据：声学测量的时间窗不等于音素边界。')
    if guess and in_scope:
        guidance.append('「' + guess['candidate'] + '」只是文件名推测（未验证实际录音内容），'
                        '不能当作转写或音素边界。')
    if in_scope and not references:
        guidance.append('未登记参考音对照测量，不能声称已做实测标准音对比；一般发音知识须与本录音诊断区分。')

    if not (in_scope or guess or boundary['status'] != 'none'):
        return {}
    facts: dict[str, Any] = {'boundary_evidence': boundary}
    if windows:
        facts['acoustic_time_windows'] = windows
        if boundary['status'] == 'none':
            facts['acoustic_time_windows_are_phone_boundaries'] = False
    if guess:
        facts['filename_guess'] = guess
    facts['reference_evidence'] = 'present' if references else 'not_recorded'
    if references:
        facts['reference_evidence_ids'] = references
    facts['guidance'] = ''.join(guidance)
    return facts


def tail_guidance(state: Any) -> str:
    """只要尾指导那一句（``report_instructions`` 直接追加用）；没有就是空串。"""

    return str(report_boundary_context(state).get('guidance') or '')


def _phone_tokens(clause: str) -> list[str]:
    """这一分句里点名的音素；具体写法（假名/带括号的 IPA）优先于「音素」这类泛称。"""

    specific: list[str] = []
    for pattern in (_PHONE_KANA, _PHONE_DELIMITED_IPA):
        for match in pattern.finditer(clause):
            token = (match.group(1) if match.groups() else match.group(0)).strip()
            if token and token not in specific:
                specific.append(token)
            if len(specific) >= 4:
                return specific
    if specific:
        return specific
    generic: list[str] = []
    for match in _PHONE_WORD.finditer(clause):
        token = match.group(0).strip()
        if token and token not in generic:
            generic.append(token)
        if len(generic) >= 2:
            break
    return generic


def _phone_window_claim(clause: str, carried_window: bool = False) -> dict[str, Any] | None:
    """这一分句是不是「没有边界证据却把音素和测量窗口一一对应」。

    命中条件（全部成立才算，宁缺勿滥）：有音素 token、有对应关系、有时间值或窗口词
    （``carried_window`` 表示紧邻的上一分句刚说了时间窗/等分），且否定/弱化/一般知识
    标注都不在范围内。
    """

    clause = clause.strip(' \t-*#>「」“”"')
    if not clause or len(clause) > 400:
        return None
    if _HARD_DENIAL.search(clause) or _MARKED_GENERAL.search(clause) or _HEDGE.search(clause):
        return None
    if not (carried_window or _TIME_VALUE.search(clause) or _WINDOW_NOUN.search(clause)):
        return None
    mapping = _MAPPING.search(clause)
    if not mapping:
        return None
    if _NEGATION.search(clause[max(0, mapping.start() - 16):mapping.start()]):
        return None
    phones = _phone_tokens(clause)
    if not phones:
        return None
    return {'rule': 'report.window_as_phone', 'clause': clause[:160], 'phones': phones[:4]}


def validate_alignment_claims(state: Any, text: Any, *, retry: bool = False) -> list[dict[str, Any]]:
    """没有音素边界证据却把具体音素对应到测量窗口的分句，逐条报出。

    返回 issue 列表（``rule`` / ``clause`` / ``phones`` / ``message``）；``retry=True``
    时抛 ``ModelRetry``（并挂 ``rule``），调用方决定重试还是就地标注。**不**改写正文、
    不写 ``state.metrics``，也不 import ``report_guards``：整合位置由主线程决定。

    限制（有意为之）：一有边界证据（对齐产物或 TextGrid 标签）就完全放行——本函数只抓
    「证据不存在却做了一一对应」这一种矛盾，不核对每个边界的时间是否真的对得上（那是
    数值守卫的事）。弱化措辞（可能/推测）、明确否定、一般知识标注都会放行。
    """

    if boundary_status(evidence_items(state))['status'] != 'none':
        return []
    issues: list[dict[str, Any]] = []
    carried = False
    for clause in _CLAUSE_SPLIT.split(str(text or '')):
        issue = _phone_window_claim(clause, carried)
        # 「按等分窗口切成四段，依次对应おも、い、だ、す」：窗口词在上一分句，
        # 所以窗口语境只往后带一格（带太久会把整段长文都当成有窗口）。
        carried = bool(_TIME_VALUE.search(clause) or _WINDOW_NOUN.search(clause))
        if issue is None:
            continue
        # 只有「音素」这类泛称时不再引号重复一遍（「音素」这类音素是病句）。
        specific = [phone for phone in issue['phones'] if not _META_PHONE.fullmatch(phone)]
        quoted = ('「' + '/'.join(specific) + '」') if specific else '具体音素'
        issue['message'] = ('本轮没有任何音素级边界证据（只有声学时间窗）；不能把'
                            + quoted + '与测量窗口一一对应。'
                            '先取得音素边界，或把窗口限定为声学分段并说明它不是音素边界。')
        issues.append(issue)
    if issues and retry:
        from pydantic_ai import ModelRetry

        error = ModelRetry(issues[0]['message'])
        error.rule = issues[0]['rule']
        raise error
    return issues
