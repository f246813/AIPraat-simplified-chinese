from __future__ import annotations

from dataclasses import dataclass, field, replace
import threading
from copy import deepcopy
import math
from typing import Any

from . import escape_policy
from .alignment import dtw_align, mean_and_stddev
from .audio import extract_phone_tracks
from .config import AlignmentConfig
from .forced_alignment import (
    AlignmentCancelled,
    CompositeAligner,
    build_aligner,
    supports_cancel,
)
from .models import (
    AnalysisRequest,
    AnalysisResult,
    PhoneFeatureTracks,
    PhoneSpec,
    PronunciationError,
)
from .pronunciation_rules import evaluate_phone_rules, profile_snapshot, resolve_profile
from .normalization import apply_formant_normalization, audio_identity
from .normalization_prosody import apply_prosody_normalization, evaluate_prosody_observations, pitch_settings


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


#: 解释守卫里给一族特征用的可读名：报告守卫按这些名字认量名（F1→formant.1，基频→pitch…）。
_EXPLANATION_LABELS = {"f1": "F1", "f2": "F2", "f3": "F3", "pitch": "基频", "intensity": "强度"}


def _acoustic_evidence(role: str, tracks: list[PhoneFeatureTracks], context_text: str,
                       object_id: Any) -> escape_policy.Evidence:
    """一侧音频的证据行：区间起止、时长，以及每个已测量特征的均值。"""

    rows: list[str] = []
    latest = 0.0
    for item in sorted(tracks, key=lambda phone: (phone.start, phone.phone_index)):
        start = float(item.start)
        end = float(item.end)
        if math.isfinite(start) and math.isfinite(end) and end > start:
            rows.append(f"区间 起点 {start:.3f} 秒")
            rows.append(f"区间 终点 {end:.3f} 秒")
            rows.append(f"时长 {end - start:.3f} 秒")
            latest = max(latest, end)
        for name, track in item.tracks.items():
            values = [value for value in track.values if math.isfinite(value)]
            if not _valid_values(values):
                continue
            mean, _ = mean_and_stddev(values)
            label = _EXPLANATION_LABELS.get(name, name)
            unit = f"{track.unit}".strip()
            rows.append(f"{label} {role}均值 {mean:.3f} {unit}".rstrip())
    if latest > 0:
        rows.append(f"总时长 {latest:.3f} 秒")
    # 签名里必须带侧别：两侧的证据行结构相同，只按工具名+来源会被 ``add_evidence`` 去重丢掉。
    arguments: dict[str, Any] = {"side": role}
    if object_id is not None:
        arguments["object"] = object_id
    return escape_policy.Evidence("pronunciation_acoustics", arguments, rows, context_text)


def explanation_evidence(
    result: AnalysisResult,
    *,
    context_text: str = "",
    reference_object: Any = None,
    learner_object: Any = None,
) -> escape_policy.AnalysisState:
    """把一次纠音分析整理成报告守卫要的证据（与云端 ``state.evidence`` 同形状）。

    每个音素每一侧一条证据，行里写清「特征 + 侧别 + 均值」以及区间起止与时长。
    这样解释里引用的实测值核得上、编造或张冠李戴的数值核不上：解释与云端报告共用
    同一份证据契约，而不是各写一套提示词兜底。
    """

    state = escape_policy.AnalysisState(goal="纠音解释", context_text=str(context_text or ""))
    state.add_evidence(_acoustic_evidence(
        "参考", list(result.reference_phones), state.context_text, reference_object))
    state.add_evidence(_acoustic_evidence(
        "学习者", list(result.learner_phones), state.context_text, learner_object))
    return state


def conclusion_caveat(result: AnalysisResult) -> str:
    """本次对比不完整时要附在解释后的结论等级说明；完整时返回空串。

    解释和 JSON 报告必须说同一件事：``check_statuses`` 说没测全，解释就不能给出
    无条件结论。这里把状态原样写进去，而不是另起一套措辞。
    """

    gaps = []
    for key, label in (("phone_acoustics", "音位声学对比"),
                       ("pitch_formant_measurement", "基频/共振峰测量"),
                       ("independent_phone_recognition", "独立音素识别")):
        status = str(result.check_statuses.get(key) or "")
        if status and status not in {"observed", "ok"}:
            gaps.append(f"{label} {status}")
    if not gaps:
        return ""
    return ("（本次对比不完整：" + "；".join(gaps)
            + "。上面的解释只覆盖已测量的部分，不构成独立音素识别结论，"
              "也不等于发音正确性判定。）")


