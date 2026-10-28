# 测量口径与验证入口

算法、参数和标注对象的设计依据见[官方手册本地原版与两项参考](../../docs/reference/praat-manual/README.md)。

跨入口测量协议、纠音修复要求与验收设计见[声学测量设计](../../docs/ACOUSTIC-MEASUREMENT-DESIGN.zh-CN.md)。该设计不代表相关修复已在实际入口实现。

聊天工具实际从 `ai/praat_ai/tools/` 包加载。`tools.py` 是兼容副本，不是聊天运行入口；
修改其仍保留的模板时需与包内实现同步。`measures.tsv` 管理表驱动参数，专用工具由
`tools/schema.py` 注册。源码存在、工具注册、实际执行成功和指标定义正确是不同条件。

## 候选、验证与派生测量

聊天的 VOT 与基频路径由 `chat._execute_action` 接收机器回执，`acoustic_events.py` 与
`pitch_events.py` 保存来源、绝对时间范围、候选事件／观察帧、验证结果和派生公式。
VOT 在地标验证后相减；基频统计与斜率只使用同一批经过能量、连续性和候选带筛查的帧。
自动候选及 `screened` 状态不等于音素身份或发音诊断已经确认。

证据中的 `acoustic` 随工具记录与报告上下文保存。完整基频观察帧另存 JSON，保留每帧
取值、接受／排除状态与原因；模型上下文使用地标、区间、检查摘要和派生量，不灌入整条曲线。
报告守卫直接读取带量名的结构化测量，筛查阈值和帧数不当成录音的 F0。
基频提取上下限和有效帧数使用独立的 `analysis.*` 量名，帧数按整数核对；
它们不能替代均值、极值或其他实测基频。成功执行但没有有效 F0 的结果仍保存范围、
所求量及缺测原因，供报告和后续对话引用；保留缺测证据不表示测量已经完成。
报告直接引用工具已返回的均值和斜率，只对新增的差值、比例等使用证据引用做四则推导。

瞬时地标写入 TextGrid 的点层（文件类名 `TextTier`），持续发声与闭塞写入独立
`IntervalTier`。重合的零 VOT 地标共用一个时间点；测量不通过向已有语段区间插入边界来表示爆破。
导出标注保留候选状态，不能把声学窗口直接当成音素边界。

## VOT

VOT = 浊音起始绝对时刻 − release burst 绝对时刻。起声早于 burst 为负值，重合为零值；
不能因某条实现只支持正值就改变指标定义。手动输入 `burst`、`voicing` 时按原地标相减，
不重新排序。人工地标必须来自用户输入；模型不能把自己猜测的数值升级为人工标注。
`closure_start` 可提供目标闭塞起点，`vowel` 可提供清晰元音起点。

自动路径在用户给定范围内估计 burst，检查爆破前低频周期段，并独立找爆破后稳定周期性。
周期性候选要求至少 3 个周期且不少于 20 ms；这是保守的候选条件，不保证识别
所有短预浊、低信噪比或不规则发声。结果需结合闭塞段和语图人工确认，不能把前一音的发声
自动归给目标塞音。检查保留整个选区，不用固定 150 ms 截取真正起点。

结果分别列出预浊候选、Release burst、爆破后稳定周期性候选与其间隔。自相关基频不能
独立确定“清晰元音起点”，该地标显示未自动判定；可用 `vowel` 提供人工绝对时刻，单列
`Burst-to-clear-vowel interval`。该间隔不能替代负 VOT。起声被选区左端截断或地标无效时，VOT 无法确定，其他有效地标和间隔仍保留。
除阻前发声中断本身不决定 VOT 符号；闭塞归属未确认的自动结果仍为候选。

`fon/SegmentAcousticAnalysis.cpp` 的原生候选与序列化是另一条实现；头文件中的导出声明
未注册为可调用动作，不作为聊天能力或验收入口。

## 音高方法

