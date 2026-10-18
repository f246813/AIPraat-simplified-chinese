"""分析路径的 Python 运行库能力：进程内轻量检测 + 单一口径的提示文案。

`ai/runtime_dependencies.py` 的 ``detect`` 为每个运行库项起子进程做「加载 + 参考功能」检查，
适合安装向导和交付前体检；纠音流程需要在**提交之前**告诉用户「这一步现在能不能跑」，
所以这里只做 import + 版本比对，并把「缺什么 / 去哪儿装 / 装完要做什么」收在同一个地方：

- 前端 ``tutor.capabilities``（缺依赖时禁用开关并给出可执行提示）；
- :mod:`praat_ai.audio` 基频/共振峰缺失的原因（交付物里的 ``pitch_formant_reason``）；
- :mod:`praat_ai.normalization` 共振峰校准的前置条件；
- :mod:`praat_ai.normalization_prosody` 响度归一化的依赖门控；
- 「路径配置 → 配置运行库…」那个入口（``tutor.configure_runtime``）。

版本区间不在本模块里写死：从 ``ai/requirements.txt`` 读（与安装向导同一份约束）。
"""

from __future__ import annotations

import importlib
import importlib.metadata
import platform
import sys
from pathlib import Path

_AI_ROOT = Path(__file__).resolve().parents[1]
if str(_AI_ROOT) not in sys.path:
    # 让 `praat_ai` 在没把 ai/ 放进 sys.path 的宿主里也能复用同一份 requirements 解析。
    sys.path.insert(0, str(_AI_ROOT))

from runtime_dependencies import requirements, satisfies  # noqa: E402

REQUIREMENTS_PATH = _AI_ROOT / "requirements.txt"

#: requirements 里的包名 → 可导入名（两者不一致时才有必要列出）。
IMPORT_NAMES = {
    "numpy": "numpy",
    "scipy": "scipy",
    "pyloudnorm": "pyloudnorm",
    "praat-parselmouth": "parselmouth",
}

#: 修好依赖要走的入口：与 ui.py 的 ``tutor.configure_runtime``、前端按钮文案一致。
FIX_HINT = "请在“路径配置 → 配置运行库…”安装；装好后关闭并重新打开纠音窗口。"

#: 与纠音提交直接相关的能力。id 与前端 ``NormalizationSelection`` 的开关一致
#: （formants / pitch / loudness），label 只用于文案。
FEATURES = (
    dict(
        id="formants",
        label="共振峰校准与 F1/F2 观察",
        packages=("numpy", "praat-parselmouth"),
    ),
    dict(
        id="pitch",
        label="基频提取与相对音高（半音）",
        packages=("numpy", "praat-parselmouth"),
    ),
    dict(
        id="loudness",
        label="整段恒定响度（LUFS）",
        packages=("numpy", "scipy", "pyloudnorm"),
    ),
)

FEATURE_INDEX = {feature["id"]: feature for feature in FEATURES}


def _interpreter_note(package: str) -> str:
    """有些运行库是**故意**不装在较新解释器上的（requirements 里有 python_version 标记）。

    这种「缺」不是待修的问题，提示必须说清原因，否则用户会去反复重装一个装不上的包。
    """

    if package == "praat-parselmouth" and sys.version_info[:2] >= (3, 13):
        return (
            "requirements 对 Python 3.13 及以上不安装 praat-parselmouth（没有官方 wheel）；"
            "需要这一步时请改用 Python 3.12 的解释器。"
        )
    return ""


def _specs() -> dict[str, str] | None:
    """读 ``requirements.txt`` 的权威版本区间；读不到时返回 None（只说「装没装」）。"""

    try:
        return {row["id"]: row["spec"] for row in requirements(REQUIREMENTS_PATH)}
    except Exception:  # noqa: BLE001 - 约束文件缺失/格式变化不该让纠音流程起不来
        return None


