# Anki 一键查看语图

重启更新后的 Praat，在对象窗口“打开 → Anki 连接…”打开设置；声音编辑器的“前端”菜单也有同一入口。

1. 选择“新增查看语图按钮”“复用原放大镜”或“保留两个入口”。
2. 点击“导出 Anki 插件”，在桌面 Anki 的“工具 → 插件 → 从文件安装”中安装 `.ankiaddon`，重启 Anki。
3. 打开带本地音频的卡片，点击入口。多段音频先选择；程序打开对应 SoundEditor，开启语图，并复用已经打开的相同音频。

插件自动读取当前 Anki 配置档的 `collection.media`，无需手动复制声音文件。默认收集当前卡片正反面的 `[sound:...]` 文件；如只想打开单词发音，把“卡片音频字段”设为该笔记的单词音频字段名。TTS 标签没有对应的本地文件。

如果一张卡片对应长录音中的一段，可以填“开始秒数字段”和“结束秒数字段”，字段值使用秒数，例如 `12.4` 和 `13.1`。打开时会选中这段并调整可视范围。没有时间字段时，从声音开头显示最多五秒；可继续缩放查看整段录音。

## 用 VS Code 改放大镜

在 Anki 插件管理中选择本插件，点击“查看文件”，打开 `entry.js`：

```js
const AIPRAAT_ENTRY = "magnifier"; // button / magnifier / both
const AIPRAAT_MAGNIFIER = ".Search, .search a, .dict-link, #dict-link";
```

保存后重新显示卡片即可生效。`button` 使用新按钮，`magnifier` 复用放大镜，`both` 同时提供两者。自定义卡组只需修改下一行的 CSS 选择器。

已核实 JLPT 卡组的放大镜使用大写 `.Search`，默认选择器已包含它。它原来的点击脚本按平台选择 MOJi / GoldenDict 等词典；Windows 分支的 URL 只有单词。你提供的 `goldendict://xn--88j6e391o` 对应“接する”，不包含声音文件或时间。

因此这里复用的是放大镜的点击入口，后台通过 Anki 的卡片对象取得真实音频。移动端的卡组模板和词典设置继续按原来的方式工作。安装本插件无需接管系统的 `goldendict` 协议。

## M4A 和临时 WAV

Windows 的普通“打开文件”和 Anki 入口都支持原生读取 M4A：使用系统 Media Foundation 解码为浮点 PCM，直接创建 Praat Sound，保持解码器输出的采样率和声道；不启动外部转码程序，不生成中间 WAV。AAC M4A 已用实际文件验证。M4A 是容器，其他编码是否支持取决于 Windows 提供的解码器。

保留“原生读取失败时按需解码为临时 WAV”。备用路线需要 `ffmpeg.exe`，可在设置中选择，或通过 PATH 查找。仅在 Praat 明确返回读取失败后转换；不会在结果未知的超时情况下重投。转换选择第一条音频流，使用 24 位 PCM WAV，不指定重采样或声道混合；Praat 读入完成后删除临时文件，并以原文件作为对象关联路径。原音频不会被修改。

连接设置存放于 `ai/anki_config.json`，与 AI 模型配置分开。从 GitHub Release 下载的插件会读取当前 Windows 用户的 `Preferences.txt`，自动定位 AIPraat 安装目录和已配置的 Python；请先启动一次 AIPraat 并完成 Python 配置，再重启 Anki。在 AIPraat 中导出的插件会写入当前 Python 和桥接程序路径；移动安装目录后重新导出安装插件。

## 开发接口

桌面插件使用 Anki 的 `card_will_show`、`webview_did_receive_js_message` 和 `pycmd`。入口后台为 `ai/praat_ai/anki_bridge.py`；原生命令为：

```praat
Open Anki audio: "音频路径", 0.2, 0.8, "结果文件路径", "原音频路径"
```

结果写入 `ok` 或 `error`。命令必须在 GUI 实例执行，通过现有 Message.txt / WM_APP 通道定向投递。Anki 和 AI 共享跨进程命令锁，并保持到结果确认，避免覆盖彼此的消息。

源码依据：[Anki 声音标签接口](https://github.com/ankitects/anki/blob/main/pylib/anki/sound.py)、[JLPT 放大镜模板](https://github.com/5mdld/anki-jlpt-decks/blob/main/deck-source/templates/eggrolls-JLPT10k-v3.5-%E6%97%A5-%E4%B8%AD-front.html)、[Microsoft 音频解码教程](https://learn.microsoft.com/en-us/windows/win32/medfound/tutorial--decoding-audio)。

构建：`installer/verification/build-anki-m4a.ps1`。原生验证：`installer/verification/verify-anki-m4a.py`；后台测试：`ai/tests/test_anki_bridge.py`；界面和放大镜测试：`ai/frontend/tests/browser/anki-integration.spec.ts`。本机未安装 Anki，插件接口已对照官方源码并做模拟测试，实际牌组仍需在 Anki 中验证。
