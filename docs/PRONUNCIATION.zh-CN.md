# 双音频声学对比与音位判据

测量协议、说话人参数、VOT 接入与验收要求见[声学测量设计](ACOUSTIC-MEASUREMENT-DESIGN.zh-CN.md)。本文描述当前入口与行为。

## 入口与输入

在 Praat 中选中两个 Sound，打开“开始 AI 纠音”，指定标准音和学习者录音。`ai/run_ai_tutor.py` 调用 `praat_ai.tutor.run_from_praat`；表单使用 `ui.py` 和 React `PronunciationForm.tsx`，提交后由 `run_tutor` 执行分析。基础计算不要求大模型，文字和图表解释是可选步骤。

Windows 等待纠音表单或分析子进程期间，Praat 语图窗口可通过标题栏移动、窗口边框缩放和最小化；对象编辑控件暂不可操作，菜单、计时器和 IPC 回调在等待结束后处理。资源配套按模型、音位映射和启动环境检查生成能力。“配置配套资源”的叉号、“取消”和 Esc 直接关闭弹窗并丢弃未保存设置，已导入的资源仍保留。

目标读音有两种来源：

- 手动填写 IPA。表单产生的等分区间只是定位种子，不是人工音素边界；分析仍需适用的对齐资源。
- 选择 MFA 配套读音预设，用参考文本生成读音并确认候选。`mfa_g2p.py`、`mfa_resources.py` 和 `mfa_task.py` 管理 G2P、词典、声学模型及任务快照。修改文本、IPA 或资源后须重新生成或明确切回手动模式。该路径使用 `mfa-strict`，失败不执行比例回退。

G2P 使用统一的 Unicode 输入流程，由所选模型的字素表、音位标签和显式 IPA 映射决定输入能力。空格 / 标点分隔输入单元；连续文字整体提交模型，用户可按模型要求分隔。其他语言可在“纠音资源配套”中新建配套，保存后在纠音表单或 G2P 模型库选择 G2P；自定义配套使用声学对比，语言专属音位判据仍需独立准备。G2P 库的“选用”和表单的模型下拉框均修改具体配套，检查并保存后生效。“配置配套资源”弹窗提供词典、声学模型、音位映射和 MFA 启动设置，不重复提供 G2P 选择或添加控件。

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

