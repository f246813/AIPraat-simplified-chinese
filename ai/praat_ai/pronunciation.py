from __future__ import annotations

from dataclasses import replace
import math

from .alignment import dtw_align, mean_and_stddev
from .audio import extract_phone_tracks
from .config import AlignmentConfig
from .forced_alignment import CompositeAligner, build_aligner
from .models import (
    AnalysisRequest,
    AnalysisResult,
    PhoneFeatureTracks,
    PhoneSpec,
    PronunciationError,
)
from .pronunciation_rules import evaluate_phone_rules, profile_snapshot, resolve_profile


def infer_learner_segments(
    phones: list[PhoneSpec],
    learner_duration: float,
) -> list[PhoneSpec]:
    if not phones:
        return []
    reference_start = min(phone.reference_start for phone in phones)
    reference_end = max(phone.reference_end for phone in phones)
    reference_duration = max(reference_end - reference_start, 1e-9)
    scale = learner_duration / reference_duration
    result: list[PhoneSpec] = []
    for phone in phones:
        result.append(
            PhoneSpec(
                ipa=phone.ipa,
                label=phone.label,
                phone_index=phone.phone_index,
                reference_start=phone.reference_start,
                reference_end=phone.reference_end,
                learner_start=max(
                    0.0,
                    (phone.reference_start - reference_start) * scale,
                ),
                learner_end=min(
                    learner_duration,
                    (phone.reference_end - reference_start) * scale,
                ),
            )
        )
    return result


def _valid_values(values: list[float]) -> bool:
    if len(values) < 2:
        return False
    return any(abs(value) > 1e-9 for value in values)


def _direction(delta: float, unit: str) -> str:
    if abs(delta) <= 1e-9:
        return "无明显偏差"
    direction = "偏高" if delta > 0 else "偏低"
    if unit == "amplitude":
        direction = "偏强" if delta > 0 else "偏弱"
    elif unit in {"ratio", "dB"}:
        direction = "偏大" if delta > 0 else "偏小"
    return direction


def compare_phone(
    request: AnalysisRequest,
    reference: PhoneFeatureTracks,
    learner: PhoneFeatureTracks,
) -> list[PronunciationError]:
    errors: list[PronunciationError] = []
    for feature_name, reference_track in reference.tracks.items():
        learner_track = learner.tracks.get(feature_name)
        if learner_track is None:
            continue
        reference_values = [value for value in reference_track.values if math.isfinite(value)]
        learner_values = [value for value in learner_track.values if math.isfinite(value)]
        if not _valid_values(reference_values):
            continue
        if not _valid_values(learner_values):
            errors.append(
                PronunciationError(
                    phone_index=reference.phone_index,
                    ipa=reference.ipa,
                    start=learner.start,
                    end=learner.end,
                    feature=feature_name,
                    score=max(2.0, request.error_threshold * 1.5),
                    severity=1.0,
                    direction="缺失或无法测量",
                    message=f"{reference.ipa} 的 {feature_name} 无法可靠测量。",
                reference_value=0.0,
                learner_value=0.0,
                unit=reference_track.unit,
                alignment_confidence=learner.alignment_confidence,
            )
            )
            continue

        aligned_reference, aligned_learner, normalized_distance = dtw_align(
            reference_values,
            learner_values,
        )
        if not aligned_reference:
            continue
        reference_mean, reference_stddev = mean_and_stddev(aligned_reference)
        learner_mean, _ = mean_and_stddev(aligned_learner)
        delta = learner_mean - reference_mean
        tolerance = max(reference_track.minimum_tolerance, reference_stddev)
        score = abs(delta) / max(tolerance, 1e-9)
        score = max(score, normalized_distance / max(tolerance, 1e-9))
        if score < request.error_threshold:
            continue

        severity = min(1.5, score / max(request.error_threshold, 1e-9))
        direction = _direction(delta, reference_track.unit)
        uncertainty = ""
        if learner.alignment_confidence < 0.70:
            uncertainty = (
                f"（对齐置信度 {learner.alignment_confidence:.2f}，"
                "建议复核该区间）"
            )
        errors.append(
            PronunciationError(
                phone_index=reference.phone_index,
                ipa=reference.ipa,
                start=learner.start,
                end=learner.end,
                feature=feature_name,
                score=round(score, 4),
                severity=round(severity, 4),
                direction=direction,
                message=(
                    f"/{reference.ipa}/ 的 {feature_name} {direction}："
                    f"参考 {reference_mean:.3f}{reference_track.unit}，"
                    f"学习者 {learner_mean:.3f}{reference_track.unit}。"
                    f"{uncertainty}"
                ),
                reference_value=round(reference_mean, 6),
                learner_value=round(learner_mean, 6),
                unit=reference_track.unit,
                alignment_confidence=learner.alignment_confidence,
            )
        )

    errors.sort(key=lambda item: (-item.severity, item.feature))
    return errors[: request.maximum_errors_per_phone]


