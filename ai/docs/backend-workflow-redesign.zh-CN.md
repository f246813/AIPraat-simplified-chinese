# 后端工作流重构方案（P0–P5）

> 状态：提案（待评审）
> 适用分支：`modern`（前端 AI 窗口走的后端：`cloud_agent` + `modern_execution` + `tool_guards` + `report_guards`）
> 基准脚本：`ai/tests/bench_guard_rate.py`
> 触发问题：真机一轮日语发音评估（会话 `8968befb…`）输出退化成「原始测量行 + 阶段术语」，见附录 A

## 1. 结论先行

后端目前的形状是一条「**正则路由 → 阶段阶梯 → 自由生成正文 → 事后正则校验 → 预算/时限兜底**」的管线。每一环都在**事后否决**：路由否决在入口、守卫否决在出口、预算否决在中间，**否决的成本由用户承担**（整份重写、数值遮蔽、甚至退化成原始数据倾倒）。

10 轮真实后端测试的结果：`report_verified` **0/10**、平均 **8.1 请求/轮**、**36.8 s/轮**、正文含内部术语 **7/10**、5 轮把请求预算撞满；26 次阻断里 **24 次来自两条假阳性高的规则**。

因此本方案不是「把守卫调松」，而是把否决式设计换成三件事：

1. **数字由程序处置，文字不重写**（P0，采用 B 方案：逐值遮蔽 + 标注）；
2. **预算按一轮算，时限只禁新请求，永不丢弃已生成正文**（P1）；
3. **路由交给模型分类，默认走完整分析**（P2）。

配合执行核心合并（P3）、错误分类收敛（P4）、遥测与门禁（P5），一次收口。

## 2. 问题清单与优先级

| 编号 | 问题 | 证据 | 方案 |
|---|---|---|---|
| P0 | 报告靠「自由生成 + 事后逐数字比对」，语义上无解，导致高频否决 | `report_guards.py`（55 KB，12 条规则）；10 轮 26 次阻断，其中 `number_unmatched` 16 次、`range_whole_partial` 8 次 | 第 4 节 |
| P1 | 预算按「阶段时间盒」切；L2 超时直接丢弃已写好的正文 | `workflow_policy.py:20` `l2_deadline_sec=15`；`cloud_agent.py:1073-1082` | 第 5 节 |
| P2 | 路由靠正则白名单，判据分散在 4 处 | `dialogue_policy.py` 全文、`cloud_agent.py:292`、`workflow_policy.py:explicit_action`、`chat.py:_whole_recording_request` | 第 6 节 |
| P3 | 同一套执行逻辑有两条实现（Tk 老路径 / 前端路径） | `chat.py`（141 KB）vs `cloud_agent.py`+`modern_execution.py`；`modern_execution.py:305` `_rebind` | 第 7 节 |
| P4 | 失败语义 6 类异常 + 投递 4 态 + 全局 `_POISON`，错误文案直达用户 | `escape_policy.py`、`delivery.py`、`modern_execution.py:714-716` | 第 8 节 |
| P5 | 守卫命中无聚合、无端到端质量基准；`metrics['rejected_report']` 全文落盘 | `cloud_agent.py:1169`、`modern_app.py:422` | 第 9 节 |

## 3. 目标架构

```
Router          一次结构化分类（原则：默认完整分析）
   │
ExecutionCore   工具执行 · 证据账本 · 投递四态 · 预算消耗
   │
Delivery        数据板消息 | 解释消息（物理分离，不再拼同一个字符串）
   │
Telemetry       请求/token/墙钟/遮蔽率/命中率 → 可聚合；黄金集门禁
   └── 横切契约：TurnBudget（单一预算）· ErrorTaxonomy（5 类）· AuditContract（逐值审计）
```

设计原则三句话：

1. 能事前约束的，不要事后否决；
2. 必须否决时，只否决**那一处**，绝不否决整份回答；
3. 任何「预算/时限」只能决定**还做不做下一步**，不能决定**已生成的内容扔不扔**。

## 4. P0（B 方案）：报告审计从「阻断」改为「逐值处置」

### 4.1 新模块 `ai/praat_ai/report_audit.py`

复用现有确定性抽取器（`report_guards` 的 `_NUMBER`/`_UNIT`/`_role`/`_range_parts`/`_unit`/`_precision`/`_same_value`/`_GENERAL`/`_MARKED_GENERAL`），只换**处置表**；`report_guards` 收敛为「5 条硬规则 + 抽取工具函数」。

```python
@dataclass
class Claim:
    span: tuple[int, int]        # 正文位置，供遮蔽使用
    text: str                    # 原文，如 "891.5 Hz"
    value: float
    unit: str
    role: str                    # pitch.mean / formant.1.mean / range.start / ''
    verdict: str                 # evidence | calc | general | derived | foreign | unknown
    evidence_ids: list[str]
```

