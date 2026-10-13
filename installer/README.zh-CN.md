# AIPraat 安装与路径说明

双击 AIPraat-install.exe，依次选择目录、配置 Python、必需运行库和 FFmpeg、完成安装。

默认使用 C:\AIPraat；已有同名文件夹直接复用。浏览按钮选择的是最终安装文件夹，不会再在其里面套一层 AIPraat。可输入其他驱动器或包含中文、空格的完整路径。默认不要求管理员权限；如果目标目录不可写，安装失败页可选择其他位置，或以管理员权限重试。

## 前置软件路径

| 向导字段 | 要选择的文件 | 是否必需 |
|---|---|---|
| Python 环境 | Python 3.10–3.12 x64 环境内的 python.exe，推荐 3.12 | 必需 |
| 运行库 | Python 下方显示实际检测状态，点击唯一的“运行库明细…”入口检测、本地安装或联网补齐 | 全部检测通过后才能继续或保存 |
| FFmpeg | 已安装的 ffmpeg.exe；缺失时点击“安装 FFmpeg…” | 必需，实际解码通过后才能继续或保存 |
| llama.cpp | llama-server.exe，与其原有 DLL 放在一起 | 可留空 |
| 前端模型 | 语言模型 .gguf | 可留空 |
| 视觉投影 | 和语言模型配套的 mmproj .gguf | 可留空，只在使用图像能力时需要 |
| wav2vec2 模型 | 本地模型目录，使用文件夹浏览按钮选择 | 可留空 |
| MFA 程序 | mfa.exe，也支持已有 .bat / .cmd 启动器 | 可留空 |

安装向导第二步和路径配置共用同一个依赖面板。必需环境显示 Python、其下方的运行库状态区和 FFmpeg，不重复显示只读 Python 路径。安装向导保留“可选分析工具”和“本地模型与资源”两组，后者提供可编辑的语言模型、视觉投影和 wav2vec2 模型目录；wav2vec2 使用“选择目录…”按钮。路径配置仅保留“可选工具与资源”一组，包含 llama.cpp、MFA 和视觉投影；语言模型与 wav2vec2 行已移除，已有语音模型设置在保存时保留，由前端资源管理维护。已有可选配置或校验错误会展开对应分组。打开界面、修改路径或完成查找会自动检测；运行库明细中的日志按需查看，查找结果使用“查看程序与安装包识别结果…”入口。FFmpeg 未就绪时显示“安装 FFmpeg…”，Python 仅在找到真实安装包且尚未就绪时显示“安装 Python…”；可选项保留单一浏览操作。MFA 声学模型和词典仍由语音资源管理维护，不根据文件名推测模型配套关系。

安装向导依次为“选择目录、检查环境、安装程序”；第二步的主按钮为“开始安装”。标题、步骤指示和底部操作固定，内容按需滚动；安装时显示真实百分比和当前任务，写入期间禁用返回及取消，完成后显示“完成”。目录权限错误时可返回选择目录、以管理员权限重试或重试安装。

顶部提供两个全局入口：“自动查找”只检查已有配置、项目内约定目录、AIPraat 托管安装目录、系统 PATH 和 Windows 注册的 Python，**不递归扫描磁盘**。“从文件夹识别…”递归检查用户选定文件夹及其子目录，跳过目录链接，不进入范围外的文件夹。已验证可用的手动路径保留；同类多个候选由用户选择。程序必须实际运行验证；识别到 wheel、FFmpeg ZIP 或 Python/WebView2 安装包只表示“安装包已找到”，完成安装和检测后才算就绪。递归找到的 wheel 目录会传给现有离线安装流程，用于解析间接依赖。

保存保留对齐后端、启用状态、Conda 和其他运行参数，并将运行库窗口选择的 CPU/CUDA 同步到 wav2vec2。已有 wav2vec2 模型 ID 以及 `mfa` 命令也会回填并保留。使用 Conda 启动 MFA 时，原有 Conda 配置继续生效。

安装器和“前端 → 路径配置”共用“运行库配置（必需）”窗口。清单来自 `ai/requirements.txt`（包含 `requirements-wav2vec2.txt`）：NumPy、Pillow、pywebview、pythonnet、PyTorch、Transformers、Safetensors、praat-parselmouth、pydantic-ai-slim[openai]、pydantic-graph、jsonschema，以及 WebView2 Runtime 和 Tcl/Tk，共 13 项。已安装且检测通过的项无需再提供安装包。切换 python.exe 会清除旧结果；安装、保存前再次实际检测所选环境。

