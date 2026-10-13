# 参考、借用与依赖

这个仓库是 **Praat 简体中文版 + 本地 AI 前端**，不是从零写的：主体来自下面的基座
与上游项目，另有一批「借来用」的代码库。这里列出它们各自的用途、许可

*开发者手写的经验之谈：Vibecoding一定不要让AI从零开始干，只会堆出屎山，不管干什么都应该优先让AI去扒开源代码库，然后像搭积木一样拼起来，AI只负责粘胶水，提示词和文档里应当明确约束：尽可能搬运已有代码库，不要从零开始写*

## 基座与上游（必须保留的署名）

| 项目 | 用途 | 许可 |
| --- | --- | --- |
| [KasumiKitsune/praat-simplified-chinese](https://github.com/KasumiKitsune/praat-simplified-chinese) | **本仓库的基座**：Praat 7.0.02 的简体中文汉化（界面/手册/打包），`modern` 分支的现代版界面 | 跟随上游 Praat |
| [praat/praat](https://github.com/praat/praat)（[praat.org](https://praat.org)、[praat.github.io](https://praat.github.io)） | 上游软件本体，作者 **Paul Boersma & David Weenink** | GPL-3.0-or-later（见 [LICENSE](LICENSE)），文档/图片部分另有 CC BY-SA 4.0 等，完整清单在 [`docs/LICENSE.txt`](docs/LICENSE.txt) |

本副本由 `f246813` 发布，**与上游（praat/praat）和基座仓库没有隶属关系**；基座里
原有的署名、许可和链接一律保留。@kasumitsune贡献的开源汉化版主页仍在
[kasumikitsune.github.io/praat-simplified-chinese](https://kasumikitsune.github.io/praat-simplified-chinese/)。

## 借用了代码或设计的代码库

| 项目 | 借了什么 | 落在哪里 |
| --- | --- | --- |
| [chengafni/praat](https://github.com/chengafni/praat) | 声学测量脚本的公式与做法：`plugin_CompleteAnalysis`（表驱动：`objects.txt`/`queries.txt`/`settings.txt`）、`plugin_SpectralEmphasis`、`plugin_HL`、`plugin_HammarbergIndex`、`plugin_PA`、`plugin_IntensitySlope`、`plugin_PitchPeakLatency` 的原理与原脚本 | `ai/praat_ai/tools.py` 的测量模板、`ai/praat_ai/measures.tsv`（表结构参考）、`ai/plugin/`（原生插件的 `setup.praat` + `Add menu command` 做法），说明见 [ADR-005](ai/docs/adr/ADR-005-table-driven-measurements.md)、guide §8 |
| [QwenLM/Qwen-Agent](https://github.com/QwenLM/Qwen-Agent) | agent 循环的设计参考：工具自带 JSON Schema → 交给服务端原生 function calling → 把结果作为 function/tool 消息回灌 → 再规划；工具报错当「可恢复的观察」；输入按 token 预算截断 | `ai/praat_ai/qwen.py`、`ai/praat_ai/chat.py`（见 [ADR-003](ai/docs/adr/ADR-003-planning-interface.md)、[ADR-004](ai/docs/adr/ADR-004-agent-loop.md)、[ADR-008](ai/docs/adr/ADR-008-context-token-budget.md)） |
| [Ron-312/PraatPlugin](https://github.com/Ron-312/PraatPlugin) | 调用层的做法参考：Praat 安装位置的定位（对应 `PraatInstallationLocator`）、脚本投递与结果契约的取舍 | `ai/praat_ai/praat_app.py`（找 Praat）、`ai/docs/adr/ADR-001/002`（当前桥接契约说明） |
| [mdbootstrap/TW-Elements](https://github.com/mdbootstrap/TW-Elements)（MIT） | **界面设计语言的参考**：色板（primary `#3B71CA`、success `#14A44D`、danger `#DC4C64`、warning `#E4A11B`、info `#54B4D3`）、卡片/按钮/进度条/状态胶囊/提示条的组件形态与层级。它是给网页用的 Tailwind 组件库，我们**没有复制它的代码**——显式 legacy 界面使用 Python + Tkinter，圆角卡片与进度条是 Canvas 自绘 | `ai/praat_ai/ui_theme.py`（令牌）、`ai/praat_ai/ui_widgets.py`（自绘控件）、`ai/praat_ai/chat.py` / `api_settings.py` / `progress_popup.py` / `ui.py`（四个界面），说明见 guide §8 |

## 直接复用或移植的源码贡献者

音位声学判据复用 [PanPhon](https://github.com/dmort27/panphon) 的 IPA 特征表和
FeatureTable 的 Unicode 归一化、最长音段匹配代码（MIT，固定版本
a49c242d3307864312a2701055faefa4e548501c）。数据与薄适配放在
ai/praat_ai/_vendor/panphon/；完整许可和修改范围见
[PanPhon NOTICE](ai/praat_ai/_vendor/panphon/NOTICE.md)。语言声学规则仍为实验性。

以下补充现代 AI 前端及上下文管理中实际提取、复制或移植的上游代码贡献者；名字按固定源码版本中相关文件的提交记录核对。它不是这些大型项目的完整贡献者名册。逐文件来源、哈希、修改范围与许可证见 [前端源码清单](docs/ai-frontend/FRONTEND-SOURCES.md)、[PI-Desktop NOTICE](ai/third_party/pi-desktop/NOTICE.md) 和 [Codex NOTICE](ai/third_party/codex/NOTICE.md)。

| 上游源码 | 本项目复用范围 | 相关贡献者 |
| --- | --- | --- |
| [PI-Desktop](https://github.com/vastsa/PI-Desktop/tree/0d47d26769ecbeca1c3ab56fa83b58a91de8190e)（LGPL-3.0，固定提交 `0d47d26769ecbeca1c3ab56fa83b58a91de8190e`） | 菜单、滚动条、侧栏分组/预览与搜索输入的局部源码和交互 | [Lan（@vastsa）](https://github.com/vastsa)、[@zszz3](https://github.com/zszz3)、[@KtzeAbyss](https://github.com/KtzeAbyss)、[@yuxino](https://github.com/yuxino)、[@ZeroY](https://github.com/zeroy1024)；完整名单见[上游贡献者记录](https://github.com/vastsa/PI-Desktop/graphs/contributors) |
| [OpenAI Codex](https://github.com/openai/codex/tree/ab45264919aaeb8a421cc156f1a5459ef9d60b72)（Apache-2.0，固定提交 `ab45264919aaeb8a421cc156f1a5459ef9d60b72`） | 会话分区协议源码；搜索、历史分页和 fork 行为移植 | [Joey Trasatti（@joeytrasatti-openai）](https://github.com/joeytrasatti-openai)、[Owen Lin（@owenlin0）](https://github.com/owenlin0)、[Eric Traut（@etraut-openai）](https://github.com/etraut-openai)、[Can Sar（@cansar-oai）](https://github.com/cansar-oai)、[@andrewgu-oai](https://github.com/andrewgu-oai)、[David de Regt（@ddr-oai）](https://github.com/ddr-oai)、[Michael Bolin（@bolinfest）](https://github.com/bolinfest)；完整名单见[上游贡献者记录](https://github.com/openai/codex/graphs/contributors) |
| [Pi](https://github.com/earendil-works/pi/tree/28dcce2ba45ce4a9efeb0f5b686f0be830fd89b9)（MIT，固定提交 `28dcce2ba45ce4a9efeb0f5b686f0be830fd89b9`） | token 估算、压缩边界、对话循环与按需技能目录的实现模式移植到 Python | [Mario Zechner（@badlogic）](https://github.com/badlogic)、[Armin Ronacher（@mitsuhiko）](https://github.com/mitsuhiko)、[Vegard Stikbakke（@vegarsti）](https://github.com/vegarsti)、[David Brailovsky（@davidbrai）](https://github.com/davidbrai)、[Cristina Poncela Cubeiro（@cristinaponcela）](https://github.com/cristinaponcela)、[Alexey Zaytsev（@xl0）](https://github.com/xl0)、[Aliou Diallo（@aliou）](https://github.com/aliou)；完整名单见[上游贡献者记录](https://github.com/earendil-works/pi/graphs/contributors) |
| [OnlyTerp/prompt-cache-skills](https://github.com/OnlyTerp/prompt-cache-skills/tree/5b58ae26bd446bfb2eee97e8dad076ffb46a6715)（MIT，固定提交 `5b58ae26bd446bfb2eee97e8dad076ffb46a6715`） | `check_cache.py` 原样复用；缓存验证流程改编为项目开发 skill | [OnlyTerp](https://github.com/OnlyTerp) |

React、assistant-ui、Tiptap、Markdown 等作为锁定版本的包依赖使用，不等同于复制其整个源码仓库；版本和许可证清单见 [前端源码清单](docs/ai-frontend/FRONTEND-SOURCES.md) 与 [第三方依赖通知](ai/frontend/public/THIRD-PARTY-NOTICES.txt)。仅作界面参考、未复制源码的项目仍按上表原有说明标注。

## 运行依赖

| 依赖 | 用途 |
| --- | --- |
| [ggml-org/llama.cpp](https://github.com/ggml-org/llama.cpp)（`llama-server`） | 本地推理服务，OpenAI 兼容接口 + 原生 tool calling |
| [unsloth/Qwen3.5-0.8B-GGUF](https://huggingface.co/unsloth/Qwen3.5-0.8B-GGUF)、[unsloth/Qwen3.5-2B-GGUF](https://huggingface.co/unsloth/Qwen3.5-2B-GGUF) | 本地模型与 `mmproj` 视觉投影文件 |
| [YannickJadoul/Parselmouth](https://github.com/YannickJadoul/Parselmouth)（praat-parselmouth） | Python 侧读音频、做特征跟踪（可选） |
| [Montreal Forced Aligner](https://montreal-forced-aligner.readthedocs.io/) + [Kalpy](https://github.com/mmcauliffe/kalpy)/Kaldi/OpenFST 等 | 音素强制对齐（可选，见 `ai/Install-MFA.ps1`） |
| [facebook/wav2vec2-lv-60-espeak-cv-ft](https://huggingface.co/facebook/wav2vec2-lv-60-espeak-cv-ft) | 另一种音素对齐后端（可选） |
| Praat `external/` 里的第三方库（espeak-ng、portaudio、whisper.cpp、FLAC、LAME、libvorbis 等） | 随上游 Praat 一起分发，署名与许可见 [`docs/LICENSE.txt`](docs/LICENSE.txt) |

## 测量公式的参考文献

借来的几条嗓音/频谱测量原样照抄了公式，出处写在各工具函数的 docstring 里：

- Traunmüller, H. & Eriksson, A. (2000) — 谱强调（spectral emphasis）
- Hammarberg, B. et al. (1980) — Hammarberg 指数
- Hillenbrand, J. et al. (1994) — 峰值/有效值比、CPPS（Hillenbrand 式）

## 当前集成范围

前端菜单使用 picojson（BSD-2-Clause，完整单头文件）、gpustat 的显存非负处理（MIT，适配片段）和 pywebview 关闭事件绑定示例（BSD-3-Clause，适配片段）。来源版本、实际复用范围与许可证位置见 [前端菜单状态修复验收](docs/2026-10-05-前端菜单状态修复验收.md)。

在基座 `modern` 分支之上加的是**本地 AI 前端**（对话窗口、规划循环、表驱动声学测量、
Praat 原生插件、社区脚本包装、token 预算与可取消的等待）。当前后端契约看 [`ai/docs/adr/`](ai/docs/adr/README.md)，维护方式看 [`guide.md`](guide.md) §8。
