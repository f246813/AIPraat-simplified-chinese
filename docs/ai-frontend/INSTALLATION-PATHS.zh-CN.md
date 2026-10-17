# 安装向导与路径配置参考

安装向导 `installer/src/WizardForm.cs` 和路径配置 `PathSettingsForm.cs` 共用 WinForms `DependencyPanel`，不是 WebView 页面。该面板负责发现路径、检测所选环境、显示逐项结果及进入明确的配置／安装操作。前端与 Windows 发布条件见 [桌面宿主](CURRENT-HOST.zh-CN.md)。

## 两种窗口的字段

| 项目 | 路径配置 | 安装向导 |
| --- | --- | --- |
| Python／运行库状态／FFmpeg | 必需环境 | 必需环境 |
| llama.cpp／MFA | 可选工具与资源 | 可选分析工具 |
| 视觉投影 | 可选工具与资源 | 本地模型与资源 |
| 语言模型 | 无该行，前端模型设置维护 `server.model_path` | 可编辑 GGUF 文件 |
| wav2vec2 模型 | 无该行，保留配置中的已有值，语音资源界面维护 | 本地模型目录选择 |

`DependencyPanel(bool installMode, ...)` 决定上述差异。路径配置的 `Alignment` 投影保留已有 wav2vec2、MFA 词典和声学模型值，不因没有显示字段而清空。可选项可留空；填写或修改的路径仍接受 `PathValidation.OptionalPathsError`／`AlignmentPaths.ValidationErrorForChanges` 校验。

## 检测与状态

- Python 下方显示运行库状态及已返回项数，只有一个“运行库明细…”入口。检测清单从随程序嵌入的 `ai/runtime_dependencies.py` 读取，不固定宣称某个总项数。
- `RuntimeReport.ToProbe()` 要求 supported、completed、ready、无安装错误、清单匹配且所有项通过；不是只检查 Python 版本或某个包能 import。支持的解释器检查入口在 `PythonSetup.cs`／`DependencyDiscovery.cs`。
- FFmpeg 经 `FfmpegDependencies.Probe` 检查实际音频解码；可运行的版本命令不等同于解码通过。
- 面板 `Ready` 为：不在安装操作中、Python 运行库通过、FFmpeg 通过、可选路径校验无错。检测未完成或失败会禁用“保存／开始安装”，并显示原因。“日志未保存”单独提示，不把成功检测改判为失败。
- 自动查找与从文件夹识别使用 `DependencyDiscovery`；只找到安装包不代表工具已安装。Python／FFmpeg 的安装入口依赖实际可执行安装动作；运行库配置在明细窗口处理。模型路径显示存在也不证明模型与目标语言／投影兼容。
- MFA 行另有“查看对齐环境明细…”，展示逐项原因和修复建议，可编辑 conda 程序及环境名并重新检测。尚未配置 MFA 时也可进入明细；仅已配置且检测失败时突出行下阻塞。探针接口、与资源静态检查的区别见 [对齐检测](ALIGNMENT-VERIFICATION.zh-CN.md)。

当前 MFA 环境报告没有加入 `DependencyPanel.Ready` 的布尔表达式；可选路径校验仍独立生效。因此“允许保存／开始安装”不能作为 MFA 可实际对齐的证明。纠音资源弹窗的检测结果也不是分析提交的自动硬门禁。

## 路径保存与配置边界

`installer/src/PathSettings.cs` 的 `SavePaths` 在写入前重新检测 Python／FFmpeg，校验变更路径，并取得 `service_transition.lock` 与配置锁。它重新读取当前 JSON 后合并，保留其他设置；如果窗口打开后模型或活动预设被其他窗口切换，则拒绝保存并要求重开，避免把投影绑定写到错误模型。

保存涉及 AI 配置、已有 `install-settings.json` 中的 Python 路径、Praat profile 的 `Preferences.txt`，以及调用方指定的结果文件。`WriteTransaction` 备份原文件并逐项原子替换；失败尝试恢复，恢复失败会报告备份位置，不保证任何 I/O 故障下都能完整回滚。

`AlignmentPaths.Apply` 只合并路径及启动器字段，不重设 backend、enable 或其他对齐参数。MFA conda 字段写入 `alignment.mfa.conda_executable`／`conda_environment`；当前 `PutLauncher` 只写非空值，清空明细文本不会清除已保存的 conda 启动方式。这是现有保存路径的限制，不是用户必须保留 conda 的业务规则。资源配套编辑器另用 `pronunciation.launcher` 启动器，不能把两者当作同一份配置。

## 安装向导

步骤为“选择目录 → 检查环境 → 安装程序”。目录页验证路径语法和位置，不预先保证写入权限；环境页以共用面板状态控制“开始安装”。安装中显示引擎返回的进度／当前文件、保存配置和快捷方式状态，禁止退出及修改步骤。完成后提供“完成”，不自动启动模型；失败保留错误原因并提供重试，权限错误另提供返回目录及管理员重试。

`InstallEngine.Run` 再次验证必需环境及可选路径，检查目标 Praat 是否仍在运行，再解包、复制、合并配置和创建快捷方式。ZIP 路径经边界校验；存在 `payload-manifest.json` 时逐文件核对 SHA256。当前实现允许清单不存在，不能仅凭引擎有哈希代码断言所有输入包均被校验。写入前备份受影响文件，失败路径尝试恢复。打包由 `installer/build.ps1` 负责，源码、生成的 ZIP 和实际 EXE 内嵌负载是否一致需针对具体产物核对，不能从旧验收日期推断。

## 检查入口

脚本位于 `installer/tests/`：`Run-PathTests.ps1`、`Run-PathParentTests.ps1`、`Run-AlignmentPathTests.ps1`、`Run-DependencyTests.ps1`、`Run-ContractTests.ps1`、`Run-PythonSetupTests.ps1`。运行库与 MFA 聚合检查为 `Run-LoudnessFullRegressionTests.ps1`；独立 MFA 宿主测试在 `MfaAlignmentProbeTests.cs`。这些脚本可能启动 WinForms、子进程并写临时／测试记录；需要可写临时目录和对应依赖，单元／契约结果不代替干净环境的真实安装验收。

目录内的 `path-settings-preview-20261014.html`、`install-wizard-preview-20261014.html` 与 PNG 是交互预览资源，不执行生产检测、配置保存或真实安装，也不描述当前机器是否已安装依赖。
