"""解释准确性策略：测量能说明什么、不能说明什么。

背景（用户复核过的真实报告）：把 ``50.67`` / ``50.92`` 的首尾、``50.599`` 的均值与
``334.57 Hz/s`` 的平均绝对斜率当成「语调平淡」的定论，再据此诊断「喉部肌肉过度紧张 /
气息支撑不足」；把 ``563.45 Hz`` 的孤立峰说成「突然喉部收紧造成的」；在没有词典字段的
情况下指定日语词的标准重音型。

原则：**测量存在 ≠ 测量可信 ≠ 可以作生理诊断**。声学值只描述这一段信号，不描述当前
说话人的喉部、声带、气息、性别或舒适音域。

三个公开入口（都不 import ``report_guards`` / ``cloud_agent``，也不写 ``state.metrics``；
整合位置由调用方决定）：

* :func:`check_interpretation_claims`：逐分句报出越界断言，每项 ``rule`` / ``clause`` / ``message``；
* :func:`sanitize_interpretation`：把越界分句换成就地边界说明，**保留测量片段**；
  不是末尾加一句免责声明、让原结论照样成立；
* :func:`task_tail_guidance`：只在相关录音目标（语调/嗓音、日语重音）出现时给一段尾指导，
  不做动态 system prompt。

刻意不管的事（与相邻模块分工，避免重复误杀）：

* 数值对不对、``50 Hz`` 之类的硬阈值——``report_guards`` 的数值核对；本模块**不设任何阈值**，
  只看句子是不是把测量当成生理/人群结论；
* 「语调平淡 / 上升 / 下降」这类走向结论——``report_guards._PROSODY`` 已就地标注；
* 词条读音、重音型、音位事实本身的真假——没有词典字段可核，本模块只要求来源与变体，
  判据里**不认任何具体词**（没有假名词表，也没有某词的读音或重音型）。

允许的口径：一般生理/语音学知识（标明一般知识且不指向本录音）、用户自述（不冒充算法
诊断）、温和练习建议（「放松下颌、自然发声」）、以及「无法判断」的否定。弱化措辞
（可能/或许）**不能**让本录音的病因断言通过——报告里正是这么写的。

已知取舍（有意保守）：判据是确定性的词法，不看数值大小，也不做语义理解；带「你」的
机制句若没有条件从句，可能与断言一起被报出（``_CONDITIONAL`` 只挡「…的时候/如果…」）。
命中只说明这句话需要复核或改写，不说明用户目标无法回答。
"""
from __future__ import annotations

import re
from typing import Any

__all__ = ['check_interpretation_claims', 'sanitize_interpretation', 'task_tail_guidance']

#: 生理部位/器官：断言里必须真的点到身体，才谈得上「生理诊断」。
_BODY_PART = re.compile(r'喉部|喉头|喉咙|嗓子|声带|声门|气息|气流|呼吸|肺活量|肺|'
                        r'下颌|下巴|舌根|软腭|硬腭|鼻腔|胸腔|共鸣腔|口腔|肌肉')
#: 部位的状态/动作（配合 ``_BODY_PART`` 才成立）。
_BODY_STATE = re.compile(r'紧张|僵硬|挤压|压喉|过紧|过度用力|用力|闭合|支撑|收紧|松弛|'
                         r'疲劳|不稳|漏气|憋气|发紧|颤抖|发抖|机能|消耗')
#: 直接点名「有毛病」的谓语：没有数值在场时，靠它区分「诊断」与「机制说明」。
_DEFECT = re.compile(r'不足|不够|过度|过紧|过松|不好|有问题|不健康|不正常|不稳|漏气|'
                     r'虚弱|疲劳|紧张|僵硬|挤压|太用力|用力过猛')
#: 人群/性别/舒适音域：声学值不能推出这些。
_NORM = re.compile(r'舒适(?:范围|区|音域)|正常范围|正常值|符合(?:女性|男性|成年|年龄)|'
                   r'属于(?:女性|男性|女声|男声)|(?:女性|男性|女声|男声|性别|成年|年龄)')
