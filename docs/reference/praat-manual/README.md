# 声学设计规范参考

本目录保存 Praat 官方英文手册的离线副本，供核对算法、参数含义和标注对象；按需打开相关章节。量的定义还须结合下列论文及用户要求，不能仅凭已有代码或测试反推。

## Praat 官方手册（本地原版）

- [手册首页](manual/Intro.html)、[手册导航说明](manual/Manual.html)。
- [如何选择基频分析方法](manual/how_to_choose_a_pitch_analysis_method.html)：区分语音基频、嗓音分析与数学周期性等用途，核对适用算法。
- [滤波自相关基频分析](manual/pitch_analysis_by_filtered_autocorrelation.html)：核对语音基频算法、参数与适用条件。
- [标注与 TextGrid](manual/Intro_7__Annotation.html)、[TextGrid 对象](manual/TextGrid.html)：核对点、区间及标注对象的语义。

来源：[Praat 官方在线手册](https://www.fon.hum.uva.nl/praat/manual/)。获取日期：2026-10-20。`manual/` 内保存官方 1,955 个 HTML 页面和 203 张图片，独立于本项目的[中文翻译手册](../../manual/Intro.html)。另有 2 个 Unicode 文件名别名，用于兼容原站链接中的字符组合差异，内容与对应原页相同。这是获取时的网页副本，适用参数与命令仍须核对实际运行的 Praat 版本。

作者及许可见[官方许可说明](LICENSE.upstream.txt)；手册页面和图片采用 GPL-3.0-or-later，许可证全文见[手册内的 GPL 第 3 版](manual/General_Public_License__version_3.html)。[文件校验清单](SHA256SUMS.txt)用于核对离线文件完整性。更新时用完整官方副本替换本目录原文，并同步来源、校验清单和本页链接。

## 另外两项参考

1. **VOT 测量定义** — Abramson & Whalen（2017），*Voice Onset Time (VOT) at 50: Theoretical and practical issues in measuring voicing distinctions*。网址：[https://pmc.ncbi.nlm.nih.gov/articles/PMC5665574/](https://pmc.ncbi.nlm.nih.gov/articles/PMC5665574/)。用于核对爆破释放与相关声带振动起点的关系、正负 VOT 及预浊等特殊样本的测量含义；爆破到清晰元音的间隔应保留独立量名。
2. **TextGrid 标注实践** — Will Styler，*Using Praat for Linguistic Research*。网址：[https://github.com/stylerw/usingpraat/blob/main/usingpraat.md](https://github.com/stylerw/usingpraat/blob/main/usingpraat.md)。教程明确区分区间层与点层：元音、词和音段等持续对象用区间，爆破释放等瞬时事件用点。用于设计事件地标与持续区间的标注方式；教程示例的命令和参数须结合官方手册与实际版本核对。

本项目的指标、地标与验证入口见[声学测量说明](../../../ai/docs/measurements.zh-CN.md)。