本地安装：逐项选择对应 `.whl`，可再指定“间接依赖包文件夹”。安装前校验包名、版本、Python 和架构；只从所选包目录及依赖包文件夹离线解析依赖。缺包时日志显示 pip 报出的具体包名，补充 wheel 后重试，或主动点击“运行 PowerShell 联网补齐”。不能用库文件夹路径替代安装。pywebview 的 `proxy_tools` 等只有源码发布的间接依赖，需要先在联网电脑上用 `python -m pip wheel` 构建 wheel，再放进依赖包文件夹。

WebView2 本地安装需要微软 x64 Evergreen 完整离线安装程序 `MicrosoftEdgeWebView2RuntimeInstallerX64.exe`；联网补齐使用微软官方 Bootstrapper。两种方式都检查微软数字签名。Tk 随 Python/Conda 提供；离线时通过该环境的本地安装组件手动修复，联网时沿用官方 Python/Conda 修复流程。系统级安装可能显示管理员权限提示。CPU 是默认设备；CUDA 需能完成 GPU 张量运算。检测包含实际导入、DLL 桥接、Tk 窗口、张量运算和新前端窗口启动，不下载模型。

联网补齐根据当前逐项检测结果，只把未就绪的项目加入安装清单，保留 requirements 的版本约束与 `[openai]` 扩展。界面把检测结果传给 PowerShell；单独运行脚本时，先检测以生成清单。安装后由 PowerShell 做一遍完整复检，界面直接显示返回结果；若结果缺失或失败，保持未就绪，可点击“重新检测”。保存路径或继续安装程序前的最终环境确认仍保留。

pip 默认使用缓存。只有检测到缓存权限或损坏错误时，才禁用缓存重试一次；网络错误或 Python 目录写入权限错误不会触发此重试。检测及安装日志优先保存到 `%LOCALAPPDATA%\AIPraat\logs\runtime`，不可写时依次尝试 `%TEMP%\AIPraat\logs\runtime`、程序目录的 `logs\runtime`，文件名带 UTC 时间戳；尽力在旁边保存检测 JSON，安装还保存补齐清单和版本约束。所有日志目录均不可写时仍继续检测和配置，提示“日志未保存，不影响检测”，“查看本次日志”置灰。检测结果通过临时工作目录独立传回窗口；临时目录不可写时，工作文件依次回退到程序的 ai/runtime、logs/runtime/work 目录，使用后清理。关闭窗口或清理临时脚本后已保存的日志仍保留。独立 PowerShell 调用可用 `-LogPath` 自定义日志文件，或用 `-NoLog` 禁用日志。

安装器已取消“暂不配置”入口。全部必需运行库及 FFmpeg 解码检测通过后才能安装或保存路径。MFA 程序及模型仍可选，模型文件仍由“管理语音词典与模型”或“添加模型路径”维护，不要求提前下载。路径配置保留现有模型和 API 设置，并备份原配置；正在运行的服务或对话窗口需重新启动后使用新路径。云端 API 可在 AI 窗口配置，不要求本地 llama.cpp 和模型文件。

PowerShell 可独立调用（所有 pip 命令均使用所选解释器；以下是源码目录调用方式，已安装程序使用 `ai\ConfigurePython.ps1`）：

```powershell
.\installer\src\ConfigurePython.ps1 -PythonPath 'C:\Python312\python.exe' -Mode Online -Device cpu -ResultPath '.\runtime-result.json'
.\installer\src\ConfigurePython.ps1 -PythonPath 'C:\Python312\python.exe' -Mode Local -Device cpu -PlanPath '.\local-plan.json' -ResultPath '.\runtime-result.json'
```

本地计划 JSON 格式：`{"packages":{"numpy":"D:\\wheels\\numpy-2.0.0-cp312-cp312-win_amd64.whl"},"dependency_folder":"D:\\wheels","webview2":"D:\\MicrosoftEdgeWebView2RuntimeInstallerX64.exe"}`。未选的库不会强制索要安装包；复检仍要求全部必需项通过。

## 路径保存位置

“前端 → 路径配置”在 Python 区域下方提供 FFmpeg 音频解码配置。可浏览已有 `ffmpeg.exe`，使用顶部“自动查找”，或在缺失时通过“安装 FFmpeg…”安装。检测先验证程序版本，再用随程序提供的 OGG 样本实际解码成 WAV；未通过时不能保存路径或继续安装。安装向导也保存 `ai/ai_config.json` 的 `audio.ffmpeg`；共享读取层在下一次备用解码时读取，无需重启来应用 FFmpeg 路径。

