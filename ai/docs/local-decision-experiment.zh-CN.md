# 本地小模型决策实验

保留现有脚本、守卫、提示词和 skill 路径，另用本地 Qwen 提出意图、对象、范围、工具和参数计划。本实验用于判断这种分工是否可行，尚未接入桌面前端或默认路由。

`ai/praat_ai/local_decision.py` 提供三个实验接口：

| 接口 | 模型负责的工作 |
|---|---|
| `decide` | 输出 JSON 计划 |
| `decide_native` | 从固定的声学工具清单生成原生工具调用 |
| `decide_slots` | 调用单个语义抽取函数；程序把测量类别映射到工具 |

这些接口只提出计划，不投递 Praat，也不提供实测结论。未来接入执行链路仍需通过现有对象、范围、参数和证据守卫。不能把结构化输出格式当作语义正确的保证。

在项目根目录执行，需已有本地模型配置、运行中的 Praat、真实 Sound 和选中的 Harmonicity 对象：

```powershell
# 同一组 28 个问题；使用 4K 本地文本上下文、关闭思考。
python ai/tests/run_local_decisions.py --object 1 --format slots --output _diag/local-decisions-new

# 只复测少量场景，输出目录必须尚不存在。
python ai/tests/run_local_decisions.py --object 1 --format slots --cases 01,06,22,23 --output _diag/local-decisions-smoke-new
```

`--format` 支持 `slots`（默认）、`native`、`json`。`--cpu` 可强制选择 CPU 配置，但本次没有测试 CPU 性能。脚本从真实 Praat 快照取得对象和选区，同一接口的所有问题使用相同候选清单。评分预期只在推理结束后使用，不发给模型。

每项保存 `input.json`、`request.json`、成功返回时的 `response.json` 和 `result.json`，总表为 `summary.json` / `report.md`。记录包含用户问题、对象名称和模型输出，分享前应按实际数据检查。运行使用禁用云端 API 的临时配置，结束删除副本并停止本次管理的模型服务。

本实验的静音项只构造对象上下文，不做静音测量；后续解释项提供的是之前提出的计划，标为 `plan_only`，不是真实测量证据。`pass` 表示该项自动计划检查通过，不能视为完整语义准确率。综合发音分析仅检查路由，需人工审阅；无需动作的路由项也不能证明对象和参数选择能力。

2026-10-07 的 Qwen3.5-0.8B Q4 实测中，最终语义抽取版本 28 项仅 3 项通过完整计划检查，尚未显示收益。因此保留为实验入口，日常路径继续使用原实现。结果和边界见 `_diag/local-qwen-summary-20261007/findings.md`。
