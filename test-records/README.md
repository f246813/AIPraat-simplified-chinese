# 测试记录

这里保存测试运行输出与结果文件。记录只说明对应运行的源码、依赖、环境和材料，不代表当前版本的测试状态。日志中的失败、错误、跳过和部分完成保留原意；没有执行新测试时，不新增通过结论。

| 目录 | 内容 |
| --- | --- |
| `project/` | 项目构建、Python 与 Praat 集成验证日志及结果 |
| `frontend/` | React、浏览器及 WebView2 交互结果 |
| `installer/` | 安装器、依赖环境、宿主与打包验证 |
| `pronunciation/` | 纠音归一化的上游对照、材料审计、模板/门禁运行与各批测试日志 |
| `diagnostics/` | 专项诊断及回归日志 |

文件名用于定位检查，`red`、`green`、`before` 等标识区分运行条件，不代表产品决策。发布文件中的凭据值与 Windows 用户目录名称作脱敏处理，测试状态、异常和测量结果保留。日志按可确认的编码转为 UTF-8。

可执行检查脚本位于 `ai/tests/`、`installer/tests/`、`installer/verification/` 和前端测试目录。交接说明见 [handoff/](../handoff/README.md)。
