# 现代 AI 聊天前端参考

现代客户端使用 React／assistant-ui external-store runtime、Tiptap 和本地 Markdown 渲染链；Python 应用服务管理会话、配置、任务与 Praat 执行。Windows 桌面壳为 pywebview／WebView2，不包含 Pi 或 Codex 的 Agent 运行时。

相关参考：[宿主接口](HOST-CONTRACT.md) · [桌面宿主、构建与完整性](CURRENT-HOST.zh-CN.md) · [安装与路径配置](INSTALLATION-PATHS.zh-CN.md) · [对齐检测](ALIGNMENT-VERIFICATION.zh-CN.md) · [前端来源与许可](FRONTEND-SOURCES.md) · [模型数据来源](PI-MODEL-SOURCES.md)。下列路径相对项目根目录，数值仅描述当前代码参数。

## 状态与任务

- `ai/frontend/src/store.ts` 的 `ChatStore` 持有宿主状态；`Chat.tsx` 将消息投影给 assistant-ui。聊天页通过 `bridge.ts` 的 `rpc(method, params)` 白名单调用 Python，独立纠音／资源／Anki 页面使用各自宿主接口，不共用聊天方法表。
- `modern_app.py` 为任务捕获配置、会话、取消信号与目标。同一会话拒绝重叠提交；不同会话可后台运行，当前活动任务总上限为 8。切换前台会话不改变事件的 `sessionId`／`taskId`。
- Praat 执行由 `modern_execution.py` 串行核对目标并调用现有工具桥。恢复历史只恢复上下文、摘要和证据，不重放历史操作；新的普通提交重新捕获目标。消息上的“解释结果／分析原材料／通用讨论”显式续接动作使用 `tasks.followup`，由宿主核对原任务证据、目标绑定和当前能力；这些按钮只把一句可修改的请求填进输入框，是否发送由用户决定，原样发回才按续接提交，改过文字或加了附件就是新的普通提交。
- 现代库默认是 `ai/runtime/modern/sessions.sqlite3`。旧库用 SQLite `mode=ro` 打开；旧记录的分区、归档及隐藏入口元数据写入现代库。旧记录不能直接续写，可创建现代可写分叉。

## 会话组织与数据保护

`src/pi/sidebar/Sidebar.tsx` 提供置顶、自定义分区、默认“会话”区、折叠、四种排序、多选、拖放、键盘操作、会话菜单和悬停预览。普通历史可按项目、连接或单列表组织；置顶与自定义分区保留独立成员关系。当前宿主记录本地工作区，分叉继承来源；导入的远程来源元数据可分组，但没有远程连接管理或远程执行服务，模型 API URL 不是连接身份。

`modern_organization.py` 管理稳定分区 ID 和成员顺序。删除分区只解除成员关系，不删除对话。归档会话仍可查看、移动和分叉，恢复后才能提交；包含活动任务的归档／删除操作会被拒绝。分叉复制当前历史、摘要及证据，使用独立消息／活动 ID，清空草稿与阅读位置，重置置顶和归档，并将运行片段标为中断。

设置中的已归档会话页由 `src/codex/ArchivedSessions.tsx`／`archived-groups.ts` 实现：支持正文／标题搜索、来源与项目筛选、按分区或项目分组、打开、恢复和删除。组删除针对该组当前显示的成员；“全部删除”针对全部归档记录，不受搜索和分类筛选缩小，确认框显示范围。现代删除移除记录及关联数据；归档旧记录的删除只隐藏本应用入口，原库保留。归档操作即时保存，其他未保存设置仍有离开确认。

空白会话行为由 `preferences.save_empty_sessions`（默认 `false`）与 `empty_session_policy`（默认 `delete_on_exit`）控制：

- 保存空会话开启：前端立即创建宿主会话，关闭时跳过空白扫描。
- `delete_on_exit`：立即创建；关闭时宿主扫描现代库，删除标题为“新会话”、无草稿／摘要／消息／证据／模型事件、无置顶或组织元数据的记录。
- `never_persist` 且未开启保存空会话：前端先创建 `local-draft-N` 内存记录；首次提交会落库。当前 `store.ts` 的重命名、置顶、移动、归档、分组或分叉等操作也可使其提前落库，因此不能把“不落库”理解为所有操作下都不写数据库。

侧栏全局“新建会话”在当前可写会话已加载、无消息且草稿为空白时只聚焦编辑器；分区菜单的显式新建仍创建会话。独立维护工具 `ai/praat_ai/prune_blank_sessions.py` 使用同一空白筛选条件；它会实际删除数据，不是只读检测。

## 输入、消息与上下文

