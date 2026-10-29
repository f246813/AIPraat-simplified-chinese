"""结构化意图分类辅助函数。

正则、可选模型分类与失败回落使用共同的意图枚举；实际调用由执行入口决定。"""
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
            # 分类只要一小段 JSON：关掉思考档位、把正文额度压到 320 token。否则
            # qwen 系列会把整份额度花在思考上，返回空文本 + finish_reason=length
            # （pydantic-ai 会报「token limit exceeded before any response」）。
            import copy as _copy

            from .modern_budget import text_request
            small = _copy.deepcopy(config)
            small.api.thinking_level = 'off'
            small.api.force_deep_thinking = False
            small.api.plan_max_tokens = min(getattr(small.api, 'plan_max_tokens', 320) or 320, 320)
            options = dict(runtime=runtime) if runtime is not None else {}
            raw = text_request(small, messages, cancel=cancel, phase='router', state=state, **options)
        intent, confidence, needs_audio = _parse(raw)
    except Exception as error:                       # noqa: BLE001 - 路由失败必须回落
        fallback = RouterIntent(intent='analyze', dialogue=hint, source='fallback')
        _note(state, fallback, str(error))
        return fallback

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