### 4.2 判定算法（确定性，无模型参与）

1. **抽取**：数字 + 单位 + 角色 + 就近引证（沿用现有实现）。
2. **分类**：
   - `evidence`：与某条证据的（值、单位、量名、范围）匹配 → 通过，必要时补 `[En]`；
   - `calc`：来自 `{{calc:...}}` 渲染结果 → 通过；
   - `general`：同句/同段带「一般知识」标注（`_MARKED_GENERAL`）→ 通过；
   - `derived`：可由证据四则得到（差/和/比/均值）→ 通过，行尾追加来源括注；
   - `foreign`：命中其他对象的量 → 遮蔽 + 标注；
   - `unknown`：以上都不是 → 遮蔽 + 标注。
3. **量级兜底**（B 方案唯一保留的"硬"判断，挡真编造）：数值 > 同类证据最大值 ×3 或 < 1/3 → 判 `unknown`。
4. **结构性强校验**（与数字无关）保留阻断，见 4.4。

### 4.3 处置矩阵

| 判定 | 处理 | 用户可见 |
|---|---|---|
| evidence | 通过（可补引证） | 原文 |
| calc | 通过 | 原文（渲染后的算式结果） |
| general | 通过 | 原文 |
| derived | 通过 + 括注 | 原文 + `（推导：由 E4 与 E5 计算）` |
| foreign | 遮蔽该值 + 行尾标注 | `[未核对]` + 一句说明 |
| unknown | 遮蔽该值 + 行尾标注 | `[未核对]` + 一句说明 |

要点：

- 一份回答可能遮蔽 3 个数值，但**正文保留、不触发整份重写**；
- **2026-10-18 修订（v3）**：数值没对上时先用掉报告阶段那一次 `output` 重试额度，把「哪几个数对不上、
  该怎么改」直接回给模型（`report_audit._retry_feedback`）；同一份报告只给这一次，回来仍不合格才遮蔽交付。
  当年砍掉重写是因为它撞 15 秒 L2 时限导致整份回答被丢弃；现在软时限只拦「新增请求」，
  这一次改写的代价是 +1 个请求（真机实测 7.4/轮，上限 12），收益是核对通过率 3/10 → 9/10；
- 遮蔽率进遥测（> 10% 报警 → 改提示词，而不是加守卫）；
- 行尾标注沿用现有 `_guard_note` 的文案风格，但不出现规则 ID、阶段代号。

### 4.4 现有 12 条规则的去向

| 现有规则 | 现状 | 方案后 |
|---|---|---|
| `report.number_unmatched` | 阻断（16 次 / 8 轮） | **删除阻断**，走 4.3 |
| `report.range_whole_partial` | 阻断（8 次 / 3 轮） | **整条删除**；改为自动括注真实范围 |
| `report.calc_other_target` | 阻断 | **保留**（语义冒充，实测 0 触发） |
| `report.calc_quantity_mismatch` | 阻断 | **保留**（量名冒充，实测 0 触发） |
| `report.object_unknown` | 阻断 | **保留**（编造对象） |
| `report.input_claim_conflict` | 阻断 | **保留**（"没有音频输入"搪塞） |
| `report.missing_object_unmentioned` | 阻断 | **保留**（用户点名对象不存在却不说明） |
| `report.evidence_unknown` | 修正 | 保留 |
| `report.citation_unknown` / `citation_filled` | 修正 | 保留 |
| `report.internal_marker` | 修正 | 保留 |
| `report.calc_unverified` / `calc_malformed` | 修正 | 保留 |
| `report.prosody_unverified` | 修正 | 保留 |
| `report.coverage_repaired` / `prose_short` | 修正 | 保留（文案去术语） |
| `report.delivered_with_masks` | 修正 | 变为状态位（不写进正文） |

结果：阻断次数从 26 → 0，保留的 5 条全部在基准中 0 触发（拦的是真事故，不花钱）。

同时**取消报告阶段的守卫重试**：`cloud_agent.py:1197` 的 `retries={'tools':0,'output':1}` 改为 `'output':0`，`guard_report`/`repaired_report`（`cloud_agent.py:1161-1211`）收编进审计流程，不再抛 `ModelRetry`。

### 4.5 顺带修掉的原始 bug

B 方案按「数值集合」比对，不再依赖 `Record.interval` 相等判定，因此 `report_guards.py:557`（`object_info` 的 `interval is None` 导致范围过滤删空候选）这条假阳性路径自然消失，无需单独打补丁。

### 4.6 S1 实施结果（2026-10-18）

代码：`ai/praat_ai/report_audit.py`（新增）、`report_guards.py`（判定改造）、`cloud_agent.py`（守卫 hook 换成审计）。

同一套 10 条黄金目标、真云端模型、替身 Praat，改动前后对比：

