# 扩展后端排查：GPT 28 项之外的 68 个真实用例（2026-10-07）

本文回答三件事：GPT 那份模拟真实用户输入的 28 场景文档覆盖了什么、后端逻辑复核发现什么、以及另外 68 个它没覆盖的问题在真机上跑出什么结果。

- 被复核的文档：[backend-scenarios.zh-CN.md](backend-scenarios.zh-CN.md)、[backend-scenarios.questions.md](backend-scenarios.questions.md)、[backend_scenarios.json](../tests/backend_scenarios.json)、[run_backend_scenarios.py](../tests/run_backend_scenarios.py)。
- 新增用例定义：[backend_scenarios_extra.json](../tests/backend_scenarios_extra.json)（68 项，46 工具层 + 22 真实 RPC/模型）。
- 新增运行器：[run_backend_scenarios_extra.py](../tests/run_backend_scenarios_extra.py)。它复用 28 项那套已验证的机制（`run_tools`、`live_app`、控制台解码、静音夹具），只增加 28 项没有做的断言。

```powershell
python ai/tests/run_backend_scenarios_extra.py --mode plan          # 只列题目
python ai/tests/run_backend_scenarios_extra.py --mode tools         # 46 项真实 Praat 工具/边界
python ai/tests/run_backend_scenarios_extra.py --mode live --object 1   # 22 项真实 RPC + 真实模型
```

---

## 一、28 项覆盖了什么、没覆盖什么

28 项的质量在于「用同一段音频重复手测容易漏掉范围、对象类型和多轮上下文」这个判断是对的，工具层协议检查也可复用。但按工具清点后，覆盖面集中在少数几个测量工具上：

| 范围 | 28 项实际覆盖 | 从未运行 |
|---|---|---|
| 工具（36 个） | 14 个：duration、object_info、pitch、pitch_statistics、intensity、intensity_statistics、formant_frequency、formant_bandwidth、formant_statistics、harmonicity_statistics、measure、vot、spectral_emphasis、intensity_slope | **22 个**：spectrogram、textgrid_info、textgrid_set_interval、textgrid_insert_boundary、select_object、view_edit、play、rename_object、remove_object、create_sound、concatenate_sounds、extract_part、save_sound、read_file、resample_sound、duplicate_object、hl_ratio、hammarberg_index、pitch_peak_latency、peak_to_average_ratio、run_praat_script、custom_script |
| `measure` 参数（40 个） | 6 个：centre_of_gravity、skewness、kurtosis、local_jitter、local_shimmer_percent、cpps | 34 个，含 median/sd/slope、f3/b3、rms/peak_amplitude、rap_jitter、apq3_shimmer、local_shimmer_db、6 个专用参数、编辑器专属参数 |
| 参数契约 | 只有少量非法对象/类型 | 别名 `finish`/`end`、未知参数键、`from>to`、超时长范围、频带上下限、`additionalProperties`、引号/反引号转义、NaN 选区列 |
| 对话与模型路径 | 3 项（26 一般知识、27 发音边界、28 多轮复用） | 多轮换量/换范围/失败后恢复、提示注入、英语/繁体、单位换算（kHz/毫秒）、临床边界、界面请求、超长输入、不存在对象名、跨对象类型工具 |

结论：28 项证明的是「几个主力测量模板在真实 Praat 里能跑」；它没有覆盖 22 个工具、34 个参数，也没有覆盖任何参数契约边界，live 侧的多轮行为只有一个样例。

---

## 二、真实运行结果

### 工具层 46 项（真实 Praat 批处理，无模型）

报告：[_diag/extra-20261007/tools-final/report.md](../../_diag/extra-20261007/tools-final/report.md)（首轮 44 项的原始报告在 [_diag/backend-extra-tools-20261007-013151](../../_diag/backend-extra-tools-20261007-013151/report.md)，期望修正后的复跑在 [_diag/extra-20261007/tools-refix](../../_diag/extra-20261007/tools-refix)）。

**45 项通过，1 项 review（E29）。** 22 个从未运行的工具、34 个未测参数在真机上都跑通了，`measure` 的混合分隔符（`、，`）、Bark 单位、`times` 别名、8 时刻上限也都正常。

review 的 E29 是真实问题：`pitch_floor=500、pitch_ceiling=100` 这种上下限写反的请求没有被拒绝，脚本照跑，然后报告「该区间没有周期性声源」——把**参数错误**说成了**录音没有周期成分**，用户会据此怀疑自己的录音。

