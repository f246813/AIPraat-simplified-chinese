# 参考、借用与依赖

这个仓库是 **Praat 简体中文版 + AI 前端与对比纠音**，不是从零写的：主体来自下面的基座
与上游项目，另有一批「借来用」的代码库。这里列出它们各自的用途、许可

*开发者手写的经验之谈：Vibecoding一定不要让AI从零开始干，只会堆出屎山，不管干什么都应该优先让AI去扒开源代码库，然后像搭积木一样拼起来，AI只负责粘胶水，提示词和文档里应当明确约束：尽可能搬运已有代码库，不要从零开始写*

## 基座与上游（必须保留的署名）

| 项目 | 用途 | 许可 |
| --- | --- | --- |
| [KasumiKitsune/praat-simplified-chinese](https://github.com/KasumiKitsune/praat-simplified-chinese) | **本仓库的基座**：Praat 7.0.02 的简体中文汉化（界面/手册/打包），`modern` 分支的现代版界面 | 跟随上游 Praat |
| [praat/praat](https://github.com/praat/praat)（[praat.org](https://praat.org)、[praat.github.io](https://praat.github.io)） | 上游软件本体，作者 **Paul Boersma & David Weenink** | GPL-3.0-or-later（见 [LICENSE](LICENSE)），文档/图片部分另有 CC BY-SA 4.0 等，完整清单在 [`docs/LICENSE.txt`](docs/LICENSE.txt) |

本副本由 `f246813` 发布，**与上游（praat/praat）和基座仓库没有隶属关系**；基座里
原有的署名、许可和链接一律保留。@KasumiKitsune贡献的开源汉化版主页仍在
[kasumikitsune.github.io/praat-simplified-chinese](https://kasumikitsune.github.io/praat-simplified-chinese/)。

## 借用了代码或设计的代码库

| 项目 | 借了什么 | 落在哪里 |
| --- | --- | --- |
| [chengafni/praat](https://github.com/chengafni/praat) | 声学测量脚本的公式与做法：`plugin_CompleteAnalysis`（表驱动：`objects.txt`/`queries.txt`/`settings.txt`）、`plugin_SpectralEmphasis`、`plugin_HL`、`plugin_HammarbergIndex`、`plugin_PA`、`plugin_IntensitySlope`、`plugin_PitchPeakLatency` 的原理与原脚本 | `ai/praat_ai/tools.py` 的测量模板、`ai/praat_ai/measures.tsv`（表结构参考）、`ai/plugin/`（原生插件的 `setup.praat` + `Add menu command` 做法），说明见 [ADR-005](ai/docs/adr/ADR-005-table-driven-measurements.md)、guide §8 |
| [QwenLM/Qwen-Agent](https://github.com/QwenLM/Qwen-Agent) | agent 循环的设计参考：工具自带 JSON Schema → 交给服务端原生 function calling → 把结果作为 function/tool 消息回灌 → 再规划；工具报错当「可恢复的观察」；输入按 token 预算截断 | `ai/praat_ai/qwen.py`、`ai/praat_ai/chat.py`（见 [ADR-003](ai/docs/adr/ADR-003-planning-interface.md)、[ADR-004](ai/docs/adr/ADR-004-agent-loop.md)、[ADR-008](ai/docs/adr/ADR-008-context-token-budget.md)） |
| [Ron-312/PraatPlugin](https://github.com/Ron-312/PraatPlugin) | 调用层的做法参考：Praat 安装位置的定位（对应 `PraatInstallationLocator`）、脚本投递与结果契约的取舍 | `ai/praat_ai/praat_app.py`（找 Praat）、`ai/docs/adr/ADR-001/002`（当前桥接契约说明） |
| [mdbootstrap/TW-Elements](https://github.com/mdbootstrap/TW-Elements)（MIT） | **界面设计语言的参考**：色板（primary `#3B71CA`、success `#14A44D`、danger `#DC4C64`、warning `#E4A11B`、info `#54B4D3`）、卡片/按钮/进度条/状态胶囊/提示条的组件形态与层级。它是给网页用的 Tailwind 组件库，我们**没有复制它的代码**——显式 legacy 界面使用 Python + Tkinter，圆角卡片与进度条是 Canvas 自绘 | `ai/praat_ai/ui_theme.py`（令牌）、`ai/praat_ai/ui_widgets.py`（自绘控件）、`ai/praat_ai/chat.py` / `api_settings.py` / `progress_popup.py` / `ui.py`（四个界面），说明见 guide §8 |

## 直接复用或移植的源码贡献者

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

前端菜单使用 picojson（BSD-2-Clause，完整单头文件）、gpustat 的显存非负处理（MIT，适配片段）和 pywebview 关闭事件绑定示例（BSD-3-Clause，适配片段）。固定来源、实际复用范围与许可证见 `external/picojson/`、`ai/third_party/gpustat/`、`ai/third_party/pywebview/`；前端集成范围见[来源说明](docs/ai-frontend/FRONTEND-SOURCES.md)。

在基座 `modern` 分支之上加的是**本地 AI 前端**（对话窗口、规划循环、表驱动声学测量、
Praat 原生插件、社区脚本包装、token 预算与可取消的等待）。当前后端契约看 [`ai/docs/adr/`](ai/docs/adr/README.md)，维护方式看 [`guide.md`](guide.md) §8。

## AI 与纠音依赖

下表说明运行框架、前端组件和音位资源。包依赖、直接源码适配与仅作参考分别注明；模型能力及本项目声学规则不由特征表提供。

| 项目 | 实际用途 / 复用范围 | 许可与出处 |
| --- | --- | --- |
| [Pydantic AI / Pydantic Graph](https://github.com/pydantic/pydantic-ai) | 云端工具调用、类型化输入输出与阶段工作流；运行依赖锁定为 2.52.0 | MIT；[依赖声明](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/requirements.txt) |
| [PanPhon](https://github.com/dmort27/panphon/tree/a49c242d3307864312a2701055faefa4e548501c) | 直接复用 IPA 特征表、Unicode 归一化、trie 与最长音段匹配，适配标准库 CSV 读取；英语／日语声学规则为本项目新增 | MIT，Carnegie Mellon University；[固定来源与修改范围](https://github.com/f246813/praat-simplified-chinese-ai/blob/v8.0.3/ai/praat_ai/_vendor/panphon/NOTICE.md) |
| [VowSpace](https://github.com/alicagank/VowSpace/tree/3143f636c56cb34e26cc83da0f425494bb3f9c94) | 将 Lobanov 小函数适配为 NumPy 独立拟合与冻结变换（样本标准差 ddof=1）；独立校准采样、界面和近音实验扩展为本项目新增，不引入整包 GUI | GPL-3.0；[源码、完整许可与修改说明](ai/praat_ai/_vendor/vowspace/NOTICE.md) |
| [librosa](https://github.com/librosa/librosa/tree/08e2c0a20d650fe00f10aaf3a1eaa9698ac19641) | 仅适配 Hz → MIDI 浮点半音转换小函数，不安装完整 librosa；基线、缺口与独立观察为本项目新增 | ISC；[许可及修改说明](ai/praat_ai/_vendor/librosa/NOTICE.md) |
| [pyloudnorm](https://github.com/csteinmetz1/pyloudnorm) / [SciPy](https://github.com/scipy/scipy) | pyloudnorm 正式发行包提供整段 integrated LUFS 测量；SciPy 提供滤波。只派生恒定增益曲线，不覆盖 WAV/Sound | MIT / BSD-3-Clause；[版本、wheel 哈希、功能验证与许可](ai/third_party/pyloudnorm/NOTICE.md) |
| [phonR](https://github.com/drammock/phonR/tree/c6d419f187f25d2be315cc4cdc35b7ad2b8ce6ee) | 仅用于开发期 Lobanov 数值对照，不要求用户安装 R | GPL-3；[固定来源及运行边界](ai/third_party/normalization-reference/NOTICE.md) |
| [OpenPronounce](https://github.com/Halleck45/OpenPronounce/tree/74bc17ea406e6f057f15eea0e70951e1931af868) | 音素识别与序列比较的调研参考；当前规则未复制其识别模型、总分或阈值 | 参考范围见上述 PanPhon NOTICE 与[音位判据](docs/PRONUNCIATION.zh-CN.md) |
| [NumPy](https://github.com/numpy/numpy)、[Pillow](https://github.com/python-pillow/Pillow) | 数值数组与声学计算；生成声学叠加图和报告图像 | NumPy BSD-3-Clause；Pillow HPND；运行依赖见 requirements.txt |
| [PyTorch](https://github.com/pytorch/pytorch)、[Transformers](https://github.com/huggingface/transformers)、[Safetensors](https://github.com/huggingface/safetensors) | 可选 Wav2Vec2 对齐后端的推理、模型加载和权重读取 | PyTorch BSD-3-Clause；其余 Apache-2.0；[对齐依赖声明](ai/requirements-wav2vec2.txt) |
| [React](https://github.com/facebook/react)、[assistant-ui](https://github.com/assistant-ui/assistant-ui)、[Tiptap](https://github.com/ueberdosis/tiptap)、[ProseMirror](https://github.com/ProseMirror) | 前端组件、聊天运行时与富文本输入；作为 npm 包使用 | MIT；版本与适配边界见[前端源码清单](docs/ai-frontend/FRONTEND-SOURCES.md) |
| [Radix UI](https://github.com/radix-ui/primitives)、[Lucide](https://github.com/lucide-icons/lucide) | 历史菜单与子菜单；界面 SVG 图标 | MIT / ISC；包版本见[前端依赖声明](ai/frontend/package.json) |
| [Mermaid](https://github.com/mermaid-js/mermaid)、[KaTeX](https://github.com/KaTeX/KaTeX)、[highlight.js](https://github.com/highlightjs/highlight.js) | 对话中的流程图、公式、代码高亮；离线资产随前端分发 | MIT / MIT / BSD-3-Clause；完整文本见[第三方通知](ai/frontend/public/THIRD-PARTY-NOTICES.txt) |
| [react-markdown](https://github.com/remarkjs/react-markdown)、remark / rehype、[DOMPurify](https://github.com/cure53/DOMPurify) | Markdown、表格与公式处理；Markdown / SVG 清理 | MIT；DOMPurify 为 Apache-2.0 OR MPL-2.0；同上第三方通知 |
| [pywebview](https://github.com/r0x0r/pywebview)、[pythonnet](https://github.com/pythonnet/pythonnet) | WebView2 桌面宿主和 Python / .NET 桥接 | BSD-3-Clause / MIT；分别锁定 6.2.1 / 3.2.0 |
| [Pi 的 pi-ai 模型数据](https://github.com/badlogic/pi-mono/tree/781152fc24841dc54b22284514604048ebe5e2c9/packages/ai) | 从 0.73.1 发布包抽取四条模型窗口与输出元数据；没有引入 Pi Agent 内核 | MIT 来源说明、包哈希与端点限制见[模型数据来源](docs/ai-frontend/PI-MODEL-SOURCES.md) |
| [FFmpeg](https://ffmpeg.org/)、[Gyan Windows builds](https://www.gyan.dev/ffmpeg/builds/) | Praat 原生读取失败后的备用音频解码；一键安装从 Gyan 发布包下载并核对 SHA-256 | 许可随所选构建及启用组件而定；安装脚本保留构建附带 LICENSE / README，不能统一视为 LGPL |
| [Anki](https://github.com/ankitects/anki) | 通过 Anki 插件接口取得当前卡片音频，连接 AIPraat；本仓库提供独立 .ankiaddon | Anki 本体为 AGPL-3.0；[本项目插件说明](ai/anki_addon/README.md)，插件不等同于复制 Anki 源码 |

## 发音词典、声学模型与演示素材

| 来源 | 使用范围 | 许可 / 版本记录 |
| --- | --- | --- |
| [MontrealCorpusTools/mfa-models](https://github.com/MontrealCorpusTools/mfa-models) | 英语、美式 ARPA、普通话 MFA 与日语 MFA 的词典和声学模型；原始资源用于可选强制对齐 | 所收录资源为 3.0.0、CC BY 4.0；发布标签、原始下载、SHA-256 与许可记录在安装包外的资源目录 `声学模型与语音词典\\来源及网址.md`，该目录不随代码仓库发布 |
| [facebook/wav2vec2-lv-60-espeak-cv-ft](https://huggingface.co/facebook/wav2vec2-lv-60-espeak-cv-ft) | 可选 CTC 音素对齐模型与配套文件；资源原始内容未重新训练或修改 | Apache-2.0；固定版本和资源清单见上述来源表 |
| [En-us-hello.ogg](https://commons.wikimedia.org/wiki/File:En-us-hello.ogg)，Dvortygirl | 音频读取验证与纠音表单示例的美音 hello | 公有领域 |
| [En-uk-hello.ogg](https://commons.wikimedia.org/wiki/File:En-uk-hello.ogg)，Judith Franck / Association Shtooka | 音频读取验证与纠音表单示例的英音 hello；没有将说话人当作发音错误样本 | CC BY 3.0 US |

仓库首页沿用两张基座界面图片；新增界面截图与流程图的来源见[图像说明](docs/pictures/aipraat/README.md)。模型和素材许可独立于本项目代码许可。