| 用途 | 方法 | 静音阈值 |
| --- | --- | --- |
| 语音 F0／语调 | filtered autocorrelation | 0.09 |
| 预浊候选 | filtered autocorrelation | 0.01 |
| 数学周期性 | raw autocorrelation | 0.03 |
| 嗓音周期分析 | raw cross-correlation | 0.03 |

方法选择见 `pitch_analysis.py`；聊天语音工具的范围预设为 75–600 Hz，可显式调整，
不称其为 Praat filtered AC 的默认 50–800 Hz。已有 Pitch 对象的未知提取设置保持未知。
Parselmouth 的旧内嵌 Praat 使用同一高斯预滤波后再做 AC；原生与内嵌路径按相同参数对照。
纠音 F0 轨迹保留实际方法、范围与阈值，不与原始共振峰校准设置混淆。

## 其他声学数值

- 基频点查询、统计与通用 `measure` 共用质量筛查；无可信帧不回退未筛查均值。
  比较数值前核对对象、范围、单位和分析参数，不混用不同频带的同名统计。
- `pitch_slope` 是选区内相邻有效帧的平均绝对变化率（Hz/s），不跨缺测间隔。
  `pitch_slope_octave_free` 也只使用选区内筛查帧，按 Praat 的定义将逐对有效帧的
  绝对半音差折叠到 0–6 半音，求和后除以首末有效帧的时间跨度（含中间缺测间隔），
  单位为半音/s。两者都是变化幅度，不提供升降方向；不足两帧时斜率缺测。
  已有 Pitch 的方法、提取范围与阈值在结构化证据中保持未知，查询参数不作为其提取设置。
- `pitch_start` / `pitch_end` 是选区内首个／末个有效帧的 F0，不是选区边界处的插值。
  结果及结构化地标保留其实际绝对时刻，缺帧时保留缺测原因。首尾比较只支持这两个
  观察时刻之间的净变化；用于整段升降判断时，两端观察必须在半个帧步长内覆盖所述范围，
  不能将内部有声段外推到静音或缺测的边缘，也不能由净变化断言中间单调。
- `time="auto"` 按区间长度取点，默认每 50 ms 一个、8–64 点；未知长度用 16 个相对位置。
  无数据的端点如实返回缺测，不挪到别处冒充原请求时刻。
- 音素身份与时间边界需要对齐或人工标注，文件名与等分窗口不构成边界证据。
  声学数值不直接证明发声生理原因、性别或音素错误。

## 纠音测量协议

纠音结果使用 `acoustic-measurement-v2`。轨迹保存测量 ID、角色、录音内容标识、
对象来源、请求范围、真实观察时刻及实际算法参数；未知原 Sound 时间域不推断为零起点。
原生 Python 导出提供时间域与 WAV 偏移时，分析先核对映射与录音时长。

高频能量比采用单边 FFT 平方幅值，内部频点权重为 2，DC 和偶数长度的 Nyquist
权重为 1；默认 Hann 窗、25 ms 帧长、10 ms 步长、2000 Hz 分界。双方使用不超过较低
Nyquist 的共同分析频带，频带以频点中心闭区间选取；重采样不恢复源录音带宽。
静音、空帧、无高频带和非有限样本分别保留原因。零高频能量／非零总能量得到合法零值。
未记录版本的旧比例不与新能量口径混比，新能量比不继承旧幅值比例的异常阈值。

F0 与共振峰保留实际观察时刻；F0 使用共享质量筛查，不将无声零频率当作有效帧。
RMS 不加抬高静音的常数。合法零 RMS、零过零率、零能量比及负强度不因符号被排除。
新能量比和跨录音原始 RMS／强度差只输出描述性观察，不直接判为发音错误。

