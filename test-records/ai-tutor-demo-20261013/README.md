# 音频备用读取与 FFmpeg 配置验证

2026-10-13，Windows x64。根目录 `Praat.exe`、`AIPraat-paths.exe` 和 `AIPraat-install.exe` 已更新。原有运行中的 Praat 实例保留，重新启动后使用新主程序；旧主程序备份为 `Praat-before-ffmpeg.exe`。

## 读取规则

普通打开、拖放、脚本 `Read from file` 和 Anki 读取进入同一个 Sound 读取层：先尝试 Praat 原生解码，明确失败后调用 FFmpeg 写入唯一临时目录下的 24 位 PCM WAV，再原生读入。成功和失败均清理临时目录。不修改源文件、不指定重采样或声道混合。Anki 后台只投递一次命令，超时不触发重投或第二套转换。

## 验证证据

| 检查 | 结果 / 文件 |
|---|---|
| 两份真实 OGG + 一份 AAC 普通读取 | `native-reader-tests.log`：读取和导出成功，源文件 SHA-256 不变，临时目录已清理 |
| 原生优先 | 将 FFmpeg 设置为不存在的路径，OGG 仍成功；AAC 失败且没有遗留临时目录 |
| 保存的共享路径 | 没有 `PRAAT_AI_FFMPEG` 测试覆盖值时，C++ 读取 `audio.ffmpeg` 成功 |
| 实际 FFmpeg 探测 | `ffmpeg-tests.log`：程序版本 + 公有领域 OGG → WAV 实际解码 |
| 路径保存与交互 | 真实依赖探测通过、共享路径保存、控件位置和检测/安装按钮、异步检测通过后启用保存 |
| 无效路径 | `path-missing.json`：提示缺失、保存禁用，未写入开发机配置 |
| 一键安装后台 | 实际联网下载、发布方 SHA-256 校验、安装、安装后解码；安装日志见 `%LOCALAPPDATA%\AIPraat\logs\ffmpeg` |
| 校验失败 | 离线 ZIP 的错误校验值被拒绝，已有 FFmpeg 文件 SHA-256 不变 |
| Python 相关回归 | `python-tests.log`：34 项通过 |
| 现有路径配置回归 | `path-tests.log`：15 项通过 |
| Anki 前端 | `frontend-anki-tests.log`：安装的 Edge 上 2 项通过；默认 Playwright 浏览器缺失，改用现有 Edge 后成功 |
| 安装包一致性 | `package-tests.log`：主程序、路径窗口、安装脚本、Python 模块和前端入口与包内清单 SHA-256 一致 |

`ffmpeg-path-settings.png` 是测试中实际 WinForms 窗口的 `DrawToBitmap` 渲染图，已人工检查。Computer Use 的实时窗口截图返回 `window capture timed out: timed out waiting on channel`，因此本记录不将渲染图称为实时截图。

原生测试为隔离运行使用工作区内的可写 TEMP/TMP 和独立 Praat 首选项目录。程序默认仍使用操作系统临时目录。Anki 的实际牌组操作未在本机验证；本次覆盖桥接后台、前端及共享原生读取层。

## 样本来源

- `En-us-hello.ogg`：[Wikimedia](https://commons.wikimedia.org/wiki/File:En-us-hello.ogg)，Dvortygirl，公有领域。下载地址：`https://upload.wikimedia.org/wikipedia/commons/5/52/En-us-hello.ogg`。SHA-1：`4cda0527da53ae7534968dcba6ec986c243ff072`。
- `En-uk-hello.ogg`：[Wikimedia](https://commons.wikimedia.org/wiki/File:En-uk-hello.ogg)，Judith Franck / Association Shtooka，CC BY 3.0 US。下载地址：`https://upload.wikimedia.org/wikipedia/commons/4/48/En-uk-hello.ogg`。SHA-1：`b7b60280c9a307cb762a72412f8f4d55047680ba`。
- `hello-fallback.aac`：使用 FFmpeg 从公有领域的美音样本生成，用于覆盖本机 Praat 原生读取失败、备用转换成功的分支。

## 重跑

```powershell
<WORKSPACE>\venv-ai-modern\Scripts\python.exe ai/tests/verify_audio_ffmpeg.py --exe Praat.exe --ffmpeg "$env:LOCALAPPDATA\AIPraat\ffmpeg\ffmpeg.exe" test-records/ai-tutor-demo-20261013/En-us-hello.ogg test-records/ai-tutor-demo-20261013/En-uk-hello.ogg test-records/ai-tutor-demo-20261013/hello-fallback.aac
powershell -NoProfile -ExecutionPolicy Bypass -File installer/tests/Run-FFmpegTests.ps1 -FFmpeg "$env:LOCALAPPDATA\AIPraat\ffmpeg\ffmpeg.exe" -Python <WORKSPACE>\venv-ai-modern\Scripts\python.exe
```

后一命令加 `-Install` 可验证一键安装的联网后台，会再次下载并更新托管 FFmpeg。

测试安装包目录 `install-test` 的清理被自动审批以 `blocked by policy` 拦截，因此保留了离线测试 ZIP 和安装副本。它们不会进入生产安装包。