#: 性格/情绪：声学测量不涉及心理状态。
_PSYCH = re.compile(r'性格|内向|外向|焦虑|抑郁|自信|情绪|心理状态|压力大|社交|攻击性|亲和力')

#: 日语词汇重音的语境、可肉眼核对的重音型、以及「给标准」的断言。
_ACCENT_CTX = re.compile(r'重音|アクセント|accent|音调核|核后|核内|音调型|高低型', re.I)
_ACCENT_TYPE = re.compile(r'平板型|头高型|中高型|尾高型|平板调|起伏型|头高|'
                          r'(?:重音|アクセント核|音调核)(?:在|位于|落在|应在|放在)第?\s*'
                          r'[一二三四五六七八九十\d]+\s*(?:个)?(?:拍|モーラ|音拍)')
_ACCENT_NORM = re.compile(r'标准(?:型|重音|读法|发音|音调)|正确(?:的)?(?:重音|读法)|'
                          r'应该读(?:成|作)|要读成|须读成|读作.{0,8}型|'
                          r'(?:本录音|这段录音|你的).{0,10}(?:是|读作).{0,8}型')
#: 有来源、有变体标记，或本来就是一般日语知识：不拦（没有词典字段就不做事实裁判）。
_ACCENT_SOURCE = re.compile(r'词典|辞書|字典|辞典|NHK|大辞林|明镜|新明解|来源|出处|据|资料|'
                            r'参考|教科书|两种读法|变体|方言|口音|个人习惯')
_ACCENT_GENERAL = re.compile(r'一般知识|通用知识|标准语|日语中|日语的|通常|大多数|例如|示例|'
                             r'非本录音|general knowledge', re.I)
_KANA = re.compile(r'[ぁ-ゖァ-ヺー]')
_JAPANESE = re.compile(r'日语|日本語|japanese', re.I)

#: 测量在场：数值+单位，或测量类词。分句里没有时可以用上一分句的（``carried``）。
_VALUE = re.compile(r'\d+(?:\.\d+)?\s*(?:Hz/s|Hz|赫兹|kHz|千赫|dB|分贝|半音/s|毫秒|ms|秒|%|％|度|次/秒)',
                    re.I)
_MEASURE_WORD = re.compile(r'测量|测得|实测|统计|数值|数据|均值|平均值|斜率|首尾|最高|最低|峰值|'
                           r'范围|读数|曲线|频谱|波形|图上|高于|低于')
#: 指向本次录音/用户：一般知识与「这一轮」的分界。
_USER_LINK = re.compile(r'你|您|本录音|这段|本段|本次|当前录音')
_STRONG_CAUSAL = re.compile(r'说明|表明|证明|意味着|反映|可见|看出|代表|造成|导致|引起')
#: 明确否定：不能判断、不代表、未测量——这些正是我们希望保留的表述。
_DENIAL = re.compile(r'不能|不可|无法|不足以|不代表|不等于|不应|不宜|未测|没有测|未做|不做|'
                     r'不据此|尚不能|无法判断|不构成|不说明|不推断|不作|不用于')
_ADVICE = re.compile(r'^\s*(?:[-*·•]\s*)?(?:建议|可以尝试|可尝试|可以|试着|试试|练习时|练声|'
                     r'小练习|提示|下一步|请)')
_PRACTICE = re.compile(r'放松|自然发声|轻柔|慢慢|不要用力|别憋气|少量多次|循序渐进')
_SELF_REPORT = re.compile(r'你说|你提到|你自述|你描述|你觉得|你说自己|按你的描述|你的感觉')
_CONDITIONAL = re.compile(r'的时候|时(?:声音|会|就|容易)|如果|假如|一旦|若')
_GENERAL = re.compile(r'一般知识|一般原理|通用知识|常见|通常|一般来说|一般而言|教科书|文献|研究|'
                      r'人群|成人|从声学上|从生理上|原理上|概念上|示例|非本录音|general knowledge',
                      re.I)

