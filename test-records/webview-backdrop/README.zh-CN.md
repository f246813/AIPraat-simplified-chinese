# WebView 宿主透明验证证据（历史）

> **已退役（2026-10-15）。** 桌面微透被放弃，本目录只作历史证据；生成这些记录的实现与脚本已移至 `scratch/abandoned-webview-transparency/`。

完整实施/范围/限制/命令见：[`../../docs/ai-frontend/WEBVIEW-HOST-TRANSPARENCY-RESULTS.zh-CN.md`](../../docs/ai-frontend/WEBVIEW-HOST-TRANSPARENCY-RESULTS.zh-CN.md)。

最终资产 index SHA256：`a76b5377cf618bafe64508cb4ccccd39282ba03f864ed222c540153e94938e2e`。

## 最终证据（优先阅读）

- `final-manifest.json`：最终四浅色、四深色、透明关闭回退共九轮，同 SHA、起止一致、全部 passed；收尾真实注册表/进程检查。
- `final-light/{chat,pronunciation,dictionaries,anki}/result.json` 与对应 PNG。
- `final-dark/{chat,pronunciation,dictionaries,anki}/result.json` 与对应 PNG。
- `final-transparency-off/chat/result.json`：Windows 实际透明偏好关闭后的不透明回退。
- `final-light/dictionaries/modal-roi-report.json` 与 `final-dark/dictionaries/modal-roi-report.json`：从原始 PNG 计算全客户区/模态外侧/正文 ROI；不要把正文中心 0 错判为整个客户区 0。

截图：

- `production-background-{1,2}.png`：生产页面，真实桌面 / 真实 Notepad 背后窗口。
- `chat-expanded-sidebar-*`、`settings-*`：真实展开侧栏与设置页。
- `mfa-presets-*`、`mfa-resource-dialog-*`：配套列表和 MFA 模态；repeat 图用于 A→B→A 噪声检查。
- `ACTUAL-BACKGROUND-*`：宿主隐藏时的真实背景。
- `NATIVE-ONLY-*`：隐藏 WebView2 子控件的原生底层诊断，不是生产 UI 验收截图。

## 其他材料

- `unit-tests.log`：新增宿主测试 17 项。
- `modern-suite.log`：modern 系列 112 项；`modern-host-tests.log` 为其另跑子集，不重复计数。
- `tutor-tests.log` / `dictionary-tests.log` / `anki-tests.log`：6 / 19 / 10 项。
- `python314-isolated-tests.log`：不依赖 pydantic/Pillow 的独立宿主测试也可在默认 Python 3.14 运行。
- `final-dark/*-build-race.log`：并发 build 临时删除 dist 导致的两次启动失败；已同 SHA 重跑成功，保留失败日志。
- `final-pages-light/dictionaries/modal-roi-report.json`：旧 blur5 的历史对照，仍能证明外围有桌面变化；不是最终版。
- 其余 `attempt*` / `chat-attempt*` / `latest-light` / `modal-check` / 根层四窗口目录为调查过程，不能覆盖最终证据。探索期材质名称可能尚未更正，不将它们当最终实现状态。

恢复：最后 `EnableTransparency=1`、`AppsUseLightTheme=1`；所有最终轮都正常恢复系统偏好、执行 UndoMinimizeAll、自建 Notepad 关闭。Anki 仅读取已有连接设置，未 save/export，不宣称其配置隔离到临时目录。
