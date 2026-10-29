"""normalization-v1: independent vowel calibration, never fit on target phones.

The inventories/window/token count below are provisional sampling protocols,
not validated accuracy thresholds. Pitch/loudness live in normalization_prosody.py.
Raw tracks and the legacy comparison remain untouched.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import math
from pathlib import Path

import numpy as np

from ._vendor.vowspace import fit_lobanov, transform_lobanov
from .models import FeatureTrack, PhoneFeatureTracks, PhoneSpec

VERSION = "normalization-v1"
METHOD = "lobanov_independent_vowel_calibration"
SOURCE = "VowSpace@3143f636c56cb34e26cc83da0f425494bb3f9c94/vowspace/core/normalization.py"
PROTOCOL = "balanced-monophthong-middle-40-60-three-v1"
# Only these actual rule presets have a sampling protocol. No UK/US inference.
INVENTORIES = {
    "ja-JP-standard-v1": ("a", "i", "ɯ", "e", "o"),
    "en-US-general-v1": ("i", "ɪ", "ɛ", "æ", "ɑ", "ɔ", "ʊ", "u", "ʌ"),
}
MEASUREMENT_SETTINGS = {
    "extractor": "praat-parselmouth-burg", "time_step_sec": 0.01,
    "max_number_of_formants": 5.0, "maximum_formant_hz": 5500.0,
    "window_length_sec": 0.025, "pre_emphasis_from_hz": 50.0,
    "sampling": "burg-observation-centres-v2",
}
FORMANTS = ("f1", "f2", "f3")


def resolve_measurement_settings(options: dict | None, role: str) -> dict:
    """Resolve each speaker's explicit Burg preset, without demographic inference."""
    if role not in {"reference", "learner"}:
        raise ValueError("未知说话人角色。")
    supplied = (options or {}).get("measurement_settings", {})
    if not isinstance(supplied, dict) or supplied.keys() - {"reference", "learner"}:
        raise ValueError("测量设置必须按 reference/learner 保存。")
    selected = supplied.get(role, {})
    if not isinstance(selected, dict) or selected.keys() - MEASUREMENT_SETTINGS.keys():
        raise ValueError("未知 Burg 测量参数。")
    result = {**MEASUREMENT_SETTINGS, **selected}
    for key in ("extractor", "sampling"):
        if result[key] != MEASUREMENT_SETTINGS[key]:
            raise ValueError("共振峰算法或采样协议不支持。")
    for key, low, high in (("maximum_formant_hz", 1000., 20000.), ("max_number_of_formants", 3., 10.),
                           ("time_step_sec", .001, .1), ("window_length_sec", .005, .1),
                           ("pre_emphasis_from_hz", 0., 2000.)):
        value = result[key]
        if isinstance(value, bool):
            raise ValueError("Burg 参数必须为有限数字。")
        try:
            value = float(value)
        except (ValueError, TypeError):
            raise ValueError("Burg 参数必须为有限数字。") from None
        if not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"{key} 必须在 {low}–{high} 之间。")
        result[key] = value
    return result


def audio_identity(sound) -> str:
    """Identity independent of WAV headers; prevents self-calibration by rename."""
    digest = hashlib.sha256(str(sound.sample_rate).encode("ascii"))
    digest.update(np.asarray(sound.samples, dtype="<f8").tobytes())
    return digest.hexdigest()


def _state(status, reason="", **extra):
    return dict(status=status, reason=reason, **extra)


