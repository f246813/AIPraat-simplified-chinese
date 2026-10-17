/**
 * 续接草稿的提交语义。
 *
 * 「解释结果 / 分析原材料 / 通用讨论」这类按钮只把一句请求草拟进输入框，绝不代替用户发送。
 * 用户原样发回才算续接；改过文字或加了附件，就是一轮新任务（与 Tk 版 pending_analysis 同义）。
 */
export type FollowupDraft = {sessionId: string; taskId: string; mode: 'L2' | 'L3' | 'L4'; text: string};

export const FOLLOWUP_DRAFT: Record<'L2' | 'L3' | 'L4', string> = {
  L2: '本次解释结果',
  L3: '本次分析原材料',
  L4: '基于这些结果进行通用讨论',
};

export function draftFollowup(sessionId: string, taskId: string, mode: 'L2' | 'L3' | 'L4'): FollowupDraft {
  return {sessionId, taskId, mode, text: FOLLOWUP_DRAFT[mode]};
}

/** 原样发回才返回续接目标；改过文字、加了附件、或换了会话都返回 null（按新任务提交）。 */
export function followupContinuation(
  draft: FollowupDraft | null,
  sessionId: string,
  text: string,
  attachmentCount: number,
): FollowupDraft | null {
  return draft && draft.sessionId === sessionId && draft.text === text && attachmentCount === 0 ? draft : null;
}
