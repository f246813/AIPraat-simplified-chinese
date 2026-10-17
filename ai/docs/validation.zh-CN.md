# AI 后端验证入口

验证分为离线协议、真实 Praat、真实模型、桌面窗口和安装产物。某一层通过不能代替其他层；脚本、旧日志和文档中的命令不代表当前环境已通过。

## 离线回归

在仓库根目录使用已安装项目依赖的 Python：

```powershell
$env:PYTHONPATH = 'ai;ai/tests'
python -m unittest test_phase_context test_cloud_protocol test_dialogue_protocol `
  test_cloud_escape test_report_guards test_tool_guards test_delivery_state test_no_request_routing -q
python -m unittest test_report_chain_regressions test_token_budget -q
python -m unittest test_evidence_consistency -q
```

`test_evidence_consistency.py` 是「缺测与完成状态」的回归入口（纯逻辑；只有「一段音频只提取一次」
那条需要真实的 praat-parselmouth，缺依赖时如实报 skipped，不报通过）。

这些测试使用隔离夹具或模拟供应商协议。完整发现测试前检查外部依赖与 GUI 要求：

```powershell
python -m unittest discover -s ai/tests
```

当前验收解释器（`venv-ai-modern`，Python 3.12.14）下完整发现是 **1499 个用例、0 失败、6 跳过**。
第 3 轮之前它是 `11 failures + 3 errors`，逐条真因与结论见
`test-records/diagnostics/20261017-evidence-consistency/`。
分析路径的运行库先过能力门（与 release 工作流同一条），缺依赖不要跳过：

```powershell
python -c "from praat_ai import capabilities as c; import json; r=c.analysis_capabilities(); print(json.dumps(r, ensure_ascii=False)); assert r['ready']"
```

## 检查边界

- 路由：没有材料引用、领域词和请求动作词的短输入判为一般讨论，不进入工具执行；真实测量请求仍走 L0。
- 工具调用：对象、类型、范围、参数和重复调用限制；执行前被拒与实际执行失败分别记录。
- 报告：量名、数值、单位、对象、范围、引用及推导；VOT、时长与基频不能靠数字碰巧相同互相替代。
- 报告恢复：遮蔽无法核实的位置，保留合法测量；恢复稿的未核实状态不能当成验证通过。
- 投递：未发送、已发送、结果未知和阻断分别记录；模型重试不能重放已经完成的对象操作。
- 缺测语义：什么都没测到不能写成「没有差异」；量不出来（未定义值、依赖缺失、提取失败）
  不能记成严重偏差，必须落成「证据不足／未评估」并带上可读原因。
- 依赖状态：解释器与依赖要分别报；「依赖未安装」「已安装但提取失败」「测了没有差异」
  三种情形必须可区分，缺依赖不能只看空特征就当成正常。
- 本机交付：本机规划器的最终正文与云端报告共用 `report_guards.guard_delivered_text`
  这一个入口；核对不上的数值遮蔽并标记，不许原样交付。本地摘要（实测结果行原文）
  不是模型主张，不做数值遮蔽——遮蔽它等于丢证据。
- 可选 AI 解释：解释与云端报告共用同一份证据守卫；对不上的数值遮蔽并标注，对比不完整时
  必须带结论等级说明。视觉解释失败要留原因，不能静默丢弃。
- 对齐超时/取消：正式 MFA 对齐要有等待上限，超时或取消都要真的终止对齐进程树；取消不得
  被当成「对齐失败」而回退比例对齐，也不得产出「已完成」的报告；取消前已取得的测量结果
  要保留。分析阶段的界面停止按钮（第 3 轮新增）：`analysis_progress.run_with_stop` 用
  `progress_popup.MiniProgress`（可选 `on_stop`）给出「停止分析」，点它（或关窗键）置位
  `cancel`；取消状态在**分析结束那一刻**记下，收尾用 `MiniProgress.complete` 推到 100%
  并禁掉按钮——结束之后再点一下不算「要求停止」，不能把已完成的分析报成已取消；
  分析跑在 worker 线程、进度经 `root.after` 回主线程；拿不到 Tk 或 `--no-gui` 时退回
  同步执行。`run_from_praat` 在测量完成前取消会如实报「没有生成报告」并返回退出码 3；
  测量之后取消则报告照常交付、只少 AI 解释。Praat 菜单那一档仍用 Praat 自带进度窗
  （窗口属于 Praat），那里没有停止按钮。
- 上下文：阶段消息追加、工具调用／返回配对、配置身份、摘要边界及非文本持久化范围。
- 取消：检查工具执行后、存储前及请求等待中的取消；保存已取得证据，不将停止等待说成撤销脚本。

## 请求形状与缓存

`cloud_metrics.py` 记录请求指纹及用量，缺失缓存统计保持未知。比较同阶段连续请求的最终 HTTP payload，核对固定系统提示、schema、工具顺序以及动态任务／技能／证据的位置。配置或摘要改变产生的上下文版本需分开比较。

`ai/tests/benchmark_context_cache.py` 只有显式 `--send` 才调用配置的 API，会产生费用。它的 baseline 依赖固定备份目录，缺失时不能用于当前 A/B。`summarize_context_cache.py` 面向固定样本布局和价格假设，不是当前通用账单工具。性能比较必须控制模型、设置、材料、连接和缓存条件，分别报告输入、请求／重试、首字时间和总耗时；缓存覆盖率本身不能证明提速。

## 实际执行

- [场景运行器](backend-scenarios.zh-CN.md)：区分工具批处理、桌面 RPC 和真实模型；`needs_live`／`needs_fixture` 不计通过。
- `ai/tests/verify_chat_templates.py`、`verify_chat_planning.py`：运行前检查其 Praat、模型与材料要求。
- `ai/frontend`：`npm.cmd test -- --run`、`npm.cmd run build`、`npm.cmd run test:ui`；release 门槛（第 3 轮）跑的是 `npx tsc -b` + `npm test`（vitest 单元），浏览器测试需要真实窗口，**不在 CI 里**；两者都不能证明 WebView2 和原生菜单行为。
- [安装器](../../installer/README.zh-CN.md)：运行库、路径、安装宿主及真实安装后的功能分别检查。
- [双音频](../../docs/PRONUNCIATION.zh-CN.md)与[归一化材料](../../docs/NORMALIZATION-MATERIALS.zh-CN.md)：合成音数值对照不能代替人工录音复核。
- release 门槛（第 3 轮新增）：`.github/workflows/release.yml` 里 `ai-offline-regression`
  与 `frontend-tests` 是 `build-windows-x64`、`build-macos` 的 `needs` —— 测试不过就不构建、
  不发布。后端门槛按顺序做：装分析运行库（不装模型、不联网下载）→ `compileall` →
  **能力门必须为真** → `python -m unittest discover -s tests -t tests`。
  真实录音材料验收与安装产物验收**不在**这条门槛的覆盖范围内，仍需按各自文档独立进行。

## 分析依赖的能力门（第 3 轮新增）

`ai/praat_ai/capabilities.py` 是「这一步现在能不能跑」的唯一口径：进程内 import + 版本比对，
版本区间直接读 `ai/requirements.txt`（不在代码里写第二份数字）。与 `runtime_dependencies.py`
的分工是：后者是安装向导的体检（每项起子进程做加载与参考功能检查），前者是纠音流程里的
轻量门控。四个使用点复用它，说法必须一致：

- `audio._open_parselmouth`：缺 praat-parselmouth 时，交付物里
  `check_statuses["pitch_formant_measurement"]="dependency_missing"` 的原因直接给出
  「缺什么 + 去哪儿装」；
- `normalization.import_formant_calibration`：共振峰校准前置条件不满足时抛同一句提示；
- `normalization_prosody.apply_loudness`：响度依赖缺失或版本落在区间外（例如 scipy 1.15.0）
  时，报告原因写明具体缺口（`scipy>=1.14.1,<1.15` 这类），并保留
  `repair_action="path_settings.configure_runtime"`；
- 前端 `tutor.capabilities`：提交之前查询，缺依赖时禁用并撤销该项开关、显示同一条提示；
  拿不到状态（旧后端 / RPC 失败）不拦人，**不提供自动安装**。

检查这条门时用与 CI 相同的入口（`analysis_capabilities()["ready"]` 必须为真），并注意
`praat-parselmouth` 在 Python 3.13 及以上是**有意不装**的，那里只能报「改用 3.12 解释器」，
不要当成待修故障。

## 证据与完成状态（2026-10-17 收敛新增）

交付物里这几个字段是「这次到底测到没有」的机器可读出口，检查时按字段读，不要只看正文措辞：

- `check_statuses["pitch_formant_measurement"]`：`ok` / `dependency_missing`（该解释器没有
  praat-parselmouth）/ `extraction_failed`（装了但提取失败）/ `unavailable`（音频太短）。
  这是测量能力，不等于「音位有没有差异」。
- `check_statuses["phone_acoustics"]`：`observed` / `partial` / `not_evaluated` /
  `not_applicable`；旧式对比路径与音位判据路径都给这一项。
- `phone_assessments[i]["status"]`：`observed` / `partial` / `insufficient_evidence`，
  并在 `checks` 里逐特征给出 `reason`（例如「…无法可靠测量。」）。
- 旧式对比的 TextGrid 标签：`observed` 才写「未发现超过旧式声学对比阈值的差异」；
  没比成的一律写「证据不足：…未评估」，不要用「没有差异」掩盖没测量。
- 本机最终交付：`chat.TurnOutcome.delivery_guard` 记录 `verified` / `rejected_masked` /
  `no_evidence` / `not_model_text` / `empty`，`reply_source` 记录正文来源。
- 纠音报告的可选 AI 解释：`AnalysisResult.explanation_status` 记录 `verified` /
  `rejected_masked`（有数值没核对上、已遮蔽） / `unavailable`（模型没跑成） / `cancelled`
  （取消后不再生成解释）。解释里的数值按本次测量核对，对不上就遮蔽并标注；本次对比
  不完整时正文末尾附上与 `check_statuses` 同一套状态的结论等级说明。视觉解释失败会写进
  `warnings`，不再静默丢弃。
- 对齐的等待与取消：`AlignmentConfig.mfa.timeout_sec`（默认 1800 秒）是正式 MFA 对齐的
  等待上限，超时按失败报告并杀掉整棵进程树；取消抛
  `forced_alignment.AlignmentCancelled`，**不是**「对齐失败」，不允许回退比例对齐
  （那等于用猜测边界冒充实测）。`analyze_pronunciation(..., cancel=)` 与
  `run_tutor(..., cancel=)` 把取消贯穿到对齐层；取消发生在测量之后时，已取得的测量结果
  照常写进报告，只跳过 AI 解释（`explanation_status="cancelled"`）。

解释器门限（2026-10-17 第 3 轮更新）：`requirements.txt` 对 `praat-parselmouth` 带
`python_version < "3.13"`，`runtime_dependencies.py` 只支持 Python 3.10–3.12 x64。
第 3 轮按 requirements 区间把 `venv-ai-modern`（3.12.14）补齐：NumPy 2.2.6、SciPy 1.14.1、
pyloudnorm 0.2.0（原 NumPy 2.5.3 不满足区间，scipy<1.15 与 numpy 2.5 也不兼容）。
之后 `runtime_dependencies.detect` 报告 NumPy／SciPy／pyloudnorm／praat-parselmouth
全部「已就绪」，能力门 `analysis_capabilities()["ready"]` 为真，LUFS 参考探针 −23.045048
与 CHECK 一致（记录见 `test-records/diagnostics/20261017-evidence-consistency/`）。
命令行默认的 3.14.7 没有 NumPy，也没有 praat-parselmouth（requirements 有意不装），
仍只能做语法检查——依赖装好不等于测试通过，换解释器、装或改依赖后必须重新出这份报告
与能力门结果，不能沿用旧结论。

真实云端请求、音频上传、模型加载或安装环境变更前确认授权和范围。诊断产物可能包含对象名、路径、提问及模型输出，分享前检查隐私与脱敏。原始运行证据存放约定见[测试记录说明](../../test-records/README.md)。