_CLAUSE_SPLIT = re.compile(r'([。；;！？!?，,\n])')
_SENTENCE_END = '。！？!?\n'
#: 句末标点才结束测量/一般知识/来源的背书；换行（列表项之间）不算。
#: 指向本次录音本身（标签不能给它背书）：数值已在 ``_VALUE``，这里补指代词。
#: 对本次录音下人群结论时的比较语气：带这种语气的「一般知识」标签不能免责。
_COMPARATIVE = re.compile(r'远?低于|远?高于|超出|超过|达不到|不足|偏低|偏高|过低|过高|'
                          r'太低|太高|下限|上限|附近|接近|处于')
_LOCAL = re.compile(r'这段|本段|本次录音|当前录音|你的|录音中|读作|读成')
#: 改进/练习类的项目符号行：整行按建议处理，不做诊断判定。
_ADVICE_LINE = re.compile(r'^\s*[-*]?\s*\*\*[^*]{0,12}(?:改进|建议|练习|下一步|提示|训练)[^*]{0,12}\*\*')
#: 标题行（Markdown 标题或加粗小标题）：结束上一个区块的语境。
_HEADING_LINE = re.compile(r'^\s*(?:#{1,6}\s|\*\*[^*\n]{1,80}\*\*\s*$)')
_SENTENCE_PUNCT = '。！？!?'

#: 一般知识里的具体阈值/区间写法：本轮无从核实，不能拿来当参照标准（真机里的
#: 「85–180 Hz」「斜率常大于 200 Hz/s」就是这样被当成标准值的）。
_THRESHOLD = re.compile(r'大于|小于|高于|低于|超过|以上|以下|左右|之间|范围内|'
                        r'常大?于|一般在|大约|约为')
#: 用户/参考材料给出的参照：那是可核对的输入，不是模型自带的常识数字。
_REFERENCE_INPUT = re.compile(r'你提供|您提供|用户提供|参考录音|参照|范本|你说|你给|'
                              r'你自述|材料里|标注为')

#: 判「这个量太高/太低」需要可比基线：模型常先用自带阈值、再用「极低/几乎平板」下结论。
_EVALUATIVE = re.compile(r'极低|极高|极弱|极差|太低|太高|明显过低|明显过高|过于(?:低|高|平坦|平淡)|'
                         r'几乎(?:呈|为|是).{0,6}(?:平板|直线)|缺乏.{0,6}起伏|起伏(?:不足|小)|变化小')
#: 句子里先写了「均值/统计 = 数值」再谈范围时，这是测量句：整句删会连实测值一起丢。
_MEASURED_IN_SENTENCE = re.compile(r'(?:均值|平均值|平均|统计|测得|实测|测量值)[^。；]{0,24}\d')
#: 音高/强度这类「可判定强弱」的量（避免把「音量太小」这类日常说法全拦掉）。
_PROSODY_QUANTITY = re.compile(r'音高|基频|F0|斜率|语调|音调|起伏|强度|音强|平板|直线|单调|平淡')
_BASELINE = re.compile(r'参考录音|参照|范本|与.{0,12}相比|相对.{0,8}(?:低|高)|差\s*\d|'
                       r'低于.{0,12}参考|高于.{0,12}参考')

