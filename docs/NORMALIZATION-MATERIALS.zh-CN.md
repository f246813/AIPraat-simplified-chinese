# 归一化真实录音材料协议

`ai/tools/validate_normalization_materials.py` 是本地材料门禁与成对分析工具，不联网，不调用云 API，不自动生成或修改人工标注。协议版本、配置常量和字段以工具及 `normalization.py`、`normalization_prosody.py` 为准。协议合规不等于分析准确。

## 材料清单

每个用例需要四份真实 PCM WAV；使用独立音高基线时需要六份。路径不得越出 manifest 目录，音频与标注以 SHA-256 绑定。

| 槽位 | 说话人 | 内容与标注 |
| --- | --- | --- |
| `reference_target` | 标准说话人 | 与学习者相同文本和音位序列；人工 IPA 边界；逐音位判断 `acceptable` |
| `learner_target` | 学习者 | 相同目标序列；逐音位判断 `acceptable` 或 `local_deviation` 并说明原因 |
| `reference_calibration` | 标准说话人 | 独立多元音校准，不是待测句；日语五元音各至少三个 token，美式九元音各至少三个 |
| `learner_calibration` | 学习者 | 自己的独立多元音校准，覆盖要求相同 |
| `reference_pitch_baseline` | 标准说话人 | 仅 `independent` 模式需要；人工确认中性语句并填写 `neutral_phrase_text` |
| `learner_pitch_baseline` | 学习者 | 自己的同协议中性语句 |

同时提供：

- `speakers[]`：匿名 ID、语言及可核验 `provenance`。不要求公开姓名，但需要能确认录音归属。
- 来源：`kind=real_recording`、`origin`、`permission`、ISO 格式 `recorded_at` 和独立 `recording_id`。合成音、TTS、未知来源或占位声明不能通过。
- 人工标注 JSON：`schema_version=normalization-human-phones-v1`、绑定的 `audio_sha256`、一致的说话人／语言／读音、非空 `annotator` 与 ISO `reviewed_at`；`method=manual` 或 `human_reviewed`，后者还需原对齐来源；`label_set=IPA`，区间有序、不重叠且位于音频范围内。
- 与[归一化测量协议](NORMALIZATION.zh-CN.md)相同的提取设置。

未逐段复核的 MFA／ASR／等分边界不是人工标注。不得用同次录音的转码副本冒充独立材料。中性音高语句不是元音校准，既不要求元音清单覆盖，也不作为被判错语料。

## 模式与语料隔离

`pitch_baseline_mode` 取 `utterance` 或 `independent`。本句模式不携带独立基线；独立模式需要双方同协议中性语句。基线与待测录音相同、语言／读音／测量范围不匹配或可靠有声帧不足时，核心返回原因，不改用另一种基线。

工具对整份清单核查：

1. 拟合录音（元音校准或音高基线）不能同时作为任一用例的评估对象。
2. 同份录音不能同时归到两位说话人。
3. 比较使用 PCM 内容及声明身份，能识别改名和普通元数据改写副本；不是说话人验证，也不能保证识别重采样、剪辑或拼接。

违反隔离要求时材料被阻断。`isolation` 与说话人摘要供人工复核，不证明来源声明真实。

## 运行与输出

先从工具的 `--template` 输出建立清单并填写真实材料，再运行校验。以下路径为示例，须换成自己的材料与尚不存在的输出文件：

```powershell
$env:PYTHONPATH = 'ai'
python ai/tools/validate_normalization_materials.py 'D:\corpus\manifest.json' `
  --output "$env:TEMP\normalization-validation.json"

# 显式执行本地分析；不发送音频给模型。
python ai/tools/validate_normalization_materials.py 'D:\corpus\manifest.json' `
  --analyze --output "$env:TEMP\normalization-analysis.json"
```

只校验时退出码为 `0=ready`、`1=blocked`、`2=输入或 CLI 错误`。输出存在时拒绝覆盖。`--analyze` 固定关闭模型解释，在同一人工边界运行原始与归一化两条路径：

| 字段 | 含义 |
| --- | --- |
| `prediction_raw` | 不启用归一化的原始观察 |
| `prediction` | 启用所选归一化的观察 |
| `human_judgments` | 原样保存的人工判断 |
| `comparison` | 逐音位对齐的描述性计数 |
| `usability.states` | 各请求特征状态及原因 |
| `pitch_scope` | 实际基线方式 |
| `isolation` | 语料级隔离核查 |

工具核对运行核心的协议、清单、提取参数、音高范围及目标 LUFS；不兼容时返回不可用，不静默套用另一协议。

## 结果能说明什么

`comparison.metric` 为 `not_defined`，`accuracy` 为 `not_measured`。当前工具不计算准确率或误报率；材料就绪、观察覆盖及工程测试通过不能替代这些指标。

解释描述性结果时同时披露可用数、缺失数、说话人和用例覆盖。将来若评估准确率，必须另行明确评价单位、特征级人工 gold、覆盖下限及分歧裁决；这些尚未实现的指标不能写成当前能力。

材料是否齐备应以当前 manifest 的门禁输出为准，不能根据旧清点数量或测试通过数推断。仓库材料审计文件位于 `test-records/pronunciation/`；它只证明对应运行的材料状态。

检查入口：`ai/tests/test_normalization_materials_acceptance.py`。其中合成夹具只验证门禁、协议和链路，不是实际发音效果证据。