def _package_status(package: str, spec: str) -> dict:
    """一个包的「能不能导入 + 版本是否落在 requirements 区间内」。

    这里要的是「这个进程现在能不能用」，不看元数据是否登记：先 import，再比版本。
    """

    name = IMPORT_NAMES.get(package, package)
    row = dict(id=package, import_name=name, installed="", ready=False, reason="")
    try:
        importlib.import_module(name)
    except Exception as error:  # noqa: BLE001 - 任何导入失败都要落到「缺依赖」这一档
        row["reason"] = f"未安装/无法加载 {package}（{type(error).__name__}）"
        note = _interpreter_note(package)
        if note:
            row["reason"] = f"{row['reason']}；{note}"
        return row
    try:
        row["installed"] = importlib.metadata.version(package)
    except Exception:  # noqa: BLE001 - 元数据读不到时不做版本判断，只记录事实
        row["installed"] = ""
    if spec:
        if not row["installed"]:
            row["reason"] = f"无法确认 {package} 的版本（模块可导入，但元数据缺失）"
        else:
            try:
                if satisfies(row["installed"], spec):
                    row["ready"] = True
                else:
                    row["reason"] = f"版本不匹配：需要 {package}{spec}，当前 {row['installed']}"
                    return row
            except Exception as error:  # noqa: BLE001 - 版本号怪到无法比对也要如实说
                row["reason"] = f"版本号无法比对：{package} {row['installed']}（{error}）"
                return row
    row["ready"] = True
    return row


def check(feature_id: str, *, specs: dict[str, str] | None = None) -> dict:
    """检测一个能力（``formants`` / ``pitch`` / ``loudness``）。

    返回 ``ready``、逐包明细、以及一句话原因与可执行提示；缺依赖时 ``reason`` 非空。
    """

    try:
        feature = FEATURE_INDEX[str(feature_id)]
    except KeyError:
        raise ValueError(f"未知的能力项：{feature_id}") from None
    known = _specs() if specs is None else specs
    packages = []
    for package in feature["packages"]:
        packages.append(_package_status(package, (known or {}).get(package, "")))
    missing = [row for row in packages if not row["ready"]]
    reason = "；".join(row["reason"] for row in missing if row["reason"])
    return dict(
        id=feature["id"],
        label=feature["label"],
        ready=not missing,
        packages=packages,
        reason=reason,
        hint="" if not missing else FIX_HINT,
    )


def blocker(feature_id: str, *, specs: dict[str, str] | None = None) -> str:
    """给运行路径用的一句话：为什么这一步现在跑不了（能跑则返回空串）。

    句子不带主语，由调用方补上下文（基频/共振峰、共振峰校准、响度归一化……各说各的对象）。
    """

    status = check(feature_id, specs=specs)
    if status["ready"]:
        return ""
    detail = status["reason"] or "；".join(row["id"] for row in status["packages"])
    return f"缺少分析依赖：{detail}。{status['hint']}"


def _environment_report() -> dict:
    """运行环境缺件（Praat / ffmpeg / wav2vec2）的只读体检。

    Praat 缺了整条分析链就跑不起来，所以在 ``ready`` 里一并算；ffmpeg 之类缺了只是降级。
    体检本身失败不能把纠音窗口带崩，按「不阻塞」处理并留下原因。
    """

    try:
        from . import environment

        return environment.report()
    except Exception as error:  # noqa: BLE001 - 面板坏掉不该挡住使用
        return dict(ready=True, degraded=False, items=[], missing=[],
                    message=f"运行环境体检未完成：{type(error).__name__}")


def analysis_capabilities() -> dict:
    """前端 ``tutor.capabilities`` 的负载：纠音提交相关能力的完整状态。"""

    specs = _specs()
    features = {feature["id"]: check(feature["id"], specs=specs) for feature in FEATURES}
    missing = [item["label"] for item in features.values() if not item["ready"]]
    environment = _environment_report()
    reasons = []
    if missing:
        reasons.append("当前解释器缺少部分分析依赖：" + "、".join(missing) + "。" + FIX_HINT)
    if environment.get("missing"):
        reasons.append("运行环境缺少必需件：" + "、".join(environment["missing"]) + "。")
    return dict(
        python=sys.executable,
        version=platform.python_version(),
        requirements=str(REQUIREMENTS_PATH) if specs is not None else "",
        ready=not missing and bool(environment.get("ready", True)),
        features=features,
        environment=environment,
        message="".join(reasons),
    )
