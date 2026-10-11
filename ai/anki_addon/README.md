# AIPraat · 查看语图

在 AIPraat 的“打开 → Anki 连接…”导出 .ankiaddon，然后在 Anki 的
“工具 → 插件 → 从文件安装”中安装并重启。默认在有音频的卡片下方显示“查看语图”。
也可使用 Ctrl+Alt+P。多音频卡片会先让你选择文件。

从 GitHub Release 下载的插件会读取当前 Windows 用户的 `Preferences.txt`，
自动定位 AIPraat 安装目录和已配置的 Python。请先启动一次 AIPraat 并完成 Python 配置，再重启 Anki。
在 AIPraat 中导出的插件则直接写入本机路径；安装目录变更后重新导出即可。

## 改两个配置复用放大镜

在 Anki 插件管理中选择本插件、点击“查看文件”，用 VS Code 打开 entry.js。
把 `AIPRAAT_ENTRY = "button"` 改为 `"magnifier"`（或 `"both"`）。
第二行 `AIPRAAT_MAGNIFIER` 是原按钮的 CSS 选择器。若不清楚选择器，打开
卡片模板，查找 goldendict 对应的 `<a>`：如 class="search-link"，填 `.search-link`。
保存后重新显示卡片即可生效，不需要改 Android / iOS 卡片模板。

本入口取得的是当前卡片真实音频，而不是仅凭单词查找同名文件。
普通 [sound:...] 字段无需配置；插件配置中的 audio_field 可指定只读取某个字段。
start_field / end_field 可指定笔记里的秒数字段，留空时打开音频开头视窗。

## M4A

Windows 普通“打开文件”也可直接读取 M4A，使用系统解码器，保留采样率和声道。
在系统解码失败或使用 OGG/Opus/AAC 时，可由 FFmpeg 按需生成临时 WAV。
在 Anki 连接窗口设置 ffmpeg.exe，或放入 PATH。WAV 读取完成后清理，原音频不修改。
程序显示备用路径的执行结果，不会在 Praat 无响应时重复投递。
