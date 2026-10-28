# 开发与维护

从[文档索引](docs/README.md)按任务阅读；维护规则见 [AGENTS.md](AGENTS.md)，模块职责见[当前实现](docs/CURRENT-IMPLEMENTATION.zh-CN.md)。文档与注释只保留现状，不保存阶段规划、执行流水或被替代的决策。

## 翻译与原生构建

- UI 映射源为 `tools/generate_translation_map.py::EXACT_MAP`，运行脚本生成 `sys/praat_translate.cpp`。
- 手册正文维护各模块 `manual_*.cpp`，`docs/manual/` 为生成结果；`MAN_BEGIN` 名称与链接目标保持一致。
- 术语源为 `tools/build_glossary.py::MANUAL_OVERRIDES`；GUI 控件与报错入口在 `sys/Gui*.cpp`、`sys/Ui.cpp` 及具体调用方核对。
- Windows 使用 MSYS2 CLANG64 和根 Makefile，例如 `make PRAAT_COMPILER=clang PRAAT_ARCH=x64v3 -j2`。其他平台见[上游构建说明](HOW_TO_BUILD_ONE.md)。

C++、翻译表和手册源修改需要重建 Praat。Python 修改后重开 AI 窗口；前端修改后重新构建并重开窗口加载资源。Windows 发布需满足[文件与进程完整性要求](docs/CURRENT-IMPLEMENTATION.zh-CN.md#windows-发布)。

## 后端检查

在仓库根目录使用满足 `ai/requirements.txt` 的项目 Python：

```powershell
$env:PYTHONPATH = 'ai;ai/tests'
python -m compileall -q ai/praat_ai
python -c "from praat_ai.capabilities import analysis_capabilities; assert analysis_capabilities()['ready']"
python -m unittest discover -s ai/tests
```

按修改范围可先运行相关用例：

| 范围 | `ai/tests/` 中的入口 |
| --- | --- |
| 云端协议、上下文与缓存 | `test_phase_context`、`test_cloud_protocol`、`test_dialogue_protocol`、`test_cloud_escape` |
| 工具、证据与报告 | `test_tool_guards`、`test_report_guards`、`test_evidence_consistency`、`test_report_chain_regressions` |
| 投递与取消 | `test_delivery_state`、`test_alignment_control` |
| 本地规划与数值推导 | `test_explanation_guard`、`test_calc_delivery` |
| 测量与 VOT | [测量说明](ai/docs/measurements.zh-CN.md#回归) |
| 归一化与真实材料 | [方法及材料入口](docs/NORMALIZATION.zh-CN.md) |
| 文档引用 | `test_maintenance_docs` |

测量检查核对用户实际调用入口、输入来源、对象、范围、量名、单位与缺测原因，覆盖适用的正／零／负值和无法确定样本。依赖缺失、提取失败、未评估和没有差异必须区分。完成状态以 `check_statuses`、逐音位 `checks`、`delivery_guard` 与 `explanation_status` 等实际结果字段为准。

协议夹具和合成音只证明对应行为。真实 Praat 需要实际可执行程序，可用 `PRAAT_AI_PRAAT_EXECUTABLE` 指定；真实录音须核对人工地标及材料来源。需要 GUI、模型或外部依赖的脚本先读其参数与运行要求，跳过需如实报告。

`run_backend_scenarios.py --mode tools` 运行隔离 Praat 模板，`--mode live` 使用模型与真实 RPC；场景见 `backend_scenarios.json`，参数见 `--help`。模型模式可产生费用，测试不继承旧记录中的授权。

修改模型消息、技能、压缩或 schema 时，比较同一阶段连续三次请求的最终 HTTP 消息，核对固定前缀和动态尾部；配置或摘要改变的上下文版本分别检查。`cloud_metrics.py` 缺失统计不补零，不以缓存覆盖率单独推断提速。

## 前端与桌面检查

在 `ai/frontend` 运行：

```powershell
npm ci
npm test
npm run build
npm run test:ui
```

`prebuild` 复制离线资源、第三方许可及可重建来源，`build` 生成 `dist/`；构建会更新来源镜像与产物。生产运行只需离线资产、WebView2 与项目 Python 依赖，不需要 Node。

会话缩略导航与正文间距运行 `npm run test:ui -- tests/browser/minimap.spec.ts tests/browser/pane-resize.spec.ts`；用例覆盖不同窗宽、侧栏状态、对话区宽度和明暗主题，并检查正文边界及横向溢出。

浏览器用例见 `ai/frontend/tests/browser/`；桌面检查入口为 `ai/tests/verify_modern_desktop.py`，窗口拖动为 `test_window_drag.py`。浏览器夹具不替代真实 WebView2、实体输入法、Praat 菜单或多屏检查。平滑流式显示的单元和浏览器入口为 `tests/smooth-text.test.tsx`、`tests/browser/smooth-stream.spec.ts`；`python ai/tests/verify_smooth_stream_desktop.py --output test-records/frontend/smooth-stream-webview2.json` 使用构建产物、正式宿主事件与离线文字，在可见 WebView2 窗口记录逐步增字及最终全文一致性，不调用模型或 Praat。

新对话预设按钮可用 `npx playwright test tests/browser/empty-layout.spec.ts` 检查入口。交互核验时分别点击三个按钮，检查空草稿填入预设、已有多行草稿追加后保留原文、连续点击可组合需求；切换会话后草稿和附件仍保留，点击按钮不会提交任务。

Windows 对象列表右键框选的原生验证入口为 `tools/verify-native-list-selection.ps1`。它需要现有原生构建库，编译当前 `GuiList.cpp`、`praat.cpp` 和 `ai/tests/native_list_selection.cpp`，在独立测试进程中创建 Sound 对象并检查真实列表及 Praat 对象选择回调。完整原生重建后传入 `-UseBuiltSystemLibrary`，可直接使用根 Makefile 生成的 `sys/libsys.a` 验证。测试构建位于 `scratch/native-list-selection`，不替换根目录程序。可设置 `PRAAT_LIST_TEST_SCREENSHOT` 保存框选截图。消息驱动用例覆盖框选、反向、修饰键、撤销、菜单、键盘与边缘滚动；实体鼠标手感和多屏 DPI 需人工检查。

Python 脚本编辑器与退出流程的入口为 `tools/verify-native-python-editor-quit.ps1`：编译 `ai/tests/native_python_script_editor.cpp` 并链接当前各库与 `main/praat_win.o`，在与产品相同的独立进程内打开真正的原生窗口，答掉写盘失败提示与另存为询问，并重新读取磁盘文件；其中“保存工程并退出”由第二个进程点击按钮后自行结束。Whisper 模型维度检查的入口为 `tools/verify-native-whisper-model-dims.ps1`，用维度计数 `-1`、`0`、`2`、`4`、`5` 的最小 ggml 夹具走 `Create SpeechRecognizer...` 实际路径。配置共存的入口为 `tools/verify-native-prefs-coexistence.ps1`，在隔离的 `USERPROFILE` 下分别以批处理和关闭窗口两种方式退出 `Praat.exe`，核对新文件写出、`Preferences5.ini` 与 `Buttons5.ini` 字节不变；关窗那条会短暂显示主窗口。三者都在 `scratch/` 下建立私有目录，不加载用户偏好，不替换根目录 `Praat.exe`；`make ... all` 末尾的发布步骤需 Medium 完整性会话，失败不影响链接产物。

Python 编辑器和 Whisper 维度验证脚本将 `-OutputDirectory` 作为输出根目录，每次在其中创建独立的 `run-<GUID>` 子目录，结果位于该子目录的 `result.json`；不清理既有文件或上次结果。Python 编辑器验证的输出路径须使用 ASCII。`tools/verify-native-output-isolation.ps1` 对这两个真实入口各运行两次，核对现有文件与前一次结果保持不变。

日常启动程序与测试构建须分别核对。将通过验证的原生构建安装到本地入口时，运行 `tools/Publish-NativeExecutable.ps1 -Source <构建路径> -Destination ./Praat.exe -MediumDirectory ..`；该脚本保留旧文件备份并核对哈希和完整性。已经打开的 Praat 仍运行原程序，须保存音频和编辑内容、退出后重新打开根目录 `Praat.exe` 才能加载更新；测试目录构建成功不代表当前进程已更新。

框选视觉回归还会在两条 Sound 对象下逐像素比较空白区，检查松开、Esc、捕获丢失后的背景恢复、缩小选框后的旧区域清理及重复重绘的颜色稳定性。设置 `PRAAT_LIST_TEST_CLEANUP_SCREENSHOT` 可保存松开后的列表截图。

Ctrl 交互回归覆盖原生 Ctrl＋左键的添加与取消选择、Ctrl＋右键不显示框选，以及框选途中通过按键或鼠标修饰键状态检测 Ctrl 后恢复原选区；Shift 仍可扩展右键框选。

## 安装与发行

`installer/build.ps1` 构建安装包，`installer/publish-release.ps1` 发布；安装与环境检查见[安装说明](installer/README.zh-CN.md)。源码、包内载荷、安装后文件及运行进程分别核对。

`.github/workflows/release.yml` 手动触发，后端能力门和离线回归、前端 TypeScript 与单元测试通过后才构建 Windows x64 及可选 macOS。Windows 生成 `Praat.exe` 后运行真实 VOT、基频与事件分层回归。该门槛不覆盖真实录音效果和干净环境安装验收。

本次日志与材料保存到 `test-records/`；旧结果不代表当前版本通过，也不写回维护文档作为固定通过数。图示生成入口为 `node docs/pictures/aipraat/build-detailed-workflows.mjs`，图示内容须与实际调用路径一致。
