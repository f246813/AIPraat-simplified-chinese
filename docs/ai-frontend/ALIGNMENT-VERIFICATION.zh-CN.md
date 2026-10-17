# 强制对齐与环境检测接口

这里区分三种证据：环境探针检查进程／依赖／文件事实，资源静态检查核对配置及可读元数据，真实音频对齐才产生可评估的边界。任何一个 `ready`、版本号或成功生成区间，都不等于语言／口音匹配或边界准确率已经验证。

## 共用 MFA 环境探针

`ai/mfa_alignment_probe.py` 由安装器嵌入，并由 `praat_ai/mfa_resources.py` 按路径加载。它只依赖标准库编排；被检测的子进程仍需要目标 MFA 环境中的依赖。

- 默认编排模式：解析启动器，运行 `mfa version`，用目标环境 Python 执行 `-I <探针> --child`，检查配置的词典／声学模型并聚合 JSON。
- 同时填入 conda 可执行文件和环境名时，`resolve_launcher` 优先使用 `conda run -n <env> mfa` 与该环境的 Python；否则使用直接路径，`.bat/.cmd` 经 `cmd /d /s /c` 包装。直接启动器通过相邻目录寻找 `python.exe`，不替用户激活 conda。
- `--child` 报告 `soundfile`／libsndfile、PATH 上的 `fstcompile` 与 `pywrapfst`、kalpy、librosa、MFA 包版本，并在临时目录创建／删除一个文件验证可写性。输出为 ASCII JSON，避免启动器重编码破坏非 ASCII 内容。

| 检查 ID | 当前检查范围 |
| --- | --- |
| `launcher` | 版本命令能否启动并成功退出 |
| `child` | 无法执行或解析目标环境子探针时的阻塞项 |
| `soundfile`、`openfst`、`kalpy`、`librosa`、`mfa` | 目标环境导入、二进制定位或包版本；不运行真实语音对齐 |
| `temp` | 实际临时文件写入是否成功 |
| `ascii_temp` | 非 ASCII 临时路径被当前探针列为阻塞；这是探针的保守兼容性检查，不是对所有 MFA 版本的普遍断言 |
| `dictionary` | 文件存在、非空及文本读取；报告条目数最多扫描到 100000 行，不检查全部词典语义 |
| `acoustic` | 文件存在且非空，或模型目录非空；不是完整模型加载或内容验证 |

报告结构：

```text
{schema:"mfa-alignment-probe-v1", ready:boolean, summary:string,
 launcher:string, environment:string, version:string, python:string,
 checks:[{id,label,status:"ok"|"missing"|"error",detail,repair}], blocking:string[]}
```

顶层 `blocking` 是失败检查 ID；`ready` 表示聚合检查全部为 ok。探针不安装／下载软件，不保存用户配置；会启动子进程，导入依赖可能触发其缓存行为，临时可写性检查及 CLI 输出也会写文件，不能称为“绝无磁盘写入”。修复命令只作为提示显示，不自动执行。

CLI 参数为 `--mfa-executable`、`--conda-executable`、`--conda-environment`、`--dictionary`、`--acoustic-model`、`--timeout` 和可选 `--output`。`--output` 拒绝覆盖已有路径；编排模式退出码 0 表示 ready，1 表示不完整，参数错误为 2。`--child` 无论各导入项结果如何都以 JSON 报告并返回 0，不能用它的退出码代替逐项判断。

## 安装器／路径窗口

`installer/src/MfaAlignmentProbe.cs` 用所选 Python 启动嵌入探针，读取临时 JSON 并清理输出文件；缺 Python、缺脚本、超时或无输出时返回说明。`DependencyPanel.cs` 在 MFA 检查路径中生成报告，未配置 MFA 时也保留明细入口；已配置且报告不完整时显示首个阻塞并展开可选组。明细可填写 conda 路径／环境名并重新检测，值随路径窗口保存到 `alignment.mfa`。

MFA 行的版本状态、环境报告和面板必需环境 `Ready` 是独立条件。`DependencyPanel.Ready` 没有以 MFA 报告作为安装／保存硬门禁，字段保存限制见 [安装与路径配置](INSTALLATION-PATHS.zh-CN.md)。

## 资源编辑器 RPC

`MfaPresetEditor.tsx` 中“检测对齐环境／重新检测”通过纠音或语音资源宿主转发给 `MfaResourceService.rpc`，不属于聊天 `HostBridge` 方法表：

```text
mfa.environment {preset: <完整草稿预设对象> | <已保存的预设 ID>, launcher?: {
  executable:string, conda_executable:string, conda_environment:string
}}
```