def fit_formant_calibration(phones: list[PhoneFeatureTracks], *, speaker_label: str,
                            language: str, profile_id: str, reading_id: str = "",
                            measurement_settings: dict | None = None,
                            material: dict | None = None) -> dict:
    """Take each token's middle-20% median, then exactly three per vowel/formant.

    F3 coverage/variance is evaluated separately. A missing/undefined frame is
    never replaced with zero. Extra tokens do not overweight any vowel.
    """
    inventory = INVENTORIES.get(profile_id, ())
    settings = deepcopy(MEASUREMENT_SETTINGS if measurement_settings is None else measurement_settings)
    result = dict(version=VERSION, method=METHOD, algorithm_source=SOURCE, protocol=PROTOCOL,
                  speaker_label=speaker_label.strip(), language=language, profile_id=profile_id,
                  reading_id=reading_id or profile_id, inventory=list(inventory), tokens_per_vowel=3,
                  window=[0.4, 0.6], ddof=1, measurement_settings=settings,
                  material=deepcopy(material or {}), formants={})
    for feature in FORMANTS:
        tokens = {vowel: [] for vowel in inventory}
        for phone in sorted(phones, key=lambda p: p.start):
            if (phone.ipa not in tokens or not math.isfinite(phone.start)
                    or not math.isfinite(phone.end) or phone.end <= phone.start
                    or not math.isfinite(phone.alignment_confidence) or phone.alignment_confidence < 0.7
                    or phone.alignment_source in {"proportional", "ui_equal_estimate"}):
                continue
            track = phone.tracks.get(feature)
            if not track or track.unit != "Hz" or len(track.times) != len(track.values):
                continue
            low, high = (phone.start + fraction * (phone.end - phone.start) for fraction in (0.4, 0.6))
            values = [value for time, value in zip(track.times, track.values)
                      if math.isfinite(time) and low <= time <= high and math.isfinite(value) and value > 0]
            if values:
                tokens[phone.ipa].append(dict(phone_index=phone.phone_index, start=phone.start,
                    end=phone.end, value_hz=float(np.median(values)), valid_frames=len(values)))
        counts = {vowel: len(items) for vowel, items in tokens.items()}
        selected = {vowel: items[:3] for vowel, items in tokens.items()}
        details = dict(coverage=counts, selected_tokens=selected)
        if not inventory or len(inventory) < 2:
            state = _state("not_applicable", "当前读音预设没有固定校准清单。", **details)
        elif not result["speaker_label"]:
            state = _state("insufficient_evidence", "缺少说话人标签。", **details)
        elif any(count < 3 for count in counts.values()):
            missing = "、".join(f"/{vowel}/ {count}/3" for vowel, count in counts.items() if count < 3)
            state = _state("insufficient_evidence", "有效单元音 token 不足：" + missing, **details)
        else:
            try:
                mean, std = fit_lobanov([token["value_hz"] for items in selected.values() for token in items])
                state = _state("applied", mu_hz=mean, sigma_hz=std, **details)
            except ValueError:
                state = _state("insufficient_evidence", "校准方差为零或非有限。", **details)
        result["formants"][feature] = state
    result["status"] = ("applied" if all(result["formants"][f]["status"] == "applied" for f in ("f1", "f2"))
                        else "insufficient_evidence")
    # Content-addressed snapshot, not a mutable registry reference.
    import json
    result["id"] = hashlib.sha256(json.dumps(result, sort_keys=True, ensure_ascii=False,
                                            allow_nan=False).encode("utf-8")).hexdigest()
    return result


