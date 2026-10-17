# 桌面宿主、构建与 Windows 完整性

## 启动与生命周期

正常入口是本项目 Praat → 导入音频 → 查看并编辑 → 前端／启动前端。`ai/start_ai_chat.py`、`run_ai_chat.py`、`praat_ai/desktop_launch.py` 与 `modern_host.py` 负责启动、依赖／资产预检和桌面生命周期；预检不启动模型或发送模型探测请求。维护入口为 `python ai/run_ai_chat.py`，`--legacy` 显式打开旧 Tk 窗口；现代宿主另有 `--config`、`--data-dir`、`--allow-cloud` 参数。

`modern_host.py` 从 `ai/frontend/dist` 提供离线资产，用随机端口的 `127.0.0.1` HTTP 服务承载页面。服务拒绝目录列表、错误 Host 头和越过资产根的路径，返回 no-store、CSP、nosniff 等响应头。`HostAPI` 对页面 origin 复核，公开桥接入口只有 `rpc`，应用对象保持私有。聊天接口见 [宿主契约](HOST-CONTRACT.md)。

首次生产 bootstrap 写私有就绪记录；重复菜单使用恢复／聚焦已有窗口。只有显式绑定的原生父进程身份被监视，绑定父进程退出时窗口关闭。Praat 启动快照不是实时测量，也不是任务执行目标；任务提交另行捕获、核对目标。启动失败经 `desktop_launch.report_failure` 报告并清理凭据文本，不能只依靠 pythonw 不可见的标准错误输出判断状态。

Windows 使用 `gui='edgechromium'` 和 `private_mode=True`；会话、草稿、阅读位置和尺寸偏好由宿主 SQLite／配置保存，不把 WebView profile 当作持久数据。运行构建产物需要 WebView2 Runtime 与 `ai/requirements.txt` 的 Python／.NET 依赖，不需要 Node。

普通桌面入口设置 `configured_cloud=True`，允许当前启用的 API 配置；直接构造 `ModernApplication` 默认不允许远程请求，`--allow-cloud` 为显式覆盖。该许可不自动扩展到本地模型栏的远程 URL。启动／设置保存不发模型请求；设置页对远程测试提供网页确认，Python `settings.test` 本身仅做配置／端点检查并执行请求，不含原生确认对话框。

## WebView 外观与窗口拖动

聊天、AI 纠音、语音词典与模型、Anki 的生产创建路径分别在 `modern_host.py`、`ui.py`、`speech_dictionaries.py`、`anki_integration.py`。它们没有透明材质挂接，不设置整窗 opacity、颜色键或 DWM／Acrylic／BlurBehind 材质。此描述只涉及这些 WebView，不涉及 Praat 原生波形、编辑器和菜单外观。

`ai/frontend/src/styles.css` 的 `#root` 绘制不透明主题基色（浅色 `#fff`，深色 `#1b1b1e`）。主区、侧栏、卡片的 RGBA 表面是在该基色上分层，不透出桌面；CSS `backdrop-filter` 只处理网页内部合成。标题下 14px 与输入栏上 18px 的渐隐／模糊过渡层均为 `pointer-events:none`，消息区保留 mask 渐隐。普通 checkbox 显示为 32×20px 开关；锁定／多选例外保持方框，radio 保持单选，checked／disabled／标签与键盘语义仍由原生输入控件承担。

