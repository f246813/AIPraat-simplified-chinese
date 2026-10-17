# 公开真人录音归一化试跑（2026-10-16）

已下载 10 段 CMU ARCTIC 原始真人录音：BDL / SLT 两位说话人各读相同的 5 句话，组成 5 组对比。另补取 8 段独立录音尝试多元音校准；目标录音不参与校准拟合。

**结果：5 组全部完成，0 组运行失败；相对音高和整段 LUFS 校正均为 5/5 applied。共振峰为 0/5 applied：双方校准材料都只得到 1 个有效 /ɔ/，当前协议要求每元音至少 3 个。未放宽协议、未用目标音素补校准，也未伪造 z 值。**

## 来源与许可

- 原始语料：[CMU ARCTIC](http://festvox.org/cmu_arctic/)，Carnegie Mellon University，2003，BDL / SLT 0.95；许可原文分别保存为 `bdl-COPYING` / `slt-COPYING`。
- 原始 WAV 保留在 `audio/`；补充校准 WAV 在 `calibration-audio/`。下载 URL、精确 SHA-256、采样率、时长及文本见 `samples.json` 与 `calibration-downloads.json`。本轮所有原始下载文件均再次通过 SHA-256 核对。
- 原始文本和说明保存为 `*-etc-txt.done.data`、`*-README`。校准合成文件只是这 8 段独立真人录音的拼接，未生成语音；拼接偏移和原始来源保存在 `calibration/*_fit.json`。

## 配对结果

每组分别运行原始声学观察、音高/响度归一化，以及带独立元音校准尝试的归一化。下表中的数量是观察记录数，可能含同一音素的多个连续有声区间，不是正确/错误音素数。

| 句子 | 匹配音素数 | 相对音高观察 | 校正强度观察 | 校正 RMS 观察 | 共振峰 z 观察 |
| --- | ---: | ---: | ---: | ---: | ---: |
| arctic_a0001 | 33 | 20 | 33 | 33 | 0 |
| arctic_a0002 | 38 | 25 | 33 | 33 | 0 |
| arctic_a0003 | 36 | 25 | 31 | 31 | 0 |
| arctic_a0004 | 25 | 19 | 24 | 24 | 0 |
| arctic_a0005 | 15 | 13 | 13 | 13 | 0 |

## 测量与报告

- 整段原始响度约 −22.37 至 −16.89 LUFS；使用共同目标 −23 LUFS 计算恒定增益。所有目标素材均无需提升电平，未预测到增益后削波。保留原始 WAV，没有覆写或压缩波形。
- 本句有效 F0 基线：BDL 约 120–128 Hz，SLT 约 169–193 Hz；使用相对半音去基线，保留句内音域。所用跟踪范围 75–600 Hz；未把本句定中心称为独立说话人音高校准。
- 存在有声区间不匹配和低可用帧区间，对应音高观察被跳过；详情见各报告 `comparison_gaps`。
- 自动对齐后，原始双方序列有 5 个未匹配音素（参考侧 3、比较侧 2），详见 `batch-results.json`。只配对顺序一致的同 IPA 音素，不把缺口裁掉后冒充全序列一致。
- `reports/<句子>/raw/`、`normalized/`、`calibrated/` 分别保存 JSON、TextGrid 和 PNG，共 15 套。已核对 JSON 不含 NaN/Infinity，15 个 TextGrid 均由 Praat/Parselmouth 实际读回，15 张 PNG 均通过文件校验。
- 总结：`run-summary.json`；全量首轮：`batch-results.json`；独立校准尝试：`batch-results-calibrated.json`；每段录音数值：`sample-measurements.csv` / `.json`。

## 这轮的证据范围

这些是公开真人录音的工程运行结果。BDL 和 SLT 仅作为对照 A/B，未将其中一位标成错误学习者。音素边界和校准标签由本机 MFA english_us_arpa 自动产生，尚无人审；未调用付费 API 或解释模型。本次数据可检查链路、适用状态和缺口，不能据此给出发音准确率或归一化收益百分比。

本轮全部目标录音长于 400 ms，所以未覆盖短录音活动区间 RMS 分支，也未覆盖独立中性语句音高基线。共振峰要继续验证，需补足 /ɔ/ 的独立有效 token，并核对自动标签与真实读音；当前有效元音不足没有被程序隐藏。

## 复现与环境

使用 `scratch/normalization-runtime/full312/venv/Scripts/python.exe` 依次运行 `run_batch.py`、`run_calibrated.py`。脚本只读取本批音频及本地模型，已存在的对齐结果会复用。

本机 Windows MFA 的临时目录/模型缓存/Numba 缓存需要指向本次可写目录，Kaldi/OpenFst 工作路径需要 ASCII。脚本核验已有 X: 映射确实指向当前工作区后，将 MFA 工作文件放在 X:/scratch 下；没有更改现有 Conda 环境或系统目录权限。MFA 命令与日志见 `alignment-command.json`、`calibration-alignment-command.json` 及对应 `.log`。

分析环境：Python 3.12.14，numpy 2.2.6、scipy 1.14.1、pyloudnorm 0.2.0、praat-parselmouth 0.4.7。

发布后的脚本以 `AIPRAAT_TEST_ASCII_WORKSPACE` 指定指向源码仓库的 ASCII 路径；它替代原实验机器的固定驱动器别名。环境检查脚本使用 `AIPRAAT_TEST_CONDA`、`AIPRAAT_TEST_MFA_MODELS` 和 `AIPRAAT_TEST_WAV2VEC2_MODEL` 提供本地资源，不在源码中保存开发机路径。
