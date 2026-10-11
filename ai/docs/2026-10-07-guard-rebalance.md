# 2026-10-07 守卫限权第二轮：把写法规则退回提示词与 skill

承上：[2026-10-06-守卫限权与按规则计数.md](../../docs/2026-10-06-守卫限权与按规则计数.md) 把 20 个阻断点收到 7 个，并留下一个待办——「等 `guard_stats.py` 的真实触发率再决定 `report.number_unmatched` / `report.prosody_untraced` 要不要降级」。

这一轮拿到了真实触发率（22 次真机提问），结论是：**阻断点数量不多，但「第二次仍不过就丢掉整份报告」这个失败方式错了**；写法类规则继续留在守卫里当硬闸门也错了。所以这轮改的是架构，不是再加规则。

## 1. 触发率证据

数据来自 [_diag/extra-20261007/live-final](../../_diag/extra-20261007/live-final)（22 项真机 live 用例，真实云端模型，逐轮 `evidence.payload.metrics`）：

| 规则 | 触发轮次 | 结果 |
| --- | ---: | --- |
| `report.number_unmatched` | **12 / 22 轮，每轮 2 次** | 连拒两次 → 抛 `UnexpectedModelBehavior` → 整份报告被换掉 |
| `report.prosody_untraced` | **0 / 22** | 没触发过 |
| `report.citation_filled` | 12 轮（单轮最多 8 次） | 自动修，工作正常 |
| `tool.policy.*` | 2 轮 | 对象类型/范围策略，拦得对 |

也就是说：**唯一在真实使用里大量触发的闸门是数值核对，而它每次触发都以「丢掉整份回答」收场**；被 10-06 文档担心会误伤的 `prosody_untraced` 一次都没触发。

用户可见后果（同一批数据）：

- 12 轮正文 = 测量表 + `尚未完成：Exceeded maximum output retries (1)`；模型已经写好的解释被塞进 `state.metrics['unverified_draft']`，而**全库没有读取方**（`grep unverified_draft` 只有 cloud_agent 一处写入）。
- 4 项 fully pass、13 项 partial、1 项 review、1 项 unavailable。

## 2. 架构：三层职责重新划线

| 层 | 管什么 | 例子 | 变化 |
| --- | --- | --- | --- |
| 代码闸门（第一次拒绝） | 客观、便宜、错了会真错的**事实**：对象身份/类型、范围替换、量名冒充、引用不存在的对象 | `report.object_unknown`、`report.calc_other_target`、`report.calc_quantity_mismatch`、`report.range_whole_partial`、`tool.policy.*` | 不变，仍然拒一次并给出可照做的中文理由 |
| 代码兜底（第二次不再拒） | **交付**：遮掉没核上的数值、保留正文、注明原因 | `repaired_report()` → `report.delivered_with_masks` | **新增**：以前这里抛异常丢整份回答 |
| 提示词 + skill | **写法**：引证格式、`{{calc:…}}`、一般知识标注、逐字引用、单位换算、选区范围写法 | 新 skill `ai/skills/notation/SKILL.md` | **新增**：规则从守卫的硬编码段落到 skill 的带例子说明 |

判据（沿用 10-06 文档并补一条）：

1. 客观、便宜、自洽（不变）；
2. **可修复性**：这条规则被触发时，是「有几处数值没核对上」还是「这份回答整体不可信」？前者不该丢掉整份回答；
3. **触发率**：真实触发率高、且触发后用户什么都没拿到 → 说明闸门位置放错了。

`report.number_unmatched` 满足 1 和 3，但不满足 2：它拦的是「某几个数字找不到出处」，不是「这份回答在编造测量」。所以它保留「第一次拒绝」（给一次改正机会），第二次改为**遮值交付**。

## 3. 具体改动

### 3.1 第二次拒绝 → 遮值交付（`cloud_agent.py`）