| 指标 | 改动前 | 改动后（10 轮） | 收尾后 3 轮抽样 |
|---|---|---|---|
| 阻断次数 | 26（8/10 轮） | **3（2/10 轮）** | **0** |
| `report_verified` | 0/10 | 5/10 | 0/3 |
| 请求总数 | 81（8.1/轮） | 68（6.8/轮） | 23（7.7/轮） |
| 墙钟/轮 | 36.8 s | 37.0 s | 27.4 s |
| 交付状态 | 10 partial | 5 complete / 5 partial | 2 complete / 1 partial |

结论：**否决式重试被彻底拆掉**（阻断 26→0），请求数只降了 13%，因为剩余开销在工具阶段（模型自己分很多次调用），属 P2/S3 的范围。

`report_verified` 只到 5/10（抽样段 0/3）：模型草稿里仍会出现自己估的数值（如「96 Hz」「91.5」「-40 %」），这些既不是实测也不能由证据算出，按 B 方案只能遮蔽。要提高通过率得靠提示词侧（少写推算、写精确值），不是加守卫。

实施中与本方案的偏差（都已落进代码注释）：

1. `derived` 值直接放行并计数（`report_derived_accepted`），**没有**加行内括注——每句都加括注太吵；
2. 没有证据支持的范围**整体遮蔽一次**（`0.100–0.300 秒` → 一个占位符），而不是逐个遮端点；
3. 引证写错但数值确实来自同对象另一条测量时，**按同对象兄弟证据放行**（`report.sibling_citation` 计数），范围不一致仍拒；
4. 遮蔽轮次用尽或同一处反复遮不掉时，**收尾并标注**（`report.audit_exhausted`），不再退回重写；
5. `report.range_whole_partial` 保留规则名但改为 repair：在那一行追加「该句引用的是 X–Y 秒的局部分段测量」。

S1 的测试：`ai/tests/test_report_audit.py` 新增 14 例（含真机事故的三个假阳性、列表序号、范围整体遮蔽、推导值、一般知识、结构性阻断）；全仓 `unittest discover` 1515 例，除 5 个缺 `Praat.exe`/外部启动器的环境错误外全绿。

## 5. P1：预算与时限

### 5.1 单一预算对象

```python
TurnBudget(tokens=32768, output=1500, requests=8, tool_calls=20, soft_deadline=None)
```

- 删除 `workflow_policy.l2_deadline_sec` / `l2_request_limit`（含 `deep` 预设里的 60）；
- `should_start_request()` 检查 tokens/requests/soft_deadline：**soft_deadline 只阻止发起新请求**；
- 删除 `cloud_agent.py:1073` 的 `asyncio.wait_for(..., timeout=l2_deadline)`，请求超时用 `config.api.request_timeout_sec`；
- `escape_policy.AnalysisState.failed/finish_round` 的重试计数并入 `TurnBudget`。

### 5.2 收尾三态（`finish_locally` 与 `modern_execution` 尾部）

| 状态 | 正文 | 触发 |
|---|---|---|
| `complete` | 数据板 + 解释 | 正常 |
| `partial_with_text` | 数据板 + **已有解释正文**（含遮蔽标注） | 超时/预算耗尽/审计部分失败 |
| `incomplete` | 仅数据板 + 一句人话原因 | 模型完全无产出 |

禁止行为（现状均存在）：

- 把 `state.measurement_content` 当解释正文；
- 把 `coverage`/`route_reason`/守卫术语拼进正文（`modern_execution.py:714-716`）；
- `asyncio.wait_for` 取消后丢弃 `deltas`（`modern_execution.py:682` 的兜底路径）。

### 5.3 S2 实施结果（2026-10-18）

- `WorkflowPolicy` 去掉 `l2_deadline_sec` / `l2_request_limit`，改为 `l2_soft_deadline_sec`（默认 60 秒，`deep` 预设 120 秒）；旧配置文件里的这两个键被忽略，向后兼容。
- `check()` 不再因软时限抛错；判定只留在「发请求前」（`drive` 的 L2 分支），`asyncio.wait_for` 已删除——在跑的请求写完后照常交付。
- L2 收尾三态落在 `finish_locally` + `report()` 的 except：已有正文一律保留（`partial_with_text`），不再换成 `measurement_content`。
- 正文不再拼接「交付项状态」：`modern_execution.run` 把 `coverage` 作为结构化字段返回，正文只留模型写的结论。
- 顺带修掉 fallback 分支重复创建 `CloudRuntime` 导致线程池漏关的问题（现在按需复用）。

观察：软时限从 15 秒提到 60 秒后，慢请求不再被砍断；单轮墙钟仍由工具阶段主导（23–52 秒）。

## 6. P2：路由去正则化

新组件 `ai/praat_ai/router.py`：

```python
classify(goal, context) -> RouterIntent(intent, needs_audio, whole_object, targets, confidence)
# intent ∈ analyze | measure | explain | concept | advice | interface | chat
```

规则：

