"""Measurement identity, quantity domains and reproducible DTW derivations."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from .models import FeatureTrack

VERSION = "acoustic-measurement-v2"


def identity(payload) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, allow_nan=False, default=str)
    return "m-" + hashlib.sha256(data.encode("utf-8")).hexdigest()[:24]


def source_identity(source):
    return {key: source.get(key) for key in ("role", "audio_id", "id", "original_sound_domain", "wav_to_sound_offset_sec")}


def valid_value(name, value):
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if name in {"pitch", "f1", "f2", "f3"}:
        return value > 0
    if name in {"high_frequency_ratio", "zcr"}:
        return 0 <= value <= 1
    if name in {"rms", "spectral_centroid"}:
        return value >= 0
    return True


def valid_samples(track):
    if track is None or len(track.times) != len(track.values):
        return []
    return [(index, time, value) for index, (time, value) in enumerate(zip(track.times, track.values))
            if math.isfinite(time) and valid_value(track.name, value)]


def bind_tracks(phones, source):
    for phone in phones:
        for name, track in {**phone.tracks, **phone.derived_tracks}.items():
            metadata = track.analysis
            metadata.update(measurement_protocol=VERSION, quantity_type="trajectory", source=deepcopy(source),
                            phone_index=phone.phone_index, ipa=phone.ipa,
                            requested_scope=[phone.start, phone.end])
            samples = valid_samples(track)
            metadata["valid_frames"] = len(samples)
            metadata["coverage"] = [samples[0][1], samples[-1][1]] if samples else None
            metadata.setdefault("reason", "" if samples else "没有符合量定义的有效观察帧")
            # Only extraction settings participate, not computed summaries/identifiers.
            metadata["measurement_id"] = identity(dict(source=source_identity(source), phone_index=phone.phone_index,
                name=name, unit=track.unit, scope=[phone.start, phone.end],
                algorithm=metadata.get("algorithm", metadata.get("method", "unrecorded")),
                protocol=metadata.get("protocol", metadata.get("protocol_version", "unrecorded")),
                settings={**metadata.get("settings", metadata.get("parameters", {})),
                          **{k: metadata[k] for k in ('floor_hz', 'ceiling_hz', 'silence_threshold', 'baseline_id') if k in metadata}},
                calibration=metadata.get("calibration_id")))


def compatibility(left, right):
    if left is None or right is None:
        return "缺少测量"
    if left.name != right.name or left.unit != right.unit:
        return "量名或单位不一致"
    if left.name == "high_frequency_ratio":
        a, b = left.analysis, right.analysis
        if not a.get("protocol") or not b.get("protocol"):
            return "高频比例版本未记录，不能与能量口径直接比较"
        if a.get("protocol") != b.get("protocol") or a.get("algorithm") != b.get("algorithm"):
            return "高频比例算法或协议不同"
        for key in ("band_low_hz", "band_high_hz", "cutoff_hz", "window", "frame_sec", "hop_sec"):
            av = a.get(key, a.get("parameters", a.get("settings", {})).get(key))
            bv = b.get(key, b.get("parameters", b.get("settings", {})).get(key))
            if av != bv:
                return "高频比例频带或窗参数不一致"
    return ""


def compare_trajectories(left: FeatureTrack, right: FeatureTrack):
    """DTW means use actual accepted observations; persist every pairing index/time."""
    a, b = valid_samples(left), valid_samples(right)
    if len(a) < 2 or len(b) < 2:
        raise ValueError("连续轨迹缺少至少两个有效观察帧")
    costs = [[math.inf] * (len(b) + 1) for _ in range(len(a) + 1)]
    costs[0][0] = 0.0
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            costs[i][j] = abs(a[i-1][2] - b[j-1][2]) + min(costs[i-1][j], costs[i][j-1], costs[i-1][j-1])
    i, j = len(a), len(b)
    pairs = []
    while i > 0 and j > 0:
        pairs.append((i-1, j-1))
        _, i, j = min((costs[i-1][j-1], i-1, j-1), (costs[i-1][j], i-1, j), (costs[i][j-1], i, j-1))
    pairs.reverse()
    av, bv = [a[i][2] for i, _ in pairs], [b[j][2] for _, j in pairs]
    mean_a, mean_b = sum(av)/len(av), sum(bv)/len(bv)
    inputs = [left.analysis.get("measurement_id"), right.analysis.get("measurement_id")]
    derivation = dict(method="dtw", version="dtw-absolute-v1", statistic="dtw_resampled_mean",
        aligned_frames=len(pairs), reference_valid_frames=len(a), learner_valid_frames=len(b),
        normalized_distance=costs[-1][-1]/len(pairs), input_measurement_ids=inputs,
        reference_mean=mean_a, learner_mean=mean_b, unit=left.unit,
        pairs=[dict(reference_index=a[i][0], learner_index=b[j][0],
                    reference_time=a[i][1], learner_time=b[j][1]) for i, j in pairs])
    derivation["measurement_id"] = identity(derivation)
    variance = sum((v-mean_a)**2 for v in av)/len(av)
    return mean_a, mean_b, math.sqrt(variance), derivation


def explanation_summary(payload):
    """Summarize attachments for the model without mutating the saved evidence."""
    def compact(value):
        if isinstance(value, list):
            return [compact(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key: compact(item) for key, item in value.items()
                  if key not in {'frames', 'frame_statuses', 'raw_observations', 'accepted_values'}
                  and not (key == 'observations' and isinstance(item, list) and item and isinstance(item[0], dict) and 'time' in item[0])}
        if 'name' in value and 'times' in value and 'values' in value:
            samples = [v for v in value['values'] if v is not None and valid_value(value['name'], v)]
            result.pop('times', None)
            result.pop('values', None)
            result['summary'] = dict(statistic='raw_frame_mean', valid_frames=len(samples),
                value=sum(samples)/len(samples) if samples else None,
                minimum=min(samples) if samples else None, maximum=max(samples) if samples else None)
        if 'pairs' in result:
            result['pairing_count'] = len(result.pop('pairs'))
        return result
    return compact(payload)