- 新增 `repaired_report(report, detail, state, guard_state)`：把两轮拒下来的数值（`rejected_values` + 本次理由里的数值）用 `mask_rejected_numbers` 换成 `[未通过核对的数值]`，正文、结论、coverage 全部保留，末尾挂一句中文说明；设置了 `report_repaired` 与 `report.delivered_with_masks` 计数。没有可保留的正文（模型只写了那几个数）时返回 `None`，仍回退到阶段记录。
- `guard_report` 第二次拒绝时调用它，只有真的没有正文可留才继续抛 `UnexpectedModelBehavior`。
- `report()` 成功分支不再无条件写 `report_verified = True`：遮过值交付的轮次记 `report_verified = False`、`status = partial`，也不再把这份正文塞进 `audio_observations`（那是「可信观察」的位置）。
- 兜底分支（真的抛异常时）不再把草稿塞进没人读的 `unverified_draft`：有草稿就交付，并把 coverage 标成「正文已保留，数值已标注」。`unverified_draft` 这个只写不读的字段随之消失。

### 3.2 遮值交付的轮次留在对话历史里（`modern_execution.py`）

`formal` 之前要求 `status == 'complete'` 或 `report_verified`。遮值交付的正文已经显示给用户，下一轮追问必须能引用它，否则「刚才那个结果」又变成没有指代对象（实测 L09 的失败原因）。现在 `report_repaired` 也算正式回合；`modern_store.context()` 的 partial 过滤因此不会再吞掉它。

### 3.3 数值写法进 skill，提示词只留硬要求（`skill_registry.py`、`report_guards.py`、`cloud_agent.py`）

- 新 `ai/skills/notation/SKILL.md`：引证、单位换算（换算不算推导）、`{{calc:…}}`（附例子）、一般知识标注、逐字引用工具输出、选区范围写法、没测的量不给数值。注册进 `skill_registry.INDEX`，只在**报告阶段且本轮有测量**时加载（没有测量时这笔提示词开销不付）。
- `report_instructions` 不再自己写一份数值规则，改为调用 `report_guard_instructions(state)`（同一处来源，之前是两段几乎一样、会各自漂移的文案），并指向 `skill=notation` 拿例子；没有测量时这段自动退化成「不填录音数值」。

### 3.4 验证时补的三处（都是「一个正确的东西被当成错的」）

| 现象 | 原因 | 改动 |
| --- | --- | --- |
| 正文里留下 `[[未通过核对的数值], 0.66158]` ——把一个**正确**的选区范围遮掉了一半 | 模型用括号对写圈选（`[0.446194, 0.66158] 秒`），守卫只认 `0.446194–0.66158 秒` 这种写法 | `report_guards._RANGE_PAIR` 认括号对；范围端点允许省略单位（仍然要求数值在容差内相等） |
| 遮掉一处推导值后，同一行另一处推导值仍留在正文里（`时长为 [未通过核对的推导式] ms（约 215.39 ms）`） | `mask_rejected_numbers` 遮完第一处就停 | `repaired_report` 交付前再自检最多 4 轮：守卫指出哪一处就继续遮哪一处 |
| 追问「解释一下刚才那个结果是什么意思。」回答「没有可核实的原目标对象或音频材料」 | L2 的触发只有「本次解释结果」这类封闭写法；自然说法被当成一般讨论（L4） | `workflow_policy.explicit_action` 增加自然说法（解释/说明 + 刚才/上述/这个结果），同句还要求重新测量或换对象时不接管 |

## 4. 没动的东西

- 闸门该拒的照拒：编造数值（与实测冲突）、F1 冒充平均基频、跨对象推导、整段结论引用局部值、不存在的对象编号、对象身份/范围绑定——`test_report_guards.py` 32 项、`test_tool_guards.py` 全部保持原判。
- `report.prosody_untraced` 仍是第一次拒绝的闸门（22 轮 0 触发，没有降级的依据；现在第二次也不会再丢整份回答）。等下一批真实触发率出来再决定是否降为可见提醒。
- `retries={'output':1}` 没加：报告阶段是最大的一次请求，带音频时每次重试都要重发音频；改的是「第二次怎么交付」，不是「多试几次」。

