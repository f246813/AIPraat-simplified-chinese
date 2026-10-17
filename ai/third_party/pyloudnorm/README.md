# pyloudnorm 发行包、源码与许可记录

本目录不提供另一个实现，也不随应用私带 site-packages；用户需主动在共用“配置运行库…”入口安装正式 wheel。

## 选定约束

- `pyloudnorm==0.2.0`：实际从 PyPI 下载的正式发行版，不是根据仓库的版本字符串推定。
- `scipy>=1.14.1,<1.15`：验过的发行版为 1.14.1。
- 同一条 NumPy 登记改为 `numpy>=2.0,<2.3`；与 SciPy 1.14.1 的 `numpy>=1.23.5,<2.3` 交集一致。显式上限也防止“只修 NumPy”时越过已安装 SciPy 的兼容边界。
- Python 支持仍为 **CPython 3.10–3.12 Windows x64**，没有扩大范围。
- 0.2.0 的运行依赖只有 NumPy/SciPy；不引入旧版的 future，不引入 librosa、pandas、PyQt、R 或 VLNM。

## 来源与对应关系

- 发行元数据：<https://pypi.org/pypi/pyloudnorm/0.2.0/json>
- SciPy 元数据：<https://pypi.org/pypi/scipy/1.14.1/json>
- 上游源码：<https://github.com/csteinmetz1/pyloudnorm/tree/b8d67bfd3ce5deef872f688fcfa491a0ca69fddd>
- 已实际下载 wheel、sdist 与上述 commit 的源码归档，逐字节比较 wheel 内 **全部五个 Python 文件**，均与 sdist / commit 相同；每文件 SHA-256 见 `provenance.json`。
- SciPy wheel 的版本、Requires-Python、NumPy 依赖、内置许可均已检查，源版本为 <https://github.com/scipy/scipy/tree/v1.14.1>；未进行 SciPy 二进制可重复构建证明。

| 实际下载并校验的文件 | SHA-256 |
| --- | --- |
| pyloudnorm-0.2.0-py3-none-any.whl | `9bb69afb904f59d007a7f9ba3d75d16fb8aeef35c44d6df822a9f192d69cf13f` |
| pyloudnorm-0.2.0.tar.gz | `8bf597658ea4e1975c275adf490f6deb5369ea409f2901f939915efa4b681b16` |
| scipy-1.14.1-cp310-cp310-win_amd64.whl | `a49f6ed96f83966f576b33a44257d869756df6cf1ef4934f59dd58b25e0327e5` |
| scipy-1.14.1-cp311-cp311-win_amd64.whl | `716e389b694c4bb564b4fc0c51bc84d381735e0d39d3f26ec1af2556ec6aad94` |
| scipy-1.14.1-cp312-cp312-win_amd64.whl | `2ff38e22128e6c03ff73b6bb0f85f897d2362f8c052e3b8ad00532198fbdae3f` |
| numpy-2.2.6-cp311-cp311-win_amd64.whl（实测闭包） | `e8213002e427c69c45a52bbd94163084025f533a55a59d6f9c5b820774ef3303` |

这些是开发验收的制品记录，不是对用户自选任意 wheel 的签名保证。安装前仍使用现有名称、版本、Python/架构和 METADATA 校验。

## 许可证

对外归属说明入口为 [NOTICE.md](NOTICE.md)，可供 CREDITS 链接。

`LICENSE` 是 0.2.0 wheel 内的 MIT 许可原文（换行统一为 LF），也已核对与 commit 的许可一致；版权所有者为 Christian Steinmetz。安装器与路径配置程序共用资源列表，在新编译的验证宿主中均已嵌入并解包为 `pyloudnorm-LICENSE.txt`。顶层交付 exe 未重建或替换，不能据此声称现有生产二进制已更新；发布验收仍未完成。

SciPy 主项目为 BSD-3-Clause。各 SciPy wheel 内的 `scipy-1.14.1.dist-info/LICENSE.txt` 同时包含其二进制捆绑组件的声明，必须随对应发行包保留，不能只用 pyloudnorm 的 MIT 许可替代。

## 实测边界与复跑

已核对环境为 CPython 3.11.17 x64、NumPy 2.2.6、SciPy 1.14.1、pyloudnorm 0.2.0。Windows x64 的 3.10／3.12 SciPy wheel、哈希与元数据已核对；这两个解释器的安装链路测试尚未运行。

Python 3.12 的数值验证在独立 `pip --target` 目标环境安装同一组版本后完成，未改动生产环境；Python 3.10 的实际运行仍待验证。

运行时探针：SciPy `lfilter` 的固定输出 `[0.5,0.75,0.375,0.1875]`；pyloudnorm 使用 48 kHz、1 秒、峰值 0.1 的 1 kHz 正弦，参考 `-23.04504759460712 LUFS`，恒定增益后为 `-23.000000000000004 LUFS`。两项均在独立 `-I` 子进程执行，并检查有限数值、已知参考与校正结果。默认 400 ms 门控不变。

另行使用上游 WAV 验证 sine / relative gate / absolute gate / 双声道参考，并复核上游 sine 的 -6 LUFS 校正。测试用 SciPy 解码这些 WAV，不给用户增加 soundfile 依赖；这不是宣称跑完上游全部测试。绝对门控材料改变增益后可能改变入门块集合，不能拿它强行断言任意目标的增益不变性。

复跑入口为 `installer/tests/Run-LoudnessRuntimeTests.ps1`、`installer/tests/verify_loudness_install.py`、`installer/tests/verify_loudness_release.py` 与 `installer/tests/Test-PipCacheRetry.ps1`；运行库清单与检测入口见 `installer/README.zh-CN.md`。
