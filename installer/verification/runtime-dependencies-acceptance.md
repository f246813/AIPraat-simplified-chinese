# 必需运行库配置验收（2026-10-12）

已重新构建工作区根目录的 `AIPraat-install.exe` 与 `AIPraat-paths.exe`。二者包含相同的运行库窗口、检测实现、PowerShell 脚本和项目 requirements 资源。共 13 项必需运行库：11 个 Python 包、WebView2 Runtime、Tcl/Tk。

实现入口：`installer/src/RuntimeDependenciesForm.cs`（共用窗口）、`installer/src/RuntimeDependencies.cs`（所选解释器执行与资源解包）、`ai/runtime_dependencies.py`（实际运行检测、wheel 校验、离线 pip 解析）、`installer/src/ConfigurePython.ps1`（本地/联网安装与共用复检）。模型仍由现有模型管理入口维护。CPU/CUDA 写入 `alignment.wav2vec2.device`；新环境默认 CPU。

## 实际验收

| 场景 | 结果与证据（相对于仓库根目录） |
|---|---|
| 全新 Python 3.12 x64、无 pip，联网一次补齐 | 通过，13/13；`test-records/installer/runtime-clean-online-install.log`、`runtime-clean-online.json` |
| 逐项选择 11 个真实 wheel，加间接依赖文件夹，在另一无 pip 环境离线安装 | 通过，13/13；`runtime-local-install.log`、`runtime-local.json` |
| 缺间接依赖，补至单独依赖文件夹后重试 | 实际隔离 pip 安装测试通过，首次错误明确显示缺失包名；`runtime-unit.log` |
| 选错包、版本不足、Python/架构不匹配、wheel 文件名与元数据不符 | 拒绝安装；`runtime-unit.log` |
| 已有完整环境、不提供安装包 | 13/13，允许继续/保存；`runtime-contracts.log` |
| 切换 Python、保存/安装前环境失效 | 立即清空旧结果并禁用继续/保存；最终再次检测；`runtime-contracts.log` |
| 不支持的 Python 3.14 | 真实检测拒绝；`runtime-python314.json`；3.13 和 32 位/ARM64 边界另有单元验证 |
| WebView2 缺失、错选 Bootstrapper、无效签名、安装失败 | PowerShell 函数使用模拟注册状态/安装器验证，保持未就绪并显示错误；`runtime-webview-install.log` |
| Tcl/Tk 原有修复选择与用户/系统范围 | 3 项通过；`runtime-tk-repair.log` |
| CPU 环境执行项目 wav2vec2 | 用本地生成的小模型实际调用 `Wav2Vec2Aligner._load` 和模型推理，通过；无模型下载；`runtime-cpu-inference.log` |
| 实际 AIPraat React 前端启动 | 使用离线安装环境启动工作台，检查 React 渲染和 WebView2/pythonnet RPC 桥接，通过；`runtime-frontend.log` |
| CUDA 在 CPU-only 环境中被选择 | 检测拒绝；`runtime-contracts.log` |
| 最终二进制嵌入资源、安装 payload | 两个 EXE 的四份资源逐字节与源码一致；payload 包含相同路径配置程序与独立 PowerShell 入口；`runtime-package.log`、`runtime-delivery.json` |

本机已存在 WebView2 与 Tk，未卸载它们来执行破坏性测试；缺失 WebView2 的安装调用和失败路径使用模拟验证。未在真实 CUDA GPU 上做正向安装验收。

## 回归

- Python 运行库测试：7 项通过（`runtime-unit.log`）。
- C# 运行库与保存/安装门槛：7 项通过（`runtime-contracts.log`）。
- 安装器契约：21 项通过（`runtime-installer-regressions.log`）。
- 路径配置：15 项通过（`runtime-path-regressions.log`）。
- 对齐路径：12 项通过（`runtime-alignment-regressions.log`），模型管理入口维持原有归属。
- Python 配置与参数转义：4 项通过（`runtime-python-setup-regressions.log`）。
- 原生父窗口生命周期：2 项通过（`runtime-parent-regressions.log`）。
- WebView2 安装分支：5 项通过；Tk 修复注册选择：3 项通过。

