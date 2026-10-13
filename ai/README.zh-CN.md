# Praat AI 前端

本目录提供 Praat 的本地模型与 API 对话、声学测量、社区脚本执行和纠音报告。默认对话入口是 React / assistant-ui 与 pywebview / WebView2 工作台，Tk 窗口通过 `--legacy` 启动。

## 启动

安装 Python 3.10–3.12 x64（推荐 3.12），并在 Praat 的路径配置中选择解释器。安装器与路径配置共用“运行库配置（必需）”，支持逐项选择本地 wheel 和依赖包文件夹，也支持 PowerShell 联网补齐；全部运行库检测通过后才能安装或保存。路径配置的 Python 区域下方还提供 FFmpeg 路径、自动查找、实际解码检测和一键安装；FFmpeg 检测通过后才能保存路径。安装依赖：

```powershell
python -m pip install -r ai/requirements.txt
```

将 `ai/ai_config.example.json` 复制为 `ai/ai_config.json`，填写本地模型或 API 配置。现代工作台还需要 `ai/frontend/dist` 中的离线构建资产，以及 Windows WebView2 运行时。

```powershell
python ai/run_ai_chat.py
python ai/run_ai_chat.py --legacy
```

也可以从 Praat 对象窗口或声音编辑器的「前端 → 启动前端」打开工作台，从「API 配置」设置供应商、地址、模型、key、思考强度和音频能力。打开窗口或保存配置不会自行开始音频分析。修改 Python 源码后关闭窗口并重新启动。

现代数据默认位于 `ai/runtime/modern/sessions.sqlite3`；`--data-dir` 可指定现代数据目录，`--config` 可指定配置文件。`ai/runtime/conversations.sqlite3` 作为只读历史来源。恢复对话保留消息和证据，不重放对象操作，也不恢复已中断脚本。

## 模型与配置

本地服务使用 OpenAI 兼容接口，默认地址为 `http://127.0.0.1:8000/v1`。模型下载命令：

```powershell
pwsh -NoProfile -File ai/Download-Qwen35.ps1 -OutputDirectory D:\models
```

配置可指定模型路径、llama-server 路径和上下文大小；本地多轮工具调用使用 `--jinja`，应用启动的服务会设置该参数。也可通过 `PRAAT_AI_QWEN_BASE_URL` 与 `PRAAT_AI_QWEN_MODEL` 指定本地接口。

API key 来自本地配置或 `PRAAT_AI_API_KEY`；`ai_config.json` 不纳入版本控制。正常桌面入口在提交任务时允许已启用 API 配置的远程调用；其他宿主可通过 `--allow-cloud` 提供进程级授权。地址检查由宿主设置服务执行。供应商协议、工具调用和音频支持由实际模型与端点决定。

本地模型按任务获取服务租约；API 切换时 `api.stop_local_service` 控制本地服务处理。应用只停止自己拥有且未被任务占用的服务。

## 对话与测量

在 Praat 中选择 Sound 或打开声音编辑器拖出选区，再在对话里说明目标、对象或时间范围。支持对象信息、基频、强度、共振峰、VOT、TextGrid 标注、声音编辑、读入和导出等工具。点查询未给时刻时可使用选区中点；区间查询按工具支持的范围处理。明确整段请求由 API 执行层覆盖旧选区。用户指定对象时，API 守卫核对编号、类型和名称。

通用 `measure` 从 `praat_ai/measures.tsv` 取得参数，例如 `f1,f2`、`mean_pitch`、`hnr`、`cpps`、jitter / shimmer、频谱和幅度参数。多步参数使用专用工具；只在编辑器支持的量会说明限制。实际范围、分析设置、单位和未定义值见结果行，不能把估计值或缺失值当成精确测量。

VOT 可直接计算给定爆破与起浊时刻之差，或在给定范围内估计事件位置。自动估计受范围与声音条件影响；音素身份、对齐和标准音比较可能需要人工标注或参照材料。

API 流程按任务选择专业工具、直接音频观察或一般讨论。工具与报告阶段共用每轮 12 次模型请求、20 次工具尝试，供应商临时错误最多重试一次。成功测量进入报告，列出目标完成项与缺口；同目标和范围的证据可复用。本地 Qwen 使用独立的默认 3 轮、5 动作循环，其后续修改操作受关键词启发式限制。

