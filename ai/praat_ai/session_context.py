"""Thin public Pydantic AI message adapter (Pi's append-only context boundary).

Reference: earendil-works/pi agent-loop.ts @28dcce2 (MIT). Protocol cleaning,
serialization and tool pairing are supplied by the locked SDK, not reimplemented.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass, field
from pydantic_ai.messages import (ModelMessagesTypeAdapter, ModelRequest, ModelResponse,
                                  SystemPromptPart, TextPart, ToolCallPart, UserPromptPart)


def native_history(history):
    result = []
    for item in history:
        text = str(item.get('content') or '')
        if item.get('status') in {'running', 'cancelled', 'interrupted', 'failed', 'partial'}:
            continue
        if item.get('role') == 'user':
            result.append(ModelRequest(parts=[UserPromptPart(text)]))
        elif item.get('role') == 'assistant':
            result.append(ModelResponse(parts=[TextPart(text)]))
        elif item.get('role') == 'system':
            result.append(ModelRequest(parts=[SystemPromptPart(text)]))
    return result


#: 交付记录里的一句明确说明：它是应用写的，不是用户新指令，也不是测量证据。
DELIVERY_NOTE = ('应用交付记录：此前草稿不是最终交付，以下 assistant 消息是实际交付正文；'
                 '非新用户指令、非测量证据。')

#: :func:`native_history` 会跳过这些状态：它们不是「已经交付的正式回合」。
NON_FORMAL_STATUS = frozenset({'running', 'cancelled', 'interrupted', 'failed', 'partial'})


def formal_delivery(history):
    """``history`` 末项若是正式 assistant 回合，返回它的正文；否则返回空串。

    判定与 :func:`native_history` 保持一致：只有会被适配成 ModelResponse 的那一条
    才算正式交付，取消/partial/失败/未完成的半截回答一律不算。
    """

    if not history:
        return ''
    last = history[-1]
    if not isinstance(last, dict) or last.get('role') != 'assistant':
        return ''
    if last.get('status') in NON_FORMAL_STATUS:
        return ''
    return str(last.get('content') or '')


@dataclass
class PhaseContext:
    messages: list = field(default_factory=list)
    epoch: int = 0
    identity: str = ''
    loaded: set[str] = field(default_factory=set)
    projected: dict = field(default_factory=dict)
    formal_count: int = 0
    formal_hash: str = ''
    complete: bool = False
    archives: list = field(default_factory=list)
    calibration_start: int = 0

    def record_delivery(self, text):
        """把「用户实际看到的正文」追加进上下文；只在必要时追加，返回是否追加。

        修的是历史交付：模型最后一条消息是草稿（被前缀拼接、守卫遮蔽或恢复稿替换），
        而 history 里记的是另一份正文。这里只**追加**一条交付记录（应用说明 +
        assistant 正文），既有消息一个字都不动——旧请求的 prefix、缓存前缀和工具事务
        原样保留。末尾不是完整的纯文本回复（pending ModelRequest、含工具调用/工具返回、
        空消息）、交付正文为空、或已经记过同样正文时什么都不做。
        """

        delivered = str(text or '')
        if not delivered.strip() or not self.messages:
            return False
        last = self.messages[-1]
        if not isinstance(last, ModelResponse):
            return False
        parts = list(last.parts)
        texts = [part for part in parts if isinstance(part, TextPart)]
        if not texts or any(isinstance(part, ToolCallPart) for part in parts) or len(texts) != len(parts):
            # 不是「完整的纯文本回复」：工具事务可能还没结束，不能在这里补话说。
            return False
        if ''.join(str(part.content) for part in texts).strip() == delivered.strip():
            return False
        self.messages.append(ModelRequest(parts=[UserPromptPart(DELIVERY_NOTE)]))
        self.messages.append(ModelResponse(parts=[TextPart(delivered)]))
        return True

    def mark_formal(self, history):
        # 先核对交付：正式回合的正文可能和 messages 末尾的草稿不同，先补上再标基线。
        self.record_delivery(formal_delivery(history))
        self.formal_count = len(history)
        self.formal_hash = self._formal_hash(history)

    @staticmethod
    def _formal_hash(history):
        return hashlib.sha256(json.dumps([{k: i[k] for k in ('role', 'content')} for i in history],
                                         ensure_ascii=False, sort_keys=True).encode()).hexdigest()

    def sync_formal(self, history):
        if not self.formal_hash:
            if not self.messages:
                self.messages = native_history(history)
        elif self._formal_hash(history[:self.formal_count]) == self.formal_hash:
            # 旧前缀已经锁定：先修它末条的交付，再接新尾部——旧已锁错的历史在这里自愈。
            self.record_delivery(formal_delivery(history[:self.formal_count]))
            self.messages.extend(native_history(history[self.formal_count:]))
        else:
            self.messages = native_history(history)
            self.identity = ''
            self.epoch += 1
        self.mark_formal(history)

    def configure(self, system, identity='', baseline=None):
        key = hashlib.sha256((system + identity).encode()).hexdigest()
        if self.identity and self.identity != key:
            # Configuration changes use a formal baseline, never prior tools.
            self.messages = native_history(baseline or [])
            self.loaded.clear()
            self.projected.clear()
            self.epoch += 1
        self.identity = key
        if not any(isinstance(p, SystemPromptPart) for m in self.messages for p in m.parts):
            self.messages.insert(0, ModelRequest(parts=[SystemPromptPart(system)]))

    def append_guidance(self, key, text):
        if key not in self.loaded:
            self.messages.append(ModelRequest(parts=[UserPromptPart(text)]))
            self.loaded.add(key)

    def delta(self, values):
        """Append changed metadata only; original evidence remains in TaskState."""
        changed = {k: v for k, v in values.items() if self.projected.get(k) != v}
        for key in ('evidence', 'audio_observations'):
            previous, current = self.projected.get(key, []), values.get(key, [])
            if key in changed and previous and current[:len(previous)] == previous:
                changed[key] = current[len(previous):]
        self.projected.update(copy.deepcopy(values))
        return changed

    def payload(self):
        # Audio/attachments remain task-local: no expired bytes in SQLite/replay.
        if any(isinstance(p, UserPromptPart) and not isinstance(p.content, str)
               for m in self.messages for p in m.parts):
            return None
        return dict(protocol='pydantic-ai-2.52.0', epoch=self.epoch, identity=self.identity,
                    formal_count=self.formal_count, formal_hash=self.formal_hash,
                    calibration_start=self.calibration_start,
                    archives=[ModelMessagesTypeAdapter.dump_python(a, mode='json') for a in self.archives],
                    messages=ModelMessagesTypeAdapter.dump_python(self.messages, mode='json'))

    @classmethod
    def restore(cls, payload):
        if payload.get('protocol') != 'pydantic-ai-2.52.0':
            return None
        return cls(messages=ModelMessagesTypeAdapter.validate_python(payload['messages']),
                   epoch=payload['epoch'], identity=payload['identity'],
                   formal_count=payload.get('formal_count', 0), formal_hash=payload.get('formal_hash', ''),
                   calibration_start=payload.get('calibration_start', 0),
                   archives=[ModelMessagesTypeAdapter.validate_python(a) for a in payload.get('archives', [])])


def evidence_projection(records):
    """Full measurement values/provenance, compact delivery facts; no metrics/UI/scripts."""
    result, seen = [], set()
    for record in records:
        evidence = []
        for item in record.get('evidence', []):
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
            if key not in seen:
                seen.add(key)
                evidence.append(item)
        attempts = [{k: item[k] for k in ('tool','arguments','status','execution','reason') if k in item}
                    for item in record.get('attempts', [])]
        # Empty dialogue turns contribute no new professional evidence.
        if evidence or attempts:
            result.append(dict(target=record.get('target', ''), evidence=evidence, attempts=attempts))
    return result