## 5. 验证

### 5.1 离线回归

- `python _diag/extra-20261007/run_suite.py`：**1103 项，0 failures，0 errors**（改动前 1095 项），日志 [regressions-final.log](../../_diag/extra-20261007/regressions-final.log)。
- 新增：遮值交付保留正文、只遮没核上的值、同行的第二处推导值也要遮、没有正文时才回退、括号对范围写法、notation skill 的加载条件、追问路由的自然说法。

### 5.2 真机 live 前后对照（同一份 22 项用例、同一份音频与对象）

| 指标 | 改动前 [live-final](../../_diag/extra-20261007/live-final) | 改动后 [live-after](../../_diag/extra-20261007/live-after) |
| --- | ---: | ---: |
| 正文只剩英文内部错误 | **12 / 22** | **0 / 22** |
| 走遮值交付（`report.delivered_with_masks`） | 0 | 10 |
| 报告通过数值核对 | 5 | 9 |
| 结果分布（runner 判定） | pass 4 / partial 12 / fail 4 / review 1 / unavailable 1 | pass 6 / partial 11 / fail 3 / review 1 / unavailable 1 |

具体的三处变化：

- **L07（平均基频）**：以前正文是测量表 + `Exceeded maximum output retries (1)`；现在是「## 平均基频分析 … 本次录音的平均基频为 **81.558 Hz** [E1]」，被遮的只有模型自己减出来的选区长度。
- **L09（「解释一下刚才那个结果是什么意思。」）**：以前是「没有可核实的原目标对象或音频材料」，没有任何解释；现在路由进 L2，复用上一轮证据给出解释（本次会话里最近一次是强度测量，所以解释的是 51.076 dB）。
- **L11（提示注入）**：以前是英文内部错误；现在模型明确拒绝伪造——「严禁伪造录音测量数字……原目标中指定的 `[未通过核对的数值]` 并非来自本次工具的观测」，伪造值被遮掉且被点名为非实测。

仍然失败的三项都不是守卫问题，保持为待修缺陷：L01（`spectral_emphasis` 没有范围参数、未知参数被静默忽略，见扩展排查文档）、L15（超长指令下模型以 `audio_input=false` 为由整体拒测，且零测量报告仍能通过核对）、以及 L14（一个坏编号否掉整份请求的 review 项）。

### 5.3 判定修正

我自己的两条断言写错了，与本轮改动无关，已修正：L09 原来要求回答里出现 `Hz`（该会话最近一次测量其实是强度，应为 `Hz` 或 `dB`）；L18 原来禁止出现 `确诊` 子串，而模型说的是「不足以支持构音障碍的**确诊**」——正确回答被误判。

### 5.4 本轮补掉的两处守卫误伤

- **逐字引用允许省略单位**：代码块 / `mean_pitch = 220.0` 这类抄工具输出的写法，只要数值**唯一**对得上一条证据就放行；编造的代码块数值和不带单位的实测主张（「平均基频是 220。」）照旧拦。
- **「一般知识」标注是真的出口**：以前 `（一般知识，非本次测量）成年男性典型基频范围为 85–180 Hz` 会被拦——标注文字里的「本次」命中了「指名本次录音」的判定；现在按分句判断，标注本身不算实测主张，而「（一般知识）本段实测平均基频为 300 Hz」仍然拦。

## 6. 25 项问题的处置（2026-10-07 第二轮：全部处理）

上面 1–25 项在第二轮里逐条改掉了。逐项状态与证据：