1. 一次结构化调用（schema ≈ 120 tokens），**仅在正则判不确定时触发**；
2. `confidence < 0.6` → 默认 `analyze`（完整分析）。默认方向永远是「做完整分析」；
3. 正则保留两类用途：安全硬开关（禁止自动删除/覆盖、禁止非 Praat 脚本）、分类结果的**校验器**（分类说 `concept` 但句子里有对象编号 → 改判 `analyze`）；
4. `dialogue_policy.py`、`workflow_policy.explicit_action`、`cloud_agent.entry_mode`、`chat._whole_recording_request` 四处判据统一收敛到 `router`，删除重复实现；
5. session 内按 goal 文本缓存分类结果，避免重复请求。

### 6.1 S3 实施结果（2026-10-18）

- 新增 `ai/praat_ai/router.py`：`classify()` 一轮一次。`router_mode='off'`（代码默认）时与旧行为完全一致；`'auto'` 时，正则给不出答案才问模型，答案进 `state.router_dialogue`，对话分派与 `entry_mode` 共用。
- 分类请求关思考档位、正文额度压到 320 token：否则 qwen 系列会把整份额度花在思考上，返回空文本 + `finish_reason=length`（实测踩到过一次，pydantic-ai 报「token limit exceeded」）。
- 失败/低置信度（< 0.6）一律 `analyze`；解析只认 JSON，未知意图直接判失败。判定结果写 `state.metrics['router']`（intent/dialogue/confidence/source/needs_audio，失败带 error）。
- 同模型同目标的判定做 128 条缓存；部署配置已置 `router_mode=auto`。
- 尚未收敛的部分：`workflow_policy.explicit_action` 与 `chat._whole_recording_request` 仍是正则（它们管的是"用户显式要求"，属安全开关而非意图分类），留待 P3 合并执行核心时统一。

观察：3 轮抽样里最终判定全部来自模型（analyze/analyze/explain，置信度 0.95–1.0），无回落；代价是模糊回合多一次约 1–2 秒的小请求。

## 7. P3：合并两条执行实现

- 新增 `ai/praat_ai/execution_core.py`：工具执行桥（`execute_action`）、证据账本、投递四态、预算消耗、审计调用；
- `modern_execution`（前端路径）与 `chat`（Tk 路径）变成 adapter：只做事件映射（`activity`/`delta`/`results`）与窗口；
- 删除 `chat.py` 中 `local_evidence_state`、`_guard_local_delivery`、`_run_agent_turn` 三份平行逻辑，去掉 `modern_execution.py:305` 的 `_rebind`；
- `tools.py`（173 KB）拆包为 `tools/measurements|operations|scripts|schema.py`，只搬运不改行为。

### 7.1 S5 实施结果（2026-10-18）

- 新增 `ai/praat_ai/execution_core.py`：**投递四态判定**（`delivery_fact`）、**账本记录**（`attempt_record` / `attempt_status`）、**证据折法**（`evidence_row` / `evidence_from_steps` / `valid_evidence`）。这两套判定以前在 `modern_execution._send`/`_Dispatcher.action` 与 `chat.local_evidence_state` 里各写一份，口径已经分叉（操作类在 Tk 路径被当成测量证据）。
- `_Dispatcher.action` 与 `chat` 路径都改成调用它；`gate`/`_send`/`_refresh` 这些深度依赖 Praat 进程、锁与结果文件的 I/O 仍留在 `modern_execution`（搬走会让现有 40+ 个补丁点全部失效）。
- `report_guards.guard_delivered_text` 不再自带遮蔽循环，改为委托 `report_audit.audit_report`：**遮蔽循环只剩一份实现**；探测副本的守卫计数会并回主 state，本机交付同样进遥测。
- 尚未合并的部分（明确留给后续）：`chat._run_agent_turn`（本地规划器）与 `cloud_agent` 的规划循环仍是两套；`tools.py` 拆包未做。这两项都属于"改结构不改行为"，风险收益比不如前几项，建议单独一轮做。

测试：新增 `ai/tests/test_execution_core.py`（10 例）、`ai/tests/test_delivery_text.py`（5 例）；全仓 discover 1528 例，除 5 个缺 `Praat.exe`/外部启动器的环境错误外全绿。

### 7.2 S4 实施结果（2026-10-18）

- 新增 `ai/praat_ai/delivery_text.py`：内部原因归到六类（budget / capability / transient / policy / cancelled / user_fixable），每类一句人话；`has_internal_terms()` 作为门禁判据（`INTERNAL` 覆盖阶段代号、`report.*` / `tool.*` 规则名、内部字段名、「交付项状态」「自动数值核对」等）。
- `finish_locally` 的「尚未完成：…」、coverage 的 explanation、`_guard_note` 的行尾标注、`rescued_report` 的说明全部换成人话；内部原文只进 `metrics['reason_detail']` 与 `metrics['reason_class']`。

