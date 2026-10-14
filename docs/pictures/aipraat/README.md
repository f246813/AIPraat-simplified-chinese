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
| `backend-workflow.svg` | 与 `backend-steps.svg` 内容一致，替换原来的后端总览，兼容之前的图像链接。 |
| `backend-overview.svg` | 恢复此前的后端总览图（提交 `6aab0b68`），用于首页总览板块；逐步骤详图继续单独展示。 |
| `backend-steps.svg` | A 图：逐步展开提交、上下文准备、API 阶梯、本地 Qwen 与后处理。 |
| `script-workflow.svg` | B 图：依据 `chat.py`、`tools.py`、`sendpraat.py`、`modern_execution.py` 与 `external_script.py` 展开 GUI 脚本投递及社区脚本批处理。 |
| `alignment-workflow.svg` | C 图：依据 `forced_alignment.py` 展开 MFA 子进程、TextGrid 解析、wav2vec2 前向与 CTC 强制对齐、融合和回退。 |
| `pronunciation-details.svg` | D 图：依据 `tutor.py`、`pronunciation.py`、`audio.py`、音位规则及原生 Python 桥，展开对比纠音完整调用链。 |
| `measurement-workflow.svg` | E 图：依据 `tools.py`、`measures.tsv`、`chat.py` 展开表驱动测量与 AI VOT 的显式计算／自动估计支路。 |

Computer Use 打开了 Praat，但实时窗口捕获两次超时；最终截图使用上表方法。WebView2 截图为实际桌面窗口中的页面内容，不含原生标题栏。

纠音表单中的“学习者录音”是字段名称，示例实际使用美音／英音 hello 两个样本，不能据此认为其中某个说话人的发音错误。样本出处见工作区 `test-records/ai-tutor-demo-20261013/README.md`；本目录仅分发界面截图，不分发音频。

2026-10-14 修订流程图：为前端回传线与执行线分配独立接口，纠音图的分流与合流分别使用明确节点或独立入口，新增后端阶梯图。所有连线贴合节点边界；渲染检查文字溢出及连线穿字。

后端图来源：[现代宿主](../../../ai/praat_ai/modern_app.py)、[执行适配器](../../../ai/praat_ai/modern_execution.py)、[API 阶梯图实现](../../../ai/praat_ai/cloud_agent.py)、[状态与预算](../../../ai/praat_ai/escape_policy.py)、[工作流程策略](../../../ai/praat_ai/workflow_policy.py)、[工具守卫](../../../ai/praat_ai/tool_guards.py)、[报告守卫](../../../ai/praat_ai/report_guards.py)。这些是运行层级，进入顺序取决于实际目标和配置。

逐步骤详图按 v8.0.3 发布实现核对，新增源码来源：[脚本执行适配](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/chat.py)、[工具与脚本构造](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/tools.py)、[sendpraat 投递](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/sendpraat.py)、[社区脚本包装与子进程](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/external_script.py)、[强制对齐](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/forced_alignment.py)、[纠音入口](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/tutor.py)、[双方边界与比较](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/pronunciation.py)、[声学特征](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/audio.py)、[原生 Python 桥](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/sys/praat_python.cpp)。

五张 A–E 详图由 [build-detailed-workflows.mjs](build-detailed-workflows.mjs) 生成，在仓库根目录运行：

```powershell
node docs/pictures/aipraat/build-detailed-workflows.mjs
```

分支间的区别有明确文字及连线：MFA / wav2vec2 用于音素区间对齐；当前 AI VOT 工具使用 `tools.py` 的事件时刻计算或声学估计脚本。两者没有被画成同一个统一服务。比例定位只保留近似时间及证据不足状态。