一键安装使用 [FFmpeg 官网](https://ffmpeg.org/download.html)列出的 [Gyan Windows x64 构建](https://www.gyan.dev/ffmpeg/builds/)，下载 ZIP 和发布方 SHA-256，校验、解压并实际解码后安装到 `%LOCALAPPDATA%\AIPraat\ffmpeg`。不修改 PATH，不要求管理员权限。安装日志保存在 `%LOCALAPPDATA%\AIPraat\logs\ffmpeg`，失败时界面显示日志路径。也可独立运行：

```powershell
.\ai\Install-FFmpeg.ps1
# 离线安装需提供 ZIP 和对应的发布方 SHA-256：
.\ai\Install-FFmpeg.ps1 -ArchivePath 'D:\Downloads\ffmpeg-release-essentials.zip' -ExpectedSha256 '发布方的64位SHA256'
```

音频入口统一先原生读取，仅在明确失败后转换临时 24 位 PCM WAV 并再读入；不主动重采样或混合声道。读入完成或失败均清理临时目录。检测样本 `ai/ffmpeg-probe.ogg` 来自 [Wikimedia En-us-hello.ogg](https://commons.wikimedia.org/wiki/File:En-us-hello.ogg)，录音者 Dvortygirl，已释入公有领域。

| 文件 | 保存内容 |
|---|---|
| 安装目录\install-settings.json | 选择的 Python 路径与安装信息 |
| 安装目录\ai\ai_config.json | 前端路径、模型预设，以及 alignment.wav2vec2.model、alignment.mfa.executable；MFA 词典与声学模型由资源管理窗口保存；用户自己的 API 设置也在此 |
| %APPDATA%\Praat\Preferences.txt | Python.executablePath 和 AI.projectDirectory；保留其他首选项 |
| %APPDATA%\Praat\plugin_praat_ai | 原生测量插件、指向安装目录的 AI 启动命令 |
| 安装目录\.aipraat-backups\时间和编号 | 更新前的原文件及对应路径清单 |

从桌面或开始菜单的 **AIPraat** 快捷方式启动。该入口负责关联本机路径，然后运行当前 Praat.exe。请先关闭其他正在运行的 Praat，以免不同目录的实例共用首选项和消息文件。

安装器重新配置时会保存新的选择；选“暂不配置”重新安装时保留原来的 Python 和模型配置。用户自己的音频、标注和其他文件不会被删除。

本包保留当前 Windows x64-v3 Praat，因此需要 Windows 10/11 64 位及支持 x64-v3（AVX2、FMA、F16C 等）的 Intel/AMD CPU。安装器本身使用 Windows 自带的 .NET Framework，不需要 Python 才能运行。发行文件尚未签名。

## 开发构建

在仓库根目录运行：

    pwsh -NoProfile -File installer\build.ps1

使用本机已有的 .NET Framework C# 编译器。输出为根目录 AIPraat-install.exe，无需 .NET SDK、Inno Setup 或外部压缩工具。包内没有开发机配置、API key、日志、缓存或临时运行文件。

构建完成后，批量验证：

    pwsh -NoProfile -File installer\tests\Run-Tests.ps1 -Python <Python环境>\python.exe

测试日志与结果位于 test-records\installer；编译宿主和输入夹具位于 installer\verification。UI 验证用相同向导和安装资源，但首选项、快捷方式均写入独立测试目录。

测试 runner 共用 `installer/tests/TestEnvironment.ps1`：默认先查项目及相邻目录的 `.venv`、`venv-ai-modern`、`venv-ai`，实际检查 Python 版本、架构和必需运行库，选择第一个已就绪环境。PATH 中的 WindowsApps Python 别名不参与默认选择；`-Python` 或环境变量 `AIPRAAT_TEST_PYTHON` 可明确指定解释器，显式选择未就绪时不会自动换环境，会在执行断言前提示“测试环境未就绪，请用 -Python 指定”。

`Run-DependencyTests.ps1` 可无参数运行：FFmpeg 优先检查项目目录、`tools`、AIPraat 托管目录，再查 PATH；也可用 `-FFmpeg` 指定。验证图默认写入 `test-records/installer/dependency-discovery`，可用 `-OutputDirectory` 修改。直接执行底层测试 EXE 而缺少参数时会打印用法；初始化异常会打印异常类型和具体消息。