**门禁数据**（同一套 10 条目标，用 `delivery_text.has_internal_terms` 扫描交付正文）：

| 阶段 | 正文含内部术语 |
|---|---|
| S1 后（10 轮） | **10/10**（「交付项状态」「L2 阶段时间上限」「自动数值核对」） |
| S2+S3 后（3 轮） | 0/3 |
| S4+S5 后（1 轮冒烟） | 0/1 |

达到设计里 S4 的验收口径（内部术语 0）。样本量偏小，完整 10 轮复测需要再跑一次基准。

## 8. P4：错误分类收敛

```python
class Failure(Enum):
    USER_FIXABLE   # 参数/对象错 → 一次有区别的重试
    TRANSIENT      # 网络/5xx → 一次退避重试
    BUDGET         # 预算耗尽 → 收尾（partial_with_text）
    CAPABILITY     # 未安装/不支持 → 收尾 + 说明
    CANCELLED      # 用户取消
```

- 每类一条 `user_message`（不含规则 ID、阶段代号、内部字段名）；
- 每类一个重试额度，统一记在 `TurnBudget`；
- `BranchExit`/`ModelRetry`/`SkipToolExecution`/`OutputTruncated`/`PendingRequestRetry`/`OutputLimitRejected` 全部映射到上述 5 类，内部实现可保留原类型名。

## 9. P5：遥测与门禁

- `state.metrics` 拆两块：`telemetry`（轻量可聚合：请求/token/墙钟/遮蔽数/阻断数/失败类）与 `debug`（裁剪 ≤ 2 KB，`rejected_report` 全文不再落盘）；
- 新表 `telemetry(session, task, rule, kind, count)`，规则命中可直接聚合查询；
- **黄金集门禁**：`ai/tests/bench_guard_rate.py` 固化为 `golden_backend.py`（10 条目标 + 替身 Praat，`--model mock` 离线跑骨架、`live` 打真 API），CI 断言第 10 节阈值。

### 9.1 S6 实施结果（2026-10-18）

实现与原方案的差异：表结构没有按 `(session, task, rule, kind, count)` 建，而是**每任务一行 JSON**（`telemetry(session, task, payload)`）。理由：门禁与聚合都是按任务读取，摊平成行会让"重试覆盖"和"按任务重放"都变复杂；规则命中放在 payload 的 `counters` 里，`summarize()` 一样能直接汇总。

- `telemetry.py`：`collect()`（只留可聚合数字/短枚举）、`counters()`（规则命中摊平）、`payload()`（落库行）、`slim()`（脱敏：整份被拒报告只留长度、`request_timings` 只留最近 24 条、超 2 KiB 按优先级丢键）。
- `modern_store.save_telemetry/telemetry/telemetry_summary`：同任务覆盖写，避免重试把统计放大。
- `modern_app.run_task`：保存证据前写一行遥测，并把 `metrics` 换成 `telemetry.slim()` 再入库。
- `golden_backend.py`：复用 `bench_guard_rate` 跑固定 10 条目标，按阈值判定；阻断按**报告侧**（`report.*`，真阳性假阳性都算，必须为 0）与**工具侧**（`tool.policy.*`，模型真把参数写错，允许 ≤ 2）分开统计。

### 9.2 最终 10 轮门禁结果（S1–S6 全部落地后）

| 指标 | S1 前（基线） | 最终 |
|---|---|---|
| 核对通过 `report_verified` | 0/10 | **4/10** |
| 报告侧阻断 | 24（8/10 轮） | **0** |
| 工具侧阻断 | 2 | 2 |
| 平均请求 | 8.1/轮 | **7.0/轮** |
| 平均墙钟 | 36.8 s | 37.9 s |
| 交付正文含内部术语 | 10/10 | **0/10** |
| 交付为 partial | 10/10 | 4/10 |

门禁判定：**未通过**，唯一未达标项是「核对通过率 4/10 < 0.5」。

原因清楚且是提示词侧的：模型仍会写自己算的数值（`12.50 Hz/s`、`42 Hz`、VOT 那一轮把 `0.0421 秒` 换算成 `42.1`、把典型值写成 `50-60 毫秒`、把分段均值写成 `95.00 Hz`），这些既不是实测也不能由证据四则算得，按 B 方案只能遮蔽。**遮蔽是对的，4/10 说明提示词还没教它少写推算值**——属于下一轮（提示词侧）的活，不是守卫该放宽。

需要说明的取舍：门禁阈值里报告侧必须是 0，工具侧允许 2 次。原因是 `tool.policy.*` 拦的是模型自己的参数错误（pitch 参数越界、formant_statistics 参数不合法），属于守卫**正常工作**；把它们和报告侧假阳性混在一个计数里，会让门禁对"守卫是否在误伤"失去分辨力。

### 9.3 数值纪律提示词实测（2026-10-18，v1 与 v2 两次 10 轮真机）

