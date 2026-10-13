# 专业纠音与报告模块

专业分析通过 `bridge.py` 和 Praat 工具桥获取声学数据，`audio.py` 提取逐音素特征，`forced_alignment.py` 提供音素边界，`pronunciation.py` 比较轨迹，`report.py` 输出结构化结果。聊天界面及模型设置见 [现代架构](CHAT-UI-ARCHITECTURE.zh-CN.md)。

## 边界与评分

`analyze_pronunciation` 读取标准与学习者 WAV。缺失标准边界或标记为 UI 等分估计的边界需要标准侧对齐；有效手工学习者边界走 provided 分支。同采样音频复用标准边界，并记录 same_audio 来源。其他情况由配置的对齐器处理学习者侧。

逐特征比较先做 DTW，再结合参考均值、标准差、最小容差与归一化距离生成偏差分数。缺失／不可测特征有独立报告；低对齐置信度在提示中要求复核。阈值、每音素错误数量与置信度界限是当前算法参数，不证明医学或语言学诊断有效性。

## 结果

`write_textgrid` 输出 `AI_Errors` IntervalTier 与 `AI_ErrorFeatures` TextTier；JSON 保存完整结构化结果。PNG 绘制有限数量的错误特征曲线，并依赖可用的 Pillow。结果文件能否导回 Praat 由调用路径处理，JSON 是报告文件，不是 Praat 对象。

## 当前实现限制

当前比较器遍历可用通用特征；聊天 VOT 工具不能被当作纠音评分已采用音素类别专用 VOT／CPP／HNR 的证明。未生效的 `minimum_error_duration_sec` 已从配置、请求与设置面板移除，旧 JSON 字段会被忽略。既有 TextGrid、多个标准范本及叠加展示能力应逐入口核对，不能由目标描述推断已实现。语言音素映射、声调和模型依赖见 [对齐模块](ALIGNMENT-VERIFICATION.zh-CN.md)。

可复现检查入口为 `ai/tests/test_pronunciation.py`、`test_alignment.py`、`test_report.py`、`test_report_chain_regressions.py`。隔离数据检查不保证真实人声或所有语言的准确率。
