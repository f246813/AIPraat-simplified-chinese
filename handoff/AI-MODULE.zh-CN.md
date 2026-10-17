# AI 模块维护参考

## 启动与数据

`ai/run_ai_chat.py` 调用 `praat_ai.modern_host.main`，默认打开 React / assistant-ui 与 pywebview / WebView2 工作台；`--legacy` 打开 Tk 窗口。Praat 菜单通过 `run_ai_control.py` 启动前端或 API 配置窗口。Python 文件变更后需要关闭前端再启动。

现代工作台默认数据目录为 `ai/runtime/modern`，对话库为其中的 `sessions.sqlite3`。`ai/runtime/conversations.sqlite3` 作为只读历史来源；新库保存对话、证据、任务活动与上下文。恢复会话不代表重放 Praat 操作或恢复正在运行的脚本。

## 模块与接口

| 模块 | 当前职责 |
| --- | --- |
| `modern_host.py` / `desktop_launch.py` | 本地资产服务、WebView 窗口、原生菜单启动记录与错误提示 |
| `modern_app.py` / `modern_settings.py` | 任务和会话服务、RPC 白名单、配置、附件与本地服务租约 |
| `modern_execution.py` | UI 无关执行适配，绑定 Praat 进程身份，串行投递 |
| `cloud_agent.py` / `cloud_runtime.py` | API 对话、工具与报告阶段，共用供应商连接和取消流程 |
| `session_context.py` / `modern_budget.py` | 阶段历史、上下文估算、摘要与持久化 |
| `tool_guards.py` / `report_guards.py` | 执行参数、对象、范围、脚本、报告数值与引用检查 |
| `escape_policy.py` / `delivery.py` | 请求和工具预算、分支状态、投递事实、守卫计数 |
| `chat.py` / `sendpraat.py` | 本地规划循环、Praat 消息文件、请求编号和结果读取 |
| `tools.py` / `measures.py` / `measures.tsv` | 工具 schema、脚本模板与声学参数表 |
| `materials.py` / `external_script.py` | 分析材料快照与社区脚本批处理 |

API 分析各阶段共用每轮 12 次模型请求和 20 次工具尝试；失败分支和停滞计数限制继续尝试。执行前被拒的提案通过有限纠正处理，与真正运行失败区分。已有同目标、同范围的成功证据可复用。报告守卫可以补引用、整理交付覆盖项或标注无法核对的推导；无法通过数值核对的草稿会遮蔽相应数值并附原因。自然语言解释仍需使用者判断。

本地 Qwen 的 `chat.run_turn` 使用独立的有界规划循环，默认最多 3 轮、5 个动作；这些上限不能当作 API 阶段预算。

## 权限与实现限制

- 正常桌面入口在用户提交任务时允许已启用 API 配置的远程调用；其他宿主可用 `--allow-cloud` 提供进程级授权。地址检查在 `modern_app.py` / `modern_settings.py`。查看配置和打开窗口本身不会开始分析。
- API key 从本地配置或 `PRAAT_AI_API_KEY` 读取；状态、日志和错误展示应使用脱敏接口。
- Windows 投递使用 `%APPDATA%\Praat\Message.txt` 与 `WM_APP`。现代执行器的 `PRAAT_LOCK` 串行管理进程内操作；`chat._send_script` 和 Anki 投递通过 `sendpraat.command_lock()` 共用跨进程锁。未使用该接口的外部写入者不受保护。
- 已投递但未确认完成属于执行状态不明，现代执行器阻止普通操作，允许向原进程定向核对；完整快照与进程检查成功后解除阻断。核对失败不得使用旧快照或盲目重发。取消等待不能中止 GUI Praat 中已运行的脚本。
- 自定义脚本接受语法、对象引用和命令检查；这不是操作系统沙箱。社区脚本在独立 Praat 批处理进程中运行，看不到当前 GUI 对象或编辑器选区。
- 写入消息文件失败是投递环境限制，不能解释为模型缺少音频能力。Windows 文件标签或目录权限依安装环境而定，排障时核查实际进程和目标文件权限。
- 临时 WAV 最多 120 秒、20 MiB，特定供应商另有编码体积限制；精确声学数值来自专业测量，直接音频输入只提供定性观察。

## 文档入口

- [使用与配置](../ai/README.zh-CN.md)
- [插件安装](../ai/plugin/README.zh-CN.md)
- [原生 Hooks 与守卫](../ai/docs/native-hooks.md)
- [后端验证入口](../ai/docs/validation.zh-CN.md)
- [双音频判据与限制](../docs/PRONUNCIATION.zh-CN.md)
- [归一化与材料协议](../docs/NORMALIZATION-MATERIALS.zh-CN.md)
- [模块参考索引](../ai/docs/adr/README.md)
- [现代前端架构](../docs/ai-frontend/CHAT-UI-ARCHITECTURE.zh-CN.md)

检查脚本位于 `ai/tests`，维护命令参考 README。任何运行结果都只证明对应环境和路径，不能据此宣称其他入口已验证。

## Anki 与音频入口

`ai/praat_ai/anki_integration.py` 提供连接配置与插件导出，`anki_bridge.py` 接收卡片音频并向 GUI Praat 定向投递；桌面插件源码位于 `ai/anki_addon`。发布用插件从 Windows 用户偏好定位安装目录和 Python，本机导出插件使用当前配置路径。Anki 与 AI 投递共用跨进程命令锁。

Windows M4A 读取使用 Media Foundation；连接中的 FFmpeg 备用路径仅在明确读取失败后生成临时 WAV，结果未知时不重复投递。使用与配置见 [Anki 连接说明](../ai/Anki连接说明.md) 和 [插件说明](../ai/anki_addon/README.md)。