_MESSAGE = {
    'interpretation.evaluative_without_baseline': (
        '{terms} 是拿模型自带的基线做出的高低判断：本轮没有可比的参照值，'
        '只能报告测量值本身；要判断偏高还是偏低，需要先取得同一说话人的基线或参照录音。'),
    'interpretation.general_threshold': (
        '{terms} 是模型自带的一般知识阈值：本轮没有对应的参考材料或测量依据，'
        '不能拿它当标准值来判断这段录音；请只保留定性说法，或说明需要哪些参照才能比较。'),
    'interpretation.acoustic_to_physiology': (
        '{terms} 是生理/发声解释，不是本次测量结果：声学值只能描述这段信号，'
        '不能确定你的喉部、声带或气息状态，也不能诊断肌肉紧张或支撑不足。'
        '请保留测得的数值，把生理原因写成需要另行确认的假设，或直接说明无法判断。'),
    'interpretation.acoustic_to_body_norm': (
        '{terms} 把声学值当成人群/性别或舒适音域的依据：本次录音没有人群参照，'
        '不能据此判断性别、年龄或你的舒适音域；只能说明这是这段录音的测量值。'),
    'interpretation.acoustic_to_psychology': (
        '{terms} 由声学测量推断性格或情绪：测量值不涉及心理状态，也没有心理量表依据；'
        '请删去该判断，只保留测量值与可观察的发声描述。'),
    'interpretation.accent_norm_unattributed': (
        '没有可用的词典字段：{terms} 这类日语词重音型不能当成固定真值。'
        '需要注明词典来源与该词的变体，并把「核后下降」与「核内下降」当成可核对的差异，'
        '不要直接判定对错。'),
}
#: 就地中和时放回原处的说明（不是末尾免责声明：原结论句被换掉了）。
_NEUTRAL = {
    'interpretation.acoustic_to_physiology': '现有声学测量不能确定喉部或气息状态',
    'interpretation.acoustic_to_body_norm': '本次没有个体舒适音域或人群参照，不能据此判断',
    'interpretation.acoustic_to_psychology': '现有声学测量不能确定心理状态',
    'interpretation.accent_norm_unattributed': '词汇重音须核对可靠词典及可能的变体，当前不作确定判断',
    'interpretation.general_threshold': '一般知识里的具体数值阈值本轮无法核实，已省略',
    'interpretation.evaluative_without_baseline': '本轮没有可比基线，不能判定这个量偏高还是偏低',
}
#: 判断「保留的前半句」是否干净：脏了就只保留数值本身。
_BODY_LIKE = re.compile(r'喉|嗓子|声带|声门|气息|呼吸|肺|下颌|下巴|舌根|软腭|肌肉|紧张|僵硬|'
                        r'挤压|过紧|用力|闭合|支撑|收紧|性格|内向|外向|焦虑|抑郁|自信|情绪|心理|'
                        r'女性|男性|女声|男声|性别|舒适|正常范围|年龄')

#: 尾指导的作用域：只在相关录音目标出现时才花 token。
_PROSODY_SCOPE = re.compile(r'语调|音调|音高|嗓音|音色|发声|喉|声带|气息|舒适|舒服|平淡|单调|'
                            r'起伏|纠音|intonation|voice quality|prosody', re.I)
_ACCENT_SCOPE = re.compile(r'重音|アクセント|accent|日语|日本語|japanese|音调型|高低', re.I)
_PROSODY_NOTE = ('目标涉及语调/嗓音时：可以报告这段录音的测量值（平均基频、斜率、强度等）'
                 '与可观察的描述，但不能由这些数值推断喉部紧张、气息支撑、性别或舒适音域；'
                 '没有额外证据时不为当前录音猜测生理原因；可按用户自述转述。'
                 '练习建议可以给（如放松下颌、自然发声），不要写成诊断。')
_ACCENT_NOTE = ('目标涉及日语重音时：没有可用的词典字段，不要给出某个词的固定重音型；'
                '需要注明词典来源与该词的变体，并把「核后下降」与「核内下降」当成可核对的'
                '差异（说明依据），不要直接判定对错。')


def _goal_text(state: Any) -> str:
    """本轮目标/方向（只看这两个字段，不读配置字符串）。"""

    return (str(getattr(state, 'goal', '') or '') + '\n'
            + str(getattr(state, 'direction', '') or ''))


def _japanese(state: Any) -> bool:
    """这一轮是不是在谈日语（重音规则只在日语语境下启用）。"""

    return bool(_JAPANESE.search(_goal_text(state)))