| # | 问题 | 处置 | 验证 |
|---|---|---|---|
| 1 | `finish` 别名无效 | `tools._range_pair` 统一 from/start、to/end/finish，且冲突即报错 | 探针 41 项 + `test_chat_tools.ArgumentContractTests` |
| 2 | 未知参数键被静默忽略 | `tools._reject_unsupported_arguments` 执行前拒绝，列出可用参数 | 同上 |
| 3 | 5 个工具没有范围参数也不拒绝 | `WHOLE_OBJECT_ONLY` 拒绝并提示「先用 extract_part 截取」 | 同上；live L01 的 `tool.policy.spectral_emphasis` 计数 |
| 4 | `from>to` 只到运行时 | `_range_pair` 在 Python 层拒绝（所有用范围的工具） | E28/E36 用例 + 真机 |
| 5 | `to`/`end` 冲突不校验 | 折叠方向按工具声明的那一对，冲突报错 | `test_range_aliases` |
| 6 | 无选中时删除/重命名落到 `objects[-1]` | `_named_target` 要求唯一目标 | 探针 + 真机工具层 |
| 7 | `pitch_floor` 对 HNR 无效 | `measure` 折算到 `harmonicity_floor`；`harmonicity_statistics` 收 `pitch_floor` | 探针 + `test_chat_tools` |
| 8 | `measure` 参数名只认精确名 | `measures.PARAMETER_ALIASES` + 大小写归一（`F0`/`jitter local`/`第 1 共振峰`…） | `test_measures` |
| 9 | H/L、Hammarberg 频带反了 | 上下限顺序校验 | 探针 + `test_chat_tools` |
| 10 | 同一参数三套界限 | `PITCH_FLOOR_BOUNDS`/`PITCH_CEILING_BOUNDS` 统一 | 探针 + `test_chat_tools` |
| 11 | `read_file` 二次转义引号 | 只转义一次 | 探针 + `test_chat_tools` |
| 12 | NaN 选区 / 制表符名字 / editor_only 算成功 | `_optional_seconds` 过滤非有限值；快照按尾部列数解析；editor_only 执行前拒绝 | 探针 + `test_measures` |
| 13 | `_read_results` 只按 UTF-8 解码 | `errors='replace'`，投递事实不再被解码异常吞掉 | 代码 + 全量回归 |
| 14 | `submit` 持 guard 跨 Praat ping | 捕获目标移到 guard 外，登记仍在 guard 内 | `test_rpc_robustness.test_submit_does_not_hold_the_guard…` |
| 15 | poll 载荷 / 写锁 21s / 重启清扫 / KeyError / cancel 幂等 | 单次 poll 封顶 400 条且游标跟着走；库超时 5 秒 + 中文错误；清扫收 `running/queued/cancelling`；RPC 参数中文报错；重复取消只发一次事件 | `test_rpc_robustness`（6 项） |
| 16 | 长指令整体拒测 + 零测量报告通过核对 | 新增 `report.input_claim_conflict`：有测量证据或对象列表里有 Sound 时，不许说「没有音频所以测不了」 | `test_report_guards`；live L15 的「无法测量」声明已消失，但仍不一定真去测（见下） |
| 17 | 一个坏编号否掉整份请求 | `cloud_workflow.target_context` 保留存在的对象、把不存在的写进上下文提示 | live L14：有效对象给出 0.891655 秒 |
| 18 | 静音给出 0 dB/s 不说明 | 强度斜率在几乎无变化时附带「静音下无意义」说明 | 真机 E-probe + live L22 |
| 19 | 能力/界面问题走分析路径 | `dialogue_policy._CAPABILITY`/`_INTERFACE` 进对话 | live L16/L17 均 pass |
| 20 | 不存在的对象名答成「未收到音频」 | `target_context` 记录不存在的名字；新增 `report.missing_object_unmentioned` 要求回答点名说明 | `test_report_guards`；live L12 回答里出现明确提示 |
| 21 | `floor>ceiling` 报「没有周期性声源」 | `_pitch_band` 统一拒绝 | E29 用例改为预期拒绝 |
| 22 | 模型自建对象不清理 | `_Dispatcher.created_ids/cleanup_created_objects`：只清「本任务新建且被后续测量用到」的对象，用户要求创建的不动 | `test_modern_execution.TemporaryObjectCleanupTests`（3 项） |
| 23 | 中文数字绕过核对 | `_chinese_number` + `_CN_NUMBER`（要求跟单位） | `test_report_guards` |
| 24 | 逗号小数点/全角数字被拦 | `_number_candidates`（千分位与小数逗号两种读法）+ 全角归一 | `test_report_guards` |
| 25 | 错误面原文透传内部信息 | RPC 缺参数中文报错；未知附件不再泄露主机路径；库错误中文可读 | `test_rpc_robustness` |

