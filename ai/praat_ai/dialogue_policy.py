"""Conservative local routing, without another model request."""
from __future__ import annotations
import re

from .measures import PARAMETER_ALIASES

# Operations and references win over greetings and explanatory prefixes.
_MATERIAL = re.compile(
    r'选中|选区|当前|这段|这个|这些|那个|该录音|录音|对象|刚才|上述|上一|继续|它|再做|'
    r'测量|计算|读取|打开|删除|创建|保存|截取|播放|分析|对比|比较|导出|标注|'
    r'复制|重命名|生成|重采样|翻转|拼接|合并|转换|裁剪|归一化|滤波|之前|此前|这里|那里|'
    r'\b(?:Sound|LongSound|Pitch|Formant|Spectrum|Intensity|TextGrid)\s+\d+\b|'
    r'\d+\s*号|#\s*\d+|\d+(?:\.\d+)?\s*(?:秒|\b(?:seconds?|Hz)\b)|'
    r'\d+(?:\.\d+)?\s*(?:到|至|[-–—~～])\s*\d+(?:\.\d+)?|'
    r'[A-Za-z]:[\\/]|\.(?:wav|mp3|flac|textgrid)\b|'
    r'\b(?:selected|current|recording|recordings|measure|measurement of|calculate|'
    r'open|delete|remove|create|save|extract|play|analy[sz]e|compare|export|annotate|'
    r'this|these|that|those|it|its|them|their|continue|previous|above|again|'
    r'rename|resample|copy|duplicate|generate|plot|invert|join|merge|concatenate|clips?|results?|values)\b', re.I)
_CONCEPT = re.compile(r'什么是|如何理解|原理|理论|科普|解释|练习方法|一般来说|'
                      r'\b(?:what (?:is|are)|explain|define|definition|theory|principle)\b', re.I)
_COMPOUND = re.compile(r'[，,；;\n。！？?!&]|顺便|然后|另外|同时|接着|再做|并|和|与|\b(?:and|then|also|after|next)\b', re.I)
#: 参数词表：口语词 + 测量 catalog（``measures.PARAMETER_ALIASES``）里的参数名。
#: 「F0 多少」「HNR 多少」这类简写不写进来就会落进无请求闲聊，所以把 catalog
#: 登记的别名一起编进来；加参数时路由跟着参数表走，不用再改第二份清单。
_EFFECT_CORE = ('基频', '共振峰', '强度', '时长', '波形', '音高', '音素', '声音', '音频',
                '谐噪比', '信噪比', '频谱重心', '重心', '斜率', '抖动', '微扰')
#: catalog 只用长名登记（第一共振峰/谐噪比/倒谱峰突出），这些简写单独补。
_EFFECT_SHORT = ('f0', 'f1', 'f2', 'f3', 'hnr', 'cpps', 'cpp', 'vot',
                 'pitch', 'formant', 'intensity', 'duration', 'sound', 'audio',
                 'jitter', 'shimmer')


def _domain_pattern() -> str:
    """中文词按子串匹配，拉丁词按整词匹配（短缩写不能误伤正常单词）。"""

    cjk, latin = set(_EFFECT_CORE), set(_EFFECT_SHORT)
    for alias in PARAMETER_ALIASES:
        (cjk if re.search(r'[\u4e00-\u9fff]', alias) else latin).add(alias)
    ordered = '|'.join(re.escape(word) for word in sorted(cjk, key=len, reverse=True))
    words = '|'.join(re.escape(word) for word in sorted(latin, key=len, reverse=True))
    return ordered + r'|\b(?:' + words + r')\b'


_DOMAIN = re.compile(_domain_pattern(), re.I)
_GREETING = r'(?:hello|hi|hey|你好|您好|嗨|哈喽|早上好|晚上好|早安|午安|晚安|谢谢|多谢|thanks|thank you)'
_SEPARATOR = r'[\s,，。！!？?、:：]*'
_ORDINARY = re.compile(
    r'(?:'+_GREETING+_SEPARATOR+r')*'
    r'(?:'+_GREETING+r'|what can (?:you|u) do(?: for me)?|'
    r'what (?:are|is) (?:your|ur) (?:capabilities|abilities|functions|name)|'
    r'who are (?:you|u)|how are (?:you|u)|'
    r'你(?:能帮我做什么|能做什么|有哪些功能|有什么功能|是谁|叫什么|是什么模型)|'
    r'介绍(?:一下)?(?:你自己|你的功能)|讲(?:个|一个)(?:笑话|故事)|聊聊天|随便聊聊)'
    +_SEPARATOR, re.I)