def load_formant_material(wav_path, textgrid_path):
    """Read a user-supplied independent WAV and unique IPA IntervalTier."""
    try:
        import parselmouth
        from parselmouth.praat import call
    except ImportError as exc:
        from . import capabilities
        detail = capabilities.blocker("formants")
        raise ValueError(
            detail or "共振峰校准需要 praat-parselmouth，请通过现有“配置运行库…”入口检查。"
        ) from exc
    from .audio import read_wav

    wav, grid_path = Path(wav_path), Path(textgrid_path)
    if wav.suffix.lower() != ".wav" or grid_path.suffix.lower() != ".textgrid":
        raise ValueError("校准材料必须为 WAV 和带 IPA 标签的 TextGrid。")
    sound = read_wav(wav)
    grid = parselmouth.read(str(grid_path))
    if grid.class_name != "TextGrid":
        raise ValueError("标签文件不是 TextGrid。")
    tiers = [i for i in range(1, int(call(grid, "Get number of tiers")) + 1)
             if call(grid, "Get tier name", i).lower() in {"phones", "phone", "phonemes", "phoneme"}
             and call(grid, "Is interval tier", i)]
    if len(tiers) != 1:
        raise ValueError("TextGrid 需要唯一的 phones/phonemes IPA 区间层；不自动猜测标签体系。")
    tier = tiers[0]
    phones = []
    for i in range(1, int(call(grid, "Get number of intervals", tier)) + 1):
        label = call(grid, "Get label of interval", tier, i).strip()
        start = float(call(grid, "Get start time of interval", tier, i))
        end = float(call(grid, "Get end time of interval", tier, i))
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= sound.duration + 1e-7):
            raise ValueError("校准 TextGrid 的区间无效或超出 WAV 范围。")
        if label:
            phones.append(PhoneSpec(ipa=label, reference_start=start, reference_end=end,
                                    alignment_source="calibration_textgrid", phone_index=i))
    material = dict(wav_name=wav.name, textgrid_name=grid_path.name, audio_identity=audio_identity(sound),
        wav_sha256=hashlib.sha256(wav.read_bytes()).hexdigest(),
        textgrid_sha256=hashlib.sha256(grid_path.read_bytes()).hexdigest(),
        annotation_source='user_supplied_ipa_textgrid')
    return sound, phones, material


def import_formant_calibration(wav_path, textgrid_path, *, speaker_label, language,
                               profile_id, reading_id="", measurement_settings=None) -> dict:
    from .audio import extract_phone_tracks
    _, phones, material = load_formant_material(wav_path, textgrid_path)
    settings = resolve_measurement_settings({'measurement_settings': {'reference': measurement_settings or {}}}, 'reference')
    tracks = extract_phone_tracks(wav_path, phones, learner=False, formant_settings=settings)
    return fit_formant_calibration(tracks, speaker_label=speaker_label, language=language,
        profile_id=profile_id, reading_id=reading_id, measurement_settings=settings, material=material)


def transform_formants(phones: list[PhoneFeatureTracks], calibration: dict,
                       features=FORMANTS) -> None:
    """Attach derived tracks, preserving raw Hz tracks and undefined positions."""
    for feature in features:
        parameters = calibration["formants"][feature]
        if parameters["status"] != "applied":
            continue
        for phone in phones:
            track = phone.tracks.get(feature)
            if not track or track.unit != "Hz":
                continue
            values = [v if math.isfinite(v) and v > 0 else math.nan for v in track.values]
            name = feature + "_lobanov_z"
            phone.derived_tracks[name] = FeatureTrack(name, list(track.times),
                transform_lobanov(values, parameters["mu_hz"], parameters["sigma_hz"]).tolist(),
                unit="z", minimum_tolerance=0.0, analysis=dict(protocol=PROTOCOL,
                    algorithm=METHOD, calibration_id=calibration['id'],
                    input_measurement_ids=[track.analysis.get('measurement_id')],
                    formula="(raw_hz - calibration_mu_hz) / calibration_sigma_hz"))


def _calibration_reason(calibration, language, profile_id, reading_id, target_ids, expected_settings=None):
    if not isinstance(calibration, dict):
        return "未导入该说话人的独立校准。"
    if (calibration.get("version") != VERSION or calibration.get("method") != METHOD
            or calibration.get("protocol") != PROTOCOL or calibration.get("ddof") != 1
            or calibration.get("tokens_per_vowel") != 3 or calibration.get("window") != [0.4, 0.6]
            or calibration.get("inventory") != list(INVENTORIES.get(profile_id, ()))):
        return "校准版本或采样协议不匹配。"
    if (calibration.get("language") != language or calibration.get("profile_id") != profile_id
            or calibration.get("reading_id") != (reading_id or profile_id)):
        return "校准语言或读音预设不匹配，请重新导入。"
    if calibration.get("measurement_settings") != (expected_settings or MEASUREMENT_SETTINGS):
        return "校准测量设置不匹配，请重新提取。"
    if not calibration.get("speaker_label"):
        return "缺少校准说话人标签。"
    if not isinstance(calibration.get("id"), str) or not calibration["id"]:
        return "缺少冻结校准记录标识。"
    material = calibration.get("material")
    material_id = material.get("audio_identity") if isinstance(material, dict) else None
    if not material_id:
        return "缺少独立校准材料标识。"
    if material_id in target_ids:
        return "校准与待测录音相同，目标音段不得参与自身基线拟合。"
    return ""


