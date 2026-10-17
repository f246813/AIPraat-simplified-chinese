# 双音频声学对比与音位判据

## 入口与输入

在 Praat 中选中两个 Sound，打开“开始 AI 纠音”，指定标准音和学习者录音。`ai/run_ai_tutor.py` 调用 `praat_ai.tutor.run_from_praat`；表单使用 `ui.py` 和 React `PronunciationForm.tsx`，提交后由 `run_tutor` 执行分析。基础计算不要求大模型，文字和图表解释是可选步骤。

目标读音有两种来源：

- 手动填写 IPA。表单产生的等分区间只是定位种子，不是人工音素边界；分析仍需适用的对齐资源。
- 选择 MFA 配套读音预设，用参考文本生成读音并确认候选。`mfa_g2p.py`、`mfa_resources.py` 和 `mfa_task.py` 管理 G2P、词典、声学模型及任务快照。修改文本、IPA 或资源后须重新生成或明确切回手动模式。该路径使用 `mfa-strict`，失败不执行比例回退。

高级 JSON 请求可提供双方音素边界，单位为秒。例如：

```json
{
  "reference_object": 1,
  "learner_object": 2,
  "language": "ja",
  "profile_id": "ja-JP-standard-v1",
  "phonemes": [
    {"ipa": "aː", "reference_start": 0.0, "reference_end": 0.4,
     "learner_start": 0.0, "learner_end": 0.3},
    {"ipa": "n", "reference_start": 0.4, "reference_end": 0.6,
     "learner_start": 0.3, "learner_end": 0.5}
  ],
  "qwen_explain": false,
  "qwen_vision": false
}
```

对象、音位和时间必须替换为实际材料。`profile_id` 与语言不匹配时报错，不自动切换规则。不填写 `profile_id` 使用通用 DTW 声学对比，而不是音位规则。兼容请求中的 `minimum_error_duration_sec` 被忽略。

## 定位与声学测量

`forced_alignment.py` 提供 MFA、wav2vec2/CTC 和比例定位。自动路径可合并多个后端；没有可用结果时，非严格模式可能使用比例定位。不同来源的置信值不是经过校准的正确概率。目标标签匹配不代表已独立识别出学习者实际读出的音素。

`audio.py` 从 PCM WAV 提取 RMS、过零率、频谱重心、高频占比，并通过 Parselmouth 提取基频、强度和 F1/F2/F3。`pronunciation.py` 管理双方边界、特征及比较。当前实现逐音素调用整段声学提取；Parselmouth 异常可能返回空特征，缺失原因尚未逐特征暴露。

## 英语与日语判据

`pronunciation_rules.py` 提供 `en-US-general-v1`（英语美式）和 `ja-JP-standard-v1`（日语标准语）。两者均为实验性声学观察，不是发音判错模型。

| 音类 | 适用观察 | 不能据此确认的内容 |
| --- | --- | --- |
| 元音及双元音 | F1/F2、相对时长 | 音素替换、重音或声调对错 |
| 擦音 | 频谱重心、高频占比、过零率、相对时长 | 实际音素身份 |
| 塞擦音 | 频谱重心、高频占比、相对时长 | 闭塞和释放的独立分段识别 |
| 塞音 | VOT 测量可用性、相对时长 | 当前提取器未提供 VOT 时，该项保持缺测 |
| 鼻音 | 频谱重心、相对时长 | 鼻音替换或鼻化诊断 |
| 近音 | F2/F3、相对时长 | 单凭共振峰确认替换音 |
| 日语 `cl`、长辅音 | 相对时长 | 日语拍结构和促音判错 |
| 未支持音段 | 未评估 | 不删除未知符号来冒充支持 |

音类使用 PanPhon 发音特征。日语保留长度、无声化和 `N/I/U/cl` 标签；相对时长是音素时长与邻近可靠音素平均时长之比，不是拍结构分析。

规则观察要求双方适用区间和足够有效测量。定位筛选使用 `0.70` 工程界限，比例或表单等分定位不支持音位声学结论。越界、重叠、数量或同位置标签不匹配时不能生成对应观察。原始 `phone_index` 贯穿定位和报告；`learner_phone_index` 标识可关联的学习者区间。

`observed`、`partial`、`duration_only`、`insufficient_evidence`、`not_evaluated`、`not_applicable` 分别表示已生成观察、部分可评估、仅时长、证据不足、未评估和不适用。时长观察不计作非时长声学检查通过。没有独立音素识别、已校准判错阈值或百分制总分。

## 通用 DTW 路径的限制

通用路径按原始曲线和阈值生成 `errors`，其中的分数是声学差异量，不是发音准确率。该路径的覆盖状态不完整：空特征可能得到零差异，未定义学习者特征可能生成带零值的偏差项。**零差异不能证明已经评估或发音正确；缺测也不能证明读错。** 这是当前实现缺口，不是期望的纠音规则。它与音位规则的证据状态不能混用。

## 输出与模型解释

持久报告默认位于用户主目录 `Praat AI Reports` 的独立文件夹，包含 JSON、TextGrid 和可用时的 PNG。`PRAAT_AI_REPORT_DIR` 可修改根目录，但不能位于 Praat 临时回导目录内。

- JSON 保存原始轨迹、双方边界、定位来源、警告；音位规则另保存 `profile_snapshot`、`observations`、`phone_assessments`、`check_statuses`，其 `errors` 为空。
- 音位规则 TextGrid 提供 `Target_Phones`、`AI_ProblemCandidates`、`AI_EvidenceStatus`、`AI_Observations`，保留学习者边界与空隙。
- PNG 展示曲线或相对时长。图像不可用不等于没有测量结果。
- `qwen.py` 可生成文字或图表解释。当前是独立请求路径，未接入云端对话同一套报告守卫；提示约束不构成解释正确性的保证。正式 tutor 分析尚无贯穿全链路的取消参数，MFA 正式对齐调用尚无超时参数。

归一化见[方法与限制](NORMALIZATION.zh-CN.md)，真实材料要求见[验收材料协议](NORMALIZATION-MATERIALS.zh-CN.md)。

## 验证与来源

离线检查入口：`ai/tests/test_pronunciation.py`、`test_pronunciation_rules.py`、`test_forced_alignment.py`、`test_bridge_pipeline.py`、`test_mfa_tutor.py`。合成音和模拟对齐验证链路、状态和数值，不是实际纠音准确率证据。

PanPhon 固定来源、数据和许可证见 [NOTICE](../ai/praat_ai/_vendor/panphon/NOTICE.md) 与 [LICENSE](../ai/praat_ai/_vendor/panphon/LICENSE)。资源导入不表示已验证任意模型与词典的配套质量。