#: 合并参考/学习者两侧的基频-共振峰测量状态时，越靠前的原因越根本（环境 > 数据 > 音频）。
_MEASUREMENT_STATUS_ORDER = ("dependency_missing", "extraction_failed", "unavailable")


def _measurement_status(*extractions: dict) -> str:
    """把两侧提取状态并成一个结论等级：两侧都好才算 ``ok``。

    「依赖没装」比「这一段音频太短」更根本，所以按固定优先级取第一条非 ``ok``：
    报告里给出的原因必须是这次真的说得通的那一条，不能挑一个好看的说。
    """

    statuses = [str(item.get("pitch_formant_status") or "unknown") for item in extractions]
    for candidate in _MEASUREMENT_STATUS_ORDER:
        if candidate in statuses:
            return candidate
    gaps = [status for status in statuses if status != "ok"]
    if gaps:
        return gaps[0]
    return "ok" if statuses else "unknown"


def _direction(delta: float, unit: str) -> str:
    if abs(delta) <= 1e-9:
        return "无明显偏差"
    direction = "偏高" if delta > 0 else "偏低"
    if unit == "amplitude":
        direction = "偏强" if delta > 0 else "偏弱"
    elif unit in {"ratio", "dB"}:
        direction = "偏大" if delta > 0 else "偏小"
    return direction


@dataclass
class PhoneComparison:
    """一次旧式声学对比的结果：既给偏差，也给「哪些特征根本没测到」。

    旧实现把「参考侧量不出来」当成没发生过（静默 ``continue``），把「学习者侧量
    不出来」当成严重度 1.0、双方数值 0.0 的偏差。两者都会让交付物读起来像
    「已经量过且结论明确」。这里把可测/不可测分开记账，交给上层写进结论等级。
    """

    errors: list[PronunciationError] = field(default_factory=list)
    #: 真正比过的特征名。
    measured: list[str] = field(default_factory=list)
    #: 没能比的特征及原因 ``(特征名, 原因)``。
    unmeasured: list[tuple[str, str]] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.measured and self.unmeasured:
            return "partial"
        if self.measured:
            return "observed"
        return "insufficient_evidence"