共振峰上限候选通过现有高级表单和校准 RPC 实际执行，使用独立多元音材料、相同 Burg
参数与稳定窗口；双方独立分析，禁止使用待测录音。自动建议无法区分时不改参数，人工
采用保留覆盖来源。完整候选轨迹与判据进入 `normalization.formant_selections`，即使
Lobanov 关闭仍保留选参来源。协议、分数定义、带宽及使用步骤见[候选选参](../../docs/NORMALIZATION.zh-CN.md#独立元音候选选参)。

VOT 保存为 `measurements` 中的地标派生单值，不复制为轨迹，也不进入 DTW。
可靠定位的塞音使用相邻音位上下文圈定搜索范围，经 `pronunciation_vot.py` 执行聊天的
真实 Praat 脚本并解析 `acoustic_events.py` 回执。自动结果保持 `provisional`，
释放归属、预浊截断或其他关系不充分时保留缺测；清晰元音起点不由周期性候选替代。

DTW 观察保存输入测量 ID、实际配对索引／时刻、重采样均值及派生 ID。
报告守卫核对同一派生值，不以原始帧均值代替。JSON 附件保留完整轨迹，模型输入仅保留
摘要、地标与派生关系；TextGrid 事件使用点层，预浊和闭塞使用区间层。

## 声调与语调解释

语音 F0 使用 filtered autocorrelation，定义依据见 [Pitch 对象](../../docs/reference/praat-manual/manual/Pitch.html)
与[方法选择](../../docs/reference/praat-manual/manual/how_to_choose_a_pitch_analysis_method.html)。
Praat 的 Pitch 保存逐时刻周期性候选，不直接输出语言的声调类别。
聊天语调技能区分基频观察、净升降与声调分类；两种非负斜率不能提供方向，均值和首尾相近也不证明平坦。
第一至第四声、阴平、阳平、上声、去声、轻声等类别需要音节边界、完整轮廓、语言和语境判据，
当前工具不自动识别这些类别。报告核对与交付清理会标记或移除缺少相应依据的分类断言，
保留实测基频、观察时刻和无法判断的说明。

## 回归

在仓库根目录使用项目 Python：

```powershell
$env:PYTHONPATH='ai;ai/tests'
python -m unittest test_acoustic_events test_acoustic_standards test_vot_prevoicing test_vot_dispatch test_segment_analysis_tools test_vot_object_cleanup test_chat_tools -q
python -m unittest test_pitch_statistics_regression test_point_query_count test_interpretation_boundaries test_calc_delivery -q
python -m unittest test_pitch_scope test_acoustic_standards.PitchNativeComparisonTests -q
python -m unittest test_tone_evidence -q
python -m unittest test_backend_report_accuracy test_answer_contract -q
python -m unittest test_formant_selection test_normalization_ui -q
python -m unittest test_acoustic_extraction_protocol test_measurement_protocol test_measurement_settings -q
```

`ai/tests/verify_backend_accuracy.py` 提供已知信号的真实 API 与原生 Praat 联合验证，
默认只打印计划，`--send` 才发送；`--ledger` 保存跨运行累计计数。
`--replay <JSON>` 重放该入口保存的工具提案并核对模型草稿，不发送请求。
声学数值检查与报告核对状态分别记录，不能把工具数值正确当成整份解释正确。

VOT 声学回归调用真实 Praat 执行聊天生成的脚本，检查正值、弱预浊、连续预浊、超过 150 ms
的预浊、起点截断、分离的先前发声、静音与临时对象清理。手动回归还验证负／零 VOT、
TextGrid 地标和人工清晰元音间隔。合成样本验证这些行为，真实录音的精确地标仍需复核。

测量报告用 Markdown 标题分开结论、事件／依据、计算与限制。程序保存并单列已核对的
算式；单项 VOT 使用结构化地标直接排版。请求记录保留实际输出上限与结束原因，
`finish_reason=length` 时保留可用草稿并标为未完成，不因 JSON 可解析就判完成。

方法来源：[VOT 定义与预浊](https://pmc.ncbi.nlm.nih.gov/articles/PMC5665574/)、
[Praat 音高方法](https://praat.org/manual/how_to_choose_a_pitch_analysis_method.html)、
[TextGrid 事件与区间](https://praat.org/manual/Intro_7__Annotation.html)。