`audio.py` 从 PCM WAV 提取 RMS、过零率、频谱重心和高频能量比，并通过 Parselmouth 提取基频、强度和 F1/F2/F3。双方使用共同可用频带和各自共振峰设置；Pitch、Intensity、Formant 提取独立保留状态。`pronunciation.py` 管理双方边界、测量身份及比较；协议、合法值域和缺测口径见[测量说明](../ai/docs/measurements.zh-CN.md#纠音测量协议)。

## 英语与日语判据

`pronunciation_rules.py` 提供 `en-US-general-v1`（英语美式）和 `ja-JP-standard-v1`（日语标准语）。两者均为实验性声学观察，不是发音判错模型。

| 音类 | 适用观察 | 不能据此确认的内容 |
| --- | --- | --- |
| 元音及双元音 | F1/F2、相对时长 | 音素替换、重音或声调对错 |
| 擦音 | 频谱重心、高频占比、过零率、相对时长 | 实际音素身份 |
| 塞擦音 | 频谱重心、高频占比、相对时长 | 闭塞和释放的独立分段识别 |
| 塞音 | VOT 地标派生单值及候选状态、相对时长 | 自动地标及闭塞归属不等于已确认音素身份 |
| 鼻音 | 频谱重心、相对时长 | 鼻音替换或鼻化诊断 |
| 近音 | F2/F3、相对时长 | 单凭共振峰确认替换音 |
| 日语 `cl`、长辅音 | 相对时长 | 日语拍结构和促音判错 |
| 未支持音段 | 未评估 | 不删除未知符号来冒充支持 |

音类使用 PanPhon 发音特征。日语保留长度、无声化和 `N/I/U/cl` 标签；相对时长是音素时长与邻近可靠音素平均时长之比，不是拍结构分析。

规则观察要求双方适用区间和足够有效测量。定位筛选使用 `0.70` 工程界限，比例或表单等分定位不支持音位声学结论。越界、重叠、数量或同位置标签不匹配时不能生成对应观察。原始 `phone_index` 贯穿定位和报告；`learner_phone_index` 标识可关联的学习者区间。

`observed`、`partial`、`duration_only`、`insufficient_evidence`、`not_evaluated`、`not_applicable` 分别表示已生成观察、部分可评估、仅时长、证据不足、未评估和不适用。时长观察不计作非时长声学检查通过。没有独立音素识别、已校准判错阈值或百分制总分。

## 通用 DTW 路径的限制

通用路径按有效曲线及阈值生成 `errors`，其中的分数是声学差异量，不是发音准确率。新高频能量比和原始 RMS／强度只生成 `observations`，不继承旧幅值比例或跨录音增益的判错阈值。空特征、未定义值、协议不可比较与无效区间分别报告原因，不产出零值偏差冒充测量。`check_statuses` 记录覆盖状态；有效零差异不能当作音素识别或发音正确的证明。

## 输出与模型解释

持久报告默认位于安装目录 `reports/` 下的独立文件夹，包含 JSON、TextGrid 和可用时的 PNG。`PRAAT_AI_REPORT_DIR` 可修改根目录，但不能位于 Praat 临时回导目录内。交互入口完成分析后复用 AI 工作台：报告以独立会话保存，正文展示双方测量值、具体差异、覆盖状态、缺测原因和解释，PNG 内嵌显示，原始数据作为附件保留。报告会话可以从历史记录恢复并继续提问，显示报告本身不调用模型。

`tutor_report.py` 将完成的输出交给工作台导入；`ModernStore.import_report` 原子保存正文和原有测量证据，不初始化或中断其他运行会话。附件复制到会话数据目录，独立于临时音频回导目录。命令行 `--no-gui` 保留文件路径输出，桌面启动或导入失败时显示错误并保留原文件。

- JSON 保存 `measurement_protocol`、双方来源、轨迹、逐帧状态、地标派生 `measurements`、DTW 配对和派生测量 ID。音位规则另保存 `profile_snapshot`、`observations`、`phone_assessments`、`check_statuses`，其 `errors` 为空。
- 音位规则 TextGrid 提供 `Target_Phones`、`AI_ProblemCandidates`、`AI_EvidenceStatus`、`AI_Observations`，保留学习者边界与空隙；VOT 事件点层合并重合地标，持续预浊与闭塞另用区间层。
- PNG 展示曲线、相对时长或地标单值，元数据保留测量身份与来源。图像不可用不等于没有测量结果。
- `qwen.py` 可生成文字或图表解释。输入使用测量摘要和地标，不发送完整曲线；解释通过 `report_guards.guard_delivered_text` 核对并记录 `explanation_status`。DTW 重采样统计作为独立证据，不用原始帧均值替代。取消事件贯穿 tutor／分析／对齐及 VOT 子进程。

归一化见[方法与限制](NORMALIZATION.zh-CN.md)，真实材料要求见[验收材料协议](NORMALIZATION.zh-CN.md#真实录音材料协议)。

## 验证与来源

离线检查入口：`ai/tests/test_pronunciation.py`、`test_pronunciation_rules.py`、`test_forced_alignment.py`、`test_bridge_pipeline.py`、`test_mfa_tutor.py`。合成音和模拟对齐验证链路、状态和数值，不是实际纠音准确率证据。

报告显示检查入口为 `ai/tests/test_tutor_report.py` 和 `ai/frontend/tests/browser/tutor-report.spec.ts`。`ai/tests/verify_python_bridge_hello.py --exe Praat.exe --output test-records/pronunciation/tutor-chat-report-live.json` 使用独立 Praat 进程、US/UK 录音及实际 WebView2，检查菜单分析、报告正文与图像、重复打开和历史回看，不调用模型。

PanPhon 固定来源、数据和许可证见 [NOTICE](../ai/praat_ai/_vendor/panphon/NOTICE.md) 与 [LICENSE](../ai/praat_ai/_vendor/panphon/LICENSE)。资源导入不表示已验证任意模型与词典的配套质量。

## 对齐环境与资源接口

`ai/mfa_alignment_probe.py` 由安装器嵌入，`mfa_resources.py` 在 Python 侧加载。
它分别检查启动器版本、目标 Python 依赖、临时目录可写性和配置资源；环境 ready 不表示
真实语音对齐成功。`--child` 输出 ASCII JSON，当前报告字段以该脚本为准。

MFA 可使用直接启动器，或 `conda_executable`／`conda_environment`；版本命令、环境报告和
依赖面板允许保存是独立状态。资源静态检查核对词典、声学／G2P 元数据与音位映射；
不根据文件名推测配套，不把目录存在当作模型加载成功。前端配套保存由 `mfa.save` 执行。

`CompositeAligner` 在非 strict 模式收集有效后端结果，无有效结果时可比例回退；取消不属于
可回退失败。CTC 路径由 `ctc_span_quality` 检查有效帧与跨度，退化结果拒绝交付。
模型的音位集合与语言映射单独核对，置信值不是经校准的正确概率。

检查入口为 `test_mfa_alignment_probe.py`、`test_mfa_resources.py`、`test_forced_alignment.py`、
`test_alignment_control.py` 和 `installer/tests/` 的 MFA 宿主测试；真实边界误差需要人工人声标注。