def _issues(rule: str, clause: str, terms: list[str] | tuple[str, ...] = ()) -> dict[str, Any]:
    """一条 issue：``rule`` / ``clause`` / ``message``（附 ``terms`` 便于复核与统计）。"""

    kept = [term for term in dict.fromkeys(terms) if term]
    quoted = '、'.join('「%s」' % term for term in kept) if kept else '这句话'
    return {'rule': rule, 'clause': clause[:160], 'message': _MESSAGE[rule].format(terms=quoted),
            'terms': kept}


def _physiology_claim(clause: str) -> dict[str, Any] | None:
    """声学值 → 当前说话人的喉部/气息/发声生理状态。"""

    part = _BODY_PART.search(clause)
    state = _BODY_STATE.search(clause)
    if not (part and state):
        return None
    return _issues('interpretation.acoustic_to_physiology', clause, [part.group(0), state.group(0)])


def _body_norm_claim(clause: str) -> dict[str, Any] | None:
    """声学值 → 性别/年龄/舒适音域这类人群结论。"""

    norm = _NORM.search(clause)
    if not norm:
        return None
    return _issues('interpretation.acoustic_to_body_norm', clause, [norm.group(0)])


def _psychology_claim(clause: str) -> dict[str, Any] | None:
    """声学值 → 性格/情绪；必须带因果动词（否则容易被习惯用语误伤）。"""

    psych = _PSYCH.search(clause)
    if not psych or not _STRONG_CAUSAL.search(clause):
        return None
    return _issues('interpretation.acoustic_to_psychology', clause, [psych.group(0)])


def _accent_claim(clause: str, cited: bool = False) -> dict[str, Any] | None:
    """没有词典字段却给出日语词的重音型/标准读法。

    只抓「重音型 + 给标准」的分句；同一句里已经写了来源/变体（``cited``）、分句自带来源
    或一般知识标记、或明确否定的都放行（本模块不做事实裁判，也不认任何具体词）。
    """

    # 「尾高型」这类词自带语境，所以语境也可以由重音型本身成立（不认具体词）。
    if not (_ACCENT_CTX.search(clause) or _ACCENT_TYPE.search(clause)):
        return None
    if cited or _ACCENT_SOURCE.search(clause) or _ACCENT_GENERAL.search(clause):
        return None
    norm = _ACCENT_NORM.search(clause)
    typed = _ACCENT_TYPE.search(clause)
    if not (norm and typed):
        return None
    return _issues('interpretation.accent_norm_unattributed', clause,
                   [norm.group(0), typed.group(0)])
#: 声学量本身也构成"在谈这次测量"的语境：句子里没有数字，却在用基频/斜率把责任推给
#: 身体（「基频偏低可能与发声位置靠后有关」）时，不能被当成与测量无关的闲聊放过。
_ACOUSTIC = re.compile(r'基频|音高|F0|强度|音强|斜率|共振峰|谐噪比|HNR|声学|测量值|均值')

#: 引证记号：带 ``[E3]`` 的句子是可核对的实测说法，不按「模型自带知识」处理。
_CITATION = re.compile(r'\[E\d+\]')

def _general_threshold_claim(clause: str, *, cited: bool = False) -> dict[str, Any] | None:
    """「一般知识」里自带的具体阈值/区间（85–180 Hz、斜率常大于 200 Hz/s）。

    数值纪律第 ⑦ 条要求典型范围只给定性说法：模型自带的阈值本轮无法核实，却被
    拿来判断这段录音（「均值处于下限附近」「斜率极低」），等于用一个编造的标准值
    下结论。带引证或引用用户/参考材料的阈值不算——那是可核对的输入。
    """

    if cited or _REFERENCE_INPUT.search(clause):
        return None
    values = [match.group(0) for match in _VALUE.finditer(clause)]
    if not values or not _THRESHOLD.search(clause):
        return None
    return _issues('interpretation.general_threshold', clause, values[:3])