省略 launcher 时使用 `pronunciation.launcher`；草稿预设的资源 ID 必须能在宿主登记表中解析，不能通过该 RPC 随意加入文件路径。RPC 返回：

```text
{schema, preset:{id,name,language,variant,dictionary,acoustic}, launcher,
 ready, summary, environment:<完整探针报告>,
 resources:{g2p,dictionary,acoustic}, status:<静态检查结果>, blocking:string[], notes:string[]}
```

其中资源项为 `{id,name,path,exists}` 或 null。外层 `ready` 为：探针 ready、词典及声学模型登记文件存在、静态检查无冲突、IPA 映射就绪。`_check` 核对模型类型／语言、声学音位覆盖及词典／G2P 音位映射；元数据不足时明确“未验证”，不是静态认证通过。`can_generate` 还依赖 G2P 资源、启动器和用户口音声明，不能由对齐 ready 推导。

外层 `blocking` 合并环境失败文字和 `status.issues`，后者还含用户声明、G2P 等问题，因此当前可能出现 `ready=true` 但仍有提示项；它与内层失败 ID 数组含义不同。资源静态状态和探针事实应分别阅读，不将该字段名推导为分析提交规则。

前端传递未保存的草稿预设与启动器，结果显示总体结论、首个提示、逐项修复及所选资源。草稿资源／启动器／映射改变会清除旧报告；该 RPC 不保存配套，保存仍由 `mfa.save` 完成。资源登记通过原生选择器或可信 Python 接口，移除登记不删除磁盘文件。当前环境检测不是生成读音／提交纠音前的自动硬门禁。

内置预设由 `config.py::pronunciation_presets()` 提供 `en-GB`、`en-US`、`ja-JP`。日语预设使用规则 `ja-JP-standard-v1`，补充 `dzː→d͡zː`、`dʑː→d͡ʑː`、`tsː→t͡sː`、`tɕː→t͡ɕː` 映射；这几条映射不代表任何日语模型或全部音位已经验证。

## 对齐执行与结果边界

`ai/praat_ai/forced_alignment.py::build_aligner` 按 `AlignmentConfig.backend` 选择 MFA、wav2vec2／CTC 或 auto 双后端；`mfa-strict` 使用配套 MFA 且失败时抛错，不做比例回退。非 strict 的 `CompositeAligner` 收集各后端 `AlignmentError`，无成功结果时使用 `ProportionalAligner`，置信度 0.25 并保留警告；多个可比较结果融合时保留来源和分歧影响。

`Wav2Vec2Aligner.align` 在 CTC 路径后调用 `ctc_span_quality`，记录音素数、帧数中位数、覆盖秒数／比例；无有效帧或每音素帧数中位数 ≤ 1 判为退化并抛 `AlignmentError`。CTC 发射帧概率均值即使很高也不能覆盖此检查。这是当前时间跨度质量保护，不是发音判错阈值；它也不证明所有非退化路径边界都准确。MFA／wav2vec2 音素集合仍需对应语言映射，不能从无调 tokenizer 推断中文声调或复合音素已覆盖。

`pronunciation.py::analyze_pronunciation` 处理标准与学习者两侧边界、提供边界和同采样音频复用分支；轨迹比较在 `compare_phone` 中使用 DTW、参考统计与容差。逐音素判据是否可评估取决于对应边界和特征证据，不能把比例回退当作可靠时长证据。`report.py` 提供 JSON、TextGrid 和依赖 Pillow 的 PNG 输出；`write_textgrid` 还按结果类型分支，不能把旧通用 `AI_Errors`／`AI_ErrorFeatures` 两层推广为所有报告格式。JSON 是报告文件，不是可直接导入的 Praat 对象。

## 检查入口

- Python 探针与资源接口：`ai/tests/test_mfa_alignment_probe.py`、`test_mfa_resources.py` 及其他 `test_mfa_*.py`。
- 前端：`ai/frontend/tests/mfa-preset-editor.test.tsx`；执行 `npm test -- tests/mfa-preset-editor.test.tsx`。
- C#：`installer/tests/MfaAlignmentProbeTests.cs`；嵌入资源／宿主聚合入口 `Run-LoudnessFullRegressionTests.ps1`。
- 对齐／报告：`ai/tests/test_alignment.py`、`test_forced_alignment.py`（含退化路径）、`test_pronunciation.py`、`test_report.py`、`test_report_chain_regressions.py`。

例如从项目根目录设置 `$env:PYTHONPATH='ai'` 后执行 `python -m unittest discover -s ai/tests -p 'test_mfa_alignment_probe.py'`。真实后端验证需要另备模型、真实人声、目标音素及人工边界，分别记录边界误差、映射遗漏、来源和可评估范围；合成音频／孤立夹具不等于跨语言准确率或真实双音频验收。
