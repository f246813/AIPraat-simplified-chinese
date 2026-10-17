# 当前实现与维护入口

本文描述仓库源码的当前职责和边界。源码能力、运行中进程、供应商服务及安装包载荷需要分别核实；文档或已有日志不构成当前环境测试通过的证明。

## 桌面工作台

`ai/start_ai_chat.py`、`ai/run_ai_chat.py` 和 `modern_host.py` 启动 React／assistant-ui 与 WebView2 工作台，显式 `--legacy` 使用 Tk。`modern_app.py` 提供白名单 RPC、后台任务和按游标读取的事件；会话数据由 `modern_store.py` 保存，分组、归档、分叉和搜索分别由组织与搜索模块管理。

现代库位于运行目录 `modern/sessions.sqlite3`，`conversations.sqlite3` 作为只读会话来源。恢复或分叉复制记录和证据，不重放 Praat 操作，也不保证任务临时音频仍存在。

详见[前端架构](ai-frontend/CHAT-UI-ARCHITECTURE.zh-CN.md)、[宿主契约](ai-frontend/HOST-CONTRACT.md)与[代码来源](ai-frontend/FRONTEND-SOURCES.md)。

## 对话测量与解释

`dialogue_policy.py` 保守区分明确普通问答和材料任务，不额外调用模型分类；没有材料引用、领域词和请求动作词的短输入（例如随手敲的一个词）也判为一般讨论，不进入工具阶梯。`modern_execution.py` 负责绑定 Praat 进程、对象与范围，并适配已有工具。`tools.py`、`measures.py` 和 `measures.tsv` 提供确定性测量。

云端使用 `cloud_agent.py`、Pydantic AI 和 `cloud_runtime.py`：L0 执行工具，L1 有限纠正，L2 解释证据，L3 按设置与用户动作分析原音频，L4 一般讨论。单轮默认预算为 12 次模型请求、20 次工具尝试；流程设置还可限制解释阶段。执行前被拒的提案与实际运行失败分别处理。

本地 Qwen 使用 `chat.run_turn` 的独立规划循环，默认最多 3 轮、5 个动作；现代适配仍通过函数全局命名空间绑定动作回调。它不走云端报告同一套数值与引用守卫，不能把云端验证能力概括为所有模型路径均具备。

`tool_guards.py` 核查对象、范围、工具参数及脚本；`report_guards.py` 核查可识别的数值、单位、量名、引证及部分音调结论。云端核对不通过时可有限修正或保留遮蔽数值的正文；保留草稿时附的核对说明面向用户，不含对模型下达的指令句，也不把未通过核对的数值当实测。已有测量证据、或对象列表里有可测量的声音对象时，「没有音频输入／无法测量」不能作为结论，按 `report.input_claim_conflict` 阻断；报告指令与报告上下文都不把「没有音频」写成可复述的句式，只有在音频确实随请求发出时才声明收到音频。守卫不能证明所有自然语言解释或语言学诊断正确。

详见[AI 模块](../handoff/AI-MODULE.zh-CN.md)、[原生 Hooks](../ai/docs/native-hooks.md)和[接口参考](../ai/docs/adr/README.md)。

## 投递、取消与材料

Windows 默认使用 `%APPDATA%\Praat\Message.txt` 和定向 `WM_APP`，请求编号与开始／完成标记用于核对。`sendpraat.command_lock()` 为使用该接口的调用方提供跨进程锁；现代执行器另有进程内 `PRAAT_LOCK`。未使用共享锁的外部脚本不受保护。

投递事实区分未发送、已发送、结果未知和阻断。现代执行器在未知状态下阻止普通操作，允许向原进程发送核对请求；完整快照与进程核对成功后解除阻断。核对失败不能沿用旧快照或盲目重发修改操作。停止等待不能撤销 GUI Praat 已运行的脚本。

`materials.py` 保存任务音频快照及来源。精确测量、转写、音频定性观察和一般知识是不同证据；模型听感不替代专业数值。音频能力的启用、模型名称预设、实测结果及实际可用性分别管理，需用当前端点诊断。

## 上下文与预算

`session_context.PhaseContext` 管理 SDK 消息、正式历史、配置身份和上下文版本；`skill_registry.py` 将任务匹配的技能追加到阶段材料，不注册工具或扩大权限。`modern_budget.py` 管理估算和摘要。摘要不等于新增测量，继续分析仍需保留目标、证据、缺口和投递事实。

运行时按会话复用连接，按配置身份更新客户端。连接复用不等于提示缓存命中。`cloud_metrics.py` 归一化供应商用量，缺失字段保持未知；缓存覆盖率本身不能证明耗时减少。

详见[上下文预算](../ai/docs/adr/ADR-008-context-token-budget.md)和[验证入口](../ai/docs/validation.zh-CN.md)。

## 双音频对比

`ui.py` 收集标准音、学习者录音、目标读音、资源与可选归一化设置；`tutor.py` 执行双方定位、测量、比较、可选模型解释和报告导出。

当前有通用 DTW 和英语／日语实验性音位规则两条路径。它们的状态与限制不同，不能把声学差异、未测得和音素错误混为一谈。未实现独立错音识别或经过校准的发音总分。正式 tutor 的全链路取消、MFA 对齐超时及可选解释的证据守卫尚未统一。

详见[音位判据与当前限制](PRONUNCIATION.zh-CN.md)、[归一化](NORMALIZATION.zh-CN.md)和[真实材料协议](NORMALIZATION-MATERIALS.zh-CN.md)。

## 原生 VOT

`fon/SegmentAcousticAnalysis.cpp`、`SegmentAcousticVOT.h` 和 `praat_Sound.cpp` 提供 VOT 手动边界、候选估计、Sound／LongSound 适配和 TSV 导出。原生手动值按浊音起始减爆破时刻计算，可表示零和负值；候选需要人工确认。

AI `vot` 工具使用独立脚本模板，显式边界拒绝 `voicing <= burst`，LongSound 需要提取为 Sound。它未调用原生 VOT 导出链路。这是适配差异，不能推导出 VOT 在业务上只允许正值。

## Windows 与安装

`installer/src` 提供安装向导、路径配置、运行库检测与启动器；`installer/build.ps1` 打包。支持的 Python、依赖与 FFmpeg 门禁见[安装说明](../installer/README.zh-CN.md)。

`sys/praat_python_workspace.h` 根据 Windows 进程完整性选择工作根目录：低于 Medium 时使用 LocalLow 下的 `Praat/Python`，其他情况使用系统临时目录；`get_process_workspace()` 在其下创建按进程 ID 命名的目录。这不等于所有低完整性文件访问都已可用，消息文件与实际安装产物仍需单独检查。

Tk 的 DPI／输入法适配和现代编辑器组合输入由不同路径处理；实际窗口、菜单、权限及多屏表现须桌面实测，不能由浏览器测试代替。

## 文档与验证约定

- [文档索引](README.md)按主题组织现状；临时开发阶段、AI 修改流水及废弃决策不属于产品文档或代码注释。
- 未核实的实现偏差不升级为业务规则。记录限制时区分源码事实、已复现缺陷和仍待核实的判断。
- [验证入口](../ai/docs/validation.zh-CN.md)区分离线、真实模型、Praat、桌面和安装检查；原始证据按[测试记录说明](../test-records/README.md)保存。