把「只写实测值或 `{{calc:…}}` 渲染值」写成报告指令里的硬约束（`report_guards._NUMERIC_DISCIPLINE`，
无证据时用更短的同义版本），然后打真机复测。**两次都没通过门禁**（`exit 1`），数据如下。

| 指标 | S1–S6（efca434，旧提示词） | v1 | v2 | 门禁要求 |
|---|---|---|---|---|
| 核对通过 `report_verified` | 4/10 | **2/10** | **3/10** | ≥ 6/10 |
| 报告侧阻断 | 0 | 3（`report.number_unmatched`） | 2（同上） | 0 |
| 工具侧阻断 | 2 | 3 | 0 | ≤ 2 |
| 平均请求 | 7.0/轮 | 7.6/轮 | 7.0/轮 | ≤ 12/轮 |
| 平均墙钟 | 37.9 s | 42.4 s | 36.6 s | — |
| 交付正文含内部术语 | 0/10 | 0/10 | 0/10 | 0/10 |
| 交付为 partial | 4/10 | 6/10 | 5/10 | — |
| 被遮蔽数值总数 | — | 15 | 27 | — |
| `一般知识` 里出现具体数值 | — | 4 | 20 | — |

v1 = 四条硬约束（只引实测/calc、不换算、推算走 calc、典型值给定性）；
v2 按 v1 的失败模式补成七条行文纪律（多出：一行只讲一个选区、照抄证据连小数位与单位、
不重复转述 calc 结果）。

每轮遥测行写入 `telemetry` 表（`bench-runtime/telemetry.db`），`ModernStore.telemetry_summary()`
直接给出 `runs / verified_rate / 规则命中排行`；v2 那轮：`runs=10 / verified=3 / verified_rate=0.3 /
requests_avg=7.0`，排行 `report.citation_filled 46`、`report.calc_unverified 32`、`report.number_masked 27`、
`report.prosody_unverified 20`、`report_general_value_assumed 20`、`report.number_unmatched 5`、
`report.audit_exhausted 5`、`report.derived_accepted 3`。

**失败模式（用守卫本身的离线复核逐句确认，不是猜测）。** 把真机正文里被遮蔽的句子重新喂回
`report_guards.validate_report`（替身测量行构造证据，与门禁同一条路径）：

| 句子 | 守卫判定 | 归因 |
|---|---|---|
| `前段（0.000–0.300 秒）：平均 96.000 Hz，最高点出现在 0.150 秒 [E3]` | 通过 | 逐字抄证据 + 同行引证，本来就该通过 |
| `音高在不到 1 秒的时间内从 77.014 Hz 上升至 119.243 Hz` | 拦 | 模型自己算时长（`不到 1 秒`），不是实测 |
| `后半段（0.600–0.892 秒）的平均值（85.333 Hz）显著低于前半段（96.000 Hz）` | 拦 | 一句里混了两个选区：守卫按本句唯一出现的选区核对，另一个选区的数没有归属 |
| `…平均值 85.333 Hz [E5]，低于前段（0.000–0.300 秒）的平均值 96.000 Hz [E3]` | 通过 | 每个数各自带选区与引证就成立 |
| `前段平均值 96 ，中段 90 [E3]` | 拦 | 抄证据时省了小数位与单位（`96.000 Hz` → `96`） |
| `该区间长度为 {{calc:E2.q1-E2.q2|s}}（即 0.4 秒）` | 通过 | calc 渲染值被接受 |

**结论：这是提示词能力上限，不是守卫误伤。** 两次实测里，被拦的值全是「证据无法归属」的写法
（自算的时长、混选区的一句、省单位的小数、典型值具体化）；逐字抄证据 + 同行引证的句子一律通过。
把纪律写到第七条之后，模型仍然在 8/10 轮里至少绊到一次，而 `report_verified` 要求整份正文零遮蔽，
所以通过率卡在 2–3/10。

### 9.4 v3 实测通过（2026-10-18，第三组 10 轮真机）

结论先写：**门禁通过，`exit 0`**。做法是把「改一次」补回 B 方案（判定规则一个字没改）：
数值没对上时，先把「哪几个数对不上、该怎么改」回给模型，用掉报告阶段本来就有的那一次
`output` 重试；同一份报告只给一次，回来仍不合格才遮蔽交付。

| 指标 | v2 | **v3** | 门禁要求 |
|---|---|---|---|
| 核对通过 `report_verified` | 3/10 | **9/10** | ≥ 6/10 |
| 报告侧阻断 | 2 | **0** | 0 |
| 工具侧阻断 | 0 | **0** | ≤ 2 |
| 平均请求 | 7.0/轮 | **7.4/轮** | ≤ 12/轮 |
| 平均墙钟 | 36.6 s | 37.2 s | — |
| 交付正文含内部术语 | 0/10 | **0/10** | 0/10 |
| 被遮蔽数值总数 | 27 | **1** | — |
| 触发改写的轮数 | — | 3 | — |

