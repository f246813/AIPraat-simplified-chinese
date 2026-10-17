<div align="center">
  <img src="docs/pictures/icon.png" width="96" alt="Praat 简体中文版图标" />
  <h1>AIPraat</h1>
  <p>Praat 简体中文版 + AI 声学分析工作台 + 标准音对比纠音</p>
  <p><a href="https://github.com/f246813/praat-simplified-chinese-ai/releases/latest">下载最新版</a> · <a href="ai/README.zh-CN.md">使用说明</a> · <a href="CREDITS.zh-CN.md">借物表</a></p>
</div>

用自然语言调用 Praat 的测量工具，把标准音与学习者录音放在一起比较，也可以从 Anki 卡片一键打开语图。声学测量由 Praat 和分析程序完成，模型负责规划与解释。

## 基座与上游

本项目基于 [KasumiKitsune/praat-simplified-chinese](https://github.com/KasumiKitsune/praat-simplified-chinese) 的 modern 分支：中文界面、中文手册及现代版原生界面来自该项目。原版 Praat 是 Paul Boersma 与 David Weenink 开发的语音学分析软件：[praat/praat](https://github.com/praat/praat) · [praat.org](https://praat.org)。

<p align="center">
  <img src="docs/pictures/praat-zh-modern-ui.png" width="55%" alt="基座项目的 Praat 中文现代版主窗口与右键菜单" />
  <img src="docs/pictures/praat-zh-manuel.png" width="40%" alt="基座项目的中文帮助手册" />
</p>

上面两张图片沿用基座仓库。本项目由 f246813 维护，与基座及上游没有隶属关系。

## 相比基座，增加了什么？

| 功能 | 可以做什么 |
| --- | --- |
| **AI 工作台** | 使用本地模型或云端 API 对话；支持附件、会话搜索、分组、归档、分叉，以及上下文用量与摘要管理。 |
| **自然语言操作 Praat** | 读取当前 Sound 对象与编辑器选区，查询基频、强度、共振峰、VOT、嗓音与频谱参数；支持 TextGrid 标注、声音编辑、导出及社区脚本。 |
| **标准音对比纠音** | 输入标准音、学习者录音与目标音位，经过对齐和声学对比，生成 JSON、TextGrid 与叠加图。英语（美式）和日语（标准语）可选实验性音位判据。 |
| **Anki 连接** | 在有音频的卡片上添加“查看语图”按钮，也可复用放大镜入口；多音频卡片支持选择声音，支持指定起止时间字段。 |
| **环境配置与音频读取** | 提供 Python、运行库、本地模型、对齐资源和 FFmpeg 的配置入口。音频先尝试原生读取，失败后使用 FFmpeg 备用解码。 |

例如，在声音编辑器中选中一段音频后输入：**“测量这段的平均基频和 F1、F2，并解释结果。”** 工作台会调用工具取得测量结果，再组织说明。工具支持的范围和参数见[使用说明](ai/README.zh-CN.md)。

## 实际界面

### AI 工作台

在同一个窗口里管理会话、输入分析目标、添加材料并查看结果。

![AIPraat AI 工作台：会话侧栏、任务入口和富文本输入框](docs/pictures/aipraat/ai-workbench.png)

### 模型设置

可配置本地模型或云端 API，调整上下文、思考与工作流程；文字连接与音频能力分别验证。

![模型设置：云端 API 与本地模型配置入口](docs/pictures/aipraat/ai-settings.png)

### 对比纠音与 Anki

纠音表单选择标准音与学习者录音，填写目标音位；Anki 连接页提供卡片入口与插件导出。

![新版纠音表单：录音对象、发音目标与分析摘要](docs/pictures/aipraat/pronunciation-form.png)

![新版 Anki 连接页：卡片入口、插件导出和音频定位字段](docs/pictures/aipraat/anki-connection.png)

截图更新于 2026-10-17，取自新版生产前端与真实 WebView2 宿主，使用独立配置和示例输入，未发送模型请求或提交纠音分析。截图来源与捕获方式见[图像说明](docs/pictures/aipraat/README.md)。

## AI 对话后端流程总览

![AI 对话后端总览：桌面宿主、任务路由、L0–L4、本地 Qwen 与结果交付](docs/pictures/aipraat/backend-overview.svg)

### AI 对话窗口的后端

下面展开桌面宿主、任务路由和云端 API 的 **L0–L4 阶梯**：L0 执行专用工具，L1 进行有限纠正，L2 依据证据解释目标，L3 在显式请求或自动升级条件满足时分析原音频，L4 处理一般讨论。图中同时标出本地 Qwen 的独立路径、共享预算、Praat 投递、结果持久化和继续分析。

下面直接展示 **A 图：38 个编号步骤**，从前端提交、上下文准备到 L0–L4、本地 Qwen、结果保存与继续分析。各层按目标与工作流程策略进入；L1 纠正成功返回 L0，L3 音频不可用时返回 L2 使用已有证据。点击图片可查看完整尺寸。

![A：AI 对话后端的 38 个编号步骤，包括提交、上下文、L0–L4、本地 Qwen 与结果保存](docs/pictures/aipraat/backend-steps.svg)

### 逐步骤调用详图

上面的 A 图与下面的 B–E 图展开实际函数调用、脚本与文件交接、模型请求、对齐后端和报告生成。每个节点都有编号，跨图调用在节点中注明；所有详图直接展示，点击图片可查看完整尺寸。

#### B · 脚本调用：当前 GUI Praat 与社区脚本批处理

展开脚本生成、请求编号、Message.txt、WM_APP、started / finished 标记、结果回读与状态核对。社区脚本另画出 form 默认参数、Sound / TextGrid 副本导出、wrapper.praat、独立进程、超时与输出文件回收。

![工具脚本生成、GUI 投递、社区脚本批处理与结果回读](docs/pictures/aipraat/script-workflow.svg)

#### C · MFA / wav2vec2：从输入音频到逐音素时间边界

MFA 展开临时语料、词典、命令构建、子进程、TextGrid 解析与音位匹配；wav2vec2 展开模型加载、重采样、logits、音位 token、CTC 动态规划与回溯。成功结果融合、单后端结果和比例定位回退分别连线。

![MFA 与 wav2vec2 强制对齐逐步骤调用及结果融合](docs/pictures/aipraat/alignment-workflow.svg)

#### D · 标准音对比纠音：菜单、Python 桥、双方对齐、声学特征与报告回导

展开双方边界的选择、分帧、NumPy 频谱特征、Parselmouth 特征、新音位判据与旧式 DTW 比较，以及可选文字解释、JSON / TextGrid / PNG、视觉解释和 Praat 回导。MFA / wav2vec2 的调用发生在本流程取得音素区间时。

![标准音对比纠音从 Praat 菜单到报告回导的完整步骤](docs/pictures/aipraat/pronunciation-details.svg)

#### E · 实际测量：表驱动 measure 与 AI VOT 工具

展开 measures.tsv 的查询与派生对象依赖，以及 VOT 的显式时刻计算／声学估计两个分支。当前 AI VOT 工具在 Praat 脚本中计算或估计事件边界；这条工具路径没有调用 MFA / wav2vec2。

![表驱动声学测量与 VOT 显式计算、自动估计步骤](docs/pictures/aipraat/measurement-workflow.svg)

## AI 对比纠音怎样工作？

标准音与学习者录音先定位音素区间，再提取适用声学特征。旧式路径使用曲线对齐与偏差阈值；新音位判据按音类生成声学或相对时长观察，并记录证据状态。基础报告无需 LLM，Qwen 文字／视觉解释是可选步骤。

![AI 对比纠音流程：双音频、区间对齐、特征提取、两种比较路径、报告与可选解释](docs/pictures/aipraat/pronunciation-workflow.svg)

**当前范围：** 英语、日语音位判据提供实验性声学观察；可使用手动 IPA、配套 MFA 读音生成或高级请求提供的双方边界。比例定位、低质量区间和缺失测量不支持精确音位结论；通用 DTW 路径的缺测覆盖仍有限制。尚无独立错音识别或经过校准的发音总分。见[判据与限制](docs/PRONUNCIATION.zh-CN.md)、[归一化](docs/NORMALIZATION.zh-CN.md)和[真实材料协议](docs/NORMALIZATION-MATERIALS.zh-CN.md)。

## 下载与开始使用

1. 从 [最新 Release](https://github.com/f246813/praat-simplified-chinese-ai/releases/latest) 下载 **AIPraat-install.exe**，按安装引导配置 Python 与运行库。
2. 打开 Praat，在“前端 → 启动前端”进入工作台；按需要配置本地模型或云端 API。
3. 使用 Anki 时，同页下载 **AIPraat.ankiaddon**，在 Anki 的“工具 → 插件 → 从文件安装”中安装并重启。先启动一次 AIPraat 并完成 Python 配置，插件即可定位软件。

[AI 使用说明](ai/README.zh-CN.md) · [Anki 连接说明](ai/Anki连接说明.md) · [安装与打包](installer/README.zh-CN.md)

## 借物与开发资料

感谢基座、Praat 上游，以及社区脚本、AI 框架、前端组件、对齐模型与音位特征库的作者。完整用途、复用范围和许可见 **[借物表](CREDITS.zh-CN.md)**；资源包来源见云端仓库的[发音词典与模型参考](https://github.com/f246813/praat-simplified-chinese-ai/blob/modern/%E5%8F%91%E9%9F%B3%E8%AF%8D%E5%85%B8%E4%B8%8E%E6%A8%A1%E5%9E%8B%E5%8F%82%E8%80%83/%E6%9D%A5%E6%BA%90%E5%8F%8A%E7%BD%91%E5%9D%80.md)。

[项目文档](docs/README.md) · [当前实现](docs/CURRENT-IMPLEMENTATION.zh-CN.md) · [前端源码来源](docs/ai-frontend/FRONTEND-SOURCES.md) · [后端接口](ai/docs/adr/README.md) · [维护说明](guide.md) · [验证入口](ai/docs/validation.zh-CN.md) · [单平台构建](HOW_TO_BUILD_ONE.md)

代码许可为 [GPL-3.0-or-later](LICENSE)；文档、图片与第三方资源的许可分别见 [docs/LICENSE.txt](docs/LICENSE.txt)、借物表及各资源目录。
