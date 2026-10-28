# 语图配色、不透明度与叠加对比

本文说明 Windows 原生编辑器的外观操作、绘制职责和验证入口。[交互预览](design-assets/spectrogram-comparison.html)与[配色总览](design-assets/spectrogram-comparison.png)用于解释设计，实际效果以原生窗口为准。

## 使用方式

Sound、LongSound 和带声音的 TextGrid 编辑器顶部提供 **外观**；有“前端”菜单的窗口中，“外观”紧随其后。点击后在下拉面板中选择“原版”“雾蓝灰”或“陶土灰”，并通过 **20%–100%** 滑块调整当前窗口不透明度。拖动立即生效，整数百分比位于“不透明度”标签同一行的右侧；100% 为完全不透明，无额外开关。

主要用途是将上层改色窗口叠在下层原版窗口上，比较谱纹、脉冲、音高和共振峰的位置。先让两窗口的可见时间、频率范围、尺寸和分析设置一致，再调整上层不透明度，可从 50% 开始。两种改色窗口互相叠加属于次要用途。

- 配色和不透明度按窗口独立保存于内存；新窗口为原版、100%，不随 Group 同步，也不写入外观首选项
- “原版”恢复该窗口按既有声谱图设置绘制的风格；不会把其他分析设置重置为默认值
- 在既有“声谱图颜色设置”中应用色图会同步外观状态；选择其他色图退出临时外观覆盖
- 点击面板外或按 Esc 关闭并保留当前值；再次点击“外观”也可关闭
- Tab 在配色项和滑块间移动，左右方向键在配色项间移动，Enter 或空格选择；滑块方向键调整 1%，Home 到 20%，End 到 100%
- 下拉面板保持不透明；20% 时主窗口的文字和数据会一起变淡

对象列表、Praat Picture、AI WebView、独立 Spectrogram 编辑器和无声音 TextGrid 沿用原有界面。当前功能不提供自动置顶、鼠标穿透、自动对齐、时间伸缩或差异计算。其他平台沿用原有窗口行为。

## 配色与层级

两种主色均为白底低饱和单色渐变，优先考虑与下层灰度谱纹、蓝色分析线和红色共振峰的区分。两套主色共用功能色，切换主色不改变分析项的颜色含义。

| 元素 | 雾蓝灰 | 陶土灰 | 绘制方式 |
| --- | --- | --- | --- |
| 谱纹最深色 | `#71889B` | `#9F7E71` | 白到主色的线性光渐变，复用现有强度归一化 |
| 脉冲 | `#4F6054` | `#4F6054` | 石墨绿竖线，白色衬边；先于波形绘制 |
| 音高 | `#75613F` | `#75613F` | 金棕实线或既有离散显示，白色衬线 |
| 共振峰 | `#315F5B` | `#315F5B` | 青绿实心散点，白色外缘 |
| 波形 | `#3F464D` | `#3F464D` | 中性近黑轮廓 |
| 音强 | `#59616B` | `#59616B` | 石墨灰细虚线 |

谱纹最深色的饱和度分别约 17% 和 19%，白底对比度均约 3.7:1。两套映射使用相同的强度范围与插值方式，不另加增强弱能量的指数曲线。256 级映射表复用，像素内只做查表与插值。

图面背景为 `#FFFFFF`，坐标文字 `#343A40`，辅助刻度 `#737B82`，零线与必要网格 `#E5E8EA`，光标 `#4A5056`。声音与分析区的选区为中性灰 `#D5D9DC`，TextGrid 标注层保留自身高亮。

脉冲主线宽 1.8、白色衬边总宽 3.2 个逻辑像素；音高主线宽 2.8、衬线总宽 5.2。共振峰点径继续使用用户设置，白缘总直径为点径的 `6.4/4.4` 倍；4.4／6.4 为设计参考，不覆盖用户点径。绘图接口分别按线宽和毫米点径换算，避免直接把逻辑像素作为毫米。

原有脉冲时刻、音高断点、共振峰位置与可见性条件保持不变。密集脉冲不删点；清音处不补线；共振峰不连成曲线。音高保留自身右侧刻度，共振峰和谱纹使用频率刻度，不能为视觉对齐合并纵轴。三通道 RGB 谱图继续保留声道含义。

## 界面结构

弹出面板宽 328、高 248 个逻辑像素，内边距 24；原版、雾蓝灰和陶土灰三张色卡等宽并排，间距 8，每张包含三档明度色样、名称和选中标识。下方为标签与当前百分比同排的不透明度区域，全宽滑杆行高 32，下方无端点标签。表面为白色和柔和灰影，圆角 14，滑杆使用中性灰与圆形手柄。颜色集中在数据与小色样，菜单不增加状态说明、小标签或第二个半透明模式。尺寸、字体与键盘焦点规则见[外观面板 UI](APPEARANCE-PANEL-UI-DESIGN.zh-CN.md)。

面板锚点取真实菜单项矩形，按所属窗口 DPI 布局，并限制在当前显示器工作区。失焦、Esc、外部点击和编辑器销毁均关闭面板。跨 DPI 后重新打开时重建相应尺寸。

## 实现职责

