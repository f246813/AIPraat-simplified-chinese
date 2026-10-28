---
name: failure
description: 真实失败原因与收尾
version: 1
phases: [planner, report]
source: AIPraat analysis guidance
---
从 blocked_attempts、paused_branches 和 missing_reason 取得具体失败原因，说明受影响的请求及用户可执行的下一步。成功测量照常交付。audio_input=false 表示未向模型发送音频，Praat 测量仍使用桌面声音对象。
