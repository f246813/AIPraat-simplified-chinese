# Wav2Vec2 参考模型

目录 `wav2vec2-lv-60-espeak-cv-ft/` 保存 `facebook/wav2vec2-lv-60-espeak-cv-ft` 的权重、配置、预处理器、tokenizer 和音素词表；固定提交见 [资源清单](../资源清单.json)。

`vocab.json` 是音素 token 词表，不是单词到发音的 MFA 词典。上游没有随模型提供独立单词发音词典，本目录不将 token 词表称为发音词典。

模型要求 16 kHz 输入，输出音素标签。AIPraat 的 Wav2Vec2 模型管理中选择包含 `pytorch_model.bin` 和配置文件的目录。运行依赖见项目 `ai/requirements-wav2vec2.txt`。参考资源下载完成不代表已在当前环境完成推理或验证各语言对齐质量。

来源和许可见 [来源及网址](../来源及网址.md) 与上游模型卡。
