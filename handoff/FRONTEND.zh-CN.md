# 现代前端维护入口

阅读 [架构](../docs/ai-frontend/CHAT-UI-ARCHITECTURE.zh-CN.md)、[宿主与构建](../docs/ai-frontend/CURRENT-HOST.zh-CN.md)、[宿主契约](../docs/ai-frontend/HOST-CONTRACT.md)、[来源说明](../docs/ai-frontend/FRONTEND-SOURCES.md) 与 [安装向导与路径](../docs/ai-frontend/INSTALLATION-PATHS.zh-CN.md)。

| 代码入口 | 职责 |
| --- | --- |
| `ai/frontend/src/App.tsx`、`Chat.tsx`、`store.ts` | 界面、assistant-ui 适配、宿主状态与事件 |
| `ai/praat_ai/modern_host.py` | 资产服务、桌面窗口和父进程生命周期 |
| `ai/praat_ai/modern_app.py` | RPC、任务调度、附件与目标捕获 |
| `modern_store.py`、`modern_organization.py` | 对话、证据、分区和旧库侧旁元数据 |
| `modern_settings.py`、`modern_budget.py` | 配置合并、模型窗口与上下文估算 |
| `modern_execution.py` | 模型工作流与串行 Praat 执行适配 |

维护界面时先查已有依赖及固定 Pi／Codex 来源，明确复制源码、交互参考和项目适配的区别。构建输入及许可证随资产分发。按模块检查当前行为，避免把保存的验收计数当作当前结果。专业纠音链见 [专业模块](../docs/ai-frontend/DESIGN.zh-CN.md)。