def _evaluative_claim(clause: str) -> dict[str, Any] | None:
    """没有可比基线却说这个量「极低/太高/几乎平板」（真机：斜率 127.84 Hz/s「极低」）。

    阈值本身是模型自带的，「极低」只是那个阈值的影子；删掉阈值却不删评价，用户仍会
    以为有参照。只有带用户/参考材料基线、或明确写了两值相比时才放行。
    """

    if _REFERENCE_INPUT.search(clause) or _BASELINE.search(clause):
        return None
    if not (_EVALUATIVE.search(clause) and _PROSODY_QUANTITY.search(clause)):
        return None
    return _issues('interpretation.evaluative_without_baseline', clause,
                   [_EVALUATIVE.search(clause).group(0)])


def _claim(clause: str, *, carried: bool = False, block: bool = False, japanese: bool = False,
           general: bool = False, cited: bool = False, advice: bool = False) -> dict[str, Any] | None:
    """这一分句该不该报出（宁缺勿滥：先排掉允许的口径，再逐条规则判定）。

    ``carried`` 是本句内的测量语境，``block`` 是整个列表区块的（「证据」一行支持
    下一行「不足」的结论）；``advice`` 表示这一行是改进/练习建议，不做诊断判定。
    """

    clause = clause.strip(' \t-*·#>「」“”"')
    if not clause or len(clause) > 400:
        return None
    if advice:
        return None
    if _DENIAL.search(clause) or _ADVICE.search(clause):
        return None
    # 重音规则不依赖测量在场：它管的是「没有来源就给标准」，不是「数值推出结论」。
    if japanese:
        accent = _accent_claim(clause, cited=cited or general)
        if accent is not None:
            return accent
    local = bool(_VALUE.search(clause) or _LOCAL.search(clause))
    measured = bool(carried or block or local or _MEASURE_WORD.search(clause)
                    or _ACOUSTIC.search(clause))
    user = bool(_USER_LINK.search(clause))
    causal = bool(_STRONG_CAUSAL.search(clause))
    # 没有测量在场时，认两种断言：「点名用户身体并且说它有毛病」（「你气息支撑不足」），
    # 以及带因果词的身体归因（「这可能是由于喉部肌肉过度紧张造成的」——句子自己就把
    # 原因说成身体状态，不需要数字在场）。
    body_cause = bool(causal and _physiology_claim(clause))
    if not measured and not (user and _DEFECT.search(clause)) and not body_cause:
        return None
    if _SELF_REPORT.search(clause) and not causal:
        return None
    if _PRACTICE.search(clause) and not causal:
        return None
    if _CONDITIONAL.search(clause) and not causal:
        norm_claim = _body_norm_claim(clause)
        if norm_claim is not None:
            return norm_claim
        return None
    local = bool(_LOCAL.search(clause) or (_USER_LINK.search(clause) and _VALUE.search(clause))
                 or _COMPARATIVE.search(clause)
                 # 带实测数值再谈「均值/斜率/统计」就是在说这次录音，不是背景知识。
                 or (_VALUE.search(clause) and _MEASURE_WORD.search(clause)))
    threshold = (_general_threshold_claim(clause, cited=cited)
                 if (general or _GENERAL.search(clause)) else None)
    if threshold is not None:
        return threshold
    evaluative = _evaluative_claim(clause)
    if evaluative is not None:
        return evaluative
    # 「一般知识」只豁免真正的一般表述：句子里带着本次测得的数值、或直接指向
    # 这段录音时，标签不能替这次录音的人群/生理结论背书。
    if (general or _GENERAL.search(clause)) and not user and not causal and not local:
        return None
    return (_physiology_claim(clause) or _body_norm_claim(clause) or _psychology_claim(clause))