def compare_phone_evidence(
    request: AnalysisRequest,
    reference: PhoneFeatureTracks,
    learner: PhoneFeatureTracks,
) -> PhoneComparison:
    """按特征逐个核对可测量性，再对能测的特征做旧式 DTW 对比。"""

    comparison = PhoneComparison()
    for feature_name, reference_track in reference.tracks.items():
        learner_track = learner.tracks.get(feature_name)
        if learner_track is None:
            comparison.unmeasured.append(
                (feature_name, f"/{reference.ipa}/ 的 {feature_name} 缺少学习者侧取值，未比较。")
            )
            continue
        reference_values = [value for value in reference_track.values if math.isfinite(value)]
        learner_values = [value for value in learner_track.values if math.isfinite(value)]
        if not _valid_values(reference_values):
            # 参考侧没有可用取值：这不是「没有差异」，是这次没法比较。
            comparison.unmeasured.append(
                (feature_name, f"/{reference.ipa}/ 的 {feature_name} 缺少参考侧可用取值，未比较。")
            )
            continue
        if not _valid_values(learner_values):
            # 量不出来 ≠ 偏差：不产出 severity，也不填 0.0 冒充双方测量值。
            comparison.unmeasured.append(
                (feature_name, f"/{reference.ipa}/ 的 {feature_name} 无法可靠测量。")
            )
            continue

        aligned_reference, aligned_learner, normalized_distance = dtw_align(
            reference_values,
            learner_values,
        )
        if not aligned_reference:
            comparison.unmeasured.append(
                (feature_name, f"/{reference.ipa}/ 的 {feature_name} 无法对齐，未比较。")
            )
            continue
        comparison.measured.append(feature_name)
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
        comparison.errors.append(
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

    if not comparison.measured and not comparison.unmeasured:
        # 两侧连可比的特征名都没有：这也是「没测成」，要留下可读的原因。
        comparison.unmeasured.append(("acoustics", "本次没有可比较的声学特征。"))
    comparison.errors.sort(key=lambda item: (-item.severity, item.feature))
    comparison.errors = comparison.errors[: request.maximum_errors_per_phone]
    return comparison


def compare_phone(
    request: AnalysisRequest,
    reference: PhoneFeatureTracks,
    learner: PhoneFeatureTracks,
) -> list[PronunciationError]:
    """兼容旧签名：只要偏差条目；可测/不可测的账在 :func:`compare_phone_evidence`。"""

    return compare_phone_evidence(request, reference, learner).errors


def align_learner_phones(
    learner_path: str,
    phones: list[PhoneSpec],
    language: str,
    transcript: str,
    config: AlignmentConfig | None,
    aligner: CompositeAligner | None = None,
    *,
    cancel: threading.Event | None = None,
) -> tuple[list[PhoneSpec], str, float, list[str]]:
    alignment_config = config or AlignmentConfig()
    target = aligner or build_aligner(alignment_config)
    if supports_cancel(target):
        result = target.align(
            learner_path,
            phones,
            language,
            transcript,
            cancel=cancel,
        )
    else:
        # 夹具/老式对齐器没有 cancel 参数：仍然照旧调用，但要说清取消只能发生在步骤之间。
        result = target.align(learner_path, phones, language, transcript)
        if cancel is not None:
            result.warnings.append(
                f"{getattr(target, 'name', 'aligner')}: 该对齐后端不支持取消参数，"
                "只能在步骤之间取消。")
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



def _require_not_cancelled(cancel: threading.Event | None, message: str) -> None:
    """分析阶段的取消检查点：取消是「中止」，不是「失败回退」。"""

    if cancel is not None and cancel.is_set():
        raise AlignmentCancelled(message)


def analyze_pronunciation(
    request: AnalysisRequest,
    alignment_config: AlignmentConfig | None = None,
    *,
    cancel: threading.Event | None = None,
) -> AnalysisResult:
    """一次纠音分析。``cancel`` 会被贯穿到对齐层（MFA 进程树会被终止并抛
    :class:`~praat_ai.forced_alignment.AlignmentCancelled`），分析中途取消不会再产出
    「已完成」的结果。"""

    _require_not_cancelled(cancel, '已取消分析：还没有开始读取音频。')
    rule_profile = resolve_profile(request.profile_id, request.language)
    if request.normalization is not None and not isinstance(request.normalization, dict):
        raise ValueError('归一化设置必须为 normalization-v1 对象。')
    request = replace(request, normalization=deepcopy(request.normalization), phonemes=[
        replace(phone, phone_index=phone.phone_index if phone.phone_index is not None else index)
        for index, phone in enumerate(request.phonemes, start=1)])
    reference_path = str(request.reference_object)
    learner_path = str(request.learner_object)

    from .audio import read_wav

    reference_sound = read_wav(reference_path)
    learner_sound = read_wav(learner_path)
    _require_not_cancelled(cancel, '已取消分析：音频已读取，未开始对齐。')
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
            cancel=cancel,
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
                cancel=cancel,
            )
        )
        warnings.extend(learner_warnings)
        _require_not_cancelled(cancel, '已取消分析：对齐已完成，未继续提取声学特征。')

    measurement_options = ({"pitch_settings": pitch_settings(request.normalization)}
        if rule_profile and request.normalization and request.normalization.get('pitch') is True
            and request.normalization.get('pitch_confirmed') is True else {})
    reference_extraction: dict[str, Any] = {}
    learner_extraction: dict[str, Any] = {}
    reference_tracks = extract_phone_tracks(
        reference_path,
        reference_phones,
        learner=False, diagnostics=reference_extraction, **measurement_options,
    )
    learner_tracks = extract_phone_tracks(
        learner_path,
        learner_phones,
        learner=True, diagnostics=learner_extraction, **measurement_options,
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

    normalization = apply_formant_normalization(
        request.normalization, reference_tracks, learner_tracks, language=rule_profile.language if rule_profile else request.language,
        profile_id=request.profile_id, reading_id=(request.mfa_snapshot or {}).get("preset", {}).get("id", ""),
        target_ids=(audio_identity(reference_sound), audio_identity(learner_sound)) if request.normalization else (),
    )
    if normalization is not None:
        normalization.update(apply_prosody_normalization(request.normalization, reference_tracks, learner_tracks,
            (reference_sound, learner_sound), (reference_path, learner_path),
            language=rule_profile.language if rule_profile else request.language,
            reading_id=(request.mfa_snapshot or {}).get('preset', {}).get('id', request.profile_id or ''),
            applicable=bool(rule_profile)))
        for name in ('pitch', 'loudness'):
            if request.normalization.get(name) and normalization[name]['status'] != 'applied':
                warnings.append(f"{name} 未应用：{normalization[name]['reason']}")
    if normalization and request.normalization.get("formants"):
        for feature, state in normalization["formants"].items():
            if state["status"] != "applied":
                warnings.append(f"{feature} 仅原始 Hz 观察：{state['reason']}")
        if any(state["status"] == "applied" for state in normalization["formants"].values()):
            warnings.append("Lobanov 为独立多元音校准变换；近音 F2/F3 的应用为待真实材料验证的实验性扩展。")

    errors: list[PronunciationError] = []
    observations = []
    assessments = []
    checks = {}
    # 基频/共振峰是否真的测到了：把「没装依赖」「提取失败」和「测了没差异」分开记，
    # 报告才有得可写（见 :func:`praat_ai.audio._open_parselmouth`）。
    checks["pitch_formant_measurement"] = _measurement_status(reference_extraction, learner_extraction)
    for role, extraction in (("参考", reference_extraction), ("学习者", learner_extraction)):
        # 只有真的跑过提取（诊断字典非空）才写这条：夹具里没有诊断就不是「没测到」。
        if extraction and extraction.get("pitch_formant_status") != "ok":
            warnings.append(
                f"基频/共振峰测量（{role}）：{extraction.get('pitch_formant_status')}"
                f"——{extraction.get('pitch_formant_reason', '')}"
            )
    if rule_profile:
        observations, assessments = evaluate_phone_rules(rule_profile, reference_tracks, learner_tracks, normalization=normalization)
        prosody = evaluate_prosody_observations(normalization, reference_tracks, learner_tracks)
        observations.extend(prosody)
        if normalization:
            for name, features in (('pitch', ['pitch_relative_st']), ('loudness', normalization['loudness'].get('feature_ids', []))):
                count = sum(o.feature in features for o in prosody)
                normalization[name]['observation_count'] = count
                normalization[name]['comparison_status'] = ('observed' if count else
                    'insufficient_evidence' if normalization[name]['status'] == 'applied' else normalization[name]['status'])
                checks['relative_' + name + '_observation'] = normalization[name]['comparison_status']
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
        legacy_statuses: list[str] = []
        for reference in reference_tracks:
            learner = learner_by_index.get(reference.phone_index)
            if learner is None:
                # 没有配对的学习者区间：这次没法比较，不是「没有差异」。
                assessments.append(dict(phone_index=reference.phone_index, ipa=reference.ipa,
                    status="insufficient_evidence", checks=[dict(feature="alignment",
                        status="insufficient_evidence", reason="没有与参考音位配对的学习者区间，未评估。")]))
                legacy_statuses.append("insufficient_evidence")
                continue
            if reference.phone_index in invalid_intervals:
                assessments.append(dict(phone_index=reference.phone_index, ipa=reference.ipa,
                    status="insufficient_evidence", checks=[dict(feature="alignment",
                        status="insufficient_evidence", reason="参考或学习者区间无效或超出音频范围，未评估。")]))
                legacy_statuses.append("insufficient_evidence")
                continue
            comparison = compare_phone_evidence(request, reference, learner)
            errors.extend(comparison.errors)
            legacy_statuses.append(comparison.status)
            assessments.append(dict(phone_index=reference.phone_index, ipa=reference.ipa,
                status=comparison.status,
                checks=[dict(feature=name, status="insufficient_evidence", reason=reason)
                        for name, reason in comparison.unmeasured]
                       + [dict(feature=name, status="observed", reason="")
                          for name in comparison.measured]))
        checks["phone_acoustics"] = ("observed" if legacy_statuses and
            all(status == "observed" for status in legacy_statuses) else
            "partial" if "observed" in legacy_statuses else "not_evaluated")

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
        normalization=normalization,
    )
