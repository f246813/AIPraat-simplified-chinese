"""Conservative local routing, without another model request."""
from __future__ import annotations
import re

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
_DOMAIN = re.compile(r'基频|共振峰|强度|时长|波形|音高|音素|声音|音频|'
                     r'\b(?:pitch|formant|intensity|duration|sound|audio|vot|cpp|jitter|shimmer)\b', re.I)
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

#: 「你都能测哪些声学参数？」这类能力问题：2026-10-07 实测它没有进对话，而是启动了
#: 一整轮分析（L0 工具 → L2 报告），最后以「audio_input 为 false，无法测量」作答。
_CAPABILITY = re.compile(
    r'你(?:都)?能(?:测|做|干|分析|看)(?:哪些|什么|些什么|点啥)?|'
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


def advice_request(goal: str) -> bool:
    """A request for practice methods, rather than another recording operation."""
    if re.search(r'测量|计算|读取|打开|删除|创建|保存|截取|播放|分析|对比|比较|导出|标注|'
                 r'复制|重命名|生成|重采样|翻转|拼接|合并|转换|裁剪|归一化|滤波|'
                 r'\b(?:measure|calculate|read|open|delete|remove|create|save|extract|play|'
                 r'analy[sz]e|compare|export|annotate|copy|rename|generate|resample|invert|join|merge)\b', goal, re.I):
        return False
    topic = re.search(r'练习|训练|改进|改善|\b(?:practice|practise|training|improve)\b', goal, re.I)
    request = re.search(r'如何|怎么|怎样|应当|应该|建议|方法|\b(?:how|should|advice|recommend)\b', goal, re.I)
    return bool(topic and request)


def dialogue_kind(goal: str) -> str | None:
    """Unknown domain/operation requests keep the existing analysis safeguards."""
    if re.search(r'一般知识(?:问题)?', goal):
        concept = re.sub(r'(?:这是)?一般知识(?:问题)?', '', goal)
        concept = re.sub(r'(?:不需要|无需|不要)测量(?:我的|当前|这段)?(?:录音|音频|声音)', '', concept)
        if _CONCEPT.search(concept) and not _MATERIAL.search(concept):
            return 'concept'
    if advice_request(goal):
        return 'advice'
    if not goal.strip() or _MATERIAL.search(goal):
        # 能力问题和界面问题即使带上「当前/这个」这类词，也不该启动测量流程。
        if _CAPABILITY.search(goal) or _INTERFACE.search(goal):
            return 'conversation'
        return None
    if _ORDINARY.fullmatch(goal.strip()):
        return 'conversation'
    if _CAPABILITY.search(goal) or _INTERFACE.search(goal):
        return 'conversation'
    concept = goal.strip().rstrip('。！？?!')
    if _CONCEPT.match(concept) and not _COMPOUND.search(concept):
        return 'concept'
    if _DOMAIN.search(goal):
        return None
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
