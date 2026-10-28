"""手动回归：真实切换模型预设（会重启 llama-server，请确认可以占用显存）。

用法（仓库根目录，需要事先装好模型和 mmproj）：

    python ai/tests/verify_presets_live.py [预设id] [另一个预设id]

默认在配置里的前两个预设之间来回切换，检查：

1. 每次切换后端口上加载的模型和预设一致（读 ``/v1/models``）。
2. 每次切换后 ``logs/qwen-server.log`` 里加载的 mmproj 就是预设配的那个。
3. 切回原来的预设后，状态和日志都恢复正常。

它不会被 ``unittest discover`` 收集，因为会真的停掉正在运行的模型服务。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praat_ai import control, server   # noqa: E402
from praat_ai.config import load_config   # noqa: E402
from praat_ai.presets import find_preset   # noqa: E402


PROJECT = Path(__file__).resolve().parents[2]
LOG = PROJECT / "logs" / "qwen-server.log"


def loaded_mmproj() -> str:
    """日志里最后一次加载的 mmproj 文件名。"""

    if not LOG.is_file():
        return ""
    name = ""
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        if "loaded multimodal model" in line:
            name = line.rsplit("'", 2)[-2] if "'" in line else line
    return name


def describe(config_path: Path, preset_id: str) -> bool:
    config = load_config(config_path)
    preset = find_preset(config, preset_id)
    status = control.collect_status(config_path)
    live = server.running_model(config.qwen.base_url)
    ok = True
    print(f"--- 预设 {preset_id}")
    print(f"    配置模型 : {Path(config.server.model_path).name}")
    print(f"    端口模型 : {Path(live).name if live else '(服务未响应)'}")
    print(f"    视觉     : {status['frontend_vision']}")
    print(f"    状态预设 : {status['frontend_preset']} / 匹配={status['frontend_preset_matches_live']}")
    print(f"    日志 mmproj: {loaded_mmproj() or '(无记录)'}")
    if preset is None:
        print("    !! 找不到预设")
        return False
    if not live or not server.model_name_matches(live, preset.model_path):
        print("    !! 端口上的模型和预设不一致")
        ok = False
    if preset.mmproj_path and Path(loaded_mmproj()).name != Path(preset.mmproj_path).name:
        print("    !! 日志里的 mmproj 和预设不一致")
        ok = False
    if not status["frontend_preset_matches_live"]:
        print("    !! 状态里的预设和实际加载的模型不一致")
        ok = False
    return ok


def main(arguments: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    config_path = Path(control.default_config_path())
    config = load_config(config_path)
    ids = [preset.id for preset in config.server.presets]
    if len(ids) < 1:
        print("配置里没有模型预设，先在 ai/ai_config.json 里配置 server.presets。")
        return 2
    if arguments:
        targets = arguments
    elif len(ids) >= 2:
        targets = [ids[1], ids[0]]
    else:
        targets = [ids[0]]

    failures = 0
    for preset_id in targets:
        control.apply_preset(preset_id)
        if not describe(config_path, preset_id):
            failures += 1

    print(f"\n切换 {len(targets)} 次，{len(targets) - failures} 次符合预期")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
