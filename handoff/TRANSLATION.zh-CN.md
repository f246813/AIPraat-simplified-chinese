# 翻译维护参考

UI 规范映射位于 `tools/generate_translation_map.py` 的 `EXACT_MAP`，生成结果为
`sys/praat_translate.cpp`。导出的 `praat_translations.json` 和
`praat_translations.txt` 用于审校，不是运行时读取的数据源。

手册正文源于各模块 `manual_*.cpp`；`docs/manual` 是生成页面。
术语表由 `tools/build_glossary.py` 生成，人工覆盖项位于 `MANUAL_OVERRIDES`。

定位未翻译内容时，先记录可见英文、页面标题和控件场景，再精确检索源码。
候选英文可能是链接目标、API 名称、公式或专业专名，不能仅凭英文外观判定为漏译。
手册页面标题与链接目标需保持一致，中文链接显示可用 `@@Page|中文@`。

生成或导出后检查源映射和输出的对应关系。旧候选数量、日期化报告与旧测试结果
不代表当前覆盖率。详细流程见 [维护指南](../guide.md)。
