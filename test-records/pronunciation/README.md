# 2026-10-13 音位判据验证

环境：<WORKSPACE>/venv-ai-modern/Scripts/python.exe；PYTHONPATH=ai;ai/tests。

- 纠音与表单回归：59 项通过，原始输出见 2026-10-13-phone-rules.log。
  包含 test_pronunciation_rules、test_pronunciation、test_alignment、
  test_forced_alignment、test_audio_pipeline、test_bridge_pipeline、
  test_report、test_cloud_prompt。
- 项目提示前缀/云流程回归：132 项通过，耗时 12.263 秒。
  命令：python -m unittest test_phase_context test_cloud_protocol
  test_dialogue_protocol test_cloud_escape test_report_guards test_tool_guards
  test_delivery_state -q。
- Tk 表单在隐藏的测试窗口中完成英语/日语选择及提交，验证语言、规则 ID 和禁用旧式阈值。
- 新报告通过 Parselmouth/Praat 读回；PNG 已视觉查看。
- git diff --check 对本次已有文件通过，使用 cr-at-eol 兼容原 Windows 换行。

synthetic-rule-observations.json、.TextGrid 和 .png 使用测试中的合成特征，
仅供核对规则与报告格式，不是真实发音材料或准确率证据。
关闭模型的 run_tutor 集成另使用生成的短纯音与模拟 Praat 桥接。
没有下载或运行识别模型，没有调用付费 API，也未进行原生菜单端到端或安装包发布验证。

## 证据状态与区间守卫修复

新增 7 项回归后，相关测试集共 66 项全部通过，输出见
[2026-10-13-evidence-boundary-fixes.log](2026-10-13-evidence-boundary-fixes.log)。
覆盖无声学帧的促音、缺失适用特征与纯时长观察的区分、混合音类状态、
参考和学习者两侧越界、有效旧式音素仍能比较，以及实际 WAV 提取路径。
新增测试在修改实现前复现了纯时长误标和旧式越界仍评分的问题。
JSON 与两种 TextGrid 报告均验证证据状态；TextGrid 使用 Praat 读回检查。

## 顺序配对、MFA 标签与 JSON 入口修复

再新增 10 项回归，相关测试集共 76 项全部通过，输出见
[2026-10-13-sequence-request-fixes.log](2026-10-13-sequence-request-fixes.log)。
覆盖双方索引不同、缺失中间音素、低置信度邻居、跳号邻居重叠与越界、
TextGrid 状态和 PNG 双方曲线、重复 MFA 标签的前后及非连续子序列、
缺失或顺序不符的标签、Unicode IPA，以及实际 --request 参数入口的错误提示。
预设错误测试确认返回 1、输出可读原因且不启动模型。

## 零频谱重心与无效参数清理

新增 4 项回归；纠音及配置、启动相关 Python 测试共 107 项通过，输出见
[2026-10-13-rule-cleanup.log](2026-10-13-rule-cleanup.log)。
覆盖合法零频谱重心、负值与非有限值排除、旧 JSON 与旧配置忽略已移除的
minimum_error_duration_sec，以及旧式 DTW 仍过滤非有限学习者样本且不修改原始轨迹。
零频谱重心和旧字段清理测试在修改实现前复现失败；有限值过滤测试原本即通过。

日语面板文案与 mora_structure 的未评估状态对应，前端设置移除未生效字段。
前端设置相关 4 个测试文件、8 项测试全部通过：
[2026-10-13-rule-cleanup-frontend-tests.log](2026-10-13-rule-cleanup-frontend-tests.log)。
TypeScript 和 Vite 构建成功：
[2026-10-13-rule-cleanup-frontend-build.log](2026-10-13-rule-cleanup-frontend-build.log)。

## 缺音后的原始索引与报告状态

新增 5 项测试，纠音相关测试集共 85 项通过，输出见
[2026-10-13-original-phone-index-fixes.log](2026-10-13-original-phone-index-fixes.log)。
覆盖原始索引的请求往返及双侧提取、中间缺音和开头缺音、显式非连续索引、
未知参考音位的状态不会被其他 IPA 借用，以及旧位置编号报告的状态与空缺区间。
实际 WAV 提取路径使用合成音和模拟部分对齐结果；不依赖真实识别模型。
修改前测试复现了跳过音素后的重新编号和错误 learner_phone_index。
有序 MFA 匹配用例补充了 a s 对 a s a s 的所选原始边界。