def _clauses(text: str) -> list[tuple[str, str]]:
    """按标点切分并保留分隔符（``sanitize_interpretation`` 要原样重建正文）。"""

    parts = _CLAUSE_SPLIT.split(text)
    return [(parts[index], parts[index + 1] if index + 1 < len(parts) else '')
            for index in range(0, len(parts), 2)]


def _scan(state: Any, text: Any) -> list[tuple[str, str, dict[str, Any] | None]]:
    """逐分句判定，结果带着原文分句与分隔符（两个入口共用同一套判定）。"""

    rows = []
    carried = block = general = cited = advice = False
    japanese = _japanese(state)
    for clause, separator in _clauses(str(text or '')):
        if not clause.strip():
            # 空行只结束本句语境；区块语境留到标题处才重置（列表的空行很常见）。
            carried = False
            advice = False
            rows.append((clause, separator, None))
            continue
        if _HEADING_LINE.match(clause):
            # 新小节不继承上一节的测量语境（结论只能由本节证据支持）。
            block = general = cited = False
            carried = False
        if _ADVICE_LINE.match(clause):
            advice = True
        issue = _claim(clause, carried=carried, block=block,
                       japanese=japanese or bool(_KANA.search(clause)),
                       general=general, cited=cited, advice=advice)
        # 测量语境在一个区块里持续（列表里「证据」一行支持下一行「不足」的结论）。
        measured = bool(_VALUE.search(clause) or _MEASURE_WORD.search(clause))
        carried = carried or measured
        block = block or measured
        cited = cited or bool(_ACCENT_SOURCE.search(clause))
        general = general or bool(_GENERAL.search(clause) and not _USER_LINK.search(clause)
                                  and not _STRONG_CAUSAL.search(clause))
        if separator and separator in _SENTENCE_PUNCT:
            carried = False
        if separator and separator in _SENTENCE_END:
            advice = False
        rows.append((clause, separator, issue))
    return rows


def check_interpretation_claims(state: Any, text: Any) -> list[dict[str, Any]]:
    """报出把测量当成生理/人群/心理结论、或没有来源就指定日语重音型的分句。

    返回 issue 列表（每项 ``rule`` / ``clause`` / ``message``；``terms`` 是命中的词）。
    **不**改写正文、不写 ``state.metrics``、不抛异常：调用方决定标注、重试还是丢弃。
    """

    return [issue for _, _, issue in _scan(state, text) if issue is not None]


def _measure_fragment(clause: str) -> str:
    """越界分句里可以保留的测量片段：干净的测量前缀，否则只留数值本身。"""

    marker = _STRONG_CAUSAL.search(clause)
    head = clause[:marker.start()].strip(' \t，、；：:—-') if marker else ''
    if (head and len(head) <= 80 and not _BODY_LIKE.search(head)
            and (_VALUE.search(head) or _MEASURE_WORD.search(head))):
        return head
    values: list[str] = []
    for match in _VALUE.finditer(clause):
        if match.group(0) not in values:
            values.append(match.group(0))
    return '、'.join(values[:4])


#: 整句删除用的分隔（保留标点，重建正文时不丢句号）。
_SENTENCE_SPLIT = re.compile(r'([。！？!?\n])')


