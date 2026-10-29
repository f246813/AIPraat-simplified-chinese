"""Task-tail delivery guidance; execution and measurement truth remain in their owners.

Following Pi's append-only context boundary, these are labelled user guidance,
not changing system prompts or schemas between goals in the same phase.

已复核的知识锚点登记在模块级只读注册表 REVIEWED_TOPICS 里：
每个主题自带 key / matches / note / rules / fallback / source，正文与判据都是数据。
knowledge_notes / knowledge_issues / knowledge_fallback 只按注册表顺序遍历。

适用范围（本模块只做这些）：
- 只覆盖注册表里逐条登记过的局部主题（当前仅有 'source-filter:1' 与 'japanese-u:1'），
  顺序即优先级：同一轮 goal 命中多个主题时，只挂第一个主题的 note 与 fallback；
- 主题仅在 goal 命中该主题关键词时生效；判据逐句、确定性、可复核，
  只回答「这句话是否与已登记事实冲突」，命中时才给出纠正句或复核参考正文。

不适用范围（不要当成通用事实核验器）：
- 不做通用事实核验，也不是 LLM 裁判：注册表之外的主题（例如英语元音、日语重音）
  必须原样交还模型 —— knowledge_notes 返回 []，knowledge_issues 返回 []，
  knowledge_fallback 返回 ''，既不拦截也不改写模型正文；
- 不核验数值、人群统计、健康或噪声诊断，也不代替测量，不评价论证质量，
  不判断例词是否切题（除非该例词已登记为冲突判据）；
- 判据命中只说明该句需要用复核参考改写，不说明用户目标无法回答。

每条事实的来源写在对应主题的 source 字段；没有可核对 URL 时写明
「依据：基础声学/语言学通行表述」，不写未核实的链接。
"""
from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ReviewedRule:
    """一条冲突判据（数据，不是流程）。

    applies(goal): 这条判据本轮是否启用。
    detect(sentence, negated): sentence 已按句切开，negated 表示该句出现了否定词；
        命中返回面向模型的纠正说明，未命中返回 None。
    """

    applies: Callable[[str], bool]
    detect: Callable[[str, bool], str | None]


@dataclass(frozen=True)
class ReviewedTopic:
    """一个已复核的知识主题。

    key:      稳定 id，作为 guidance 的挂载键（cloud_agent 用 'knowledge:' + key）。
    matches:  判断该主题是否适用于本轮 goal 的可调用对象。
    note:     「一般知识参考」正文（逐字保留）。
    rules:    该主题的冲突判据，按顺序生效。
    fallback: knowledge_fallback 的复核参考正文（逐字保留）。
    source:   该主题事实的来源（URL + 章节，或写明的通行表述依据）。
    """

    key: str
    matches: Callable[[str], bool]
    note: str
    rules: tuple[ReviewedRule, ...]
    fallback: str
    source: str


def _matches_source_filter(goal: str) -> bool:
    """声源—滤波器主题：goal 同时提到 F0/基频与共振峰。"""
    return bool(re.search(r'基频|F0|fundamental', goal, re.I)
                and re.search(r'共振峰|formant', goal, re.I))


def _matches_japanese_u(goal: str) -> bool:
    """日语 /u/ 主题：goal 提到日语，并提到 ɯ、/u/、[u]、う 或元音。"""
    if not re.search(r'日语|日本語|Japanese', goal, re.I):
        return False
    return bool(re.search(r'ɯ|/u/|\[u\]|う|元音|vowel', goal, re.I))


def _detect_f0_as_lowest_harmonic(sentence: str, negated: bool) -> str | None:
    if negated:
        return None
    if (re.search(r'基频|F0', sentence, re.I) and '谐波' in sentence
            and re.search(r'是(?:其中)?(?:频率)?最低的?(?:一条|谐波)', sentence)):
        return 'F0不等于最低可见谐波；基频分量可以缺失，频谱谐波间距仍可对应F0。'
    return None


def _detect_f0_above_harmonics(sentence: str, negated: bool) -> str | None:
    if negated:
        return None
    if re.search(r'F0.{0,30}(?:位于|高于).{0,15}谐波上方', sentence, re.I):
        return '不能将F0显示曲线描述为位于谐波上方；请解释周期频率与声道共振的区别。'
    return None


def _detect_hashi_as_u(sentence: str, negated: bool) -> str | None:
    if 'はし' not in sentence or not re.search(r'ɯ|/u/|う音', sentence):
        return None
    correct_i = re.search(r'/i/|\[i\]|[是为]\s*i\b', sentence)
    if correct_i or negated:
        return None
    return '「はし」的末元音是/i/，不是/u/或[ɯ]；请改用「すき」说明/u/清化。'


def _detect_kata_as_u(sentence: str, negated: bool) -> str | None:
    if 'かた' in sentence and re.search(r'ɯ|/u/', sentence) and not negated:
        return '「かた」只有/a/元音，不能作为/u/的例词。'
    return None