- `Composer.tsx`／`editor.ts` 用 Tiptap 编辑、序列化 Markdown，处理 IME 防误发送、发送键、输入历史、会话草稿、附件选择／粘贴／拖入及预览。斜杠补全连接新建、设置、取消、附件四个界面动作，没有插件命令注册表或工作区 `@` 文件 RPC。
- `Chat.tsx` 展示真实后端的思考、进度、工具及压缩事件；思考无内容时不生成占位思考。工具结果包含投递／执行事实及结构化证据。复制／右键菜单提供当前可用动作，没有消息编辑、单消息删除或重新生成 RPC。
- `Markdown.tsx` 使用 react-markdown、GFM、数学和代码渲染；原始 HTML、外部图片不作为可执行内容加载，链接经宿主处理。Mermaid 从本地离线资源加载，SVG 经净化；KaTeX 字体随前端分发。
- `ComposerStatus.tsx` 显示下一轮上下文估算、模型快速选择和推理档位。草稿变化后当前防抖为 350ms；同会话刷新期间保留上次有效估算，会话／配置变化会失效，失败显示原因。估算包含摘要、已完成历史、证据、草稿及文本附件，不是实际服务商计费用量。
- 本地与 API 参数分别保存；快速切换只合并对应字段，运行中任务保留提交时配置。本地 low／medium／high 均表示开启思考，没有独立强度分级。自动／手动／服务商决定三种 token 模式使用 `modern_budget.py`；未知模型窗口不能凭名称推断，端点适用范围见模型来源文档。
- 桌面入口允许启用的 API 配置；启动、保存和快速切换不发送模型请求。设置页对远程连接测试先显示前端确认，再调用 `settings.test`；这不是 Python RPC 内的原生确认门禁。API Key 不经设置读接口回传。

## 搜索、滚动与尺寸

| 模块 | 当前实现 |
| --- | --- |
| `src/codex/useSessionSearch.ts`、`modern_search.py` | 侧栏标题本地匹配，正文通过 SQLite RPC 返回紧凑会话 ID，不为搜索读取所有会话全文到前端；100ms 防抖与请求代次检查。搜索修订由排序后的 ID、标题、更新时间、归档状态生成，草稿／阅读／预览和数组排序不单独触发重搜。宿主还合入活动任务未落盘正文的命中 |
| `src/search.ts`、`SearchBar.tsx` | 当前渲染正文的字面量匹配，当前最多 500 处；CSS Custom Highlight API 高亮，不可用时仅定位。未挂载行需先展开，范围与持久正文搜索不同。搜索／全局快捷键放行 `isComposing` 或 keyCode 229 的输入事件 |
| `src/transcript-window.ts`、`Chat.tsx` | 首次挂载 15 行，稳态至少 60 行，每次向前扩展 40 行，触顶阈值 120px；全部已加载数据仍在内存，不是全文分页或像素虚拟化 |
| `src/Anchoring.tsx`、`scroll.ts` | 持久阅读位置为 `{messageId, offset}`，其中 offset 是行顶相对视口的偏移，保留小数像素。历史／卡片展开与内容高度变化补偿阅读位置；用户滚动解除临时锚定 |
| `src/MinimapRail.tsx`、`minimap.ts` | 导航当前挂载行；更早入口扩展挂载窗口 |
| `src/layout.ts`、`Panes.tsx` | 侧栏默认 272px、范围 240–520px，拖到 160px 以下折叠；主栏预算下限 450px。对话带默认 720px、保存范围 560–4000px，实际受窗口约束。拖动、键盘调整和复位经宿主偏好保存，不依赖 WebView 私有浏览器存储 |
| `src/pi/sidebar/styles.css` | 原生滚动条 14px 通道；静止时约 6px 可见滑块，悬停／拖动约 10px，CSS 最短高度 68px；实际长度仍受浏览器和视口影响 |

输入框由 `composer-layout.ts` 在固定紧凑宽度的 inert 探针上测量内容，超过 3.6 个行高时扩宽。`styles.css` 的常态宽度取对话带宽与主区约一半的较大者；展开取对话带宽加 96px 与主区约 7/12 的较大者，均受可用宽度限制。欢迎区、输入区与通用模态以侧栏右侧主区域为中心，宽度不是所有窗口都固定 720px。

## 检查入口与边界

- 前端：`ai/frontend/package.json` 中的 `npm test`、`npm run test:ui`；浏览器用例位于 `tests/browser/`。
- 宿主：`ai/tests/test_modern_app.py`、`test_empty_session_close.py`、`test_window_drag.py`；桌面检查 `verify_modern_desktop.py`。
- 组织与归档：`verify_history_organization_desktop.py`、`verify_history_group_actions_desktop.py`、`verify_archived_settings_desktop.py`。
- 搜索与来源：`verify_search_input_desktop.py`、`verify_search_scroll_desktop.py`、`verify_search_performance.py`、`verify_search_performance_desktop.py`、`verify_search_source.py`、`verify_sidebar_options_source.py`、`verify_history_source.py`。

脚本路径均在 `ai/tests/`。浏览器夹具、合成键盘事件、模拟执行器及源码来源检查各有范围，不证明真实模型兼容性、所有实体输入法或声学准确率；保存的历史测试计数不能代替当前运行结果。