#: 「你都能测哪些声学参数？」这类能力问题直接进入对话；不匹配时会启动完整分析，
#: 最后以「无法测量」作答而用户问题并未回答。
#: 量词是必需的：裸「你能测」只作为整句出现（「你能测吗」）才算能力问题，
#: 否则「你能测当前声音的时长吗」会被剥掉「你能测」，剩下的操作就漏进对话。
_CAPABILITY = re.compile(
    r'你(?:都)?能(?:测|做|干|分析|看)(?:哪些|什么|些什么|点啥)|'
    r'你(?:都)?能(?:测|做|干|分析|看)(?:吗|么|嘛)?[？?。.!！\s]*$|'
    r'(?:都)?能测(?:哪些|什么|些什么)|'
    r'有(?:哪些|什么)(?:声学)?(?:参数|功能|能力|测量)|'
    r'支持(?:哪些|什么)(?:参数|功能|测量|分析)|'
    r'可(?:以)?测(?:哪些|什么)|what can you (?:measure|analyse|analyze)|which (?:parameters|measurements)',
    re.I)

#: 界面/设置类请求：聊天窗口不该去做声学分析，应该指着设置页回答。
_INTERFACE = re.compile(
    r'(?:字体|字号|主题|深色|浅色|夜间模式|窗口|界面|侧边栏|快捷键|滚动条|气泡|布局)'
    r'.{0,12}(?:调|改|换|设|变大|变小|大一点|小一点|宽一点|窄一点|打开|在哪|怎么)|'
    r'(?:调|改|换|设)(?:一下)?.{0,8}(?:字体|字号|主题|界面|窗口)|'
    r'(?:打开|进入)(?:设置|偏好|配置)(?:页|页面|窗口)?', re.I)


#: 请求动作词：出现任何一个都说明用户提了要求，不能当成无请求输入。
_REQUEST_ACTION = re.compile(
    r'[测查读看听算画做改调建开删存截播比导标复重生拼合转裁归滤分试修换设记写讲帮请问给出找显]|'
    r'\b(?:please|can you|could you|what|how|why|when|which|where|who|is|are|does|'
    r'measure|calculate|read|show|list|tell|explain|open|delete|create|save|extract|'
    r'play|analy[sz]e|compare|export|annotate|copy|rename|generate|resample|invert|'
    r'join|merge|plot)\b', re.I)

#: 句子里真有个「要操作的东西」：选中/当前/这段、Sound 3、基频、1 秒、路径等。
#: 与 ``_MATERIAL`` 分开写：后者把操作动词也算进去了，看不出还有没有目标。
_TARGET = re.compile(
    r'选中|选区|当前|这段|这个|这些|那个|该录音|录音|对象|刚才|上述|上一|它|'
    r'\b(?:Sound|LongSound|Pitch|Formant|Spectrum|Intensity|TextGrid)\s+\d+\b|'
    r'\d+\s*号|#\s*\d+|'
    r'\d+(?:\.\d+)?\s*(?:秒|毫秒|ms\b|Hz\b|hz\b)|'
    r'[A-Za-z]:[\\/]|\.(?:wav|mp3|flac|textgrid)\b|'
    r'\b(?:selected|current|recording|recordings|this|these|that|those|it|its|them|their)\b|'
    r'(?:' + _DOMAIN.pattern + r')',
    re.I)

#: 真实操作动词（比 ``_ACTION_WORDS`` 宽一点：口语里「测一下」也算操作）。
_OPERATION = re.compile(
    r'测量|测一下|测一测|测|计算|统计|读取|查询|查一下|分析|提取|截取|播放|对比|比较|'
    r'导出|标注|复制|重命名|生成|重采样|翻转|拼接|合并|转换|裁剪|归一化|滤波|'
    r'打开|保存|删除|创建|'
    r'\b(?:measure|calculate|read|analy[sz]e|compare|export|annotate|copy|rename|'
    r'generate|resample|invert|join|merge|plot|extract|play|open|delete|create|save)\b',
    re.I)