def apply_formant_normalization(options: dict | None, references, learners, *, language,
                                profile_id, reading_id="", target_ids=()) -> dict | None:
    """Validate frozen metadata, then pair ONLY matching methods and units."""
    for phone in (*references, *learners):
        for feature in FORMANTS:
            phone.derived_tracks.pop(feature + "_lobanov_z", None)
    if options is None:
        return None  # Old JSON: no normalization metadata, no changed behavior.
    if not isinstance(options, dict):
        raise ValueError("归一化选项必须为 normalization-v1 对象。")
    options = deepcopy(options)
    result = dict(version=VERSION, method=METHOD, algorithm_source=SOURCE,
                  reference_calibration=options.get("reference_calibration"),
                  learner_calibration=options.get("learner_calibration"), formants={},
                  pitch=_state("disabled", "相对音高未启用。"),
                  loudness=_state("disabled", "响度归一化未启用。"))
    result['measurement_settings'] = {role: resolve_measurement_settings(options, role) for role in ('reference', 'learner')}
    result['formant_selections'] = deepcopy(options.get('formant_selections', {}))
    reason, status = "", "insufficient_evidence"
    if not profile_id:
        status, reason = "not_applicable", "旧式统一阈值路径仅使用原始测量。"
    elif options.get("formants") is not True:
        status, reason = "disabled", "共振峰归一化已关闭。"
    elif options.get("version") != VERSION:
        reason = "归一化配置版本不匹配。"
    elif options.get("confirmed") is not True:
        reason = "未确认双方校准与本次说话人、语言/读音及测量设置一致。"
    elif profile_id not in INVENTORIES:
        status, reason = "not_applicable", "当前预设没有固定元音校准清单。"
    for feature in FORMANTS:
        problems = [reason] if reason else []
        for role, label in (("reference", "标准说话人"), ("learner", "学习者")):
            if reason:
                break
            calibration = result[role + "_calibration"]
            problem = _calibration_reason(calibration, language, profile_id, reading_id, target_ids,
                                          result['measurement_settings'][role])
            if not problem:
                formants = calibration.get("formants")
                state = formants.get(feature, {}) if isinstance(formants, dict) else {}
                state = state if isinstance(state, dict) else {}
                mu, sigma = state.get("mu_hz"), state.get("sigma_hz")
                counts = state.get("coverage", {})
                if (state.get("status") != "applied" or not all(
                        isinstance(v, (float, int)) and not isinstance(v, bool) and math.isfinite(v) for v in (mu, sigma))
                        or sigma <= 0 or not isinstance(counts, dict) or any(
                            not isinstance(counts.get(v), int) or counts[v] < 3 for v in INVENTORIES[profile_id])):
                    problem = state.get("reason") or "有效元音覆盖不足、参数无效或方差为零。"
            if problem:
                problems.append(label + "：" + problem)
        valid_frames = {}
        for role, phones in (("reference", references), ("learner", learners)):
            valid_frames[role] = sum(sum(math.isfinite(v) and v > 0 for v in track.values)
                for phone in phones if (track := phone.tracks.get(feature)) is not None and track.unit == "Hz")
        if not problems and any(count < 2 for count in valid_frames.values()):
            problems.append("待测双方缺少有效共振峰测量帧，未应用该派生比较。")
        result["formants"][feature] = _state(status if reason else ("insufficient_evidence" if problems else "applied"),
            "；".join(problems), feature_id=feature + "_lobanov_z", unit="z", valid_target_frames=valid_frames)
    applicable = [f for f in FORMANTS if result["formants"][f]["status"] == "applied"]
    if applicable:
        transform_formants(references, result["reference_calibration"], applicable)
        transform_formants(learners, result["learner_calibration"], applicable)
    return result