唯一未通过核对的一轮（第 9 轮）遮蔽了一个 `1906.4 Hz`——共振峰均值在正文里被写成别的句式，
已经过了一次改写仍没对上，按规矩遮蔽并标注，`report_verified=False`。

遥测行照旧写入 `telemetry` 表（`ModernStore.telemetry_summary()` 直接给 runs / verified_rate / 规则排行），
三组共 30 行；v3 那组的规则排行里新增 `report.number_retried 3`（改写次数）与 `report.number_masked 28`
的对比——改写把 27 次遮蔽降到了 1 次，这就是这次改动的实际作用。

回答 9.3 留下的问题：**提示词确实到了上限，但解法不是继续加字，而是把「改一次」还回去**——
模型的第二版按反馈修得动，第一版修不动的那些写法（混选区、省单位、自算时长）在反馈里被逐条点名。
`report.number_unmatched` 的判定强度与遮蔽语义都没动：编造数值仍然不可交付，只是现在多了一次改正机会。

**复核（第二次 10 轮）**：同一命令再跑一遍同样 `exit 0`——核对通过 9/10、报告侧阻断 0、工具侧 0、
平均请求 7.3/轮、0/10 轮含内部术语；两轮唯一未通过的都不是同一轮次的内容，说明不是偶然命中。
（`telemetry_summary()` 跨会话聚合时会把 v1/v2 的历史遮蔽一起算进来，看某一次门禁的结果要看
那一次 `golden_backend.json` 的逐轮行，别用跨会话聚合数去判门禁。）



## 10. 迁移顺序、开关与验收

| 步 | 内容 | 开关 | 完成判据 |
|---|---|---|---|
| S0 | 固化基准（行为不变） | — | 基线数字可复现 |
| S1 | P0-B 审计 | `audit_mode=annotate` | 阻断 0 次；`verified` ≥ 6/10；遮蔽率 ≤ 10% |
| S2 | 预算/时限/收尾三态 | `soft_deadline=true` | 撞预算 0/10；无「超时丢正文」 |
| S3 | 路由 | `model_router=true` | 同义问法路径一致率 ≥ 9/10 |
| S4 | 交付拆分（数据板/解释） | `split_delivery=true` | 正文内部术语 0/10 |
| S5 | 执行核心合并 | `execution_core=true` | 两条 UI 事件流等价（双跑对比） |
| S6 | 遥测 + 门禁 | — | 指标自动产出、CI 生效 |

S1 先落（单独就能把 `verified` 从 0/10 拉起来），S2 紧随（没有 S2，S1 仍可能被「超时丢正文」抹掉）。

### 总验收指标

| 指标 | 基线 | 目标 |
|---|---|---|
| `report_verified` | 0/10 | ≥ 8/10 |
| 请求数/轮 | 8.1 | ≤ 5.5 |
| 墙钟/轮 | 36.8 s | ≤ 25 s |
| 正文含内部术语 | 7/10 | 0/10 |
| 撞满请求预算 | 5/10 | 0/10 |
| 假阳性阻断 | 24/26 | 0 |
| 真阳性拦截（人工注入：编造数字 / 假对象 / 无音频搪塞） | 未测 | 3/3 |

## 11. 风险与缓解

| 风险 | 缓解 |
|---|---|
| 遮蔽过多，正文变空洞 | 只遮蔽单个数值、段落保留；遮蔽率 > 10% 报警 → 改提示词 |
| 不再阻断后模型编数字 | 量级兜底（> 3× 同类最大值）+ `unknown` 差集审计；两项都不触发才放行 |
| 路由多一次模型调用 | 仅正则不确定时调用 + session 缓存 + 120 token schema |
| 合并执行核心影响 Tk 路径 | adapter 双跑对比事件流；开关默认 off，旧路径保留一个版本 |
| 删除 `range_whole_partial` 后真出现范围错引 | 审计改为自动追加真实范围括注，用户仍能看到范围 |

## 12. 文件级改动清单

**新增**

- `ai/praat_ai/report_audit.py`
- `ai/praat_ai/router.py`
- `ai/praat_ai/execution_core.py`
- `ai/praat_ai/telemetry.py`
- `ai/tests/golden_backend.py`（由 `bench_guard_rate.py` 演化）
- `ai/tests/test_report_audit.py`、`ai/tests/test_router.py`

**修改**

- `cloud_agent.py`：报告守卫 hook → 审计（`:1161-1211`）；L2 去 `wait_for`（`:1073`）；`finish_*` 三态；错误映射（`:961-965`）
- `modern_execution.py`：收尾拼装（`:701-760`）、`coverage` 移出正文（`:714-716`）、遥测
- `workflow_policy.py`：预设去 `l2_deadline_sec`/`l2_request_limit`（`:19-21`、`:52-58`）；`finish_locally` 三态
- `escape_policy.py`：`Budget` → `TurnBudget`（`:41-58`）；状态三态
- `dialogue_policy.py` / `cloud_agent.entry_mode`（`:292`）/ `chat._whole_recording_request`（`:485`）：正则 → router hint
- `report_guards.py`：保留 5 条硬规则（`:433`、`:884`、`:893`、`:975`、`:986`）+ 抽取工具函数，其余迁出
- `chat.py`：老路径改 adapter

