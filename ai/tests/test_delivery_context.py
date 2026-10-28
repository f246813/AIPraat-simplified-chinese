"""历史交付修复（离线）：``PhaseContext.record_delivery`` 与两个正式基线入口。

要修的问题：正式回合结束后 ``messages`` 末尾留着模型**草稿**（ModelResponse），而用户
实际拿到的是另一份正文（拼上前缀、被守卫遮蔽、被恢复稿替换……）。``mark_formal`` 只按
history 标 hash，``sync_formal`` 只往后接新尾部，于是下一轮请求和持久化 payload 里
「assistant 说了什么」永远是那份草稿——历史交付被记错，而且一旦锁定就一直错。

边界（刻意写死，超出就什么都不做）：

* 只在 ``messages`` 以**完整纯文本 ModelResponse 结尾**、正文与实际交付不同时补一条
  交付记录（ModelRequest(UserPromptPart) + ModelResponse(TextPart(交付正文))）；
* 不改、不删任何既有消息（旧请求 prefix 原样保留，缓存前缀不失效）；
* 末尾是 pending ModelRequest、含 ToolCallPart/ToolReturnPart 时不补，不伪造工具返回；
* 空消息、空交付正文、已经记过同样正文 → 不重复记录（幂等）；
* 取消/partial 的 assistant 不是正式交付，不许被当成交付正文；
* ``context.complete`` 由宿主管，这里不动。
"""

import copy
import unittest

from pydantic_ai.messages import (ModelRequest, ModelResponse, SystemPromptPart, TextPart,
                                  ToolCallPart, ToolReturnPart, UserPromptPart)

from praat_ai.session_context import PhaseContext

DELIVERED = '实际交付正文：这一段是用户真正看到的回答。'
DRAFT = '草稿正文：模型先写、但被替换掉的那一版。'
NOTE_HINT = '应用交付记录'


def message_request(text):
    return ModelRequest(parts=[UserPromptPart(text)])


def message_response(text):
    return ModelResponse(parts=[TextPart(text)])


def turn(user, assistant):
    return [dict(role='user', content=user), dict(role='assistant', content=assistant)]


def texts(messages):
    return [str(part.content) for message in messages for part in message.parts
            if isinstance(part, TextPart)]


def user_prompts(messages):
    return [str(part.content) for message in messages for part in message.parts
            if isinstance(part, UserPromptPart)]


class RecordDeliveryTests(unittest.TestCase):
    def context(self, *messages):
        return PhaseContext(messages=list(messages))

    def test_identical_delivery_records_nothing(self):
        context = self.context(message_request('问题'), message_response(DELIVERED))
        before = list(context.messages)
        self.assertFalse(context.record_delivery(DELIVERED))
        self.assertEqual(context.messages, before, '正文相同就不该动消息')

    def test_whitespace_only_difference_is_not_a_new_delivery(self):
        context = self.context(message_response(DELIVERED + '\n'))
        self.assertFalse(context.record_delivery(DELIVERED))

    def test_different_delivery_appends_only(self):
        context = self.context(message_request('问题'), message_response(DRAFT))
        before = list(context.messages)
        self.assertTrue(context.record_delivery(DELIVERED))

        self.assertEqual(len(context.messages), len(before) + 2)
        self.assertEqual(context.messages[:len(before)], before, '既有消息必须原样保留')
        record_request, record_response = context.messages[-2:]
        self.assertIsInstance(record_request, ModelRequest)
        self.assertIsInstance(record_response, ModelResponse)
        notes = [str(part.content) for part in record_request.parts if isinstance(part, UserPromptPart)]
        self.assertEqual(len(notes), 1)
        self.assertIn(NOTE_HINT, notes[0])
        self.assertIn('不是最终交付', notes[0])
        self.assertIn('非新用户指令', notes[0])
        self.assertIn('非测量证据', notes[0])
        self.assertEqual(texts([record_response]), [DELIVERED])
        # 草稿还在原处，没有被删掉或改写。
        self.assertEqual(texts([context.messages[1]]), [DRAFT])

    def test_next_turn_keeps_the_old_prefix_intact(self):
        context = self.context(message_request('问题'), message_response(DRAFT))
        original = context.messages[:]
        context.record_delivery(DELIVERED)
        prefix_length = len(original)
        # 下一轮：宿主把新的用户/assistant 回合接在后面。
        context.messages.extend([message_request('追问'), message_response('第二轮回答')])
        self.assertEqual(context.messages[:prefix_length], original)

    def test_empty_inputs_do_nothing(self):
        empty = PhaseContext()
        self.assertFalse(empty.record_delivery(DELIVERED))
        self.assertEqual(empty.messages, [])
        context = self.context(message_request('问题'), message_response(DRAFT))
        before = list(context.messages)
        self.assertFalse(context.record_delivery(''))
        self.assertFalse(context.record_delivery('   '))
        self.assertFalse(context.record_delivery(None))
        self.assertEqual(context.messages, before)

    def test_pending_model_request_is_not_papered_over(self):
        context = self.context(message_request('问题'), message_response(DRAFT),
                               message_request('工具返回后的追问'))
        before = list(context.messages)
        self.assertFalse(context.record_delivery(DELIVERED))
        self.assertEqual(context.messages, before)

    def test_tool_call_and_tool_return_transactions_are_untouched(self):
        call = ModelResponse(parts=[ToolCallPart('measure', {'object': 1}, tool_call_id='call-1')])
        returned = ModelRequest(parts=[ToolReturnPart('measure', '时长 = 1 s', tool_call_id='call-1')])
        context = self.context(message_request('量一下'), call, returned, message_response(DRAFT))
        before = copy.deepcopy(context.messages)
        self.assertTrue(context.record_delivery(DELIVERED))
        self.assertEqual(context.messages[:4], before, '工具事务一个字节都不能动')
        calls = [part.tool_call_id for message in context.messages for part in message.parts
                 if isinstance(part, ToolCallPart)]
        returns = [part.tool_call_id for message in context.messages for part in message.parts
                   if isinstance(part, ToolReturnPart)]
        self.assertEqual(calls, returns)

    def test_response_with_tool_call_is_not_treated_as_complete_text(self):
        pending = ModelResponse(parts=[TextPart(DRAFT), ToolCallPart('pitch', {}, tool_call_id='call-9')])
        context = self.context(message_request('问题'), pending)
        before = list(context.messages)
        self.assertFalse(context.record_delivery(DELIVERED), '工具事务没结束就不能补交付记录')
        self.assertEqual(context.messages, before)

    def test_repeated_recording_is_idempotent(self):
        context = self.context(message_request('问题'), message_response(DRAFT))
        self.assertTrue(context.record_delivery(DELIVERED))
        after_first = list(context.messages)
        self.assertFalse(context.record_delivery(DELIVERED))
        self.assertFalse(context.record_delivery(DELIVERED))
        self.assertEqual(context.messages, after_first)
        self.assertEqual(len([text for text in texts(context.messages) if text == DELIVERED]), 1)

    def test_complete_flag_is_left_to_the_host(self):
        context = PhaseContext(messages=[message_response(DRAFT)], complete=True)
        context.record_delivery(DELIVERED)
        self.assertTrue(context.complete, '交付记录不该改 complete')
        context = PhaseContext(messages=[message_response(DRAFT)], complete=False)
        context.record_delivery(DELIVERED)
        self.assertFalse(context.complete)


