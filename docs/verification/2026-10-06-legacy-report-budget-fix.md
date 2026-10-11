# 2026-10-06 旧窗口报告预算修复

已修复上一轮留存的 `test_general_continue_preflight_matches_actual_dialogue_context` 失败，没有扩大 4096 token 测试窗口或放宽断言。

根因是旧窗口预检使用报告历史摘录，实际报告却由 `phase_context` 再注入完整原生对话；同时报告构造把 `visible_dialogue` 强制设为空，失去原有摘录。这使预检与实际输入不一致。

`ai/praat_ai/cloud_agent.py` 的最小修复：旧窗口报告不再额外注入完整原生对话，沿用 `compact_report_context` 的历史摘录与用户自述；现代前端仍使用完整分阶段原生历史，不重复注入 JSON 对话。没有调整模型、思考档、超时、输出额度、报告闸门或投递权限。

新增回归位于 `ai/tests/test_staircase_review_fixes.py` 和 `ai/tests/test_phase_context.py`：检查历史摘录只携带一次、原历史不被修改，并通过 SDK／本地 HTTP 检查最终请求。原失败案例的 4096 token 初次音频报告、3072–4096 token 范围内预检接受的续接均通过。现代三轮 dialogue／planner／report 的最终 HTTP 前缀、工具事务、缓存用量口径，以及上一轮三个缺陷和取消持久化回归也通过。

验证：先红后绿，最终 **234 项全部通过**。日志为 `_diag/legacy-budget-red.log`、`_diag/legacy-budget-green.log`、`_diag/legacy-budget-related.log`。命令：

```powershell
$env:PYTHONPATH='ai;ai/tests'
$env:NO_PROXY='127.0.0.1,localhost'
python -X utf8 -m unittest test_staircase_review_fixes test_report_chain_regressions test_phase_context test_cloud_protocol test_dialogue_protocol test_cloud_escape test_report_guards test_tool_guards test_delivery_state test_dialogue_stream test_cancel test_report test_modern_execution test_modern_app -q
```

全部验证离线进行；用户数据库读取使用 `mode=ro`，没有真实云端调用或 Praat 操作重放。实现备份和 diff 位于 `backups/legacy-report-budget-20261006/`。

确认无活动任务，发现旧后端 PID 5408 已退出后，通过现有 `start_ai_chat.main()` 入口启动新后端 **PID 1180**。`frontend-ready.json` 已确认 `react-connected`／`ModernExecutor`，启动晚于源文件修改。Praat PID **3056** 身份不变，原会话全部保留；加载前后消息、证据和模型事件逐表 SHA-256 一致。实际配置仍为 qwen3.7-flash／high／120 秒。加载记录：`_diag/legacy-budget-backend.json`。
