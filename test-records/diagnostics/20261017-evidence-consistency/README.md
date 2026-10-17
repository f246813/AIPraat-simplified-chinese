# 阶段 1–2：证据与完成状态收敛（2026-10-17）

本目录记录「冻结基线 + 缺测语义/依赖状态收敛」这一轮的运行证据。日志只说明对应运行时的
源码、解释器和依赖状态，不代表之后版本的结论。

## 基线（改动前）

| 项目 | 值 |
| --- | --- |
| commit | `fe37eaf375cc504ccf8f71cf31beab497d5aa89c`（分支 `codex/release-v8.0.3`） |
| 解释器 | `<WORKSPACE>\venv-ai-modern\Scripts\python.exe`，Python 3.12.14，praat-parselmouth 0.4.7 |
| 工作区状态 | 780 个未提交改动；本轮只动下面列出的文件 |

命令与结果（改动前）：

```powershell
$env:PYTHONPATH='ai;ai/tests'
python -m unittest test_phase_context test_cloud_protocol test_dialogue_protocol `
  test_cloud_escape test_report_guards test_tool_guards test_delivery_state -q   # 132 OK
python -m unittest test_report_chain_regressions test_token_budget -q            # 31 OK
```

日志：[offline-regression-batch1-before.txt](offline-regression-batch1-before.txt)、
[offline-regression-batch2-before.txt](offline-regression-batch2-before.txt)、
[baseline-commit.txt](baseline-commit.txt)、[baseline-interpreter.txt](baseline-interpreter.txt)。

## 三个缺陷：先失败，后通过

回归测试写在 `ai/tests/test_evidence_consistency.py`（8 条）。

- 修复前（改动前的代码 + 新测试）：**3 failures + 5 errors**
  → [regression-defects-before.txt](regression-defects-before.txt)
  关键失败：`'999' unexpectedly found in '0.5 秒处基频 999 Hz。'`（缺陷③本机交付绕过守卫）、
  `Lists differ: [PronunciationError(... severity=1.0 ...)] != []`（缺陷②不可测量被记成偏差）、
  `IndexError`（缺陷①两侧特征全空时连一条「未测量」记录都没有）。
- 修复后：**Ran 8 tests — OK** → [regression-defects-after.txt](regression-defects-after.txt)

## 改动后的离线回归

```powershell
python -m unittest test_phase_context test_cloud_protocol test_dialogue_protocol `
  test_cloud_escape test_report_guards test_tool_guards test_delivery_state `
  test_report_chain_regressions test_token_budget test_evidence_consistency -q   # 171 OK（含本轮 8 条）