完整 Python 回归执行 1165 项，失败 6、错误 2，记录在 `test-records/installer/runtime-ai-unit.log`。失败涉及旧 Tk 折叠交互 2 项、VOT 工具 5 项、IME 字体 1 项。使用 HEAD 中修改前的 `config.py`（CUDA 默认值）在内存中复跑这 8 项，折叠和 VOT 的 7 项仍然失败，IME 项通过，见 `runtime-existing-failures-baseline.log`。本次未修改这些功能；不能将全量回归报告为全绿。

## 离线与联网说明

离线方式使用所选 `python.exe -I -m pip --isolated install --no-index --find-links`，保留 requirements 中的版本限制与 `[openai]` 扩展。间接依赖只从所选包目录和依赖文件夹解析，缺包由 pip 指明。pywebview 的 `proxy_tools` 需要在联网机器预先构建 wheel；验收已用 `pip wheel` 生成完整 wheel 文件夹，再实际离线安装。

联网方式优先 wheel，允许为只有源码发布的间接依赖构建 wheel。默认保留 pip 缓存，仅在明确出现缓存权限或缓存文件损坏错误时，使用 `--no-cache-dir` 重试一次；网络和环境目录权限错误不触发该重试。PyTorch 的 CPU/CUDA 使用官方 wheel 索引，CUDA 最终要求真实设备运算通过；所有检测均不下载模型。

## 自动补齐耗时与日志优化验收

- 联网安装根据当前逐项检测结果生成补齐清单，保留项目版本限制、云端固定版本和 `[openai]` 扩展；完整环境不调用 pip。独立调用脚本且没有匹配的检测结果时先检测，GUI 复用当前环境与设备对应的结果。
- PowerShell 安装后执行一次完整复检（含前端窗口启动），界面读取同一份 JSON，不再重复导入或启动窗口。最终保存/安装程序前的环境确认仍保留。
- 每次检测和安装持久保存 UTC 时间戳命名的日志及 JSON；安装另保存补齐清单和约束。默认目录为 `%LOCALAPPDATA%\AIPraat\logs\runtime`，窗口提供“查看本次日志”。PowerShell 5.1 的日志使用 UTF-8 BOM，保留中文状态和失败原因。
- Python 清单与 wheel 验证测试 10 项通过：`test-records/installer/runtime-optimization-unit.log`；缓存分支 6 项通过：`runtime-cache-retry.log`；C# 实际环境、结果复用、唯一完整复检、中文持久日志、安装失败状态等契约 9 项通过：`runtime-optimization-contracts.log`。
- 实际从独立离线验收环境卸载 jsonschema 后联网修复，只安装该失败项，并显示 `Using cached`，未重装健康的 PyTorch；最终 13/13 通过：`runtime-selected-repair.log`、`runtime-selected-repair.json`。真实 CUDA 正向验收限制和上文全量回归中的既有失败仍适用。
- 独立 PowerShell 使用匹配的检测结果完成空补齐清单验证，只执行一次完整复检；默认持久日志包含完整中文成功信息：`runtime-complete-cli.log`、`runtime-complete-cli.json`。

## 配置入口与检测进度显示

安装器及路径配置的“配置运行库…”按钮调整为 128×34，与各自 Python “浏览…”按钮右边缘一致。检测未完成时运行库摘要只显示“待检测”；下方显示“正在检查 Python 环境…：当前项目”，异步进度更新时保留所选 Python 的校验，切换环境后忽略旧进度。实际完整环境中，两窗口均观察到 PyTorch 检测进度与待检测摘要，完成后显示就绪结果，见 `test-records/installer/runtime-display-visual.log`。两个 EXE 已重新构建并通过资源/payload 核对。

实现依据：[pip 本地包安装](https://pip.pypa.io/en/stable/cli/pip_install/)、[微软 WebView2 部署](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution)、[PyTorch 官方安装说明](https://pytorch.org/get-started/locally/)。