#: 明确的录音/对象操作动词；``advice_request`` 用它区分「给建议」和「又要操作」。
_ACTION_WORDS = re.compile(
    r'测量|计算|读取|打开|删除|创建|保存|截取|播放|分析|对比|比较|导出|标注|'
    r'复制|重命名|生成|重采样|翻转|拼接|合并|转换|裁剪|归一化|滤波|'
    r'\b(?:measure|calculate|read|open|delete|remove|create|save|extract|play|'
    r'analy[sz]e|compare|export|annotate|copy|rename|generate|resample|invert|join|merge)\b',
    re.I)

#: 否定式测量/分析声明（「不测录音」「不需要测量我的录音」
#: 「without measuring the recording」）：它是范围说明，不是操作请求。
#: 先摘掉它，后面的材料/操作判断才不会被这句话误触成工具调用。
#: ``别`` 只在不构成「区别/分别/差别/类别/性别/个别/特别/告别」时才是否定词。
_NEGATED_ACTION = re.compile(
    r'(?:不需要|无需|不用|不必|不要|(?<![区分差类性个特告])别|勿|不)(?:再|去|做|进行|用)?'
    r'(?:测量|检测|检查|分析|读取|计算|测)'
    r'(?:一下|一测)?(?:我的|我这段|当前的|当前|这段|这个|这些)?(?:录音|音频|声音|语音|文件|材料)?|'
    r'(?:不需要|无需|不用|不必|不要|没有|没)(?:任何|我的|当前的|这段)?(?:录音|音频|声音|语音|文件|材料)|'
    r'\b(?:without|no need to|not need to|do not|don\'t|never)\s+'
    r'(?:measure|measuring|analy[sz]e|analy[sz]ing|read|reading|check|checking)'
    r'(?:\s+(?:the|my|this|that|current|selected)\s+(?:recording|recordings|audio|sound|file))?',
    re.I)

#: 讲述/讲解类请求：要的是「用你自己的知识讲」，不是读当前录音。
_TEACH = re.compile(
    r'讲|讲解|解释|说明|介绍|教|纠正|纠音|发音|练习|训练|建议|方法|如何|怎么|怎样|'
    r'\b(?:explain|teach|how (?:do|should|can)|practice|practise|pronunciation|advice|tips)\b',
    re.I)

#: 练习/发音话题：这类问题给练习建议（advice），纯解释才走 concept。
_PRACTICE = re.compile(
    r'练习|训练|纠音|纠正|改进|改善|发音|口音|'
    r'\b(?:practice|practise|training|pronunciation|accent)\b', re.I)


def _strip_negated(text: str) -> str:
    """去掉否定式测量声明，只留真正的请求内容。"""

    return _NEGATED_ACTION.sub(' ', text)


def _live_operation(text: str) -> bool:
    """摘掉能力/界面子句后，句子里是否还剩「操作 + 具体目标」。

    「你能测哪些参数？顺便测量当前声音的平均基频」既有能力问句又有真实测量；
    能力问句不能把后面的操作一起吞进无工具对话。
    """

    rest = _INTERFACE.sub(' ', _CAPABILITY.sub(' ', text))
    return bool(_OPERATION.search(rest) and _TARGET.search(rest))


def _capability_only(text: str) -> bool:
    """这一句只是能力/界面问题：没有附带任何真实操作。"""

    return bool((_CAPABILITY.search(text) or _INTERFACE.search(text)) and not _live_operation(text))


def no_executable_request(goal: str) -> bool:
    """输入里没有任何可执行请求（例如随手敲的一个词或一串无意义字符）。

    判定刻意保守：命中材料引用、领域词、概念问句、能力/界面问题、练习建议，
    或者出现任何请求动作词，都返回 ``False`` 走原有分析阶梯。因此真实测量请求
    不会被误判成闲聊；漏判的代价只是这类输入仍按分析处理。
    """

    text = goal.strip()
    if not text or len(text) > 24:
        return False
    if _MATERIAL.search(text) or _DOMAIN.search(text) or _CONCEPT.search(text):
        return False
    if _CAPABILITY.search(text) or _INTERFACE.search(text) or advice_request(text):
        return False
    return not _REQUEST_ACTION.search(text)


