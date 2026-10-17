# 共享依赖面板验收

安装向导第二步与路径配置共用 DependencyPanel。必需项为 Python、13 项 Python 运行库、FFmpeg；可选分析工具和模型资源默认折叠，详细结果按需展开，缺失项提供“处理…”。保留运行库安装/修复窗口与 FFmpeg 安装脚本。

“自动查找”仅访问已有配置、项目约定目录、AIPraat 托管目录、PATH 和 Windows 已注册 Python，不递归扫描磁盘。“从文件夹识别…”才递归用户选中的目录，跳过链接，多个程序候选由用户选择。安装包与已就绪程序分开，GGUF 文件不自动推测用途或配套关系。

验证使用真实 Python 3.12.14 完整环境及 FFmpeg 9.0.2。共 84 项检查通过：搜索 7、路径配置 15、Python 设置 4、对齐资源 12、运行库 C# 9、运行库 Python 10、安装器契约 21、FFmpeg 4、父进程生命周期 2。各套输出保存在同目录的 dependency-discovery-tests.log 与 shared-panel-*.log。

搜索测试覆盖范围限制、有效手动路径保留、多个候选交互选择、损坏 ZIP 后继续识别、wheel 元数据识别、递归发现的间接 wheel 离线安装、安装包不提前就绪、模型不自动配对、路径修改立即撤销旧就绪状态。实际安装的 ai_config.json 已确认保存验证后的 FFmpeg 完整路径；安装与保存均保留最终复检。

根目录 AIPraat-paths.exe 与 AIPraat-install.exe 已重新构建。shared-panel-delivery.log 确认两者包含共享面板及识别实现，嵌入运行库脚本、FFmpeg 脚本和解码样本与源码一致，包内程序与根目录交付一致，安装器 SHA-256 与 build-report.json 一致。Praat.exe 沿用已验证的原生读取 → 明确失败后临时 WAV → 再读入实现。

界面图由 WinForms 测试 DrawToBitmap 渲染，不是桌面截图：

- dependencies-paths-ready.png：路径配置必需项就绪。
- dependencies-wizard-ready.png：安装向导第二步，必需项就绪，下一步启用。
- dependencies-wizard-expanded.png：展开模型资源，确认所有路径行位于可视区域内。
- dependencies-package-pending.png：仅找到安装包，仍保持待安装。