def _detect_articulator_millimetres(sentence: str, negated: bool) -> str | None:
    if (re.search(r'舌|唇|下颌|口腔', sentence)
            and re.search(r'\d+(?:\.\d+)?\s*(?:cm\b|mm\b|厘米|毫米)', sentence, re.I)
            and not negated and not re.search(r'不要|不应|不可', sentence)):
        return '缺少可核对来源，不能给发音器官编造厘米或毫米级位移标准；改为定性说明。'
    return None


def _detect_sushi_suu_minimal_pair(sentence: str, negated: bool) -> str | None:
    if all(word in sentence for word in ('すし', 'すう', '最小对立')) and not negated:
        return '「すし」与「すう」不能证明日语中/u/和/ɯ/的音位对立。'
    return None


def _detect_devoicing_lip_switch(sentence: str, negated: bool) -> str | None:
    """Voicing context alone cannot prescribe a rounded/unrounded lip switch."""
    if re.search(r'不能|并非|不等于|不要|不可|不能简化|不同维度', sentence):
        return None
    context = r'浊境|清境|词首|词末|清化环境|清辅音后|浊辅音前|清化变'
    lips = r'微圆|圆唇|唇.{0,12}(?:扁平|变平|放松)|(?:微圆|扁平).{0,12}唇'
    if re.search(context, sentence) and re.search(lips, sentence):
        return '清浊环境或词首词末不能单独决定圆唇/扁唇切换；清化与唇形须分别解释，删除此动作规则。'
    return None


def _any_reviewed_topic(goal: str) -> bool:
    """只要命中任一已复核主题就为真。

    重构前「はし / かた / 发音器官厘米毫米 / すし+すう」这四条判据只以
    「knowledge_notes(goal) 非空」为前提，并不要求命中日语主题本身；
    这里按原样保留（含对声源—滤波器主题也生效的历史行为），不收紧也不放宽。
    """
    return any(topic.matches(goal) for topic in REVIEWED_TOPICS)


REVIEWED_TOPICS: tuple[ReviewedTopic, ...] = (
    ReviewedTopic(
        key='source-filter:1',
        matches=_matches_source_filter,
        note=(
            '一般声学知识参考：F0是周期声源的重复频率，对应谐波的频率间距；'
            '即使基频分量很弱或缺失，仍可能有基频和对应音高，不能定义为最低可见谱峰。'
            '共振峰是声道的共振频率，反映谱包络形状，并非每条谐波。'
            '软件显示的基频曲线和共振峰标记不等于声源本身，也不存在“F0曲线位于谐波上方”的定义。'
            '解释区别时用同一元音改变音高、同一音高改变元音的例子即可；不添加未经核对的菜单路径或诊断。'
        ),
        rules=(
            ReviewedRule(applies=_matches_source_filter, detect=_detect_f0_as_lowest_harmonic),
            ReviewedRule(applies=_matches_source_filter, detect=_detect_f0_above_harmonics),
        ),
        fallback=(
            '基频（F0）描述周期声源重复得多快，通常与音高有关；共振峰描述声道在哪些频率附近更容易共振，'
            '与元音音质等音色特征有关。两者都用Hz表示，但不是同一种量。\n\n'
            '例如，保持同一个元音唱高一点或低一点，主要改变F0；大致保持同一音高从「啊」换成「衣」，'
            '声道形状变化会改变共振峰。频谱中的谐波间距可对应F0，基频分量即使缺失，也可能仍听到相应音高；'
            '所以不能简单把F0等同于最低可见谱峰。共振峰则体现在频谱包络的共振特征中，不是每一条谐波。'
        ),
        source=('依据：基础声学通行表述（F0＝周期声源的重复频率、谐波间距可对应F0；'
                '共振峰＝声道的共振频率、体现在谱包络而非单条谐波）。'
                '本轮未引用具体 URL。'),
    ),
    ReviewedTopic(
        key='japanese-u:1',
        matches=_matches_japanese_u,
        note=(
            '一般语音学参考，非录音证据：日语「う」的音位常写作/u/，典型音值常记为近后、高元音'
            '[ɯ̟]或带唇压缩的[ɯ̟ᵝ]；具体分析、方言和语境可有差异。'
            '唇压缩不等于像普通话「乌」那样明显向前撮唇，也不等于双唇完全闭合。'
            '抽象IPA的[u]与[ɯ]区别不能被说成日语中两个独立对立音位，'
            '也不能虚构“词首或浊辅音前圆唇、词末或清辅音前不圆唇”的条件切换。'
            '/i,u/的常见清化环境包括清辅音之间、某些清辅音后的句末环境，受重音、语速、方言等影响，'
            '清化与圆唇程度不是同一维度。例子「すき」中的/u/可讨论清化；'
            '「かた」不含/u/，「はし」的末元音是/i/，不能拿它们证明/u/的两种音值或最小对立。'
            '如无可靠例词，可只用「う」说明唇形，提供简短练习，不承诺固定训练周期。'
        ),
        rules=(
            ReviewedRule(applies=_any_reviewed_topic, detect=_detect_hashi_as_u),
            ReviewedRule(applies=_any_reviewed_topic, detect=_detect_kata_as_u),
            ReviewedRule(applies=_any_reviewed_topic, detect=_detect_articulator_millimetres),
            ReviewedRule(applies=_any_reviewed_topic, detect=_detect_sushi_suu_minimal_pair),
            ReviewedRule(applies=_matches_japanese_u, detect=_detect_devoicing_lip_switch),
        ),
        fallback=(
            '日语「う」通常用音位符号 /u/ 表示，实际音值常记作 [ɯ̟] 或带唇压缩的 [ɯ̟ᵝ]。'
            '这不表示日语中有一对 /u/ 与 /ɯ/ 的对立音位。和普通话「乌」相比，'
            '日语「う」通常没有那么明显的向前撮唇；唇部可轻度压缩，但不要把上下唇完全闭合，也不必用力咧嘴。\n\n'
            '练习时先模仿可靠示范音中的「う」，对镜检查是否过度撮唇，再轻声、放松地连到「すき」等词。'
            '「すき」里的 /u/ 在清辅音之间可能清化，是否清化还受重音、语速和方言影响；'
            '清化与圆唇程度是不同的问题，不要为了模仿而强行挤压或憋气。\n\n'
            '以上是一般语音学说明，不是对你个人录音的测量或诊断。'
        ),
        source=('https://assets.cambridge.org/97805216/17543/excerpt/9780521617543_excerpt.pdf '
                '（Vance, The Sounds of Japanese，唇形描述）；'
                'https://pubmed.ncbi.nlm.nih.gov/12077505/ （高元音清化的声门动作）'),
    ),
)