class MarkFormalTests(unittest.TestCase):
    def test_marks_hash_and_records_the_real_delivery(self):
        history = turn('问题', DRAFT)
        context = PhaseContext(messages=[message_request('问题'), message_response(DRAFT)])
        context.mark_formal([*history[:-1], dict(role='assistant', content=DELIVERED)])

        self.assertEqual(context.formal_count, 2)
        self.assertEqual(context.formal_hash,
                         PhaseContext._formal_hash([dict(role='user', content='问题'),
                                                    dict(role='assistant', content=DELIVERED)]))
        self.assertIn(DELIVERED, texts(context.messages))
        self.assertIn(NOTE_HINT, ' '.join(user_prompts(context.messages)))

    def test_baseline_hash_does_not_depend_on_the_draft(self):
        clean = PhaseContext(messages=[message_request('问题'), message_response(DELIVERED)])
        dirty = PhaseContext(messages=[message_request('问题'), message_response(DRAFT)])
        history = [dict(role='user', content='问题'), dict(role='assistant', content=DELIVERED)]
        clean.mark_formal(history)
        dirty.mark_formal(history)
        self.assertEqual(clean.formal_hash, dirty.formal_hash)
        self.assertEqual(clean.formal_count, dirty.formal_count)

    def test_cancelled_or_partial_entries_are_not_recorded_as_delivery(self):
        for status in ('cancelled', 'partial', 'failed', 'interrupted', 'running'):
            with self.subTest(status=status):
                context = PhaseContext(messages=[message_request('问题'), message_response(DRAFT)])
                before = list(context.messages)
                context.mark_formal([dict(role='user', content='问题'),
                                     dict(role='assistant', content=DELIVERED, status=status)])
                self.assertEqual(context.messages, before, f'{status} 不是正式交付')
                self.assertEqual(context.formal_hash,
                                 PhaseContext._formal_hash([dict(role='user', content='问题'),
                                                            dict(role='assistant', content=DELIVERED,
                                                                 status=status)]))

    def test_non_assistant_tail_records_nothing(self):
        context = PhaseContext(messages=[message_request('问题'), message_response(DRAFT)])
        before = list(context.messages)
        context.mark_formal([dict(role='user', content='问题')])
        self.assertEqual(context.messages, before)
        self.assertEqual(context.formal_count, 1)


