"""一轮一次的结构化分类：走对话/概念，还是完整分析。

方案见 ``ai/docs/backend-workflow-redesign.zh-CN.md`` 第 6 节。原则三条：

1. **默认走完整分析**：拿不准就分析，永不因为"看起来像闲聊"而跳过测量；
2. **正则只是提示**：它给不出答案（``None``）时才问模型；它给得出答案且模型开关关闭时，
   行为与旧版完全一致，可随时回滚；
3. **模型调用失败一律回落**：解析失败、超时、没有 runtime 都当 ``analyze``。

调用方（``modern_execution``）每轮只调一次，结果放 ``state.router_dialogue``，
``cloud_agent.entry_mode`` 与对话分派共用同一个判断。
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass

from .dialogue_policy import dialogue_kind, direct_measurement

#: 意图取值；只有前四个会改变路由。
INTENTS = ('measure', 'analyze', 'explain', 'concept', 'advice', 'interface', 'chat')

#: 意图 → 对话分派值（与 ``dialogue_policy.dialogue_kind`` 的返回口径对齐）。
DIALOGUE = {'concept': 'concept', 'advice': 'advice', 'interface': 'conversation',
            'chat': 'conversation'}

#: 低置信度一律按完整分析处理。
CONFIDENCE_FLOOR = 0.6
_TTL_LIMIT = 128


@dataclass(frozen=True)
class RouterIntent:
    intent: str = 'analyze'
    dialogue: str | None = None
    confidence: float = 0.0
    source: str = 'default'          # regex | model | cache | fallback | default
    needs_audio: bool = False
    whole_object: bool = False

    @property
    def analyze(self) -> bool:
        return self.dialogue is None


_CACHE: dict[tuple[str, str], RouterIntent] = {}
_LOCK = threading.Lock()


def clear_cache() -> None:
    with _LOCK:
        _CACHE.clear()


def _key(config, text: str) -> tuple[str, str]:
    section = getattr(config, 'api', None)
    model = f'{getattr(section, "base_url", "")}|{getattr(section, "model", "")}'
    return model, text


def _remember(key, intent: RouterIntent) -> None:
    with _LOCK:
        if len(_CACHE) >= _TTL_LIMIT:
            _CACHE.clear()
        _CACHE[key] = intent


SYSTEM = ('你是语音分析助手的路由分类器。判定用户这一轮要的是什么，只输出 JSON：\n'
          '{"intent":"measure|analyze|explain|concept|advice|interface|chat",'
          '"needs_audio":true|false,"confidence":0..1}\n'
          'measure=单一参数测量；analyze=需要测量并解释（默认）；explain=解释已有测量结果；'
          'concept=纯概念/原理问题，不需要测量当前录音；advice=只要练习建议；'
          'interface=界面/设置怎么操作；chat=寒暄或与语音分析无关的闲聊。\n'
          '只要涉及用户自己的录音、对象、选区或"这段/当前声音"，一律不是 chat。'
          '拿不准就输出 analyze，confidence 写真实把握。')


def _parse(raw: str) -> tuple[str, float, bool]:
    text = str(raw or '').strip()
    fenced = re.search(r'\{.*\}', text, re.S)
    if not fenced:
        raise ValueError('路由分类没有返回 JSON')
    payload = json.loads(fenced.group(0))
    intent = str(payload.get('intent', '')).strip().lower()
    if intent not in INTENTS:
        raise ValueError('路由分类返回了未知意图：' + intent)
    try:
        confidence = float(payload.get('confidence', 0))
    except (TypeError, ValueError):
        confidence = 0.0
    return intent, max(0.0, min(1.0, confidence)), bool(payload.get('needs_audio'))


def classify(config, goal: str, *, context_text: str = '', runtime=None, cancel=None,
             requester=None, state=None) -> RouterIntent:
    """判定这一轮的路由；任何异常都不抛给调用方。"""

    text = str(goal or '').strip()
    hint = dialogue_kind(text)
    workflow = getattr(config, 'workflow', None)
    mode = getattr(workflow, 'router_mode', 'off')
    if hint is not None and mode != 'auto':
        # 关闭模型分类时保持旧行为：正则说什么就是什么。
        return RouterIntent(intent=DIALOGUE.get(hint, 'concept') if hint != 'conversation' else 'chat',
                            dialogue=hint, confidence=1.0, source='regex')
    if direct_measurement(text, statistics=True):
        return RouterIntent(intent='measure', confidence=1.0, source='regex')
    if mode != 'auto' or not text:
        return RouterIntent(intent='analyze', dialogue=hint, source='default')

    key = _key(config, text)
    cached = _CACHE.get(key)
    if cached is not None:
        return RouterIntent(intent=cached.intent, dialogue=cached.dialogue,
                            confidence=cached.confidence, source='cache',
                            needs_audio=cached.needs_audio)

    messages = [dict(role='system', content=SYSTEM),
                dict(role='user', content=json.dumps({'goal': text,
                                                      'context': (context_text or '')[:400]},
                                                     ensure_ascii=False))]
    try:
        if requester is not None:
            raw = requester(config, messages)
        else:
            from .modern_budget import text_request
            from .cloud_agent import create_model
            options = dict(runtime=runtime) if runtime is not None else {}
            raw = text_request(config, messages, cancel=cancel, phase='router', **options)
        intent, confidence, needs_audio = _parse(raw)
    except Exception as error:                       # noqa: BLE001 - 路由失败必须回落
        intent = RouterIntent(intent='analyze', dialogue=hint, source='fallback')
        _note(state, intent, str(error))
        return intent

    dialogue = DIALOGUE.get(intent) if confidence >= CONFIDENCE_FLOOR else None
    result = RouterIntent(intent=intent, dialogue=dialogue, confidence=confidence,
                          source='model', needs_audio=needs_audio)
    _remember(key, result)
    _note(state, result, '')
    return result


def _note(state, intent: RouterIntent, error: str) -> None:
    """把路由决策记进 metrics：没有它，路由错判永远查不出来。"""

    metrics = getattr(state, 'metrics', None)
    if isinstance(metrics, dict):
        metrics['router'] = dict(intent=intent.intent, dialogue=intent.dialogue,
                                 confidence=intent.confidence, source=intent.source,
                                 needs_audio=intent.needs_audio)
        if error:
            metrics['router']['error'] = error[:200]


__all__ = ['CONFIDENCE_FLOOR', 'DIALOGUE', 'INTENTS', 'RouterIntent', 'classify', 'clear_cache']