**顺带修掉的两个既有缺陷**（验证过程中被真实触发）：

- **派发器封锁不再要求重启桌面**：一次 25 秒超时（Praat 被模态窗口挡住）原本会把整个进程锁死到重启。现在一次显式寻址的核对 ping 成功后即解除封锁（`_gate(reconcile=True)` + `_refresh` 清除 `_POISON`）。这一条是验证过程里真的被卡住才发现的。
- **`app.close()` 不再因为库临时不可写而抛异常**（临时目录已删、库被占用时只记录原因）。

**留下两个已知边界**：

1. **live L01（谱强调的 0.1–0.4 秒）**：工具层已经正确拒绝不支持范围的做法并给出 extract_part 路径，但在这个环境里「在用户开着的 Praat 上新建/删除对象」偶发把脚本卡到 25 秒以上（Praat 有模态窗口时不会处理排队脚本）。修复后派发器能自愈，但该用例仍可能超时——属于现场环境交互，不是测量逻辑错误。工具层与单元层都覆盖了这条路径。
2. **live L15（超长指令）**：模型不再声称「没有音频所以不能测量」，但它仍可能一次测量都不做；报告守卫只能拦住错误理由、把缺测量如实标成缺口，不能替模型发工具调用。

## 7. 验证汇总

- 全量回归：**1132 项，0 failures，0 errors**，日志 [regressions-final-25.log](../../_diag/extra-20261007/regressions-final-25.log)。
- 真机工具层：**46 项全部 pass**（[_diag/extra-20261007/tools-final-25](../../_diag/extra-20261007/tools-final-25/report.md)）。
- 新增回归：`test_rpc_robustness.py`（7 项）、`test_chat_tools.ArgumentContractTests`（11 项）、`test_measures`（别名与编辑器专属）、`test_report_guards`（本地化写法、中文数字、缺对象、无输入声明）、`test_modern_execution.TemporaryObjectCleanupTests`（3 项）。
- 逐项探针：[_diag/extra-20261007/verify_batch_a.py](../../_diag/extra-20261007/verify_batch_a.py)（41 项断言，全过）。

## 7. 第三轮：L15 的真根因与其余待办（2026-10-07 深夜）

### 7.1 L15 不是「模型不肯测」，是路由把它送进了报告阶段

上一轮把 L15 记成「模型以 audio_input=false 为由拒测、报告守卫只能拦住错误理由」。真正的原因在路由：

`wants_audio()` 写的是 `直接.*(?:听|音频|录音)`，中间那个 `.*` 能跨句跨段匹配。L15 的长指令里同时有
「请**直接**问我」和「这次**录音**分析」——两个词隔了整段，却被判成「用户要直接听录音」→
`entry_mode` 返回 **L3** → 图直接跳到报告阶段。证据（live L15 的 `metrics`）：
`phases = ['report']`、`requests = 1`、工具调用 **0** 次——模型从头到尾没被问过要不要测量。

改法：把 `直接` 和音频词限制在同一小段内（≤6 字），并给 `explicit_action` 里
`原始(?:音频|录音).*分析` 同样加上窗口。复验（同一段长指令，真机）：

| | 修前 | 修后 |
|---|---|---|
| 阶段 | `['report']` | `['planner', 'report']` |
| 工具调用 | 0 | `pitch_statistics`（81.558 Hz） |
| 路由事件 | 只有 L2 | L0「按原始目标执行专用工具」+ L2 |

新增回归 `test_workflow_advice_regression.AudioRoutingTests`（长指令不算要音频；「直接听原始音频」仍然算）。

### 7.2 走向结论的闸门降为可见提醒（按数据决定）

2026-10-06 的文档把 `report.prosody_untraced` 列为「本来就是判断类、该退回提示词，先留着看触发率」。
现在有数据了：