def advice_request(goal: str) -> bool:
    """A request for practice methods, rather than another recording operation."""

    text = _strip_negated(goal)
    if _ACTION_WORDS.search(text):
        return False
    topic = re.search(r'练习|训练|改进|改善|\b(?:practice|practise|training|improve)\b', text, re.I)
    request = re.search(r'如何|怎么|怎样|应当|应该|建议|方法|\b(?:how|should|advice|recommend)\b', text, re.I)
    return bool(topic and request)


def dialogue_kind(goal: str) -> str | None:
    """Unknown domain/operation requests keep the existing analysis safeguards."""
    if re.search(r'一般知识(?:问题)?', goal):
        concept = re.sub(r'(?:这是)?一般知识(?:问题)?', '', goal)
        concept = re.sub(r'(?:不需要|无需|不要)测量(?:我的|当前|这段)?(?:录音|音频|声音)', '', concept)
        if _CONCEPT.search(concept) and not _MATERIAL.search(concept):
            return 'concept'
    text = _strip_negated(goal)
    if text != goal and not _ACTION_WORDS.search(text):
        # 整句只是在划范围（「不测录音」）+ 讲解请求：不能因为这句否定去碰工具。
        if _PRACTICE.search(text):
            return 'advice'
        if _TEACH.search(text) or _CONCEPT.search(text):
            return 'concept'
        if not _REQUEST_ACTION.search(text) and not _CAPABILITY.search(text):
            return 'conversation'
    if advice_request(text):
        return 'advice'
    if not text.strip() or _MATERIAL.search(text):
        # 能力问题和界面问题即使带上「当前/这个」这类词，也不该启动测量流程；
        # 但同一句里还有真实操作时操作优先（见 ``_live_operation``）。
        if _capability_only(text):
            return 'conversation'
        return None
    if _ORDINARY.fullmatch(text.strip()):
        return 'conversation'
    if _capability_only(text):
        return 'conversation'
    concept = text.strip().rstrip('。！？?!')
    if _CONCEPT.match(concept) and not _COMPOUND.search(concept):
        return 'concept'
    if _DOMAIN.search(text):
        return None
    if no_executable_request(goal):
        return 'conversation'
    return None


def effective_thinking_level(api, kind: str | None) -> str:
    level = api.thinking_level
    if kind == 'conversation' and level == 'high' and not api.force_deep_thinking:
        return 'off'
    return level


#: 输出总额包含推理额度，按思考档位预留；off 不预留，low/medium 使用对应预算，
#: high 使用保守预留，auto 使用中档预留。此处不改变供应商思考参数。
REASONING_RESERVE_TOKENS: dict[str, int] = {
    'off': 0, 'auto': 4096, 'low': 1024, 'medium': 4096, 'high': 8192,
}


def reasoning_reserve(level: str) -> int:
    """这个思考档位要占掉的输出额度（token）。"""

    from .config import normalize_thinking_level

    return REASONING_RESERVE_TOKENS.get(normalize_thinking_level(level), 0)


def force_high(api) -> bool:
    return api.thinking_level == 'high' and api.force_deep_thinking


def direct_measurement(goal: str, *, statistics=False):
    text = goal.strip().rstrip('。！？?!')
    chinese = r'(?:请|帮我)?(?:测量|查询|读取|告诉我)?(?:当前|选中)(?:声音|音频)(?:的)?(?:总)?(?:时长|长度)'
    english = r'(?:please\s+)?(?:what is|measure|query|get|tell me)\s+(?:the\s+)?(?:total\s+)?duration\s+(?:of\s+)?(?:the\s+)?(?:current|selected)\s+sound'
    if re.fullmatch(chinese+'|'+english, text, re.I):
        return {'tool':'duration', 'arguments':{}}
    if not statistics:
        return None
    # Closed single-statistic contracts; defaults belong to the existing templates.
    for tool, quantity in (('pitch_statistics', r'(?:平均基频|基频统计|平均F0)'),
                           ('intensity_statistics', r'(?:平均强度|强度统计)')):
        pattern = r'(?:请|帮我)?(?:测量|查询|读取|告诉我)?(?:当前|选中)(?:声音|音频)(?:的)?' + quantity
        if re.fullmatch(pattern, text, re.I):
            return {'tool':tool, 'arguments':{}}
    return None
