"""Conservative dispatch for unambiguous VOT measurement commands.

Only complete, recognized commands bypass the model. Questions, negation,
extra analysis settings and multi-step requests remain with the planner.
Search windows are never interpreted as burst/voicing landmarks.
"""

from __future__ import annotations

import re
from typing import Any


_NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
_UNIT = r"(?:毫秒|ms|秒|s)"
_RANGE = re.compile(
    rf"(?:在|范围(?:为|是)?)(?P<start>{_NUMBER})(?P<unit1>{_UNIT})?"
    rf"(?:到|至|–|-|~|～)(?P<end>{_NUMBER})(?P<unit2>{_UNIT})?(?:之间|内)?"
)
_LANDMARKS = {
    "burst": re.compile(rf"(?:爆破|除阻|burst)(?:时刻)?(?:为|是|=|:|：)?(?P<value>{_NUMBER})(?P<unit>{_UNIT})?"),
    "voicing": re.compile(rf"(?:预浊起始|浊音起始|浊音开始|voicingonset|prevoicingonset|voicing|onset)(?:时刻)?(?:为|是|=|:|：)?(?P<value>{_NUMBER})(?P<unit>{_UNIT})?"),
    "vowel": re.compile(rf"(?:清晰元音起始|clearvowelonset|vowel)(?:时刻)?(?:为|是|=|:|：)?(?P<value>{_NUMBER})(?P<unit>{_UNIT})?"),
    "closure_start": re.compile(rf"(?:闭塞起始|closure_start|closurestart)(?:时刻)?(?:为|是|=|:|：)?(?P<value>{_NUMBER})(?P<unit>{_UNIT})?"),
}
_OBJECT = re.compile(r"(?P<id>\d+)号(?:对象|声音)?的?")
_MEASURE = r"(?:测量|测|测一下|测一测|提取|计算|算|算一下|找|找一下)"
_TARGET = r"(?:(?:当前|这个|这段)(?:选中)?(?:声音|语音|对象)|(?:当前|这段)?选区)的?"
_VOT = r"(?:vot|嗓音起始时间)"
_COMMAND = re.compile(
    rf"(?:请)?(?:帮我)?(?:{_MEASURE}(?:一下)?(?:{_TARGET})?{_VOT}|{_VOT}(?:测量|计算))(?:一下|吧)?[。.!！]?"
)


def _seconds(value: str, unit: str | None) -> float:
    return float(value) / (1000 if unit in {"毫秒", "ms"} else 1)


def manual_landmarks(text: str) -> dict[str, list[float]]:
    text = re.sub(r'\s+', '', text).casefold()
    return {key: [_seconds(m['value'], m['unit']) for m in pattern.finditer(text)]
            for key, pattern in _LANDMARKS.items()}


def validate_manual_landmarks(arguments, text):
    """Model-proposed numbers cannot establish manual annotation provenance."""
    import math
    provided = manual_landmarks(text)
    for key in ('burst', 'voicing', 'onset', 'vowel', 'closure_start'):
        if key in arguments:
            value = float(arguments[key])
            if not any(math.isclose(value, t, abs_tol=1e-8, rel_tol=0)
                       for t in provided.get('voicing' if key == 'onset' else key, [])):
                raise ValueError(f'{key} 不是用户提供的人工地标；请保留自动检测候选或提供真实标注。')


def direct_arguments(user_text: str) -> dict[str, Any] | None:
    """Return only user-supplied arguments, or None for general planning."""

    text = re.sub(r"\s+", "", user_text).casefold()
    arguments: dict[str, Any] = {}
    ranges = list(_RANGE.finditer(text))
    if len(ranges) > 1:
        return None
    if ranges:
        match = ranges[0]
        arguments["from"] = _seconds(match["start"], match["unit1"] or match["unit2"])
        arguments["to"] = _seconds(match["end"], match["unit2"] or match["unit1"])
        text = _RANGE.sub("", text, count=1)
    for key, pattern in _LANDMARKS.items():
        matches = list(pattern.finditer(text))
        if len(matches) > 1:
            return None
        if matches:
            match = matches[0]
            arguments[key] = _seconds(match["value"], match["unit"])
            text = pattern.sub("", text, count=1)
    objects = list(_OBJECT.finditer(text))
    if len(objects) > 1:
        return None
    if objects:
        arguments["object"] = int(objects[0]["id"])
        text = _OBJECT.sub("", text, count=1)
    text = re.sub(r"[,，;；]", "", text)
    if not _COMMAND.fullmatch(text):
        return None
    # Do not silently prefer landmarks when a separate window was also given.
    if ranges and ("burst" in arguments or "voicing" in arguments):
        return None
    return arguments