def align_learner_phones(
    learner_path: str,
    phones: list[PhoneSpec],
    language: str,
    transcript: str,
    config: AlignmentConfig | None,
    aligner: CompositeAligner | None = None,
) -> tuple[list[PhoneSpec], str, float, list[str]]:
    alignment_config = config or AlignmentConfig()
    result = (aligner or build_aligner(alignment_config)).align(
        learner_path,
        phones,
        language,
        transcript,
    )
    aligned_by_index = {phone.phone_index: phone for phone in result.phones}
    aligned_phones: list[PhoneSpec] = []
    for index, phone in enumerate(phones, start=1):
        # Aligner indices address its input positions. Keep the original label
        # when a backend omits an input phone from its result.
        aligned = aligned_by_index.get(index)
        if aligned is None:
            continue
        aligned_phones.append(
            PhoneSpec(
                ipa=phone.ipa,
                label=phone.label,
                phone_index=phone.phone_index if phone.phone_index is not None else index,
                reference_start=phone.reference_start,
                reference_end=phone.reference_end,
                learner_start=aligned.start,
                learner_end=aligned.end,
                alignment_confidence=aligned.confidence,
                alignment_source=aligned.source,
            )
        )
    return (
        aligned_phones,
        result.source,
        result.confidence,
        list(result.warnings),
    )


def _has_reference_boundaries(phones: list[PhoneSpec]) -> bool:
    return all(phone.reference_end > phone.reference_start for phone in phones)


def _has_learner_boundaries(phones: list[PhoneSpec]) -> bool:
    return all(
        phone.learner_start is not None
        and phone.learner_end is not None
        and phone.learner_end > phone.learner_start
        for phone in phones
    )


def _proportional_seeds(phones: list[PhoneSpec], duration: float) -> list[PhoneSpec]:
    step = duration / len(phones) if phones else 0.0
    return [
        replace(
            phone,
            reference_start=index * step,
            reference_end=(index + 1) * step,
        )
        for index, phone in enumerate(phones)
    ]


def _same_audio_samples(reference_sound, learner_sound) -> bool:
    if (
        reference_sound.sample_rate != learner_sound.sample_rate
        or reference_sound.samples.shape != learner_sound.samples.shape
    ):
        return False
    reference_samples = reference_sound.samples.reshape(-1)
    learner_samples = learner_sound.samples.reshape(-1)
    chunk_size = 1_000_000
    return all(
        bool(
            (
                reference_samples[offset : offset + chunk_size]
                == learner_samples[offset : offset + chunk_size]
            ).all()
        )
        for offset in range(0, reference_samples.size, chunk_size)
    )


