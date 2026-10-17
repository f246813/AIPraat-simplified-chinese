# pyloudnorm 第三方说明

AIPraat 的响度归一化使用正式发行依赖 **pyloudnorm 0.2.0**，上游项目：<https://github.com/csteinmetz1/pyloudnorm>。

- 版权所有：Copyright (c) 2018 Christian Steinmetz。
- 许可证：**MIT**，完整许可原文见 [LICENSE](LICENSE)。本说明不能替代许可原文。
- 使用正式依赖提供的 LUFS 测量与恒定增益校正，不另行重写其 K 加权或门控算法。
- wheel SHA-256：`9bb69afb904f59d007a7f9ba3d75d16fb8aeef35c44d6df822a9f192d69cf13f`。
- 发行 wheel 全部五个 Python 文件已与 sdist 及上游 commit `b8d67bfd3ce5deef872f688fcfa491a0ca69fddd` 逐字节核对。
- 版本约束、其他制品哈希、源码及验证边界见 [README.md](README.md) 与 [provenance.json](provenance.json)。

## 相关依赖许可

pyloudnorm 0.2.0 依赖 NumPy 和 SciPy。本项目使用约束 `numpy>=2.0,<2.3`、`scipy>=1.14.1,<1.15`。SciPy 主项目采用 BSD-3-Clause；其 wheel 内 `scipy-1.14.1.dist-info/LICENSE.txt` 包含主项目和捆绑二进制组件的许可声明，须随相应发行包保留，不能用本目录 MIT 许可替代。NumPy 的许可及组件声明同样以其发行包内许可文件为准。

## 安装与交付边界

本目录提供许可及来源记录，不私带安装后的 site-packages，也不意味着可以静默安装用户依赖。应用内运行库入口仅由用户点击触发，启动既有路径配置程序，由用户在共用窗口选择安装方式。

本次只完成源码更新、scratch 中的数值/安装验证，以及两类 C# 宿主的验证编译和嵌入资源一致性测试；**未重建或替换顶层交付 exe，生产交付资源及发布验收仍未完成**。不能用开发验证结果声称现有交付二进制已含新版本资源。