**删除**

- `report.range_whole_partial`（`report_guards.py:536-550`）
- `number_unmatched` 的阻断路径（`report_guards.py:588-592`）
- `asyncio.wait_for(l2_deadline)`（`cloud_agent.py:1073`）
- `l2_deadline_sec` / `l2_request_limit` 配置项
- `metrics['rejected_report']` 全文落盘（`cloud_agent.py:1169`）

**不动**

- `delivery.py` 四态投递事实
- `Evidence` 证据账本与 `publish_results`（先发数据后解释）
- 工具层守卫（schema / 对象唯一解析 / 脚本只许 Praat / 整段不许用旧选区）
- `measurement_contract` / `measurement_complete` 完成度合同

## 附录 A：真机事故复盘（原始触发）

会话 `8968befb2d874b9487fa5c180fec7fb8`，任务 `451deb808533402795cf9ea19be5093c`（`ai/runtime/modern/sessions.sqlite3` 的 `model_events` 可完整还原）：

| 时刻（UTC） | 事件 |
|---|---|
| 17:25:26 | 目标：这是日语语音あなた，与标准音对比，指出发音不足之处与待改进点 |
| 17:25:29–57 | 8 次工具调用（pitch_statistics×4、measure、intensity_statistics×3） |
| 17:25:57.5 | 进入 L2，`deadline = +15 s` |
| 17:26:08.6 | 模型交出完整、合格的评估报告（709 output tokens） |
| 17:26:08.6 | 被守卫拒绝：「数值"0 秒"未匹配同对象/范围/量名的测量」 |
| 17:26:12.5 | 只剩 3.9 s，重写请求刚发出即撞 L2 时限 → `BranchExit('L2 阶段时间上限已用完')` |
| — | 已写好的报告被丢弃，正文换成原始测量行 + 「尚未完成：…」+ `partial` |

根因链：`report_guards.py:557` 的范围过滤把 `object_info`（`interval is None`）的候选删空 → 误拒 → 需要重写 → 15 s 预算不够 → 整份丢弃。

## 附录 B：基准方法与 10 轮数据

方法：`ai/tests/bench_guard_rate.py`，真云端 `qwen3.7-flash` + 替身 Praat 派发（`chat._execute_action` 返回与真机同模板的测量行），工具守卫、云端 agent、报告守卫、预算、收尾全部走真实实现。因此**测量数值不作结论**，但路由/预算/守卫/交付路径的行为是真的。

聚合（10 条黄金目标）：

| 项 | 数值 |
|---|---|
| 状态 | 10/10 `partial`；`report_verified` 0/10 |
| 请求 | 合计 81，平均 8.1/轮；5 轮撞满 11（上限 12−1） |
| 墙钟 | 平均 36.8 s/轮 |
| 正文 | 7/10 含「没有通过自动数值核对」「[未通过核对的数值]」 |
| 阻断 | 26 次：`number_unmatched` 16（8 轮）、`range_whole_partial` 8（3 轮）、`tool.policy.pitch` 1、`tool.policy.textgrid_info` 1 |
| 修正 | `citation_filled` 15、`calc_unverified` 8、`delivered_with_masks` 7、`calc_malformed` 2、`coverage_repaired` 2 |

被拦数值样本（判断真假阳性）：

```
"891.5 Hz"     ← 正文「全段共振峰 F1=891.5 Hz [E7]」，E7 就是 0–0.892 的整段 measure 行（假阳性）
"119.243 "     ← 真实证据值，仅缺少单位（假阳性）
"0.000 秒"/"0.6 秒" ← 范围端点与透明推导（假阳性 / 设计未覆盖）
"20 dB"        ← 一般知识典型范围（假阳性）
"42.229 Hz"/"-7.433 dB"/"7.4 dB"/"8.3 %" ← 模型自算的差值/比例（设计上要求走 calc）
```

## 附录 C：为什么选 B 方案而不是 A（结构化渲染）

A 方案（模型只输出结论 + 证据编号，数字由程序渲染）长期更干净，但要重写报告协议、提示词与前端渲染，改动面覆盖 `AnalysisReport` schema、`report_guards`、`PromptedOutput` 与前端 Markdown 渲染。

B 方案（自由文本 + 逐值处置）保留现有协议，只改处置语义，可在 S1 单步上线并用同一基准验证收益（预计阻断 26 → 0、`verified` 0/10 → 6+/10）。落 S1 后若遮蔽率仍高，再评估迁到 A。
