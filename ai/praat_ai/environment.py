"""Praat、FFmpeg 和 wav2vec2 的只读环境检查。复用各工具的发现接口，返回路径、required、ready 与可读原因；refresh 清理发现缓存。该检查不加载模型或验证真实对齐效果。"""
from __future__ import annotations

from pathlib import Path

from . import audio_decoder

PRAAT_HINT = '把 Praat.exe 放到安装目录（或设置 PRAAT_AI_PRAAT_EXECUTABLE），再点“重新检测”。'
FFMPEG_HINT = '运行 ai/Install-FFmpeg.ps1，或在“路径配置”里填 ffmpeg 路径（也可设 PRAAT_AI_FFMPEG）。'
WAV2VEC2_HINT = '在“路径配置 → MFA 预设”里导入 wav2vec2 模型目录，或把对齐后端切回 MFA。'


def praat_executable() -> str:
    """正在用的 Praat 可执行文件；找不到返回空串（定位规则见 ``praat_app``）。"""

    from . import praat_app

    try:
        return str(praat_app.find_praat() or '')
    except Exception:  # noqa: BLE001 - 探测失败按「未找到」处理，不能把面板一起弄崩
        return ''


def ffmpeg_executable() -> str:
    """ffmpeg 的解析结果；没有返回空串。"""

    try:
        return str(audio_decoder.resolve_ffmpeg() or '')
    except Exception:  # noqa: BLE001 - 配置损坏同样按「未找到」处理
        return ''


def refresh() -> None:
    """清掉 Praat 的探测缓存（界面上「重新检测」用）。"""

    from . import praat_app

    for name, value in (('_cached', None), ('_cache_valid', False), ('_last_search', 0.0)):
        if hasattr(praat_app, name):
            setattr(praat_app, name, value)


def _item(identifier: str, label: str, *, required: bool, path: str, hint: str,
          missing_detail: str) -> dict:
    ready = bool(path)
    return dict(id=identifier, label=label, required=required, ready=ready,
                detail=path or missing_detail, hint='' if ready else hint)


def _wav2vec2_models(config) -> list[str]:
    """配置里的 wav2vec2 模型目录（也可能是单数 ``model``）。"""

    if config is None:
        return []
    alignment = config.get('alignment') if isinstance(config, dict) else getattr(config, 'alignment', None)
    if alignment is None:
        return []
    backend = alignment.get('wav2vec2') if isinstance(alignment, dict) else getattr(alignment, 'wav2vec2', None)
    if backend is None:
        return []
    getter = backend.get if isinstance(backend, dict) else lambda key: getattr(backend, key, None)
    models = list(getter('models') or [])
    single = str(getter('model') or '')
    if single and single not in models:
        models.insert(0, single)
    return [str(item) for item in models if str(item).strip()]


def report(config=None) -> dict:
    """一份可以直接给前端渲染的体检结果。

    ``config`` 传 ``AppConfig``（或等价的 dict）时会把 wav2vec2 模型目录一并检查；
    不传就跳过这一项——它不是必需件，也不该为了一次面板刷新去读配置。
    """

    praat = _item('praat', 'Praat 可执行文件', required=True, path=praat_executable(),
                  hint=PRAAT_HINT, missing_detail='未找到')
    ffmpeg = _item('ffmpeg', 'ffmpeg（m4a 等压缩音频解码）', required=False, path=ffmpeg_executable(),
                   hint=FFMPEG_HINT, missing_detail='未找到')
    items = [praat, ffmpeg]

    models = _wav2vec2_models(config)
    if models:
        present = [path for path in models if Path(path).is_dir()]
        items.append(_item('wav2vec2', 'wav2vec2 对齐模型', required=False,
                           path='；'.join(present), hint=WAV2VEC2_HINT,
                           missing_detail='模型目录不存在：' + '；'.join(models)))

    missing_required = [item['label'] for item in items if item['required'] and not item['ready']]
    degraded = [item['label'] for item in items if not item['required'] and not item['ready']]
    message = ''
    if missing_required:
        message = '缺少必需运行件：' + '、'.join(missing_required) + '。'
    elif degraded:
        message = '可选项缺失（会降级，不影响主流程）：' + '、'.join(degraded) + '。'
    return dict(ready=not missing_required, degraded=bool(degraded), items=items,
                missing=missing_required, message=message)


__all__ = ['FFMPEG_HINT', 'PRAAT_HINT', 'WAV2VEC2_HINT', 'ffmpeg_executable',
           'praat_executable', 'refresh', 'report']
