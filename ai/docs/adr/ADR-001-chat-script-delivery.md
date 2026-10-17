# Praat 脚本投递参考

Windows 默认通道由 `sendpraat.py` 写 `%APPDATA%\Praat\Message.txt` 并向目标窗口发送 `WM_APP`，不主动激活窗口。消息带完全信任标记，因此该通道不是文件系统沙箱。

`chat.py` 为请求生成独立的 `runtime/commands/chat_command_<编号>.praat`，使用 `chat_started.txt` 与完成标记核对编号。消息开头把消息文件替换为空操作，避免排队通知重复读取同一脚本。超时或取消时 `cancel_pending()` 清空待处理消息。

投递事实在 `delivery.py` 中区分 `DELIVERED`、`NOT_DELIVERED`、`EXECUTION_UNKNOWN`、`EXECUTION_BLOCKED`。交付不等于成功执行；未等到完成标记时不能认定操作没发生。现代执行器阻止未知状态下的普通操作，但允许向原进程定向核对；完整快照核对成功后解除阻断，失败时不得沿用旧快照。

消息文件全局唯一。`sendpraat.command_lock()` 为调用该接口的投递者提供跨进程锁，`chat._send_script` 和 Anki 投递使用它；现代执行器另有进程内锁。未使用共享锁的外部写入者不受保护。Praat 模态窗口可能阻止消息处理。命令文件清理保留最近请求，避免删除仍被引用的脚本。

`PRAAT_AI_SEND_MODE=argv` 使用 `Praat.exe --send` 排障通道；该通道可能激活 Praat 窗口，使用后台进程和可取消轮询。

相关实现：`chat.py`、`sendpraat.py`、`modern_execution.py`、`delivery.py`；原生接收：`sys/praat.cpp`。