def drop_unsupported_sentences(text: Any) -> tuple[str, list[dict[str, Any]]]:
    """整句删掉「模型自带阈值」和「声学值 → 人群/性别」的判断。

    这两类不能只删分句：``若说话者为女性，则音调明显过低`` 删掉前半句会剩一个悬空
    的结论，``一般知识指出…85–180 Hz`` 只删数字会剩「通常在 之间」。整句拿掉，
    用户看到的是「这句没有依据，省略了」，而不是半截话。

    带 ``[E#]`` 引证、引用用户/参考材料、或本身是否定句的不动（那是可核对的说法）。
    """

    parts = _SENTENCE_SPLIT.split(str(text or ''))
    out: list[str] = []
    issues: list[dict[str, Any]] = []
    for index in range(0, len(parts), 2):
        sentence = parts[index]
        delimiter = parts[index + 1] if index + 1 < len(parts) else ''
        rule = ''
        if sentence.strip() and not (_DENIAL.search(sentence) or _ADVICE.search(sentence)
                                     or _SELF_REPORT.search(sentence)):
            if not (_CITATION.search(sentence) or _REFERENCE_INPUT.search(sentence)
                    or _BASELINE.search(sentence)):
                norm = _body_norm_claim(sentence)
                threshold = _general_threshold_claim(sentence)
                if norm is not None and (_CONDITIONAL.search(sentence)
                                         or not _VALUE.search(sentence)):
                    # 条件句（「若说话者为女性…」）整句删才不会留下悬空的结论；
                    # 句子里有实测数值时交给逐分句中和，数值得留在原位。
                    rule = 'interpretation.acoustic_to_body_norm'
                elif (threshold is not None and _GENERAL.search(sentence)
                      and not _MEASURED_IN_SENTENCE.search(sentence)):
                    # 只删真正的一般知识句（「一般知识指出…通常 85–180 Hz」）：
                    # 这类句子整句都是模型自带的参照，删掉不损失任何实测值。
                    # 「录音前半段均值为 100.00 Hz（一般知识…）」这种测量句留着，
                    # 由逐分句中和只摘掉范围断言。
                    rule = 'interpretation.general_threshold'
        if rule:
            issues.append(_issues(rule, sentence))
            # 整句删除时连同它前面的空白/项目符号一起收掉，避免留下空条目。
            if out and not out[-1].strip():
                out.pop()
            continue
        out.append(sentence + delimiter)
    return ''.join(out), issues


def sanitize_interpretation(state: Any, text: Any) -> tuple[str, list[dict[str, Any]]]:
    """把越界分句换成就地边界说明，返回 ``(safe_text, issues)``。

    测量片段（``50.599 dB`` 的均值、``334.57 Hz/s`` 这类）留在原位，被删掉的是结论本身；
    因此交付的正文里不会只剩「免责声明 + 原结论」。整句级别的越界（模型自带阈值、
    条件句里的人群判断）先整句删掉，再做逐分句中和；同一句里同一条规则的
    边界说明只说一次，避免读起来像机器故障。
    没有命中时原样返回 ``(text, [])``。
    """
    body, issues = drop_unsupported_sentences(text)
    safe: list[str] = []
    said: set[str] = set()
    for clause, separator, issue in _scan(state, body):
        if separator and separator in _SENTENCE_END:
            said.clear()
        if issue is None:
            safe.append(clause + separator)
            continue
        issues.append(issue)
        rule = issue['rule']
        if rule in said:
            # 同一句里已经说过这条边界：只收掉分句，说明不再重复。
            safe.append(separator)
            continue
        said.add(rule)
        kept = _measure_fragment(clause)
        note = _NEUTRAL[rule]
        safe.append((kept + '；' + note) if kept else note)
        safe.append(separator)
    result = ''.join(safe)
    return (_tidy(result) if issues else result), issues


def _tidy(text: str) -> str:
    """删分句留下的空档收拾干净：``，，``、句号前的逗号、行首孤立的标点。"""

    text = re.sub(r'([，,；;])(?:\s*[，,；;])+', r'\1', text)
    text = re.sub(r'\s*[，,；;]+\s*(?=[。！？!?])', '', text)
    text = re.sub(r'(?m)^[\s]*[，,；;、]+', '', text)
    return text


def task_tail_guidance(state: Any) -> str:
    """相关录音目标（语调/嗓音、日语重音）才有的一段尾指导；否则 ``''``。

    内容与具体词、具体数值无关：不写死阈值，也不写任何词条的重音型。
    """

    goal = _goal_text(state)
    parts: list[str] = []
    if _PROSODY_SCOPE.search(goal):
        parts.append(_PROSODY_NOTE)
    if _ACCENT_SCOPE.search(goal):
        parts.append(_ACCENT_NOTE)
    return ''.join(parts)