def _matched_topics(goal: str) -> tuple[ReviewedTopic, ...]:
    """本轮 goal 命中的主题，按注册表顺序（＝优先级）。"""
    return tuple(topic for topic in REVIEWED_TOPICS if topic.matches(goal))


def dialogue_guidance(kind: str) -> str:
    if kind not in {'concept', 'advice'}:
        return ''
    return (
        '专业讲解约定：先核查问题前提，区分音标的理想音值、具体语言的实际音值和音位对立；'
        '不能因符号不同就声称同一语言存在两个对立音位。'
        '例词须确实体现所讲区别，不确定时不用最小对立例子。'
        '练习动作须符合发声生理，放松唇部不等于双唇完全闭合；'
        '清化的环境应交代条件，不能把单个清辅音后的元音一概说成清化。'
        '没有可靠来源时，不要给舌、唇或下颌编造毫米/厘米级位移标准，使用定性动作描述。'
        '只展开用户所问的原理或方法，不主动增加个人诊断、训练计划或未经核对的软件菜单步骤。'
    )


def report_guidance() -> str:
    return (
        '交付约定：直接回应original_goal。单项测量给出对象、范围、所求数值与来源即可，'
        '不主动扩展到人群典型值、性别、健康或噪声诊断；用户要求解释、比较、纠音时再展开相关知识。'
        '每个判断须有同类证据，基频稳定不等于无噪声。'
        '来源使用可读的测量名称，不展示工具函数名或内部证据编号；不要反复罗列用户未请求的分析缺口。'
        '筛查诊断用于判断可信度；正文不逐项复述用户未请求的帧数、门限和候选带，必要细节保留在测量结果中。'
        'coverage的item逐字复制deliveries；一个合并目标只写一个item，在explanation内说明各部分完成情况。'
        '没有取得的量或缺失的参照应直接说明，不把待填公式或模型写作要求交给用户。'
    )


def knowledge_notes(goal: str) -> list[tuple[str, str]]:
    """Small reviewed knowledge anchors, distinct from recording observations.

    Only the first matching registry topic is attached, so priority is registry
    order (unchanged from the previous if/elif chain). The factual basis of each
    note lives in its topic's ``source`` field, not in this docstring.
    """
    matched = _matched_topics(goal)
    if not matched:
        return []
    return [(matched[0].key, matched[0].note)]


def knowledge_issues(goal: str, answer: str) -> list[str]:
    """Only known, high-confidence conflicts with reviewed notes; not an LLM judge.

    Walks the registry topic-by-topic, rule-by-rule over each sentence of
    ``answer``; order and dedup therefore match the previous hard-coded chain.
    """
    if not _matched_topics(goal):
        return []
    issues: list[str] = []
    for sentence in re.split(r'[。！？\n]', str(answer).replace('**', '')):
        negated = bool(re.search(r'不含|不是|不能|不用于|没有|并非', sentence))
        for topic in REVIEWED_TOPICS:
            for rule in topic.rules:
                if not rule.applies(goal):
                    continue
                issue = rule.detect(sentence, negated)
                if issue:
                    issues.append(issue)
    return list(dict.fromkeys(issues))


def knowledge_fallback(goal: str) -> str:
    """Reviewed reference text of the first matching topic; '' when none matches."""
    matched = _matched_topics(goal)
    return matched[0].fallback if matched else ''
