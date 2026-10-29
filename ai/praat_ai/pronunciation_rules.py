"""Experimental phone-specific acoustic rules, backed by PanPhon's IPA features.

These rules select applicable evidence; they do not infer substitutions without
an independent recognizer or use uncalibrated thresholds to declare errors.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from .measurement_protocol import compatibility, compare_trajectories, valid_samples
from functools import lru_cache

from ._vendor.panphon import FeatureTable
from .alignment import dtw_align, mean_and_stddev
from .models import PhoneFeatureTracks, PronunciationError


@dataclass(frozen=True)
class RuleProfile:
    id: str
    language: str
    name: str
    version: str = "1"
    rules_version: str = "phone-acoustics-v1"
    evaluation_status: str = "experimental"
    feature_source: str = "panphon@a49c242d3307864312a2701055faefa4e548501c"


PROFILES = (
    RuleProfile("en-US-general-v1", "en", "英语（美式）"),
    RuleProfile("ja-JP-standard-v1", "ja", "日语（标准语）"),
)


def resolve_profile(profile_id: str | None, language: str) -> RuleProfile | None:
    if not profile_id:
        return None  # Legacy JSON requests remain legacy, including cmn.
    profile = next((item for item in PROFILES if item.id == profile_id), None)
    if profile is None:
        raise ValueError(f"未知纠音判据预设：{profile_id}")
    code = language.lower().replace("_", "-").split("-")[0]
    code = {"eng": "en", "jpn": "ja"}.get(code, code)
    if code != profile.language:
        raise ValueError(f"预设 {profile.name} 与目标语言 {language} 不匹配。")
    return profile


@lru_cache(maxsize=1)
def _features() -> FeatureTable:
    return FeatureTable()


def phone_class(ipa: str, language: str) -> str:
    # Explicit OpenJTalk labels; raw ipa stays in all results. cl is contextual,
    # so it is not assigned a fabricated single IPA consonant.
    if language == "ja" and ipa == "cl":
        return "geminate"
    mapped = {"N": "ɴ", "I": "i̥", "U": "ɯ̥"}.get(ipa, ipa) if language == "ja" else ipa
    mapped = mapped.replace('ɚ', 'ə˞').replace('ɝ', 'ɜ˞')
    segments = _features().segments(mapped)
    if segments is None:
        return "unknown"
    if all(item["syl"] == "+" and item["cons"] == "-" for item in segments):
        return "vowel"
    if language == "ja" and len(segments) == 1 and segments[0]["long"] == "+":
        return "geminate"
    if language == "ja" and len(segments) == 2 and segments[0] == segments[1]:
        return "geminate"
    if len(segments) == 2 and segments[0]["cont"] == "-" and segments[1]["cont"] == "+" and all(item["son"] == "-" for item in segments):
        return "affricate"  # Untied espeak tʃ/dʒ, as well as PanPhon's tied forms.
    if len(segments) != 1:
        return "unknown"
    features = segments[0]
    if features["nas"] == "+":
        return "nasal"
    if features["delrel"] == "+":
        return "affricate"
    if features["son"] == "-":
        return "fricative" if features["cont"] == "+" else "stop"
    if features["son"] == "+":
        return "approximant"
    return "unknown"


def applicable_features(ipa: str, language: str) -> tuple[str, tuple[str, ...]]:
    category = phone_class(ipa, language)
    features = {
        "vowel": ("f1", "f2"),
        "fricative": ("spectral_centroid", "high_frequency_ratio", "zcr"),
        "affricate": ("spectral_centroid", "high_frequency_ratio"),
        "stop": ("vot",),
        "nasal": ("spectral_centroid",),
        "approximant": ("f2", "f3"),
        "geminate": (),
        "unknown": (),
    }[category]
    return category, features


def _located(phone: PhoneFeatureTracks) -> bool:
    return (
        math.isfinite(phone.start) and math.isfinite(phone.end)
        and phone.start >= 0 and phone.end > phone.start
        and math.isfinite(phone.alignment_confidence)
        and phone.alignment_confidence >= 0.70
        and phone.alignment_source not in {"proportional", "ui_equal_estimate"}
    )


def _sequence_locations(phones: list[PhoneFeatureTracks]) -> list[bool]:
    locations = [_located(phone) for phone in phones]
    for position, (left, right) in enumerate(zip(phones, phones[1:])):
        if left.end > right.start:
            locations[position] = locations[position + 1] = False
    return locations


def _values(phone: PhoneFeatureTracks, feature: str) -> list[float]:
    track = phone.derived_tracks.get(feature) if feature.endswith("_lobanov_z") else phone.tracks.get(feature)
    if track is None:
        return []
    return [value for _, _, value in valid_samples(track)]


def _observation(reference, learner, feature, rule_id, reference_value, learner_value, unit, evidence):
    delta = learner_value - reference_value
    direction = "无明显差异" if abs(delta) <= 1e-9 else ("偏高" if delta > 0 else "偏低")
    if feature == "relative_duration":
        direction = "相对时长相近" if abs(delta) <= 1e-9 else ("相对偏长" if delta > 0 else "相对偏短")
    observation_kind = "时长观察" if feature == "relative_duration" else "声学观察"
    return PronunciationError(
        phone_index=reference.phone_index, ipa=reference.ipa,
        start=learner.start, end=learner.end, feature=feature,
        score=0.0, severity=0.0, direction=direction,
        message=f"/{reference.ipa}/ 的 {feature} {observation_kind}：参考 {reference_value:.3f}{unit}，学习者 {learner_value:.3f}{unit}（{direction}）。",
        reference_value=reference_value, learner_value=learner_value, unit=unit,
        alignment_confidence=min(reference.alignment_confidence, learner.alignment_confidence),
        issue_type="observation", rule_id=rule_id,
        evidence={
            "reference_interval": [reference.start, reference.end],
            "learner_interval": [learner.start, learner.end],
            "reference_alignment_source": reference.alignment_source,
            "learner_alignment_source": learner.alignment_source,
            **evidence,
        },
    )


def evaluate_phone_rules(profile: RuleProfile, references: list[PhoneFeatureTracks], learners: list[PhoneFeatureTracks], *, normalization=None):
    """Return observations plus per-phone evidence coverage, never error scores."""
    pairs = [(reference, learners[position] if position < len(learners) else None)
             for position, reference in enumerate(references)]
    reference_locations = _sequence_locations(references)
    learner_locations = _sequence_locations(learners)
    # Do not remove unreliable phones before pairing: that would shift all
    # following positions. Index labels are independent of sequence positions.
    same_length = len(references) == len(learners)
    learner_labels = {phone.phone_index: phone for phone in learners}
    pair_locations = [same_length and right is not None and left.ipa == right.ipa
                      and reference_locations[position] and learner_locations[position]
                      for position, (left, right) in enumerate(pairs)]
    observations: list[PronunciationError] = []
    assessments = []
    for index, (reference, learner) in enumerate(pairs):
        category, features = applicable_features(reference.ipa, profile.language)
        # When cardinality differs, positions cannot establish a counterpart.
        # Original labels may associate report status, but never enable scoring.
        report_learner = learner if same_length else learner_labels.get(reference.phone_index)
        if report_learner is not None and report_learner.ipa != reference.ipa:
            report_learner = None
        assessment = dict(phone_index=reference.phone_index,
            learner_phone_index=report_learner.phone_index if report_learner is not None else None,
            ipa=reference.ipa, phone_class=category, status="not_evaluated", checks=[])
        assessments.append(assessment)
        checks = assessment["checks"]

        def check(feature, status, reason="", rule_id=None):
            checks.append(dict(feature=feature, rule_id=rule_id or f"{profile.language}.{category}.{feature}", status=status, reason=reason))

        if category == "unknown":
            check("phone_class", "not_evaluated", "未知或未支持的音段；未丢弃 IPA 符号。")
            continue
        if not pair_locations[index]:
            reason = ("缺少可靠定位或同位置音位不匹配；比例估算或低质量区间只用于浏览。"
                      if same_length else "参考和学习者音素数量不一致，无法按位置可靠配对。")
            check("alignment", "insufficient_evidence", reason)
            assessment["status"] = "insufficient_evidence"
            continue
        if not features:
            check("phone_acoustics", "not_applicable", "该音位类别仅适用时长判据，无适用声学特征。")
        derived_features = tuple(feature + "_lobanov_z" for feature in features
            if normalization and normalization.get("formants", {}).get(feature, {}).get("status") == "applied")
        for feature in (*features, *derived_features):
            if feature == "vot":
                left_measure, right_measure = reference.measurements.get("vot"), learner.measurements.get("vot")
                if not all(m and m.unit == "ms" and m.value is not None and math.isfinite(m.value)
                           and m.status in {"validated", "provisional"}
                           and m.analysis.get("acoustic", {}).get("events")
                           for m in (left_measure, right_measure)):
                    reasons = [m.reason for m in (left_measure, right_measure) if m and m.reason]
                    check(feature, "insufficient_evidence", "；".join(dict.fromkeys(reasons)) or
                          "当前提取器未提供充分的爆破与起浊地标；未估造 VOT。")
                    continue
                status = "validated" if left_measure.status == right_measure.status == "validated" else "provisional"
                evidence = dict(method="landmark_difference", status=status,
                    reference_measurement_id=left_measure.analysis.get("measurement_id"),
                    learner_measurement_id=right_measure.analysis.get("measurement_id"),
                    reference_landmarks=left_measure.analysis["acoustic"],
                    learner_landmarks=right_measure.analysis["acoustic"])
                observation = _observation(reference, learner, feature, f"{profile.language}.stop.vot",
                    left_measure.value, right_measure.value, "ms", evidence)
                observation.evaluation_status = status
                if status == "provisional":
                    observation.message += " 自动地标及闭塞归属仍为候选，未作发音诊断。"
                observations.append(observation)
                check(feature, "observed" if status == "validated" else "provisional",
                      "" if status == "validated" else "自动地标候选，闭塞归属需复核。")
                continue
            left, right = _values(reference, feature), _values(learner, feature)
            rule_id = f"{profile.language}.{category}.{feature}"
            if len(left) < 2 or len(right) < 2:
                reason = "缺少有效声学帧，未用零值替代测量，也未推断漏音。"
                if feature == "vot":
                    reason = "当前提取器未提供爆破与起浊测量；未估造 VOT。"
                check(feature, "insufficient_evidence", reason)
                continue
            derived = feature.endswith("_lobanov_z")
            left_track = (reference.derived_tracks if derived else reference.tracks)[feature]
            right_track = (learner.derived_tracks if derived else learner.tracks)[feature]
            incompatible = compatibility(left_track, right_track)
            if incompatible:
                check(feature, "insufficient_evidence", incompatible)
                continue
            left_mean, right_mean, _, derivation = compare_trajectories(left_track, right_track)
            observations.append(_observation(reference, learner, feature, rule_id, left_mean, right_mean, left_track.unit, dict(
                **derivation,
                **(dict(normalization_method=normalization["method"], normalization_version=normalization["version"],
                    reference_calibration_id=normalization["reference_calibration"]["id"],
                    learner_calibration_id=normalization["learner_calibration"]["id"],
                    experimental_approximant_extension=category == "approximant") if derived else {}),
            )))
            check(feature, "observed")

        # Normalize against adjacent, matched, reliably located phones. This is
        # phone-relative duration, not an invented Japanese mora alignment.
        neighbor_positions = [position for position in (index - 1, index + 1) if 0 <= position < len(pairs)]
        neighbors = [pairs[position] for position in neighbor_positions]
        duration_id = f"{profile.language}.{category}.relative_duration"
        if profile.language == "ja" and category == "vowel" and ("ː" in reference.ipa or "ˑ" in reference.ipa):
            duration_id = "ja.long_vowel.relative_duration"
        if not neighbors or any(not pair_locations[position] for position in neighbor_positions):
            check("relative_duration", "insufficient_evidence", "缺少相邻音素的可靠区间，无法按局部语速比较。", duration_id)
        else:
            reference_baseline = sum(left.end - left.start for left, _ in neighbors) / len(neighbors)
            learner_baseline = sum(right.end - right.start for _, right in neighbors) / len(neighbors)
            observations.append(_observation(reference, learner, "relative_duration", duration_id,
                (reference.end - reference.start) / reference_baseline,
                (learner.end - learner.start) / learner_baseline, "ratio", dict(
                    method="adjacent_phone_duration_ratio",
                    neighbor_phone_indices=[left.phone_index for left, _ in neighbors],
                    reference_duration_sec=reference.end - reference.start,
                    learner_duration_sec=learner.end - learner.start,
                    reference_baseline_sec=reference_baseline,
                    learner_baseline_sec=learner_baseline,
                    mora_structure_available=False,
                )))
            check("relative_duration", "observed", rule_id=duration_id)
        statuses = {item["status"] for item in checks if item["status"] != "not_applicable"}
        if not features and "observed" in statuses:
            assessment["status"] = "duration_only"
        else:
            assessment["status"] = "observed" if statuses == {"observed"} else ("partial" if "observed" in statuses else "insufficient_evidence")
    return observations, assessments


def profile_snapshot(profile: RuleProfile) -> dict:
    return {**asdict(profile), "thresholds_calibrated": False, "independent_recognition_available": False}
