"""Explicit, one-phone-unit mappings; original MFA labels are never discarded."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

ARPABET = {
    'AA': 'ɑ', 'AE': 'æ', 'AH': 'ʌ', 'AO': 'ɔ', 'AW': 'aʊ', 'AY': 'aɪ',
    'B': 'b', 'CH': 'tʃ', 'D': 'd', 'DH': 'ð', 'EH': 'ɛ', 'ER': 'ɝ',
    'EY': 'eɪ', 'F': 'f', 'G': 'ɡ', 'HH': 'h', 'IH': 'ɪ', 'IY': 'i',
    'JH': 'dʒ', 'K': 'k', 'L': 'l', 'M': 'm', 'N': 'n', 'NG': 'ŋ',
    'OW': 'oʊ', 'OY': 'ɔɪ', 'P': 'p', 'R': 'ɹ', 'S': 's', 'SH': 'ʃ',
    'T': 't', 'TH': 'θ', 'UH': 'ʊ', 'UW': 'u', 'V': 'v', 'W': 'w',
    'Y': 'j', 'Z': 'z', 'ZH': 'ʒ',
}


@lru_cache(maxsize=1)
def _features():
    from ._vendor.panphon import FeatureTable
    return FeatureTable()


def validate_ipa(value: str) -> str:
    value = unicodedata.normalize('NFC', value)
    if not value or re.search(r'[\s,，;；/\[\]]', value):
        raise ValueError('IPA 映射必须是单个音位单位，不能包含空格或序列分隔符。')
    # A unit can be an affricate or diphthong, but not an arbitrary string.
    segments = _features().segments(value.replace('ɚ', 'ə˞').replace('ɝ', 'ɜ˞'))
    if segments is None or len(segments) > 2:
        raise ValueError(f'无法解析 IPA 音位：{value}；请检查映射。')
    if len(segments) == 2 and not (
            all(segment['syl'] == '+' for segment in segments)
            or value.replace('͡', '').replace('͜', '') in {'tʃ', 'dʒ', 'ts', 'dz', 'tɕ', 'dʑ', 'tʂ', 'dʐ', 'pf'}
            or segments[0] == segments[1]):
        raise ValueError('IPA 映射不能包含多个不相关的音位；请使用一对一映射。')
    return value


def map_phone(raw: str, phone_set: str, mapping: dict[str, str] | None = None) -> str:
    mapping = mapping or {}
    if raw in mapping:
        return validate_ipa(mapping[raw])
    if phone_set == 'ipa':
        return validate_ipa(raw)
    if phone_set == 'arpabet':
        match = re.fullmatch(r'([A-Z]+)([012])?', raw)
        if match and match[1] in ARPABET:
            base, stress = match[1], match[2]
            ipa = mapping.get(base, ARPABET[base])
            if base == 'AH' and stress == '0' and base not in mapping:
                ipa = 'ə'
            if base == 'ER' and stress == '0' and base not in mapping:
                ipa = 'ɚ'
            return validate_ipa(ipa)
    raise ValueError(f'未知音位标签 {raw}；请补充「原始标签 → IPA」映射。')


def map_sequence(raw: list[str], preset: dict) -> dict:
    if not raw:
        raise ValueError('读音没有音位，不能应用。')
    ipa = [map_phone(token, preset.get('phone_set', ''), preset.get('phone_map', {})) for token in raw]
    stress = [int(token[-1]) if preset.get('phone_set') == 'arpabet' and re.fullmatch(r'[A-Z]+[012]', token)
              else None for token in raw]
    return dict(raw=list(raw), ipa=ipa, stress=stress)