python -m unittest discover -s ai/tests -t ai/tests        # 1453 tests，12 failures + 3 errors + 6 skipped
python -m compileall -q ai\praat_ai                        # 74 个模块通过（3.12 与 3.14 各一次）
```

日志：[final-offline-py312.txt](final-offline-py312.txt)（171 OK）、
[offline-regression-batch1-after.txt](offline-regression-batch1-after.txt)（163 OK，UTF-16 旧捕获）、
[discover-ai-tests-after3.txt](discover-ai-tests-after3.txt)。后一份日志用 UTF-8 控制台重放过
（`[Console]::OutputEncoding` 与 `PYTHONIOENCODING` 都设成 UTF-8），所以中文路径与失败原因可读。

完整发现测试里剩下的失败**都不在本轮改动范围内**，逐条原因如下（本轮抽查过 traceback，
没有一帧落在本轮改动的文件里）：

- 响度/归一化 7 条（1 error + 6 failures）：本机 venv 未安装 `pyloudnorm`／`scipy`，
  LUFS 只能是 `insufficient_evidence`，测试却期望 `applied`；另有两条直接断言
  `ModuleNotFoundError: No module named 'pyloudnorm'` 的报错文案。
- VOT／LongSound 5 条（2 errors + 3 failures）：期望的 VOT 脚本文本与对象解析，与证据状态无关。
- 会话折行像素命中 2 条、输入法合成 1 条（偶发：上一次完整发现里同样这条是通过的）。
  这些模块在改动前同样失败（它们既不导入也不调用本轮修改的函数）。
以上 12 failures + 3 errors 就是完整发现的全部失败项。

## 依赖与测量状态

`ai/runtime_dependencies.py` 的报告（各自解释器实测）：

- 3.12.14：[capability-status-py312.json](capability-status-py312.json) —
  praat-parselmouth「已就绪」；SciPy／pyloudnorm「未安装」；NumPy 版本不满足 `numpy>=2.0,<2.3`。
- 3.14.7（命令行默认）：[capability-status-py314.json](capability-status-py314.json) —
  `supported=false`、`ready=false`，每项状态为「不支持所选 Python：需要 3.10–3.12 x64」。

声学提取状态与「一段音频只提取一次」的实测（3 个音素、0.5 秒真实 WAV）：

- 3.12：[measurement-status-py312.txt](measurement-status-py312.txt) —
  `pitch_formant_status=ok`、`pitch_formant_extractions=1`、`parselmouth_Sound_opens=1`，
  特征含 f1/f2/f3/pitch/intensity；另一次刻意让 `Sound` 抛错 → `extraction_failed`，与
  「依赖未安装」可区分。
- 3.14：[measurement-status-py314.txt](measurement-status-py314.txt) —
  numpy/parselmouth 都不存在，脚本如实报「缺依赖」，没有假装跑过提取。
- 3.14 的离线测试入口：[offline-regression-py314.txt](offline-regression-py314.txt) —
  9 个模块全部 import 失败（缺 NumPy/Pillow），因此 3.14 上只有语法检查有效，
  不能记为「测试通过」。

## 本轮改动的文件

- `ai/praat_ai/pronunciation.py`、`ai/praat_ai/report.py`：缺测语义与结论等级；
- `ai/praat_ai/audio.py`：提取状态/原因可见，一段音频只提取一次；
- `ai/praat_ai/report_guards.py`、`ai/praat_ai/cloud_agent.py`、`ai/praat_ai/chat.py`：
  共用的交付守卫入口；
- `ai/tests/test_evidence_consistency.py`（新增）、`ai/tests/test_pronunciation_rules.py`（跟随语义更新）；
- `ai/docs/validation.zh-CN.md`（验收入口与状态字段）。

## 未完成（明确留给后续）

MFA 对齐的超时/取消、`run_tutor` 分析阶段的取消贯穿、双音频交付的进一步完善、
release 工作流里的 AI 后端/前端测试门槛、真实录音与安装产物验收：本轮**没有**做，
也没有用单元测试或合成音替代。

---

# 第 2 轮：阶段 3（对话主链：解释守卫）与阶段 4（对齐超时/取消）

## 三个缺口：先失败，后通过

回归测试 `ai/tests/test_explanation_guard.py`（8 条）与 `ai/tests/test_alignment_control.py`（8 条）。
修复前两个模块都**无法导入**（`guard_explanation` / `conclusion_caveat` /
`AlignmentCancelled` 还不存在）→ [round2-regression-before.txt](round2-regression-before.txt)。

修复后：

- [round2-stage3-after.txt](round2-stage3-after.txt)：`Ran 8 tests — OK`（解释守卫）；
- [round2-stage4-after.txt](round2-stage4-after.txt)：`Ran 8 tests — OK`（对齐超时/取消）；
- 期间发现并修掉的两个真问题：`guard_delivered_text` 一次只遮第一处没对上的数值
  （现在循环核对到干净）；原因回显里仍带着没核对的数值（现在原因本身也遮蔽）。

## 改动后的回归

```powershell
python -m unittest <文档入口 9 个模块> test_evidence_consistency test_explanation_guard test_alignment_control -q
# 187 OK
python -m unittest discover -s ai/tests -t ai/tests     # 1469 tests，11 failures + 3 errors + 6 skipped
python -m compileall -q ai\praat_ai                     # 3.12 与 3.14 各一次，均通过
```

日志：[round2-final-offline-py312.txt](round2-final-offline-py312.txt)、
[round2-discover-after.txt](round2-discover-after.txt)、
[round2-regression-check.txt](round2-regression-check.txt)（对齐/纠音相关 263 项全过）。
完整发现里的 14 项失败与第 1 轮相同（7 条缺 pyloudnorm 的响度检查、5 条 VOT/LongSound
脚本文案、2 条会话折行像素命中），逐条核对 traceback 都没有落在本轮改动的文件里。

## 本轮改动的文件

- 阶段 3：`ai/praat_ai/pronunciation.py`（`explanation_evidence` / `conclusion_caveat`）、
  `ai/praat_ai/report_guards.py`（循环遮蔽 + 原因遮蔽）、`ai/praat_ai/tutor.py`
  （`guard_explanation` 与两条解释路径）、`ai/praat_ai/models.py`（`explanation_status`）；
- 阶段 4：`ai/praat_ai/forced_alignment.py`（超时/取消/`supports_cancel`）、
  `ai/praat_ai/config.py`（`mfa.timeout_sec`）、`ai/praat_ai/pronunciation.py`
  （取消贯穿）、`ai/praat_ai/tutor.py`（`run_tutor(cancel=...)` 与结果保留）；
- 测试：新增两个模块；`ai/tests/test_mfa_pipeline.py`、`ai/tests/test_speech_dictionaries.py`
  跟随新实现（把 `praat_ai.forced_alignment.subprocess.run` 的假执行换到
  `run_mfa_command` 这个新接缝，返回组合输出而不是 `returncode`）。

## 未完成（留给后续）

双音频交付的进一步完善、release 工作流里的 AI 后端/前端测试门槛、真实录音与安装产物验收；
以及面向分析阶段的界面停止按钮（本轮只提供 `run_tutor(cancel=...)` 这一层 API，
前端能取消的仍是音位生成等准备操作）。

---

# 第 3 轮：测试收敛、依赖门控、停止按钮与阶段 5 门槛（2026-10-17 起）

## ① 完整发现里的 12 failures + 3 errors → 0

起点是第 2 轮结尾的完整发现（`1469 tests`，`11 failures + 3 errors + 6 skipped`）。逐条查真因，
不弱化断言；每条都在测试里写清「改了什么、为什么」：

- **7 条响度失败 = 环境缺依赖**（不是代码缺陷）：按 `ai/requirements.txt` 装上
  `numpy>=2.0,<2.3`、`scipy>=1.14.1,<1.15`、`pyloudnorm==0.2.0`（原 numpy 2.5.3 不满足区间，
  scipy<1.15 与 numpy 2.5 也不兼容）→ [round3-pip-install.txt](round3-pip-install.txt)、
  装后体检 [capability-status-py312-after-install.json](capability-status-py312-after-install.json)
  （NumPy/SciPy/pyloudnorm/praat-parselmouth 全部「已就绪」，LUFS 参考值 −23.045048 与 CHECK 一致）。
- **2 条会话折行几何失败**（`test_chat_process_fold`）：真因是 `chat.ChatWindow._process_block_at`
  只按「命中字符所在行 + 下一行」找折叠区，标题行高 17px 时「标题下方 6px」被 `@x,y` 判给下一行
  （实测命中 index 9.6），右键因此没反应——真实 UX 缺陷，不是测试苛刻。改为字符法 + 像素兜底
  （`_process_block_near_point`，容差半个行高取最近折叠区），15/15 通过。
- **5 条 VOT/segment 失败**：`739cdd86`（暂停统一 VOT、保留共享修复）遗留的半迁移测试：
  它们断言原生动作 `Write VOT analysis to file…`，而这条 Praat 命令在本仓库**只声明未实现**
  （`fon/SegmentAcousticVOT.h` 有 `praat_Sound_analyseVOT`，`fon/*.cpp` 有核心分析，但没有
  `praat_addAction` 注册）。保留实现是脚本 + 人工边界：0/0 = 未指定（自动估计）、只给一个时刻 =
  参数错误、两个时刻 = 手工相减并写 `appendFileLine`。测试改成保留契约后 6/6 通过。
- **先浊后爆（负 VOT）语义按保留契约定**：`tools._build_vot` 拒绝 `voicing <= burst`
  （`ai/tests/test_chat_tools.py` 是暂停提交更新过的核心测试），`test_segment_analysis_tools`
  里「原样放行」的陈旧断言改成断言拦下并说明原因——prevoiced 只属于已被暂停的原生路线，
  不假装它存在。
- **2 条偶发**：
  - `test_api_capability_status`（约 50% 失败）：真因是 **Windows `datetime.now` 粒度约 15 ms**，
    运行时纠正与手动设置的时间戳并列时「谁更晚」失序，用户选择被随机覆盖（两个方向的期望相反，
    无法靠偏向一侧解决）。改为**文件内递增写序号**定序（`config.ApiConfig.audio_setting_sequence`、
    `api_diagnostics.observed_later/edited_after/next_sequence`、`api_settings.save_settings` 判序），
    新增 `ai/tests/test_audio_setting_order.py`（6 条，覆盖两个并列方向 + 序号断裂 + 旧的排队成功），
    连跑 5 次全过。
  - `test_ime`（完整发现里失败过一次，报 `TEntry DPI 96: IME height -18, expected -16`）：
    单独跑 9 次全过、四档 DPI × 4 种字段 × 3 种字号的 144 次读数全对，说明不是字号公式错；
    探针读的是 IME 的组合字体——共享状态，别的窗口可以把它抢回去（负载下才出现）。
    探针改为「让 app 重新施加一次（`<FocusIn>` 就是它的真实入口）+ 有限次数内等它稳定」，
    稳定不了仍按失败报。改动后 `test_ime` 4/4 通过，完整发现亦通过
    （[round3-new-tests-after.txt](round3-new-tests-after.txt)）。

## ② 路径依赖处都做了检测与交互

- 新增 `ai/praat_ai/capabilities.py`：进程内轻量检测（import + 版本比对），**版本区间取自
  `ai/requirements.txt`**，不写死第二份数字；给出「缺什么 / 去哪儿装 / 装完要做什么」。
  与 `runtime_dependencies.detect`（安装向导的子进程体检）分工明确。
- 四处**使用点**改为复用它，口径一致：`audio._open_parselmouth`（基频/共振峰缺测原因）、
  `normalization.import_formant_calibration`、`normalization_prosody.apply_loudness`
  （版本不匹配时报告里直接写清 `scipy>=1.14.1,<1.15` 这类具体缺口）、以及新的
  `tutor.capabilities` RPC。
- 前端（`ai/frontend/src/ProsodySettings.tsx`、`NormalizationSettings.tsx`）在提交之前查询能力：
  缺依赖时禁用并撤销该项开关、给出可执行提示（复用同一句「路径配置 → 配置运行库…」），
  **不提供自动安装**；拿不到状态（旧后端 / RPC 失败）不拦人。
- 新增测试：`ai/tests/test_capabilities.py`（6 条）；前端 `tests/prosody-settings.test.tsx`、
  `tests/normalization.test.tsx` 各加缺依赖用例。

## ③ 分析阶段的「停止」按钮

- `progress_popup.MiniProgress` 新增可选 `on_stop`（不传时行为与以前逐像素一致）：多一个
  「停止分析」按钮，点一次只触发一次，随后变成不可再点的「正在停止…」。
- 新增 `ai/praat_ai/analysis_progress.run_with_stop(work, ...)`：分析跑 worker 线程，进度经
  `root.after` 回主线程刷新；点停止置位 `cancel`，`run_tutor` 的取消贯穿到对齐/分析层
  （第 2 轮已铺）；**拿不到 Tk 或 `--no-gui` 时退回同步执行**，命令行行为不变。
- `tutor.run_from_praat` 接上这一层：测量完成前取消 → 如实报「没有生成报告」并返回退出码 3；
  测量完成后取消 → 报告照常交付，只少 AI 解释（这一条由 `run_tutor` 现有的取消分支保证）。
  Praat 菜单那一档（`control.run_analysis`）仍用 Praat 自带进度窗，那里没有停止按钮
  （窗口属于 Praat，见 `guide.md` §8.16）。
- 新增测试：`ai/tests/test_analysis_stop.py`（9 条，含真实 Tk 的按钮契约与无人值守退回）。

## ④ 阶段 5：release 工作流的测试门槛

`.github/workflows/release.yml` 新增两档硬前置（`build-windows-x64` 与 `build-macos` 都
`needs` 它们，因此测试不过就不构建、不发布）：

- `ai-offline-regression`：Python 3.12 上装「分析运行库」（不装模型、不联网下载）→
  `compileall` → **能力门（`analysis_capabilities().ready` 必须为真）** →
  `python -m unittest discover -s tests -t tests`（完整离线回归）。
- `frontend-tests`：Node 22 上 `npm ci` → `npx tsc -b` → `npm test`（vitest）。
- 浏览器级 playwright（`npm run test:ui`）需要真实窗口，**不在 CI 里**，仍按 `verify_*.py` 手工跑。

## 本轮改动的文件

- 依赖门控：`ai/praat_ai/capabilities.py`（新增）、`ai/praat_ai/audio.py`、
  `ai/praat_ai/normalization.py`、`ai/praat_ai/normalization_prosody.py`、`ai/praat_ai/ui.py`；
- 停止按钮：`ai/praat_ai/progress_popup.py`、`ai/praat_ai/analysis_progress.py`（新增）、
  `ai/praat_ai/tutor.py`；
- 测试：`ai/tests/test_capabilities.py`（新增）、`ai/tests/test_analysis_stop.py`（新增）、
  `ai/tests/test_audio_setting_order.py`（新增）、`ai/tests/test_chat_process_fold.py`、
  `ai/tests/test_segment_analysis_tools.py`、`ai/tests/test_normalization_prosody.py`、
  `ai/tests/test_ime.py`、`ai/praat_ai/chat.py`；
- 前端：`ai/frontend/src/ProsodySettings.tsx`、`ai/frontend/src/NormalizationSettings.tsx`、
  `ai/frontend/tests/prosody-settings.test.tsx`、`ai/frontend/tests/normalization.test.tsx`；
- 门槛与文档：`.github/workflows/release.yml`、`ai/docs/validation.zh-CN.md`、本目录 README、
  `test-records/INDEX.md`。

## 改动后的验证

```powershell
# 验收解释器：<WORKSPACE>\venv-ai-modern\Scripts\python.exe (3.12.14)
python -m unittest <文档入口 12 个模块> -q      # 187 OK          → round3-focused-after.txt
python -m unittest test_ime test_capabilities test_analysis_stop -v   # 19 OK → round3-new-tests-after.txt
python -m unittest discover -s ai/tests -t ai/tests -q                 # 1490 tests, OK (skipped=6)
                                                                       # → round3-discover-final.txt
python -m compileall -q ai\praat_ai             # 3.12 与 3.14 各一次，均通过
cd ai\frontend; npx tsc -b; npm test            # 28 文件 / 171 用例全过 → round3-frontend-vitest.txt、round3-frontend-tsc.txt
```

能力门（CI 用的同一条）在验收解释器上的结果见
[round3-capability-gate-py312.txt](round3-capability-gate-py312.txt)。

## 本轮**没有**做的（如实标注）

- **真实录音材料验收**：共振峰校准的「中部 40%–60% 采样协议」、独立中性语句音高基线、
  LUFS 校正的听感结论，都需要用户提供真实 WAV + TextGrid（或中性语句），本轮**没有**用合成音
  或单元测试替代，也没有据此改判任何阈值。请在拿到材料后按 `ai/docs/validation.zh-CN.md`
  的条目独立走一遍。
- **安装产物验收**：`installer/` 的打包与在干净机器上的安装/运行库修复，本轮没有执行
  （需要 Windows 安装环境与真实产物），CI 只覆盖源码级离线回归与前端单测。
- **原生 VOT**：`praat_Sound_analyseVOT`/`praat_LongSound_analyseVOT` 仍只声明未实现
  （`739cdd86` 的暂停决定未改变），因此先浊后爆（负 VOT）在保留实现里是明确拒绝 + 说明原因。
- **双音频交付的进一步完善**：不在本轮范围。
- 前端浏览器级 playwright 用例未纳入 CI（需真实窗口）；本轮只在本地跑 vitest + tsc。

---

## 第 3 轮续：实现过程自查发现的遗留项（已修复）

回头审一遍本轮写的东西，找到四处「写完没收尾」的地方，逐项修好并补了测试：

1. **收尾阶段的「停止」不能反过来改结论**：分析结束到关窗之间还有最短显示时间（0.8 秒），
   以前那一段里再点一次「停止」，`run_with_stop` 会在收尾时按 `cancel.is_set()` 回报
   「已取消」——把一次**已经跑完**的分析说成被取消（退出码 3、少写 AI 解释）。改为在分析
   结束的那一刻记下取消状态；`MiniProgress.complete()` 推到 100% 并禁掉按钮，结束之后再点
   不算「要求停止」（另有 `_completed` 兜底）。测试：
   `test_stop_clicked_after_the_analysis_finished_is_not_reported_as_cancelled`。
2. **关窗键与「停止」同义**：分析窗里点右上角 X 以前什么都不发生（沿用「简约窗口不给中途
   关闭」），但同一个窗口上明明有停止按钮。现在传了 `on_stop` 时关窗键等同点停止；没传
   `on_stop` 的小窗行为完全不变（`MiniProgress._on_close`）。测试：
   `test_close_key_means_stop_and_nothing_happens_after_completion`。
3. **能力模块里没人用的注入参数**：`capabilities._package_status(..., importable=)` 是写的时候
   留的缝，实际没有任何调用方（测试也不用），删掉；检测行为不变（原有 6 条测试仍全过）。
4. **悬空的文档引用**：代码里 11 处引用 `guide.md §8.16`，而 guide.md 没有这一节（历史遗留）。
   在 guide.md 补上 `### 8.1 Tk（legacy）界面：设计语言与进度小窗`（并说明它就是那些历史引用
   指向的地方），写清 `MiniProgress` 的对外契约与新的 `on_stop`/`complete`；本轮新写的两处引用
   改成 §8.1。

验证：

```powershell
python -m unittest test_analysis_stop test_capabilities test_chat_tools test_segment_analysis_tools test_ime -q
# 139 OK                                        → round3-followup-tests.txt
python -m unittest discover -s ai/tests -t ai/tests -q
# 1492 tests, OK (skipped=6)                    → round3-discover-final.txt
python -m unittest <文档入口 12 个模块> -q        # 187 OK → round3-focused-after.txt
python -m compileall -q ai\praat_ai             # 3.12 与 3.14 均通过
```

仍然**没有**做（不在本轮范围或需要用户材料）：真实录音材料验收、安装产物验收、
原生 VOT（仍只声明未实现）、双音频交付的进一步完善；前端浏览器级 playwright 不进 CI。

依赖门控落在 WIP 文件里的那部分已经在用户确认后落盘，分两个提交：

- `53958347` `chore(wip): 归一化/前端既有未提交工作的基线快照（第 3 轮改动之前的状态）`
  —— 把 `ui.py`（500+ 行既有改动）与 7 个未跟踪文件**改动之前**的内容原样落盘（前端四文件
  取自上次构建留下的 `ai/frontend/dist/pi-desktop-source/rebuild/`，Python 四文件是逐处还原，
  两个提交合起来的差异经核对恰好是本轮改动）；
- `6fa4d0cd` `feat(runtime,ui): 分析依赖门控接入运行路径与前端（第 3 轮）`
  —— 只有本轮改动：`tutor.capabilities` RPC、共振峰校准与响度门控的提示复用、
  前端两个组件的缺依赖门控与用例（8 个文件，+107/−18）。

还原后工作区与跑过 1492 项测试的那份内容逐字节相同（对快照做了 SHA256 比对）。
