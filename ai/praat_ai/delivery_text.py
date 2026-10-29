"""用户可见文案与出口泄漏检测。

内部原因和守卫规则保存在 metrics，用户正文使用可读说明及结构化缺口。
失败分类共用 MESSAGES；find_answer_leaks 检查内部字段和未渲染占位式，
是否改写由调用方决定。"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: 六类失败的默认说法。键名与 ``Failure`` 分类一致。
MESSAGES = {
    # 不写具体归因：同一句也用于「模型输出被内容守卫拦下」的情况，那时说
    # 「时间或额度用完了」是假原因（用户该看到的是「没写完」这件事本身）。
    'budget': '这次解释没写完；已保留测得的数值，可以再问一次接着分析。',
    'capability': '这台机器缺少完成这项分析需要的组件，相关的量给不出来；其余测量仍然有效。',
    'transient': '这次和模型服务的连接没成功，已保留测得的数值；可以再问一次。',
    'policy': '当前工作流设置关闭了这一步；需要的话可以在设置里打开。',
    'cancelled': '已取消这次分析，已完成的测量都保留着。',
    'user_fixable': '这次请求的参数没对上，已保留测得的数值；换个说法或指明对象再来一次即可。',
}

NO_MEASUREMENT_MESSAGES = {
    'budget': '这次未能完成分析，尚未取得测量结果；可以缩小问题范围后重试。',
    'capability': '这台机器缺少完成这项分析需要的组件，尚未取得测量结果。',
    'transient': '这次和模型服务的连接没成功，尚未取得测量结果；请检查连接后重试。',
    'policy': MESSAGES['policy'],
    'cancelled': '已取消这次分析，尚未取得测量结果。',
    'user_fixable': '这次请求的参数或对象没对上，尚未取得测量结果；请确认对象与范围后重试。',
}

#: 从上到下的顺序即优先级：先认「用户主动取消」，再认能力缺失，最后才归到预算。
_PATTERNS = (
    (r'取消|cancelled', 'cancelled'),
    (r'工作流程未启用|已关闭|未启用|工作流', 'policy'),
    (r'缺少|未安装|没有可用|不支持|不可用|没有运行中的|未找到', 'capability'),
    (r'超时|连接|网络|状态不明|无法确认|仍然封锁|暂时|connection error|timed?\s*out|timeout|network|TLS|SSL', 'transient'),
    (r'阶段|时间上限|请求预算|工具预算|预算|额度|空回复', 'budget'),
    (r'参数|编号|对象|范围|矛盾|必须是|无效', 'user_fixable'),
)

#: 界面上「填什么」的默认回退。
DEFAULT_CLASS = 'budget'


def classify(reason: str) -> str:
    """把内部原因归到六类失败之一。"""

    text = str(reason or '')
    for pattern, kind in _PATTERNS:
        if re.search(pattern, text, re.I):
            return kind
    return DEFAULT_CLASS


def user_reason(reason: str, *, fallback: str = '', has_measurements: bool = True) -> str:
    """一句可以直接给用户看的原因；没有任何原因时用 ``fallback``。"""

    kind = classify(reason)
    if not str(reason or '').strip() and fallback:
        return str(fallback)
    return (MESSAGES if has_measurements else NO_MEASUREMENT_MESSAGES)[kind]


def has_internal_terms(text: str) -> bool:
    """正文里是否还留着不该出现的内部术语（用于门禁与测试）。"""

    return bool(INTERNAL.search(str(text or '')))


#: 不该出现在用户正文里的内部术语：阶段代号、规则 ID、字段名、守卫术语。
INTERNAL = re.compile(
    r'\bL[0-4]\b|report\.[a-z_]+|tool\.(?:policy|schema)\.|guard_blocks|guard_repairs|'
    r'route_reason|finish_state|coverage_guidance|交付项状态|自动数值核对|'
    r'阶段时间上限|请求预算|工具预算|BranchExit|ModelRetry')


# ---------------------------------------------------------------------------
# 回答泄漏检测：模型把内部要求 / 操作指导当成最终回答写出来
#
# 只认高置信组合，不做「删正文」这种大动作：命中时由调用方决定重写、退回或标注。
# 正常回答里的「无法从当前录音确定」「没有测量不能判断你的发音」「f=1/T」、
# 一般性讨论提示词怎么写、证据引用 ``[E1]`` 都不算泄漏。
# ---------------------------------------------------------------------------

#: 一处泄漏的分类；取值即 :class:`AnswerLeak` 的 ``kind``。
LEAK_UNRESOLVED_PLACEHOLDER = 'unresolved_placeholder'
LEAK_PROMPT_INSTRUCTION_ECHO = 'prompt_instruction_echo'
LEAK_USER_DELIVERY_INSTRUCTION = 'user_delivery_instruction'
LEAK_INTERNAL_TERM = 'internal_term'

#: 每类泄漏给维护者看的一句话（不进用户正文）。
LEAK_KINDS = {
    LEAK_UNRESOLVED_PLACEHOLDER: '未解析的模板占位式',
    LEAK_PROMPT_INSTRUCTION_ECHO: '把提示词/内部要求原样讲了出来',
    LEAK_USER_DELIVERY_INSTRUCTION: '把「交给用户」这类交付动作写成回答',
    LEAK_INTERNAL_TERM: '正文里出现内部字段名/规则名',
}


@dataclass(frozen=True)
class AnswerLeak:
    """一处泄漏：``kind`` 见 :data:`LEAK_KINDS`，``match`` 是原文片段，``start``/``end`` 为字符区间。"""

    kind: str
    match: str
    start: int
    end: int

    def as_dict(self) -> dict[str, object]:
        """给输出校验的 JSON 化：字段名固定，便于前端与日志对齐。"""

        return {'kind': self.kind, 'match': self.match, 'start': self.start, 'end': self.end}


#: 未解析的模板占位式：``{{calc:E1.q1*2|Hz}}``、``{{q:E1.q1}}``、被截断的 ``{{calc:``。
_PLACEHOLDER = re.compile(r'\{\{\s*(?:calc\s*:|q\s*:|E\d+|\w+[.:|])[^{}\n]{0,160}\}?')

#: 提到提示词 / 内部要求的词。
_META_SOURCE = re.compile(
    r'系统提示词?|系统指令|系统消息|内部指令|内部要求|任务要求|工作流要求|提示词|'
    r'\b(?:system|developer)\s+(?:prompt|message)\b',
    re.I)

#: 引经据典地援引看不见的要求（「按照要求我不得…」）。
_APPEAL = re.compile(
    r'(?:按照|根据|遵照|依照|按|依据)(?:上述|以上|上面|系统|内部)?(?:的)?'
    r'(?:要求|指示|指令|提示词|规则|规范|约定)'
    r'|(?:as|per|according\s+to|following)\s+(?:the\s+|my\s+)?'
    r'(?:system\s+prompt|instructions?|guidelines?|developer\s+message)',
    re.I)

#: 第一人称的义务/禁令：有这句才说明模型在讲自己的约束，而不是在讨论提示词。
_OBLIGATION = re.compile(
    r'我(?:必须|须|应当|应该|应|要|需要|只能|不得|不能|不可以|被要求|被指示)'
    r'|不得(?:随便|随意|自行)?(?:猜|编造|臆测|下结论)'
    r'|不能(?:随便|随意|凭空)?(?:猜|编造|臆测)'
    r'|\bI\s*(?:must|shall|should|need\s+to|have\s+to'
    r'|am\s+(?:required|supposed|not\s+allowed|instructed))\b',
    re.I)

#: 把「交付动作」当成回答写出来（「…后提交给用户」）。
_DELIVERY = re.compile(
    r'(?:提交|交付|呈现|汇报|转发|发送|发给|交给)(?:给)?(?:用户|对方|客户)'
    r'|向用户(?:呈现|提交|汇报)'
    r'|\b(?:present|deliver|submit|report|hand)\b[^.\n]{0,40}?\bto\s+the\s+user\b',
    re.I)

#: 一句话的边界：组合判定只在同一句里成立，跨句不算。
_SENTENCE = re.compile(r'[^。！？!?；;\n]+')

#: 「L1/L2」在语音学里也指第一/第二语言，那里不是阶段代号。
_SECOND_LANGUAGE = re.compile(r'学习者|学习|二语|母语|外语|language|learner|speaker', re.I)
_STAGE_TOKEN = re.compile(r'L[0-4]')


def _is_internal_code(match: re.Match[str], body: str) -> bool:
    """``INTERNAL`` 里除阶段代号外都是字段名/规则名；阶段代号还要排除二语习得用法。"""

    if not _STAGE_TOKEN.fullmatch(match.group(0)):
        return True
    window = body[max(0, match.start() - 8): match.end() + 8]
    return _SECOND_LANGUAGE.search(window) is None


def find_answer_leaks(text: str) -> tuple[AnswerLeak, ...]:
    """正文里「把内部要求/操作指导当最终回答」的高置信证据；没有泄漏时返回空元组。

    认四类（见 :data:`LEAK_KINDS`）：

    ① ``unresolved_placeholder``：没被替换的 ``{{calc:…}}`` 之类占位式；
    ② ``prompt_instruction_echo``：同一句里既提到提示词/要求，又出现第一人称义务
       （或「按照要求」式的援引），例如「按照要求我不得随便猜定论」；
    ③ ``user_delivery_instruction``：同一句里既有交付动作又有 ①② 的成分，
       例如「…填入公式后提交给用户」「present it to the user」；
    ④ ``internal_term``：原样写出 :data:`INTERNAL` 里的字段名/规则名/阶段代号。

    故意保守：单说「无法从当前录音确定」「没有测量不能判断你的发音」、写公式
    ``f=1/T``、一般性讨论提示词怎么写、引用 ``[E1]`` 都不算泄漏。
    返回项按出现位置排序，可重复（同一句里两类泄漏各记一条）。
    """

    body = str(text or '')
    leaks: list[AnswerLeak] = []

    for match in _PLACEHOLDER.finditer(body):
        leaks.append(
            AnswerLeak(LEAK_UNRESOLVED_PLACEHOLDER, match.group(0), match.start(), match.end()))

    for match in INTERNAL.finditer(body):
        if _is_internal_code(match, body):
            leaks.append(AnswerLeak(LEAK_INTERNAL_TERM, match.group(0), match.start(), match.end()))

    for sentence in _SENTENCE.finditer(body):
        clause = sentence.group(0)
        base = sentence.start()
        meta = _APPEAL.search(clause) or _META_SOURCE.search(clause)
        obligation = _OBLIGATION.search(clause)
        if meta is not None and obligation is not None:
            leaks.append(AnswerLeak(
                LEAK_PROMPT_INSTRUCTION_ECHO, meta.group(0), base + meta.start(), base + meta.end()))
        delivery = _DELIVERY.search(clause)
        if delivery is not None and (meta is not None or obligation is not None):
            leaks.append(AnswerLeak(
                LEAK_USER_DELIVERY_INSTRUCTION, delivery.group(0),
                base + delivery.start(), base + delivery.end()))

    leaks.sort(key=lambda item: (item.start, item.end, item.kind))
    return tuple(leaks)


# Evidence ids belong to the audit record, not the plain chat transcript. Apply
# only after numeric validation: the guards still need citations for provenance.
_DISPLAY_CITATION = re.compile(r'\[E\d+(?:\s*[,，、;/]\s*E?\d+)*\]')


def present_analysis(text: str) -> str:
    """Remove internal evidence citations from an already validated report.

    This is presentation, not validation: quantities, list numbering, phonetic
    notation and ordinary names such as F1/F2 or the musical note E4 survive.
    Raw evidence and guard diagnostics remain in structured task records.
    """

    body = _DISPLAY_CITATION.sub('', str(text or ''))
    body = re.sub(r'[（(]\s*[）)]', '', body)                     # 引证删掉后留下的空括号
    body = re.sub(r'([，,；;])\s*(?=[，,；;。！？!?])', '', body)   # 重复标点
    body = re.sub(r'[ \t]+([，。；：！？、,.;:!?])', r'\1', body)
    body = re.sub(r'[ \t]+$', '', body, flags=re.M)
    return body.strip()


def has_answer_leak(text: str) -> bool:
    """输出校验用的判定：正文里有没有 :func:`find_answer_leaks` 认出的泄漏。"""

    return bool(find_answer_leaks(text))


def describe_leaks(leaks: tuple[AnswerLeak, ...] | list[AnswerLeak]) -> str:
    """把泄漏压成一句给维护者看的说明（写日志/告警用，不进用户正文）。"""

    labels: list[str] = []
    for item in leaks:
        label = LEAK_KINDS.get(item.kind, item.kind)
        if label not in labels:
            labels.append(label)
    return '；'.join(labels)


__all__ = ['DEFAULT_CLASS', 'INTERNAL', 'MESSAGES', 'LEAK_KINDS', 'LEAK_INTERNAL_TERM',
           'LEAK_PROMPT_INSTRUCTION_ECHO', 'LEAK_UNRESOLVED_PLACEHOLDER',
           'LEAK_USER_DELIVERY_INSTRUCTION', 'AnswerLeak', 'classify', 'user_reason',
           'has_internal_terms', 'find_answer_leaks', 'has_answer_leak', 'describe_leaks']