首轮我把 E07/E08 的期望写错了（`measure` 里混用 `hl_ratio`、`hammarberg_index` 这类专用参数**本来就该被拒绝**，并提示改用专用工具），修正期望后 E07/E08/E45/E46 全部通过。这条拒绝消息设计得不错，正是「加工具比加提示词管用」的例子。

### live 22 项（真实 `sessions.create → tasks.submit → events.poll` + 真实云端模型 qwen3.7-flash）

报告：[_diag/extra-20261007/live-final/report.md](../../_diag/extra-20261007/live-final/report.md)，逐项 `events.json`／`evidence.json` 在同目录。

| 判定 | 数量 | 用例 |
|---|---:|---|
| pass | 4 | L02（范围写反被提交前拒绝）、L04（毫秒→秒的范围绑定正确）、L06（繁体中文）、L20（范围写反被拒） |
| partial（测量对，报告阶段整份丢弃） | 13 | L01、L03、L05、L07、L08、L10、L11、L12、L13、L16、L17、L19、L21 |
| fail | 3 | L09、L15、L18 |
| review | 1 | L14 |
| unavailable | 1 | L22（静音，jitter 明确返回不可计算） |

**测量层是好的**：12 项拿到了正确且标注了范围的测量行（含圈选范围 0.446–0.662、指定范围 0.100–0.400、多轮换量）。**坏在报告层**：12/22 的用户可见正文里只有测量表加一句英文内部错误 `Exceeded maximum output retries (1)`。

---

## 三、证据级缺陷

### 1（最高影响）报告核对连续两次不过 → 整份报告被丢弃，用户看到英文内部错误

