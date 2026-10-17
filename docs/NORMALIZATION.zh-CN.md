# 纠音归一化的方法与限制

归一化由 `ai/praat_ai/normalization.py` 和 `normalization_prosody.py` 实现，通过纠音表单的独立元音校准、相对音高与整段响度选项启用，默认关闭。它为[音位声学观察](PRONUNCIATION.zh-CN.md)添加派生轨迹，不覆盖音频、Sound、原始曲线或 MFA 输入，也不增加判错阈值或总分。

## 独立元音校准

共振峰采用 Lobanov z 标准化：每个说话人分别从独立多元音校准录音拟合均值和标准差，再转换其待测 F1/F2/F3。算法来源是项目内适配的 VowSpace；不从待测音位本身拟合。

协议 `balanced-monophthong-middle-40-60-three-v1` 使用以下暂定清单，不代表已验证的准确率阈值：

| 读音判据 | 单元音 |
| --- | --- |
| `ja-JP-standard-v1` | `a i ɯ e o` |
| `en-US-general-v1` | `i ɪ ɛ æ ɑ ɔ ʊ u ʌ` |

每个元音取三个有效 token；每个 token 使用其时长中间 40%–60% 区间的中位数。测量采用 Burg、5 个共振峰、最高共振峰 5500 Hz、步长 0.01 s、窗长 0.025 s、预加重起点 50 Hz，与 `MEASUREMENT_SETTINGS` 一致。

分别为标准说话人和学习者导入独立 WAV 及 TextGrid。TextGrid 须有唯一的 `phones`／`phonemes` IPA 区间层。说话人、语言、读音及测量设置需要匹配并确认；换说话人、读音或分析对象后重新导入。选择保存在当前表单，提交时复制快照。

缺一方、覆盖不足、区间不可靠、零方差或设置不匹配时保持原始 Hz 观察，并报告原因。缺 F3 不阻止有效 F1/F2。派生轨迹独立命名为 `f1_lobanov_z`、`f2_lobanov_z`、`f3_lobanov_z`；未定义值不补零。

## 相对音高

有效有声 F0 转换为半音后减去各自基线，不拉齐音域、不消除音高动态。使用适配后的 librosa 转换函数；疑似八度跳变、缺口及低质量帧保留状态，不自动推断声调或重音。

支持两种基线：

- `utterance`：双方各自待测句的可靠有声帧。
- `independent`：双方各自提供同协议、人工确认的中性语句，不能使用待测录音本身。语言、读音、提取协议和 F0 测量范围须一致，至少有连续可靠有声帧。

派生轨迹为 `pitch_relative_st`。独立基线不等于完整说话人校准，不能据此声称说话人差异已消除。

## 整段响度

正常长度录音使用 `pyloudnorm==0.2.0` 的整段 400 ms 门控 LUFS，按恒定增益生成派生强度和 RMS。任一侧不足 400 ms 时双方统一使用明确命名的活动区间 RMS；不补静音、不缩短 LUFS 块，也不把 RMS 标成 LUFS。

轨迹名称区分 `intensity_lufs_adjusted_db`、`rms_lufs_adjusted` 和 `rms_active_relative_db`。处理不改写 WAV。目标响度及算法版本以 `normalization_prosody.py` 常量为准，LUFS 依赖还包括 requirements 约束内的 SciPy。

## 状态、依赖与验证

报告分别记录关闭、已应用、证据不足和不适用，保留所用协议、校准来源、原始及派生结果。缺失依赖时保留可用原始结果并给出“路径配置 → 配置运行库…”入口，不静默安装。

运行库安装要求见[安装说明](../installer/README.zh-CN.md)。离线测试入口包括 `test_normalization.py`、`test_normalization_prosody.py`、`test_normalization_prosody_pipeline.py`、`test_normalization_upstream_evidence.py` 和 `test_normalization_ui.py`。上游数值对照使用 `ai/tests/verify_normalization_upstream.py`。

数值一致、协议检查或合成音测试不能证明真实纠音有效性。真实录音须通过[材料协议](NORMALIZATION-MATERIALS.zh-CN.md)，并由人工核对声学观察及判断。

来源与许可证见 [VowSpace](../ai/praat_ai/_vendor/vowspace/NOTICE.md)、[librosa](../ai/praat_ai/_vendor/librosa/NOTICE.md) 和[借物表](../CREDITS.zh-CN.md)。