| 路径 | 职责 |
| --- | --- |
| `foned/EditorAppearance.h/.cpp` | 配色枚举、功能色、线宽及点径单位换算 |
| `foned/EditorAppearancePanel.h/.cpp` | Windows 原生配色项、TRACKBAR_CLASS、百分比、键盘及失焦关闭 |
| `foned/FunctionEditor.h/.cpp` | 窗口独立状态、外观入口和回调、不透明截图入口 |
| `foned/SoundEditor.h`、`TextGridEditor.h` | 确定适用窗口；Sound 与 LongSound 共用 SoundEditor |
| `foned/SoundArea.cpp`、`SoundAnalysisArea.cpp`、`FunctionArea.h` | 波形、脉冲、分析标记、坐标和选区的当前窗口配色 |
| `fon/Spectrogram.cpp`、`Spectrogram_enums.h` | 追加两套单色映射，复用功率、预加重、动态范围与压缩结果 |
| `fon/Formant.cpp` | 在原位置以同一可见性条件绘制散点白缘与核心 |
| `sys/Editor.*`、`GuiWindow.*`、`motifEmulator.cpp` | 创建可分层透明的顶层类，设置整窗 alpha；绘图区保留自身 DC |
| `sys/GuiMenu.cpp` | 顶部命令入口与菜单矩形；弹出层由编辑器拥有 |
| `sys/GuiDrawingArea.cpp` | 用真实 expose 回调生成白底离屏位图，随后恢复各图形上下文 |

Windows 的 `WS_EX_LAYERED` 与 `CS_OWNDC`／`CS_CLASSDC` 不兼容。适用编辑器使用独立顶层窗口类，去掉顶层的 OWNDC；绘图子窗口继续保留原有类和 DC。100% 时移除本功能添加的分层样式。调整 alpha 不改变窗口位置、尺寸、输入命中或置顶状态。

滑块使用独立 `TRACKBAR_CLASS`。项目 Windows 模拟层的 `GuiScale` 是进度条，不能承接拖动输入。配色切换只重绘；透明度拖动只更新系统合成。两者不重建声谱图、音高、共振峰和脉冲缓存。既有颜色设置只有改变“三通道为 RGB”时才需要重建相应谱图列表。

## 图像输出

“复制发音截图”通过真实编辑器绘图回调生成离屏位图，保留当前波形、标记、坐标与可见范围，按既有规则裁剪。输出使用当前配色、白底和完全不透明 alpha，既不截取桌面合成结果，也不临时改变窗口不透明度。

Sound／LongSound 的三项复制操作保留原有内容与格式：单独图像、单独提问文字、图像与文字组合；组合包含 PNG、DIB、DIBV5、Unicode 文本和 HTML。TextGrid 不增加发音截图菜单，可沿用 Picture 绘图。

编辑器“绘制可见声谱图”使用当前有效色图，Picture 保存的图片不跟随窗口透明度。独立 Spectrogram 对象的绘图命令仍按其自身色图参数绘制。

## 构建与验证

Windows 原生构建沿用[开发与维护](../guide.md#翻译与原生构建)：MSYS2 CLANG64 下执行 `make PRAAT_COMPILER=clang PRAAT_ARCH=x64v3 -j2`。测试产物可通过 `EXECUTABLE_FILE` 指定；本地入口使用 [Publish-NativeExecutable.ps1](../tools/Publish-NativeExecutable.ps1) 更新，并核对最终文件与运行进程的完整性状态。

[verify-native-appearance.ps1](../tools/verify-native-appearance.ps1)与[NativeWindowProbe.cs](../tools/NativeWindowProbe.cs)驱动真实原生窗口，读取窗口类、分层属性、控件、剪贴板及导出图像。脚本支持 PowerShell 7 入口并转入 Windows PowerShell 使用 System.Drawing。

```powershell
$exe = 'D:\Praat-work\稳定早期版\Praat.exe'
$stage = 'D:\Praat-work\稳定早期版\test-records\native-appearance'
$parts = 'launch','menus','shell','panel','opacity','drag','colour','overlay','selection','isolation',
         'keyboard','dismiss','clipboard','picture','grid','interact','analysis','teardown'
foreach ($part in $parts) {
    & .\tools\verify-native-appearance.ps1 -Executable $exe -Stage $stage -Phase $part
    if ($LASTEXITCODE -ne 0) { throw "Verification failed: $part" }
}
```

测试使用独立首选项目录和合成声音，操作可见窗口并写入剪贴板。launch 只重启同一 Stage 记录且启动时间匹配的测试实例；发现其他 Praat 实例时停止，避免 `--send` 进入用户会话。teardown 关闭测试进程。每次正式验收使用新的 Stage，避免旧结果混入。

| 范围 | 验收内容 |
| --- | --- |
| 主要叠加 | 下层原版及其蓝色／红色标记，上层两套新色各 20%／50%／100%；重合与已知差异样本 |
| 可辨性 | 密集脉冲、音高断点、浓谱纹上的共振峰；图例、刻度、位置与形态正确 |
| 实例隔离 | 上层修改不影响下层，新窗口原版 100%；既有色图应用后外观状态一致 |
| 原生操作 | 选区、缩放、播放、滚动、最大化／还原、面板开关及销毁；不透明度滑杆的真实鼠标与键盘操作见[不透明度滑杆交互](APPEARANCE-SLIDER-INTERACTION-DESIGN.zh-CN.md) |
| 布局 | 100%／125%／150%／200%、最小窗口及跨显示器移动；检查圆角、间距、遮盖与裁剪 |
| 图像输出 | 窗口半透明时复制、组合复制、Picture 导出仍白底不透明，无下层桌面；坐标与范围正确 |
| 分析保持 | 同输入同设置下直接查询音高、共振峰、音强和脉冲时刻，切换外观前后结果一致；缓存不因外观失效 |
| 补充场景 | 真实语音、两张改色窗口叠加、三通道 RGB 与用户自定义点径 |

`checks.json` 和 `verification.json` 记录当前运行结果；截图、参数、查询文件与日志放在 `test-records/`。浏览器缩放不等于原生 DPI，设计预览和颜色常量不能代替原生验收。可见屏幕截图需确认目标窗口在前台，滚动绘图区需裁剪到窗口可见范围。未实际覆盖的 DPI、显示器或音频输出应在验收记录中列明。
