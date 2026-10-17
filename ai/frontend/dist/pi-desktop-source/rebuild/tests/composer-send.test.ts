import { expect, it } from 'vitest';
import { draftFollowup, FOLLOWUP_DRAFT, followupContinuation } from '../src/composer-send';

it('drafts an editable request instead of sending one', () => {
  // 按钮只草拟一句话放回对话框；这里断言草稿内容本身，没有发送动作可调用。
  expect(Object.keys(FOLLOWUP_DRAFT).sort()).toEqual(['L2', 'L3', 'L4']);
  expect(draftFollowup('s1', 't1', 'L4')).toEqual({sessionId: 's1', taskId: 't1', mode: 'L4', text: '基于这些结果进行通用讨论'});
  for (const mode of ['L2', 'L3', 'L4'] as const) expect(FOLLOWUP_DRAFT[mode].length).toBeGreaterThan(3);
});

it('keeps the continuation only while the draft is sent back unchanged', () => {
  const draft = draftFollowup('s1', 't1', 'L2');
  expect(followupContinuation(draft, 's1', draft.text, 0)).toEqual(draft);
});

it('abandons the continuation once the user edits it, attaches a file, or switches session', () => {
  const draft = draftFollowup('s1', 't1', 'L4');
  // 用户改了字 = 新任务，不再借用上一轮的证据上下文。
  expect(followupContinuation(draft, 's1', draft.text + '\n另外说说怎么练', 0)).toBeNull();
  expect(followupContinuation(draft, 's1', draft.text, 1)).toBeNull();
  expect(followupContinuation(draft, 's2', draft.text, 0)).toBeNull();
  expect(followupContinuation(null, 's1', draft.text, 0)).toBeNull();
});
