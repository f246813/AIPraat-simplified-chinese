# 28 场景批量排查

用同一段音频重复手测容易漏掉范围、对象类型和多轮上下文问题。这里把问题、预期行为和运行轨迹一起保存，以便修复后重跑同一组。

问题定义：`ai/tests/backend_scenarios.json`。共 28 项，覆盖基础信息、快速测量、多时间点、显式范围、共振峰、强度、HNR、频谱、jitter/shimmer/CPPS、VOT、韵律、编辑器选区、不存在对象、派生对象类型、静音、组合测量、一般讨论、发音证据边界和多轮解释。

在项目根目录执行，使用已安装项目依赖的 Python：

```powershell
# 只列出问题，不发模型请求。
python ai/tests/run_backend_scenarios.py

# 真实 Praat 批处理：25 项工具/边界检查；另外 3 项标为 needs_live。
python ai/tests/run_backend_scenarios.py --mode tools

# 使用你的 WAV 重跑工具层。
python ai/tests/run_backend_scenarios.py --mode tools --audio 'D:\录音\あなた.wav'

# 使用当前 Praat 的唯一 Sound，调用与桌面前端相同的真实 RPC 后端。
# 默认使用日常配置的模型；配置为 API 时会消耗其额度。
python ai/tests/run_backend_scenarios.py --mode live

# 强制使用本地 Qwen；只改变诊断副本，不改变日常模型选择。
# 完整现有工具清单需要较长上下文，本次实测使用 16K。
python ai/tests/run_backend_scenarios.py --mode live --provider local --thinking off --context-tokens 16384 --object 1

# 多个 Sound 时明确指定原对象编号；编号以当前对象列表为准。
python ai/tests/run_backend_scenarios.py --mode live --object 1

# 修复后只复测范围、组合测量和后续解释。28 必须和 25 一起运行。
python ai/tests/run_backend_scenarios.py --mode live --object 1 --cases 06,25,28

# 对照思考档位/解释时限：只改变诊断副本，日常配置不改。
python ai/tests/run_backend_scenarios.py --mode live --object 1 --cases 07 --thinking off
python ai/tests/run_backend_scenarios.py --mode live --object 1 --cases 07 --l2-deadline 60
```

每次默认新建 `_diag/backend-日期-时间/`，也可用 `--output` 指定一个不存在的目录。`report.md` 可直接阅读；`summary.json` 供程序比对；每个编号下保存 `result.json`。工具模式保存实际执行的 `.praat` 脚本，live 模式保存完整 `events.json`、`evidence.json`、报告、参数、原对象/范围和投递事实。

live 模式串行调用 `sessions.create → tasks.submit → events.poll`，使用真实配置模型和原生 Praat 投递。测试会话库独立于日常历史；配置使用临时副本，运行结束删除副本；每项结束恢复原对象选择。静音项新建专用静音 Sound，测完删除该对象。派生对象项复用已有 Harmonicity；选区项使用原 Sound 编辑器实际圈选。缺少这些条件时明确标为 `needs_fixture`。运行期间请保持原 Praat 和目标对象存在，并避免另开同一可执行文件的 Praat 实例。

`--provider` 支持 `configured`（默认）、`local` 和 `api`。本地模式禁用诊断副本中的 API，使用已有模型文件和 llama.cpp 服务；云端模式使用已有 API 配置。`--context-tokens` 调整本地诊断的上下文长度。云端和本地后端的多轮解释实现不同，不能把云端 L2 证据复用检查的失败直接归为本地报告失败。结构化小模型决策的独立实验见 `local-decision-experiment.zh-CN.md`。

结果状态：

| 状态 | 含义 |
|---|---|
| pass | 本项自动检查通过 |
| fail | 执行、返回结果或目标覆盖不符合预期 |
| partial | 实测检查通过，但后端报告未完成 |
| unavailable | 实际音频没有可计算的值；已明确返回原因 |
| review | 定性发音结论需要人工核对，不能由关键词证明正确 |
| needs_live / needs_fixture | 当前运行方式或对象条件未覆盖本项，不计作通过 |

工具模式验证真实 Praat 模板和结果协议；它不调用模型，也不验证原生窗口投递。live 模式验证桌面 RPC、真实模型、原生投递、结果存储及多轮解释；它不打开 React/WebView 窗口，不能证明界面按钮、滚动或渲染正常。工具覆盖检查接受带正确参数的等价 `measure` 调用。精确数值是否科学合理、主观发音判断是否准确仍需核对音频和专业分析。

固定时间点包含 0.25、0.3、0.75 秒；录音短于这些时刻时，需要调整问题 JSON。即使音频足够长，这些位置也可能是清音或静音，`unavailable` 本身不是程序故障。

整个批次会持续写入报告，便于定位卡在哪一项。`--timeout` 默认每项 180 秒；到时请求取消，取消未结束或原生投递无法确认时停止后续问题。不会自动重发同一问题。
