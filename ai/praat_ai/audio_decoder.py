"""Shared FFmpeg path resolution for external audio consumers."""
import json
import os
from pathlib import Path
import shutil
from .config import default_config_path
from .paths import installation_root


def resolve_ffmpeg():
    override = os.environ.get('PRAAT_AI_FFMPEG', '').strip()
    if override:
        return override
    config_path = default_config_path()
    config = json.loads(config_path.read_text(encoding='utf-8-sig')) if config_path.is_file() else {}
    audio = config.get('audio', {})
    if 'ffmpeg' in audio:
        if audio['ffmpeg']:
            return str(audio['ffmpeg'])
    else:
        legacy_path = config_path.parent / 'anki_config.json'
        if legacy_path.is_file():
            legacy = json.loads(legacy_path.read_text(encoding='utf-8-sig'))
            if legacy.get('ffmpeg'):
                return str(legacy['ffmpeg'])
    managed = installation_root() / 'tools' / 'ffmpeg' / ('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg')
    if managed.is_file():
        return str(managed)
    return shutil.which('ffmpeg')
