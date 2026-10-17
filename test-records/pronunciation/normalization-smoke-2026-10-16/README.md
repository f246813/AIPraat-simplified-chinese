# 冒烟运行：用户 あなた 录音 × Wikimedia CC0 参考音（2026-10-16）

**性质：冒烟/工程运行，不是验收证据，也不是准确率测量。** 本目录不含任何音频，只保留数值与日志。

## 材料与来源

| 角色 | 文件（不在本目录） | 来源与许可 | 完整性 |
| --- | --- | --- | --- |
| 学习者 | 用户本机 `Desktop/あなた.wav`（暂存为 `scratch/normalization-smoke/learner.user.wav`） | 用户自述为其本人朗读；未经外部审计 | 本地 SHA-256 `7cf1e5a63a9427a1e3eda423d5078d8972d08a5158fcc190738711d4a75cdfd7` |
| 参考 | `scratch/normalization-smoke/ckali.cc0.wav` | Wikimedia Commons / Lingua Libre，说话人 CKali，**CC0 1.0**，录制 2023-07-22，[文件页](https://commons.wikimedia.org/wiki/File:LL-Q5287_(jpn)-CKali-%E3%81%82%E3%81%AA%E3%81%9F.wav) | 本地 SHA-1 `e2c1d6fddbb36503689b217ef155d14df2957774` 与 Commons 记录一致（101164 字节） |

另一个候选 `LL-Q5287 (jpn)-Zsrtrgh-あなた.wav`（CC BY-SA 4.0）因上传服务器限流未下载。

## 录音级事实（不依赖音素边界）

| | 学习者 | 参考 |
| --- | --- | --- |
| 时长 / 采样率 | 0.892 s / 44.1 kHz | 1.053 s / 48 kHz |
| 峰值 | 0.0399（−28 dBFS） | 0.2849（−10.9 dBFS） |
| 整段 integrated LUFS | **−44.22** | **−31.04** |
| 到共同目标 −23 LUFS 的恒定增益 | **+21.22 dB** | +8.04 dB |
| 增益后峰值（是否削波） | 0.459（不削波） | 0.719（不削波） |
| F0 基线（log2 域中位数） | **95.01 Hz** | **105.54 Hz** |
| 有效 F0 帧 / 原始范围 | 39 帧 / 77.0–598.5 Hz | 33 帧 / 96.0–110.4 Hz |

基线差 **−1.82 半音**（学习者偏低）。学习者 598.5 Hz 的极值出现在很轻的录音里，属可疑跟踪值；归一化不负责修复跟踪错误。

## 音素级结果（本轮已跑通）

对齐：本机 MFA `japanese_mfa`（词典 `あなた → a n a t a`），两侧置信度 **0.85**，覆盖：参考 0.55 s / 学习者 0.46 s。

**未归一化（原始 Hz，同一人工/自动边界）**：`relative_duration: observed`、`phone_acoustics: partial`

| phone | F1 (Hz) ref→learner | F2 (Hz) ref→learner | 相对时长 ref→learner |
| --- | --- | --- | --- |
| #1 /a/ | 703.6 → 664.6 | 1432.1 → 1463.4 | 2.29 → 1.33 |
| #2 /n/ | （谱重心 1254 → 1171） | — | 0.58 → 0.67 |
| #3 /a/ | 547.4 → 557.0 | 1498.9 → 1529.5 | 1.00 → 1.11 |
| #4 /t/ | — | — | 0.78 → 1.20 |
| #5 /a/ | 701.4 → 713.5 | 1248.5 → 1545.3 | 1.67 → 0.83 |

**归一化后**：`relative_pitch_observation: observed`（3 项）、`relative_loudness_observation: observed`（10 项）

| phone | pitch_rel_st ref→learner | 校正后强度 dB ref→learner |
| --- | --- | --- |
| #1 /a/ | 0.09 → 0.55 | 68.58 → 71.80 |
| #2 /n/ | 0.53 → −1.07 | 73.89 → 77.77 |
| #3 /a/ | 0.03 → 2.14 | 73.09 → 73.39 |
| #4 /t/ | （浊音帧不足） | 57.52 → 61.73 |
| #5 /a/ | （浊音帧不足） | 74.71 → 74.84 |

读法：整段电平差 13 dB 被共同目标 −23 LUFS 消掉后，逐音素强度差从"录音太轻"变成句内强弱差异（#1、#2 学习者偏强 3–4 dB，#3、#5 基本持平）；相对半音显示学习者音高走向与参考不同（#3 /a/ +2.14 st vs +0.03 st）。

## 这不是验收证据

- 参考说话人无法提供独立多元音校准 → 共振峰只有 Hz，没有 Lobanov z；上表 F2 差异既可能是发音差异也可能是说话人/录音差异。
- 边界是 MFA 自动对齐，**不是人工核对**；参考音是下载的一个词，不满足"同一批文本 + 双方独立校准 + 人工判断"的验收协议。
- 因此不得据此给出准确率、误报率或"归一化提升了多少"的结论。

## 本机跑通 MFA 需要绕过的四件事（均未修改用户环境）

1. `aligner` conda 环境缺 libsndfile 载荷 → 用 `PYTHONPATH` 指向解包的 soundfile 0.14.0 wheel（`scratch/mfa-sndfile/wheel`，SHA-256 `299491d3499460fb1b74bb4bd78b57ffc2d243a5fafa7b6ec1b264875c78453e`）。
2. `Scripts\mfa.exe` 直接调用时 `fstcompile` 不在 PATH → 走 `conda run -n aligner mfa`（脚本默认）。
3. 沙箱内 `%TEMP%` 不可写且 MFA 的 C++ 层无法在中文路径下写文件 → `subst X: <工作区>` 后用 `X:\` 作为 `TEMP/TMP/MFA_ROOT_DIR`。
4. numba 无法在只读的 librosa 源码目录旁写缓存（`cannot cache function '__o_fold'`）→ 设 `NUMBA_CACHE_DIR=X:\scratch\numba-cache`。

## 文件

| 文件 | 内容 |
| --- | --- |
| `smoke-result.json` | 本轮结构化结果（来源、许可、哈希、对齐、逐音素观察） |
| `smoke-run-mfa-ok.log` | **成功的一轮**（MFA 对齐 + 原始/归一化两条路径） |
| `smoke-run-final.log` | 早先的 proportional 回退轮（仅录音级数字可用） |
| `smoke-run-w2v2.log` | 备用 wav2vec2 对齐轮（置信度 0.78 但每音素仅 1 帧，退化） |
| `smoke-run-mfa-attempts.log` | 各次 MFA 失败原始报错（soundfile / fstcompile / 中文路径 / numba 缓存） |

复跑（普通 shell；`aligner` 环境需能加载 libsndfile，否则带上第三个 PYTHONPATH）：

```powershell
cd <WORKSPACE>
$env:PYTHONPATH = 'ai;scratch/normalization-py312-site;scratch/mfa-sndfile/wheel'
<WORKSPACE>\venv-ai-modern\Scripts\python.exe scratch\normalization-smoke\run_smoke.py
```

期望输出：`alignment mfa confidence=0.85`，两侧覆盖率约 0.4–0.6（其余为句首句尾静音），随后列出 `f1/f2`、`relative_duration`、`pitch_relative_st`、`intensity_lufs_adjusted_db`、`rms_lufs_adjusted` 等逐音素观察。
