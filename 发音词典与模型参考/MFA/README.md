# MFA 发音词典与声学模型

| 目录 | 配套词典 | 声学模型 |
| --- | --- | --- |
| `english_us_arpa/` | `english_us_arpa.dict` | `english_us_arpa.zip` |
| `mandarin_mfa/` | `mandarin_china_mfa.dict` | `mandarin_mfa.zip` |
| `japanese_mfa/` | `japanese_mfa.dict` | `japanese_mfa.zip` |

使用官方 v3.0.0 资源；不同语言与音素集的词典、模型应配套选择。AIPraat 的“管理语音词典与模型”中添加词典 `.dict` 和声学模型 `.zip`，分别选用相应语言。日本语资源已包含词典和声学模型。

官方 MFA 安装器也支持 `mfa model download dictionary japanese_mfa` 与 `mfa model download acoustic japanese_mfa`。命令会选择官方当前版本，未必与本目录固定版本一致。

这些模型用于强制对齐，需要相应文字材料；不能仅凭模型对齐结果判断发音质量。来源和许可见 [来源及网址](../来源及网址.md)。