WebView2 [`DefaultBackgroundColor`](https://learn.microsoft.com/en-us/dotnet/api/microsoft.web.webview2.core.corewebview2controller.defaultbackgroundcolor) 的背景 alpha 约束不能作为桌面微透已实现的证明；当前不透明外观无需依赖桌面材质效果。预览 HTML／CSS 和截图位于 `webview-ui-design/`，不是生产资产或当前行为的验收依据，来源见 [预览资源说明](webview-ui-design/prototype/gradient-grid-sources.md)。

`Chat.tsx` 将标题区非交互元素的左键 pointerdown 交给 `window.drag`。`ModernApplication.begin_window_drag()` 取得显式窗口的 WinForms HWND，通过 `BeginInvoke` 在 UI 线程释放鼠标捕获，再投递 `WM_NCLBUTTONDOWN / HTCAPTION`（`0x00A1 / 2`）。非 Windows、无 native 或无句柄时返回失败原因。按钮、链接、输入及 contenteditable 不启动该拖动；`{ok:true}` 表示已经安排原生调用，不是窗口已移动的测量结果。

## 前端构建与检查入口

在 `ai/frontend` 中执行：

```powershell
npm ci
npm run build
npm test
npm run test:ui
```

`prebuild` 执行 `scripts/offline-assets.mjs` 和 `license-notices.mjs`，复制 Mermaid 离线资产、许可证／来源及可重建前端输入到 `public/`；`build` 执行 TypeScript 和 Vite，输出 `dist/`。当前 `vite.config.ts` 设置 `treeshake:false`，不能用旧包体或构建计数判断当前资源是否一致。构建会写来源镜像和产物，不是只读检查。

只更新前端不需要重编 Praat EXE，但要关闭并重新打开 AI 窗口加载新 bundle。生产聊天页面缺少宿主时拒绝操作；`src/main.tsx` 的聊天演示适配器只在开发模式、loopback、显式 `?demo=1` 且无 pywebview 时启用。

`npm run test:ui` 使用 Playwright 浏览器夹具；`PI_PLAYWRIGHT_CHANNEL=msedge` 可选择已安装的 Edge。桌面入口检查为 `ai/tests/verify_modern_desktop.py`，窗口拖动检查为 `ai/tests/test_window_drag.py`。浏览器／模拟执行器结果不替代真实 WebView2、Praat 测量或模型能力验证；源码构建与安装器负载也必须分开看待。

## Windows 文件标签与进程 token

Windows MIC 在普通 DACL 之外限制写入。若工作目录带可继承的 Low 标签，在其中新建或复制 EXE 可使文件成为 Low；从普通桌面启动时进程完整性受启动方与程序文件较低级别约束，Python 子进程继承该级别。Low Praat／Python 写 Medium 的 `ai/runtime/status.json` 可被拒绝，即使 DACL 允许当前用户修改。相同版本／字节／SHA256 不代表相同权限。

`tools/NativeIntegrity.ps1` 的 `FileLevel` 通过 `LABEL_SECURITY_INFORMATION` 读取 mandatory label，没有显式标签 ACE 时按 Medium（8192／`S-1-16-8192`）处理；`ProcessLevel` 读取真实进程 token。Low 为 4096／`S-1-16-4096`。文件标签改变不会修正已经启动的 Low 进程，保存工作退出后，需从普通桌面重新启动。

`tools/Publish-NativeExecutable.ps1` 的当前行为：

1. 要求发布进程至少 Medium，中转目录恰为 Medium，且中转与最终 EXE 同卷。
2. 若最终文件已是 Medium 且哈希一致则直接返回；否则在 Medium 目录中新建文件，只复制字节并核对标签与 SHA256。
3. 将原目标移动到目标目录的 `.aipraat-backups/`，再以同卷移动将中转对象放到最终路径，保留其安全描述符。
4. 核对最终路径标签及 SHA256；失败时恢复原目标并抛错，不把失败降为警告继续发布。

最终 `Copy-Item` 或跨卷复制不能替代这里的同卷移动。`icacls /setintegritylevel Medium` 需要相应的 `WRITE_OWNER` 权限，工具进程是 Medium 本身不保证有该权限。完整性问题不通过放宽 runtime、IPC 或私有配置 ACL 解决。

发布调用入口包括根 `Makefile` 的 Windows 链接步骤、`tools/build-python-bridge-fix.ps1`、`本机开发/build-model-path-menu.ps1` 以及 `installer/build.ps1` 的启动器／路径工具／安装引导。`本机开发/apply-model-path-menu.ps1` 还支持在既有 Medium 文件对象上原位写入并复核。安装器实际复制路径为 `installer/src/InstallEngine.cs`，其文件复制不调用上述发布脚本；不能把“构建 EXE 检查通过”推断成任意目标目录安装后的标签保证。

检查入口 `ai/tests/verify_native_integrity_startup.py` 使用相同程序字节、隔离 runtime、Low／Medium 文件及进程对照，通过原生菜单验证准备状态，不发送模型请求；需同时核对 runtime 目录和状态文件标签，不能只检查中转候选。这里不对当前机器目录、现有 EXE 标签或已运行进程级别作未检测的断言。

机制依据：[Mandatory Integrity Control](https://learn.microsoft.com/en-us/windows/win32/secauthz/mandatory-integrity-control)、[SECURITY_INFORMATION](https://learn.microsoft.com/en-us/windows/win32/secauthz/security-information)、[File Security and Access Rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights)、[MoveFile](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefile)。