报告守卫核对可识别的数值、单位、量名、对象、范围与引用，并计算带输入引用的有限算式。无法核对的数值或推导会标注，必要时交付阶段记录。守卫不能证明自然语言解释全部正确，详见 [原生 Hooks 与守卫](docs/native-hooks.md)。

## 音频、上下文与取消

直接音频分析能力支持预设与手动覆盖，可选验证使用随机合成短音频。只有明确不支持音频的模型或端点错误才持久纠正对应配置。验证通过不能证明细微发音判断准确，精确声学数值仍来自工具。

单个临时 PCM WAV 最多 120 秒、20 MiB；Qwen Omni 的 Base64 输入另受小于 10 MiB 限制。临时片段关闭时清理，源文件保留；异常退出后的清理会核验任务和进程身份。

上下文用量为宿主估算，供应商真实窗口可能更小。现代会话必要时生成并保存摘要，不能容纳时说明缺口。继续分析沿用原目标和已有证据；移动选区不会自行替换原材料。关闭额外 token 上限仍保留供应商窗口、工作量与应用内存限制。

停止会取消后续请求和等待，不能终止 GUI Praat 中已运行的脚本。已投递但结果未确认时，现代调度器停止后续投递，需要重启桌面执行进程并核实对象状态。独立社区脚本批处理可结束对应子进程。

## 插件与社区脚本

[原生插件](plugin/README.zh-CN.md) 提供 Sound 测量菜单、编辑器选区测量和启动对话窗口：

```powershell
powershell -ExecutionPolicy Bypass -File ai/plugin/install.ps1
```

`run_praat_script` 将声音副本交给独立 Praat 批处理，使用脚本表单默认值并收集输出。它看不到当前 GUI 对象列表或编辑器选区。自定义 Praat 脚本接受命令与对象引用检查，Info 输出被改写到结果文件；这些检查不构成操作系统沙箱。

Windows GUI 投递共用 `%APPDATA%\Praat\Message.txt`，多个进程或实例可能相互影响。消息文件不可写属于环境权限问题；检查实际文件权限与目标进程。`PRAAT_AI_SEND_MODE=argv` 可使用命令行投递排障，但可能激活 Praat 窗口。

## 本地纠音报告

表单的“音位判据”可选择英语（美式）或日语（标准语），均为实验性声学观察。
元音比较 F1/F2，擦音比较频谱，塞音检查 VOT 测量是否可用；相对时长按相邻音素归一化。
日语保留长度、无声化与 N/cl 等标签。比例定位、低质量区间和缺失测量标为证据不足。
这一步没有独立音素识别或校准后的判错阈值，也没有日语拍结构分析。
新路径不使用统一“旧式对比阈值”。JSON 请求显式设置 profile_id 为 en-US-general-v1
或 ja-JP-standard-v1；不设置时沿用旧式对比，不自动改变历史语言请求。
详细规则、来源与使用例子见 [音位判据实施说明](../docs/2026-10-13-音位判据实施说明.md)。

运行 `ai/run_ai_tutor.py`，选择标准 Sound 与学习者 Sound，填写音位、对象和阈值。报告默认保存在用户主目录 `Praat AI Reports` 下的独立文件夹，包含 `ai_report.json`、`ai_errors.TextGrid` 和安装 Pillow 后的 `ai_overlay.png`。Info 窗口列出完整路径。

`PRAAT_AI_REPORT_DIR` 可指定报告根目录，不能指向临时回导目录或其子目录。Praat 从临时目录导入 TextGrid 与 Photo 后清理该目录；JSON 不导入为对象。PNG 可用系统查看器打开或在 Praat Picture 中绘制。

## 维护参考

[模块索引](HANDOFF.md) 与 [接口参考](docs/adr/README.md) 描述当前职责和限制。检查脚本在 `ai/tests`，运行前按脚本确认是否启动 Praat、本地模型或收费 API：

```powershell
$env:PYTHONPATH = 'ai'
python -m unittest discover -s ai/tests
python ai/tests/verify_chat_templates.py
python ai/tests/verify_chat_planning.py
python ai/tests/verify_plugin.py
```

许可与依赖出处见仓库的 credits 和 third-party 许可文件。
