"""句子级救援：把「整份丢弃」换成「只摘掉违规的那几句」。

守卫拒绝一份报告时，旧行为是整份草稿作废、退回阶段记录。可实际问题常常只在一两句
（模型把内部规则讲了出来），整份丢掉等于把有用的解释一起扔掉。这里只做一件事：
按句边界切分，逐句用 :func:`praat_ai.delivery_text.find_answer_leaks` 判定，
删掉命中非占位类泄漏的句子，其余原样保留。

判定口径与泄漏检测完全一致，**不新增任何规则**；删完只要还能检出泄漏、或一句都
留不下，就返回空正文，由调用方退回阶段记录——不交付残句，也不交付带泄漏的正文。
"""
from __future__ import annotations

import re

from .delivery_text import LEAK_UNRESOLVED_PLACEHOLDER, find_answer_leaks

#: 句子级救援真的删过句子时追加的一句人话（不回显内部规则名，也不解释触发条件）。
OMITTED_SENTENCES_NOTE = '（有几句不适合直接交付的内容已略去，其余内容保留。）'

#: 摘完只剩这么短，就不值得当成一份解释交付了，交给阶段记录更诚实。
MIN_KEPT_CHARS = 20

_SENTENCE_END = re.compile(r'(?<=[。！？!?；;\n])')


def _has_blocking_leak(text: str) -> bool:
    return any(leak.kind != LEAK_UNRESOLVED_PLACEHOLDER for leak in find_answer_leaks(text))


def strip_leaking_sentences(text: str) -> tuple[str, int]:
    """只摘掉含内部指令复述的句子，其余正文原样保留；返回 ``(正文, 删掉的句子数)``。

    占位式（``{{calc:…}}``）不算删除理由：调用方会把它们替换成占位标记。
    """

    body = str(text or '')
    if not body.strip():
        return '', 0
    kept: list[str] = []
    removed = 0
    for part in _SENTENCE_END.split(body):
        if not part.strip():
            continue
        if _has_blocking_leak(part):
            removed += 1
            continue
        kept.append(part)
    cleaned = ''.join(kept).strip()
    if len(cleaned) < MIN_KEPT_CHARS or _has_blocking_leak(cleaned):
        return '', removed
    return cleaned, removed


def rescue_analysis(text: str) -> tuple[str, int]:
    """给交付层用的入口：摘句；摘过就在末尾补一句人话。返回 ``(正文, 删掉的句子数)``。

    没删任何句子时原样返回（调用方自己决定要不要加核对说明）。
    """

    cleaned, removed = strip_leaking_sentences(text)
    if not cleaned:
        return '', removed
    if removed:
        cleaned = cleaned.rstrip() + '\n\n' + OMITTED_SENTENCES_NOTE
    return cleaned, removed
