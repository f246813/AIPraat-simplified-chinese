# API 配置与供应商适配参考

`config.ApiConfig` 保存 API 地址、模型、key、超时、上下文及输出限制、思考档位、音频能力等设置。`api_is_active` 与 `apply_api_to_qwen` 为相关执行入口生成有效配置。本地模型配置独立保存。

Praat 菜单经 `run_ai_control.py api-config` 调用 `control.run_api_settings_dialog`，再通过 `start_api_settings.py` 跳转到现代工作台的模型页；现代配置由 `modern_settings.py` 管理。`api_settings.py` 仍提供独立 Tk 设置接口，不是该菜单的默认页面。key 保存于本地 `ai_config.json` 或来自 `PRAAT_AI_API_KEY`，配置文件不纳入版本控制；展示与诊断应使用脱敏出口。

现代 API 调用使用 `cloud_runtime.py` 和 Pydantic AI，具体协议字段与能力由供应商适配决定，不能把所有端点视为完全等价。`QwenClient` 还提供本地和兼容 API 的请求路径，本地请求使用 chat 模板参数。服务端输出上限拒绝通过对应路径有限调整或重试。

本地服务切换受 `api.stop_local_service` 和服务所有权约束。现代工作台按任务获取本地服务租约，只停止自己拥有且无任务占用的服务。配置保存不会自行开始音频分析；正常桌面宿主在提交任务时允许已启用 API 配置的远程请求；其他宿主使用自身的远程授权策略。

直接音频支持可按模型预设或手动覆盖，能力验证使用合成音频。确认端点不支持音频时只纠正对应配置身份；定性音频观察不能充当专业测量数值。

相关实现：`config.py`、`api_settings.py`、`modern_settings.py`、`modern_app.py`、`modern_execution.py`、`cloud_runtime.py`、`model_capabilities.py`。