class SyncFormalTests(unittest.TestCase):
    def locked_context(self, *, formal_assistant=DELIVERED, draft=DRAFT):
        """模拟旧历史：hash 已锁在正式交付上，messages 末尾却是草稿。"""
        formal = [dict(role='user', content='问题'), dict(role='assistant', content=formal_assistant)]
        context = PhaseContext(messages=[message_request('问题'), message_response(draft)])
        context.formal_count = len(formal)
        context.formal_hash = PhaseContext._formal_hash(formal)
        return context, formal

    def test_old_locked_hash_repairs_the_rejected_draft(self):
        context, formal = self.locked_context()
        epoch = context.epoch
        prefix = list(context.messages)
        followup = [*formal, dict(role='user', content='追问'), dict(role='assistant', content='第二轮交付')]

        context.sync_formal(followup)

        self.assertEqual(context.epoch, epoch, '哈希一致时不该重建 epoch')
        self.assertEqual(len(context.messages), len(prefix) + 2 + 2)
        self.assertEqual(context.messages[:len(prefix)], prefix, '旧 prefix 必须原样保留')
        self.assertEqual(texts([context.messages[len(prefix) - 1]]), [DRAFT])
        self.assertIn(NOTE_HINT, ' '.join(user_prompts(context.messages)))
        self.assertEqual(context.messages[-2].parts[0].content, '追问')
        self.assertIn('第二轮交付', texts(context.messages))
        self.assertEqual(context.formal_count, len(followup))

    def test_repair_is_idempotent_across_rounds(self):
        context, formal = self.locked_context()
        context.sync_formal(formal)
        after_first = list(context.messages)
        context.sync_formal(formal)
        context.sync_formal(formal)
        self.assertEqual(context.messages, after_first, '同一份历史不能补第二次')
        self.assertEqual(len([text for text in texts(context.messages) if text == DELIVERED]), 1)

    def test_matching_delivery_needs_no_repair(self):
        formal = [dict(role='user', content='问题'), dict(role='assistant', content=DELIVERED)]
        context = PhaseContext(messages=[message_request('问题'), message_response(DELIVERED)])
        context.formal_count = len(formal)
        context.formal_hash = PhaseContext._formal_hash(formal)
        before = list(context.messages)
        context.sync_formal(formal)
        self.assertEqual(context.messages, before)

    def test_cancelled_tail_is_not_used_as_the_delivery(self):
        formal = [dict(role='user', content='问题'), dict(role='assistant', content=DELIVERED)]
        context = PhaseContext(messages=[message_request('问题'), message_response(DRAFT)])
        context.formal_count = len(formal)
        context.formal_hash = PhaseContext._formal_hash(formal)
        before = list(context.messages)
        # 尾部是一条被取消的 assistant：不是正式交付，不能拿它的正文去核对。
        context.sync_formal([*formal, dict(role='user', content='取消那轮'),
                             dict(role='assistant', content='被取消的半截回答', status='cancelled')])
        self.assertEqual(context.messages[:len(before)], before)
        self.assertEqual(texts(context.messages).count('被取消的半截回答'), 0)
        self.assertEqual(texts(context.messages).count(DELIVERED), 1)

    def test_prefix_mismatch_keeps_the_original_rebuild(self):
        context = PhaseContext(messages=[message_request('旧问题'), message_response(DRAFT)])
        context.formal_count = 2
        context.formal_hash = PhaseContext._formal_hash(turn('旧问题', DELIVERED))
        epoch = context.epoch
        rewritten = [dict(role='user', content='换了一个问题'), dict(role='assistant', content='新回答')]

        context.sync_formal(rewritten)

        self.assertEqual(context.epoch, epoch + 1)
        self.assertEqual(context.identity, '')
        self.assertEqual(context.messages, [message_request('换了一个问题'), message_response('新回答')])
        self.assertNotIn(NOTE_HINT, ' '.join(user_prompts(context.messages)))


class PayloadTests(unittest.TestCase):
    def test_payload_and_restore_keep_the_real_delivery(self):
        context = PhaseContext(messages=[message_request('问题'), message_response(DRAFT)])
        context.record_delivery(DELIVERED)
        context.formal_count = 2
        context.formal_hash = PhaseContext._formal_hash(turn('问题', DELIVERED))
        payload = context.payload()

        self.assertIsNotNone(payload)
        wired = [message['parts'][0]['content'] for message in payload['messages']]
        self.assertEqual(wired[1], DRAFT)
        self.assertIn(NOTE_HINT, wired[2])
        self.assertEqual(wired[3], DELIVERED)

        restored = PhaseContext.restore(copy.deepcopy(payload))
        self.assertEqual(restored.messages, context.messages)
        self.assertEqual(texts(restored.messages).count(DELIVERED), 1)
        self.assertEqual(restored.formal_hash, context.formal_hash)
        self.assertEqual(restored.formal_count, context.formal_count)
        # 恢复后的上下文还能接着往下走，且不会再补一次。
        self.assertFalse(restored.record_delivery(DELIVERED))

    def test_system_prompt_message_survives_a_repair(self):
        context = PhaseContext(messages=[ModelRequest(parts=[SystemPromptPart('固定规则')]),
                                        message_request('问题'), message_response(DRAFT)])
        context.record_delivery(DELIVERED)
        self.assertIn('固定规则', str(context.messages[0]))
        self.assertEqual(texts(context.messages)[-1], DELIVERED)


if __name__ == '__main__':
    unittest.main()
