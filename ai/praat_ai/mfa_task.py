"""Frozen, task-local MFA resources and selected pronunciations."""
from __future__ import annotations

import copy
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .mfa_g2p import english_words
from .phone_labels import map_sequence


def file_state(paths: dict) -> dict:
    state = {}
    for kind, value in paths.items():
        if value and Path(value).is_file():
            stat = Path(value).stat()
            state[kind] = [str(Path(value).resolve()), stat.st_size, stat.st_mtime_ns]
    return state


def validate_selection(request) -> None:
    snapshot = request.mfa_snapshot
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('preset'), dict):
        raise ValueError('MFA 任务缺少有效资源快照。')
    paths = snapshot.get('paths', {})
    if any(not paths.get(kind) or not Path(paths[kind]).is_file() for kind in ('g2p', 'dictionary', 'acoustic')):
        raise ValueError('本次 MFA 配套资源文件不存在，请重新配置。')
    if snapshot.get('file_state') != file_state(paths):
        raise ValueError('本次 MFA 配套资源文件已改变，请重新生成。')
    preset = snapshot['preset']
    if request.language != preset['language'] or (request.profile_id and request.profile_id != preset.get('rule_id')):
        raise ValueError('目标读音与语言或音位判据不一致。')
    if not request.selected_readings:
        raise ValueError('MFA G2P 任务没有已选读音。')
    sequence = english_words(request.transcript)
    if sequence != [item.get('word') for item in request.selected_readings]:
        raise ValueError('参考文本与本次已选词序不一致。')
    ipa, raw, seen = [], [], {}
    for item in request.selected_readings:
        converted = map_sequence(item.get('raw', []), preset)
        if converted['ipa'] != item.get('ipa'):
            raise ValueError('已选读音的原始标签与 IPA 映射不一致。')
        word = item['word']
        if word in seen and seen[word] != item['raw']:
            raise ValueError('同一个词的重复出现需使用同一读音；请拆分本次分析。')
        seen[word] = item['raw']
        raw.extend(item['raw'])
        ipa.extend(item['ipa'])
    if ipa != [phone.ipa for phone in request.phonemes] or raw != [phone.label for phone in request.phonemes]:
        raise ValueError('请求的音位序列与选定读音不一致。')


@contextmanager
def task_alignment(request, alignment):
    if not request.mfa_snapshot:
        yield alignment
        return
    validate_selection(request)
    cfg = copy.deepcopy(alignment)
    snapshot = request.mfa_snapshot
    cfg.backend = 'mfa-strict'
    cfg.mfa.enabled = True
    cfg.mfa.acoustic_model = snapshot['paths']['acoustic']
    for key in ('executable', 'conda_executable', 'conda_environment'):
        setattr(cfg.mfa, key, snapshot['launcher'].get(key, ''))
    with tempfile.TemporaryDirectory(prefix='praat_selected_dictionary_') as directory:
        dictionary = Path(directory) / 'selected.dict'
        selected = {item['word']: item['raw'] for item in request.selected_readings}
        dictionary.write_text(''.join(word + '\t' + ' '.join(raw) + '\n' for word, raw in selected.items()),
                              encoding='utf-8')
        cfg.mfa.dictionary_path = str(dictionary)
        yield cfg


def explanation_payload(payload: dict) -> dict:
    """Keep local resource paths/metadata out of the pre-existing Qwen request."""
    return {key: value for key, value in payload.items()
            if key not in {'mfa_snapshot', 'selected_readings', 'generation_source'}}