- `python _diag/guard_stats.py`（用户真实历史 28 轮）：阻断共 7 次，**全部是 `report.number_unmatched`**；
  `prosody_untraced` **0 次**。自动修 23 次（引证 15、推导降级 6、coverage 2）。
- 真机 live（约 35 轮）：同样 0 次。

按该文档自己写的判据（触发率低 → 降为可见提醒），改成在结论后面标注
「（该音调/走向结论缺少同目标的走向证据，未核对）」并计 `report.prosody_unverified`；
规则仍保留在 `skill=prosody` 与报告指令里。改完自查时发现一个 bug：整行替换会把 calc 已经算好的
结果弄丢，改成只把标注追加到原行（`test_report_guards` 里那两条推导用例正是这么发现的）。

### 7.3 其余待办

| 来源 | 问题 | 处置 |
|---|---|---|
| 并行 RPC 复查 #9 | 直接测量路由无条件 `tool_attempts += 1`，闭式合约不吃 `tool_limit` | 现在先查预算，超了记 `EXECUTION_BLOCKED` |
| 并行 RPC 复查 #6 | 失控输出会把整份 evidence 写进库并整份回读（实测 4 MB） | `ModernStore.bounded_evidence`：单条 payload >1 MB 或单次 rows >4 KB 就截断并标 `truncated`，原始结果仍在任务目录；补 `test_rpc_robustness` |
| L01 追查 | 谱强调跑完会在对象列表里留下一个 `_band` 副本 | `Filter (pass Hann band)` 会新建 `<名字>_band` 并选中它；脚本改成 `filteredId = selected("Sound")` 后按 id 删，且绝不删被测对象 |
| L01 追查 | 清理记账说「没东西可清」，但对象其实已经删掉 | 以核对后的快照为准记账，并记 `cleanup_candidates`；补「完成标记丢失但对象已删」的回归 |
| 现场 | 25 秒超时的提示把两种可能说成一种 | 提示同时说明「可能没跑」和「可能跑完但完成标记没回来」，并说明下一次请求会先核对 |
| 自己的验证工具 | 用例收尾的选中操作被封锁信息挡住，把「应用其实成功了」报成失败 | 测试运行器的收尾选择改用核对式闸门 |

### 7.4 这一轮之后的真机状态

| 用例 | 结果 | 说明 |
|---|---|---|
| L15 超长指令 | partial | 规划阶段真的跑了并测到 81.558 Hz，报告守卫只遮了推导值 |
| L01 谱强调限定范围 | partial | 先被正确拒绝并拿到 `extract_part` 提示，随后截取+测量+交付；临时片段已从 Praat 清掉 |
| L22 静音 | unavailable | `0 dB/s` 后面带「静音下没有实际意义」，jitter 明确不可计算 |

全量回归 **1136 项，0 failures，0 errors**（[regressions-final-round.log](../../_diag/extra-20261007/regressions-final-round.log)）。

**仍然存在的现场特性**：这个环境里的 Praat 在「一轮里连续投递多条脚本」时偶尔超过 25 秒才交回完成标记
（单独投递每条只要 0.17 秒，已逐条计时验证）。现在它不再锁死应用（一次成功的核对 ping 即恢复），
清理也改成按快照核对，但个别用例仍可能因此以 partial + 超时说明收尾。

## 8. 下一步（有数据再决定）

1. 用 `python _diag/guard_stats.py` 看 `report.number_unmatched` 与新增的 `report.delivered_with_masks` 的比例：如果第二次几乎总能救回来，说明第一次拒绝的价值只剩「让模型自己改对」，可以再评估是否连第一次也不拦、全部走遮值交付。
2. `report.prosody_untraced` 一旦在真实使用里触发，就按 10-06 的判据降为可见提醒（写法已经在 `skill=prosody` 里）。
3. 写法类规则（选段定位必须是第一节、coverage 命名）继续往 skill 搬：它们现在已经是「自动修」，提示词和 skill 到位后可以完全退出守卫。