- 现象：`evidence.json` 的 `metrics.guard_blocks = {'report.number_unmatched': 2}`，`report_verified = false`，正文只剩测量表和 `Exceeded maximum output retries (1)`。22 项里 12 项如此。
- 链路：`report_guards.validate_report` 抛 `ModelRetry` → 第二次拒绝时 [cloud_agent.py:1143-1147](../praat_ai/cloud_agent.py#L1143-L1147) 抛 `UnexpectedModelBehavior('Exceeded maximum output retries (1)')`（`retries={'output':1}`，见 [:1153](../praat_ai/cloud_agent.py#L1153)）→ 模型已经写好的正文被存进 `state.metrics['unverified_draft']`（[:1241](../praat_ai/cloud_agent.py#L1241)）→ `finish_locally` 把 `state.report` 直接覆盖成裸测量行（[escape_policy.py:214-222](../praat_ai/escape_policy.py#L214-L222)）。
- `unverified_draft` **全库只有这一处写入、没有任何读取方**（`grep -r unverified_draft ai/` 只有 cloud_agent.py:1241）。也就是说模型真的写出了解释，只是永远不显示。
- 影响：正常提问的默认结局是「测量表 + 一句英文错误」。这不是模型偶发失败，而是 12/22 的系统性结局。

### 2 数值守卫误拒三类正常表述（用真实证据逐行复现）

用 L07 的真实 `evidence.json` 重放守卫（[_diag/extra-20261007/replay_guard.py](../../_diag/extra-20261007/replay_guard.py)、[guard_review.py](../../_diag/extra-20261007/guard_review.py)）：

| 正文写法 | 守卫 |
|---|---|
| 平均基频 81.558 Hz [E1] | PASS |
| 平均基频 81.6 Hz [E1] | PASS |
| 0.446–0.662 秒内…… [E1] | PASS |
| 平均基频 0.0816 kHz [E1] | PASS（单位换算支持） |
| **编辑器圈选区间 [0.446194, 0.66158]** | **BLOCK**（写成范围 `0.446194–0.66158 秒` 才通过） |
| **该选段时长为 0.215386 秒**（自己减出来的） | **BLOCK**（0.215、0.22 同样被拒） |
| **最高与最低相差 19.3%**（自身推导） | **BLOCK** |
| **成年男性典型范围 85–180 Hz**（一般知识常量，与实测值同一节） | **BLOCK** |
| **（一般知识，非本次测量）成年男性典型基频范围为 85–180 Hz** | **BLOCK**（标注了也拦） |
| `mean_pitch = 81.558`（代码块里逐字引用工具输出） | **BLOCK**（缺单位就被判未匹配） |
| **平均基频约八十一点六赫兹**（中文数字） | **PASS**（中文数字完全不核对） |

前五行说明守卫抓对了（把最高值 95.510 说成平均值会被拦、编造 217.3 Hz 会被拦）；问题在**后四行**：选段长度、百分比、一般知识常量、逐字引用都是正常报告写法，却都算「未核对数值」。守卫要求推导必须写成 `{{calc:...}}` 占位式，提示词里确实写了这条（[cloud_agent.py:168-169](../praat_ai/cloud_agent.py#L168-L169)），但模型基本不照做——于是每次正常回答都触碰这条规则。配合「只重试 1 次、第二次就丢整份报告」，结果就是缺陷 1。

反向漏洞也在同一张表里：**中文数字（八十一点六）绕过全部数值核对**，编造的数字只要写成中文就能过。

### 3 追问失去上下文（L09 fail）

`解释一下刚才那个结果是什么意思。`（同一会话紧跟 L07 的基频测量）→ 没有保留任何测量证据，正文是「没有可核实的原目标对象或音频材料」。原因是上一轮以 `partial` 结束，而 [modern_store.py:270](../praat_ai/modern_store.py#L270) 的历史过滤只要 `formal` 的助手回合，[modern_execution.py:613](../praat_ai/modern_execution.py#L613) 在取消/部分完成时把 `formal` 置为 false——于是下一轮只剩一条孤立的用户消息。缺陷 1 让轮次普遍变成 partial，这个过滤又把 partial 的正文抹掉，两个缺陷叠在一起，任何「那第二个呢」「刚才那个」都会失效。

### 4 超长指令：忽略最后一句，且「零测量报告」仍能通过核对（L15 fail）

560 字的完整要求 + 结尾「现在先只测量平均基频，别的都不要测」→ 模型一次测量都没做，正文开头是「我是 qwen3.7-flash，我被设置成 Praat 的前端……`audio_input` 为 false，因此我无法执行任何声学测量」。同一轮 `report_verified = true`：**一份没有任何测量、直接放弃任务的报告完整通过了数值核对**，因为它没有可核对的数字。

### 5 工具层契约问题（全部用 `tools.render()` 逐条复现，脚本见 [_diag/extra-20261007/verify_claims.py](../../_diag/extra-20261007/verify_claims.py)）

| # | 问题 | 证据 | 影响 |
|---|---|---|---|
| 1 | `finish` 别名在 9 个范围工具上完全无效 | `pitch_statistics {from:0.1, finish:0.7}` 渲染结果只有 `tmin=0.100000`，`tmax` 仍是 `duration`；[tools.py:1052-1059](../praat_ai/tools.py#L1052-L1059) 只读 `from/start`+`to/end`，[tool_guards.py:136-143](../praat_ai/tool_guards.py#L136-L143) 也只在工具声明了 `start/end` 时才折叠 | **静默按整段测量**，正是 28 项场景 06 想防的那种错 |
| 2 | 未知参数键被静默忽略 | `{from:0.1,to:0.4,pitch_flor:100}` 与 `{from:0.1,to:0.4}` 渲染完全相同；所有工具 schema 都没有 `additionalProperties:false` | 用户的单位/范围/上下限约束被无声丢弃 |
| 3 | `spectral_emphasis`、`hl_ratio`、`hammarberg_index`、`spectrogram`、`duration` 没有范围参数，也没有拒绝 | `spectral_emphasis {from:1.0,to:2.0}` 与 `{}` 渲染完全相同 | 「只看 0.1–0.4 秒的谱强调」会静默变成整段数值 |
| 4 | `from>to` 只在运行时由 `exitScript` 拦 | E28：Praat 返回 `Error: 查询范围无效…`，`returncode=-1`；`vot`/`extract_part` 却在 Python 层就拒绝 | 行为不一致；白跑一次投递，拿不到「请改正」的即时提示 |
| 5 | 无选中对象时 `remove_object`/`rename_object` 落到 `objects[-1]` | 快照 `Sound 1`+`Intensity 3`、都未选中时，`remove_object {}` 渲染出 `selectObject: 3` + `Remove`；[tools.py:173-174](../praat_ai/tools.py#L173-L174) 与 [:2061](../praat_ai/tools.py#L2061)/[:2048](../praat_ai/tools.py#L2048) 没有类别检查 | **误删任意对象**。pydantic-ai 那条路会用 `StrictContext` 拦住，但 [modern_execution.py:173](../praat_ai/modern_execution.py#L173) 用普通 `ToolContext` 渲染，本地/旧路径可直接命中 |
| 6 | `pitch_floor` 对 HNR 无效 | `measure hnr` 给不给 `pitch_floor=40` 脚本完全一样，`To Harmonicity (cc): 0.01, 75.000000, …` 写死 75（[measures.tsv](../praat_ai/measures.tsv) 用的是 `harmonicity_floor` 占位符） | 用户指定下限后拿到的是 75 Hz 下限的结果 |
| 7 | `measure` 参数名只认精确名 | `F0`、`f0`、`mean pitch`、`jitter local`、`共振峰`、`平均基频` 全部拒绝，只有 `mean_pitch` 通过（[measures.py:107-112](../praat_ai/measures.py#L107-L112) 精确匹配） | 模型必须记住 40 个英文名，否则整轮失败 |
| 8 | `hl_ratio` 频带上下限写反不校验 | `{low_from:8000, low_to:100}` 渲染出 `Get band energy: 8000.000000, 100.000000` | 输出一个没有意义的比值 |
| 9 | 同一参数三套界限 | `pitch_ceiling` 在 `pitch`/`pitch_statistics` 上限 2000，在 `measure` 允许 3000；`pitch_floor` 下限 `pitch` 20、`measure` 10、`vot` 40 | 同一句话因为模型选了不同工具，成功或失败 |
| 10 | `read_file`/`save_sound` 二次转义引号 | 路径含 `"` 时渲染成 `Read from file: "D:/in/my """"q"""".wav"`；[tools.py:2224](../praat_ai/tools.py#L2224) 先 `praat_path()` 转义，[:2229](../praat_ai/tools.py#L2229) 又 `quote()` 转义一次 | 带引号的路径永远读不到 |
| 11 | NaN/Inf 选区列直接进脚本 | 快照 `1 Sound S 1 nan inf` → `selection=(nan, inf)` → `tmin = nan`、`tmax = inf`，`_clamp_range_lines` 修不了 | 输出无意义结果 |
| 12 | 编辑器专属参数算「成功」 | `measure {parameter: mean_autocorrelation}` 只写一行「只能在编辑器的 Voice report 里查」，没有数值，却记成一次成功测量 | 报告阶段会把「成功证据」当成有测量 |

### 6 RPC／执行／持久化层

完整报告：[parallel-review-rpc.md](../../_diag/extra-20261007/parallel-review-rpc.md)。以下两条我独立复现过，其余标为「代码级，未逐条复现」：

- **已复现**：`chat._read_results` 只按 UTF-8 解码（[chat.py:1383-1391](../praat_ai/chat.py#L1383-L1391)），`UnicodeDecodeError` 不是 `OSError`，会穿过 [modern_execution.py:68](../praat_ai/modern_execution.py#L68) 的 `except OSError`。实测：向 `ai/runtime/chat_result.tsv` 写入 1 个非法字节后 `_read_results()` 抛 `UnicodeDecodeError`（对比 `_read_failure` 用了 `errors='replace'`）。后果是**已经投递成功的操作丢掉投递事实**，变成无法归因的失败。
- **已复现（代码级）**：`submit` 在持有全局 `self.guard` 期间调用 `capture_target`/`capture_workflow_target`（[modern_app.py:266](../praat_ai/modern_app.py#L266)、[:291-292](../praat_ai/modern_app.py#L291-L292)），那条路在 `PRAAT_LOCK` 下等 Praat 回应，上限 `EXECUTION_TIMEOUT_SEC = 25.0`（[chat.py:60](../praat_ai/chat.py#L60)）；而 `events.poll` 也要 `self.guard`（[modern_app.py:463](../praat_ai/modern_app.py#L463)）。Praat 忙或弹了模态窗口时，前端的轮询会一起卡住，界面会显示成断线。
- 未复现（来自并行审查的探针）：一次 `events.poll` 可序列化约 2 MB；写锁下 `submit` 要 21 秒才失败；重启清扫只处理 `running`，`cancelling`/`queued` 会被当成助手历史回灌；RPC 缺参数时把裸 `KeyError` 文本（`'sessionId'`）抛给前端；`tasks.cancel` 重复调用会重复发事件。

### 7 运行中观察到的产品行为

- **模型自建的对象不会清理**：L01 让模型自己 `extract_part` 出一个 0.100–0.400 秒的片段（id 145），测量完就留在对象列表里；L21 再问「只算 0.1–0.4 秒的基频统计」时，它顺手把「Sound 片段」和「Sound あなた」两个对象的时长都量了一遍。本轮结束后我已手工删除该对象，原会话状态（Sound 1 / Harmonicity 2、9、16，选中 16，圈选 0.446194–0.661580）已恢复。
- **拒绝理由常是内部术语**：L13（对 Harmonicity 测强度）返回的是「连续两轮没有新增有效证据，停止当前执行分支」，而不是「Harmonicity 对象没有强度可测」。守卫拦对了，话说得不对。
- **一个坏编号否掉整份请求**：L14「分别测量对象 1 和对象 999999 的时长」在提交前被整份拒绝（`明确指定的原目标不在对象快照中`），有效对象的时长也没有给。
- **静音对象给出无意义数值**：L22 的 `强度局部斜率 = 0 dB/s`，没有说明这是静音下的无意义值（同一轮的 jitter 正确返回「无法计算」）。

---

## 四、判定修正与我的误报

- **L01 修正为 partial**：首轮判 fail 是我的检查过严——模型先用 `extract_part` 截出 0.1–0.4 秒再测谱强调，同一轮的结果行里已经写明「区间 0.100–0.400 秒」，范围是交代了的（`spectral_emphasis` 工具本身没有范围参数，这是缺陷 5-3，不是这一项的错）。运行器已改成按整轮结果判断范围披露。
- **L18 无法判定**：临床边界那题测到了 10 行正确的声学测量（含 HNR、F1/F2、局部强度斜率），但报告被整份丢弃，用户既没拿到结论也没拿到免责说明。判定为 fail 的原因是缺陷 1，不是「模型越界诊断」。
- **E07/E08/E45/E46 期望修正**：`measure` 混用专用参数被拒是正确设计，我最初的期望写错了；修正后全部通过。
- **本机环境导致的 API 报错**：本机 `HTTP_PROXY/HTTPS_PROXY=127.0.0.1:7897` 与 `NO_PROXY` 里的 `[::1]` 会让云模型请求抛 `Invalid port: ':1]'`，整批 22 项全部失败。清空这三个变量后正常。这是本机 DSH 环境的问题，不是产品缺陷；但产品把这个错误原文当作分析结论交付给用户（`缺口与影响：Invalid port: ':1]'`），属于错误面处理不够。

---

## 五、建议修复顺序

1. **报告阶段不许整份丢**：守卫第二次拒绝时不要抛 `UnexpectedModelBehavior`；把 `unverified_draft` 真正接出来（现在写了没人读），或把被拒数值就地遮成「未核对」标记后交付。这一条能直接消掉 12/22 的失败。
2. **放宽守卫的误拒**：无单位的逐字引用（代码块/表格）应按「同对象同数值」放行；选段长度、百分比这类四则推导要么接受、要么给出可见的 `calc` 提示；一般知识常量只要标注了就应放行。同时补上中文数字，堵住反向漏洞。
3. **修历史过滤**：`modern_store.context()` 应保留 partial/cancelled 轮次的助手正文（或其摘要），否则「刚才那个」永远接不上。
4. **工具契约统一**：`finish` 别名在 9 个工具上补齐或在 schema 里删掉；给所有 schema 加 `additionalProperties:false`（或在校验里拒绝未知键）；`from>to` 在 Python 层统一拒绝；`remove_object`/`rename_object` 加类别与「唯一目标」要求；`measure` 参数名加中文/别名表。
5. **执行层**：`_read_results` 改用 `errors='replace'`（与 `_read_failure` 一致）；`submit` 在拿 `self.guard` 之前完成 Praat 目标捕获。
6. **用户体验**：把内部原因翻译成用户能懂的一句话；拒绝理由不要出现「L0/L2/分支」这类词；静音对象上的 0 dB/s 要标注为无意义。

---

## 六、没有覆盖的部分

- 46 项工具用例验证的是真实 Praat 模板与结果协议，不经过模型，不验证原生投递和前端渲染。
- 22 项 live 用例验证真实 RPC、真实模型、原生投递与多轮上下文，不打开 React/WebView 窗口，不能证明按钮、滚动、渲染正常。
- `view_edit`、`play` 会打开原生窗口或播放音频，我只在渲染层检查了参数，没有在真机上执行；`run_praat_script`/`custom_script` 只做了拒绝类边界（未知工具、非 Praat 脚本）。
- 「测量数值是否科学合理」「主观发音判断是否准确」仍需人对着音频核对。
- 第 6 节里标为「未复现」的三条来自并行代码审查（完整报告见 [parallel-review-rpc.md](../../_diag/extra-20261007/parallel-review-rpc.md)；另一份对话/编排层审查见 [parallel-review-dialogue.md](../../_diag/extra-20261007/parallel-review-dialogue.md)），本文没有把它们当作已复现的结论。
