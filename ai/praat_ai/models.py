from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any


@dataclass(slots=True)
class FeatureTrack:
    name: str
    times: list[float]
    values: list[float]
    unit: str = ""
    weight: float = 1.0
    minimum_tolerance: float = 0.05

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["values"] = [value if math.isfinite(value) else None for value in self.values]
        return payload


@dataclass(slots=True)
class PhoneSpec:
    ipa: str
    label: str = ""
    reference_start: float = 0.0
    reference_end: float = 0.0
    learner_start: float | None = None
    learner_end: float | None = None
    alignment_confidence: float = 1.0
    alignment_source: str = ""
    phone_index: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class PhoneFeatureTracks:
    phone_index: int
    ipa: str
    start: float
    end: float
    tracks: dict[str, FeatureTrack] = field(default_factory=dict)
    alignment_confidence: float = 1.0
    alignment_source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "phone_index": self.phone_index,
            "ipa": self.ipa,
            "start": self.start,
            "end": self.end,
            "tracks": {name: track.to_dict() for name, track in self.tracks.items()},
            "alignment_confidence": self.alignment_confidence,
            "alignment_source": self.alignment_source,
        }


@dataclass(slots=True)
class PronunciationError:
    phone_index: int
    ipa: str
    start: float
    end: float
    feature: str
    score: float
    severity: float
    direction: str
    message: str
    reference_value: float
    learner_value: float
    unit: str = ""
    alignment_confidence: float = 1.0
    issue_type: str = "legacy_acoustic_difference"
    rule_id: str = ""
    evaluation_status: str = "experimental"
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AnalysisRequest:
    reference_object: int | str
    learner_object: int | str
    language: str
    phonemes: list[PhoneSpec]
    transcript: str = ""
    error_threshold: float = 1.25
    maximum_errors_per_phone: int = 3
    qwen_explain: bool = True
    qwen_vision: bool = False
    output_prefix: str = "ai"
    profile_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["phonemes"] = [phone.to_dict() for phone in self.phonemes]
        return payload


@dataclass(slots=True)
class AnalysisResult:
    language: str
    reference_name: str
    learner_name: str
    reference_path: str
    learner_path: str
    duration: float
    errors: list[PronunciationError]
    reference_phones: list[PhoneFeatureTracks]
    learner_phones: list[PhoneFeatureTracks]
    explanation: str = ""
    alignment_source: str = ""
    alignment_confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)
    profile_snapshot: dict[str, Any] | None = None
    observations: list[PronunciationError] = field(default_factory=list)
    phone_assessments: list[dict[str, Any]] = field(default_factory=list)
    check_statuses: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "language": self.language,
            "reference_name": self.reference_name,
            "learner_name": self.learner_name,
            "reference_path": self.reference_path,
            "learner_path": self.learner_path,
            "duration": self.duration,
            "errors": [error.to_dict() for error in self.errors],
            "reference_phones": [phone.to_dict() for phone in self.reference_phones],
            "learner_phones": [phone.to_dict() for phone in self.learner_phones],
            "explanation": self.explanation,
            "alignment_source": self.alignment_source,
            "alignment_confidence": self.alignment_confidence,
            "warnings": self.warnings,
            "analysis_mode": "phone_rules" if self.profile_snapshot else "legacy_acoustic_comparison",
            "profile_snapshot": self.profile_snapshot,
            "observations": [item.to_dict() for item in self.observations],
            "phone_assessments": self.phone_assessments,
            "check_statuses": self.check_statuses,
        }


@dataclass(slots=True)
class AlignedPhone:
    phone_index: int
    ipa: str
    start: float
    end: float
    confidence: float = 1.0
    source: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class AlignmentResult:
    phones: list[AlignedPhone]
    source: str
    confidence: float = 1.0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phones": [phone.to_dict() for phone in self.phones],
            "source": self.source,
            "confidence": self.confidence,
            "warnings": self.warnings,
        }
