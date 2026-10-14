# 仓库介绍页图像

截图日期：2026-10-14，Windows x64。使用当前工作区软件与生产构建资产，没有伪造对话、测量数字或纠音结果。

| 文件 | 来源与捕获方式 |
| --- | --- |
| `ai-workbench.png` | 启动 `ai/run_ai_chat.py`，使用无 API Key 的示例配置与单独的现代会话目录；展开真实会话侧栏。通过实际桌面 WebView2 的 CDP 截取页面。软件按正常行为只读展示旧会话。 |
| `ai-settings.png` | 同一工作台中打开“设置 → 模型”，通过实际 WebView2 的 CDP 截取页面。 |
| `anki-connection.png` | 启动 `ai/run_anki_integration.py`，通过实际 WebView2 的 CDP 截取连接页面；没有保存设置或发送音频。 |
| `pronunciation-form.png` | 调用生产代码 `show_tutor_form` 打开真实 Tk 表单，以现有 hello 样本作为两个输入对象，通过仓库既有 `capture_png` / PrintWindow 捕获窗口。显示默认旧式比较选项，尚未提交分析。 |
| `frontend-workflow.svg` | 根据现代宿主、执行器、工具与 AI 使用说明绘制，可直接编辑的 SVG。 |
| `pronunciation-workflow.svg` | 根据 `tutor.py`、`pronunciation.py`、音位判据与报告实现绘制，可直接编辑的 SVG。 |
| `backend-workflow.svg` | 根据 `modern_app.py`、`modern_execution.py`、`cloud_agent.py`、`escape_policy.py`、`workflow_policy.py` 和工具／报告守卫绘制。展开 API L0–L4 路由、纠正、证据解释、音频升级、本地 Qwen 分支与结果交付。 |

Computer Use 打开了 Praat，但实时窗口捕获两次超时；最终截图使用上表方法。WebView2 截图为实际桌面窗口中的页面内容，不含原生标题栏。

纠音表单中的“学习者录音”是字段名称，示例实际使用美音／英音 hello 两个样本，不能据此认为其中某个说话人的发音错误。样本出处见工作区 `test-records/ai-tutor-demo-20261013/README.md`；本目录仅分发界面截图，不分发音频。

2026-10-14 修订流程图：为前端回传线与执行线分配独立接口，纠音图的分流与合流分别使用明确节点或独立入口，新增后端阶梯图。所有连线贴合节点边界；渲染检查文字溢出及连线穿字。

后端图来源：[现代宿主](../../../ai/praat_ai/modern_app.py)、[执行适配器](../../../ai/praat_ai/modern_execution.py)、[API 阶梯图实现](../../../ai/praat_ai/cloud_agent.py)、[状态与预算](../../../ai/praat_ai/escape_policy.py)、[工作流程策略](../../../ai/praat_ai/workflow_policy.py)、[工具守卫](../../../ai/praat_ai/tool_guards.py)、[报告守卫](../../../ai/praat_ai/report_guards.py)。这些是运行层级，进入顺序取决于实际目标和配置。
