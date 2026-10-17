"""用户可见文案：正文里不出现阶段代号、规则 ID、内部字段名。

设计见 ``ai/docs/backend-workflow-redesign.zh-CN.md`` 第 5.2 节与 S4。
内部原因（``BranchExit`` 的原文、守卫规则名）只进 ``state.metrics``，
交到用户手里的是「一句人话 + 结构化缺口字段」。

这条规则也顺便定下了 P4 的错误分类口径：所有失败最终落到下面六类之一，
每类只有一句话，便于前端与后续轮次引用。
"""
from __future__ import annotations

import re

#: 六类失败的默认说法。键名与 ``Failure`` 分类一致。
MESSAGES = {
    'budget': '这次的时间或额度用完了，解释还没写完；已保留测得的数值，可以再问一次接着分析。',
    'capability': '这台机器缺少完成这项分析需要的组件，相关的量给不出来；其余测量仍然有效。',
    'transient': '这次和模型服务的连接没成功，已保留测得的数值；可以再问一次。',
    'policy': '当前工作流设置关闭了这一步；需要的话可以在设置里打开。',
    'cancelled': '已取消这次分析，已完成的测量都保留着。',
    'user_fixable': '这次请求的参数没对上，已保留测得的数值；换个说法或指明对象再来一次即可。',
}

#: 从上到下的顺序即优先级：先认「用户主动取消」，再认能力缺失，最后才归到预算。
_PATTERNS = (
    (r'取消|cancelled', 'cancelled'),
    (r'工作流程未启用|已关闭|未启用|工作流', 'policy'),
    (r'缺少|未安装|没有可用|不支持|不可用|没有运行中的|未找到', 'capability'),
    (r'超时|连接|网络|状态不明|无法确认|仍然封锁|暂时', 'transient'),
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


def user_reason(reason: str, *, fallback: str = '') -> str:
    """一句可以直接给用户看的原因；没有任何原因时用 ``fallback``。"""

    kind = classify(reason)
    if not str(reason or '').strip() and fallback:
        return str(fallback)
    return MESSAGES[kind]


def has_internal_terms(text: str) -> bool:
    """正文里是否还留着不该出现的内部术语（用于门禁与测试）。"""

    return bool(INTERNAL.search(str(text or '')))


#: 不该出现在用户正文里的内部术语：阶段代号、规则 ID、字段名、守卫术语。
INTERNAL = re.compile(
    r'\bL[0-4]\b|report\.[a-z_]+|tool\.(?:policy|schema)\.|guard_blocks|guard_repairs|'
    r'route_reason|finish_state|coverage_guidance|交付项状态|自动数值核对|'
    r'阶段时间上限|请求预算|工具预算|BranchExit|ModelRetry')

__all__ = ['DEFAULT_CLASS', 'INTERNAL', 'MESSAGES', 'classify', 'has_internal_terms', 'user_reason']