def analyze_pronunciation(
    request: AnalysisRequest,
    alignment_config: AlignmentConfig | None = None,
) -> AnalysisResult:
    rule_profile = resolve_profile(request.profile_id, request.language)
    request = replace(request, phonemes=[
        replace(phone, phone_index=phone.phone_index if phone.phone_index is not None else index)
        for index, phone in enumerate(request.phonemes, start=1)])
    reference_path = str(request.reference_object)
    learner_path = str(request.learner_object)

    from .audio import read_wav

    reference_sound = read_wav(reference_path)
    learner_sound = read_wav(learner_path)
    needs_reference_alignment = (
        not _has_reference_boundaries(request.phonemes)
        or any(
            phone.alignment_source == "ui_equal_estimate"
            for phone in request.phonemes
        )
    )
    has_learner_boundaries = _has_learner_boundaries(request.phonemes)
    same_audio = reference_path == learner_path or _same_audio_samples(
        reference_sound, learner_sound
    )
    aligner: CompositeAligner | None = None
    warnings: list[str] = []
    if needs_reference_alignment and same_audio and has_learner_boundaries:
        reference_phones = [
            replace(
                phone,
                reference_start=phone.learner_start,
                reference_end=phone.learner_end,
                alignment_source="provided_learner_same_audio",
            )
            for phone in request.phonemes
        ]
    elif needs_reference_alignment:
        aligner = build_aligner(alignment_config or AlignmentConfig())
        seeds = (
            request.phonemes
            if _has_reference_boundaries(request.phonemes)
            else _proportional_seeds(request.phonemes, reference_sound.duration)
        )
        aligned_reference, _, _, reference_warnings = align_learner_phones(
            reference_path,
            seeds,
            request.language,
            request.transcript,
            alignment_config,
            aligner,
        )
        reference_phones = [
            replace(
                phone,
                reference_start=phone.learner_start,
                reference_end=phone.learner_end,
                learner_start=None,
                learner_end=None,
            )
            for phone in aligned_reference
        ]
        warnings.extend(f"Reference alignment: {warning}" for warning in reference_warnings)
    else:
        reference_phones = request.phonemes

    if same_audio:
        learner_phones = [
            replace(
                phone,
                learner_start=phone.reference_start,
                learner_end=phone.reference_end,
            )
            for phone in reference_phones
        ]
        alignment_source = "same_audio"
        alignment_confidence = min(
            (phone.alignment_confidence for phone in learner_phones), default=1.0
        )
    elif has_learner_boundaries:
        learner_phones = request.phonemes
        alignment_source = "provided"
        alignment_confidence = min(
            (phone.alignment_confidence for phone in learner_phones), default=1.0
        )
    else:
        learner_phones, alignment_source, alignment_confidence, learner_warnings = (
            align_learner_phones(
                learner_path,
                reference_phones,
                request.language,
                request.transcript,
                alignment_config,
                aligner,
            )
        )
        warnings.extend(learner_warnings)

    reference_tracks = extract_phone_tracks(
        reference_path,
        reference_phones,
        learner=False,
    )
    learner_tracks = extract_phone_tracks(
        learner_path,
        learner_phones,
        learner=True,
    )

    invalid_intervals = set()
    for role, tracks, duration in (("参考", reference_tracks, reference_sound.duration),
                                   ("学习者", learner_tracks, learner_sound.duration)):
        for track in tracks:
            if not (math.isfinite(track.start) and math.isfinite(track.end)
                    and 0 <= track.start < track.end <= duration):
                invalid_intervals.add(track.phone_index)
                track.alignment_confidence = 0.0
                warnings.append(f"{role} /{track.ipa}/ 的区间无效或超出音频范围，未评估。")

    errors: list[PronunciationError] = []
    observations = []
    assessments = []
    checks = {}
    if rule_profile:
        observations, assessments = evaluate_phone_rules(rule_profile, reference_tracks, learner_tracks)
        for feature, name in (("relative_duration", "relative_duration"), (None, "phone_acoustics")):
            statuses = [check["status"] for assessment in assessments for check in assessment["checks"]
                        if (check["feature"] == feature if feature else check["feature"] not in {"relative_duration", "phone_class", "alignment"})]
            not_applicable = bool(statuses) and all(status == "not_applicable" for status in statuses)
            statuses = [status for status in statuses if status != "not_applicable"]
            if any(check["feature"] in {"alignment", "phone_class"} for assessment in assessments for check in assessment["checks"]):
                statuses.append("insufficient_evidence")
            checks[name] = ("not_applicable" if not_applicable and not statuses else
                            "observed" if statuses and all(status == "observed" for status in statuses) else
                            "partial" if "observed" in statuses else "not_evaluated")
        checks["independent_phone_recognition"] = "not_evaluated"
        checks["stress_or_pitch_accent"] = "not_evaluated"
        if rule_profile.language == "ja":
            checks["mora_structure"] = "not_evaluated"
        warnings.append("音位判据为实验性声学观察；未执行独立音素识别，未校准判错阈值。")
    else:
        learner_by_index = {item.phone_index: item for item in learner_tracks}
        for reference in reference_tracks:
            learner = learner_by_index.get(reference.phone_index)
            if learner is not None:
                if reference.phone_index in invalid_intervals:
                    assessments.append(dict(phone_index=reference.phone_index, ipa=reference.ipa,
                        status="insufficient_evidence", checks=[dict(feature="alignment",
                            status="insufficient_evidence", reason="参考或学习者区间无效或超出音频范围，未评估。")]))
                    continue
                errors.extend(compare_phone(request, reference, learner))

    return AnalysisResult(
        language=request.language,
        reference_name=reference_path,
        learner_name=learner_path,
        reference_path=reference_path,
        learner_path=learner_path,
        duration=learner_sound.duration,
        errors=errors,
        reference_phones=reference_tracks,
        learner_phones=learner_tracks,
        alignment_source=alignment_source,
        alignment_confidence=alignment_confidence,
        warnings=warnings,
        profile_snapshot=profile_snapshot(rule_profile) if rule_profile else None,
        observations=observations,
        phone_assessments=assessments,
        check_statuses=checks,
    )
