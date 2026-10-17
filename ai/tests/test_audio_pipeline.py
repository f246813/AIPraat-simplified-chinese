import math
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from praat_ai.models import AlignedPhone, AlignmentResult, AnalysisRequest, PhoneSpec
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.audio import extract_phone_tracks
from praat_ai.pronunciation_rules import PROFILES
from praat_ai.report import write_textgrid
import parselmouth
from parselmouth.praat import call
from praat_ai.tutor import request_from_dict
from praat_ai.ui import _equal_segments


def write_tone(path: Path, sample_rate: int, frequency: float) -> None:
    times = np.arange(int(sample_rate * 0.2)) / sample_rate
    samples = (12000 * np.sin(2 * math.pi * frequency * times)).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def write_two_tones(path: Path) -> None:
    sample_rate = 16000
    first = np.arange(int(sample_rate * 0.2)) / sample_rate
    second = np.arange(int(sample_rate * 0.8)) / sample_rate
    samples = np.concatenate(
        [np.sin(2 * math.pi * 220.0 * first), np.sin(2 * math.pi * 2500.0 * second)]
    )
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes((samples * 12000).astype("<i2").tobytes())


class FixedBoundaryAligner:
    def align(self, audio_path, phones, language, transcript=""):
        del audio_path, language, transcript
        return AlignmentResult(
            phones=[
                AlignedPhone(1, phones[0].ipa, 0.0, 0.2, 0.9, "verified"),
                AlignedPhone(2, phones[1].ipa, 0.2, 1.0, 0.9, "verified"),
            ],
            source="verified",
            confidence=0.9,
        )


class AudioPipelineTests(unittest.TestCase):
    def test_extraction_preserves_explicit_phone_indices(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "tone.wav"
            write_tone(audio, 16000, 220.0)
            phones = [PhoneSpec("a", reference_start=0, reference_end=0.06,
                                learner_start=0, learner_end=0.06, phone_index=10),
                      PhoneSpec("u", reference_start=0.12, reference_end=0.2,
                                learner_start=0.12, learner_end=0.2, phone_index=30)]
            for learner in (False, True):
                with self.subTest(learner=learner):
                    tracks = extract_phone_tracks(audio, phones, learner=learner)
                    self.assertEqual([phone.phone_index for phone in tracks], [10, 30])

    def test_missing_alignment_phone_keeps_original_index_and_report_status(self) -> None:
        for missing, ipa, original_indices in ((2, ["a", "i", "u"], None),
                (1, ["a", "i", "u"], None), (1, ["?", "i", "u"], None),
                (2, ["a", "i", "u"], [10, 20, 30])):
            with self.subTest(missing=missing, ipa=ipa, original_indices=original_indices), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                reference, learner = root / "reference.wav", root / "learner.wav"
                write_tone(reference, 16000, 220.0)
                write_tone(learner, 16000, 900.0)
                bounds = [(0.0, 0.06), (0.06, 0.12), (0.12, 0.2)]
                survivors = [index for index in (1, 2, 3) if index != missing]
                alignment = AlignmentResult([AlignedPhone(index, ipa[index - 1],
                    *bounds[index - 1], 0.9, "verified") for index in survivors], "verified", 0.9)
                class PartialAligner:
                    def align(self, *args):
                        return alignment
                request = AnalysisRequest(str(reference), str(learner), "en", [
                    PhoneSpec(label, reference_start=start, reference_end=end,
                              phone_index=original_indices[position] if original_indices else None)
                    for position, (label, (start, end)) in enumerate(zip(ipa, bounds))], profile_id=PROFILES[0].id)
                with patch("praat_ai.pronunciation.build_aligner", return_value=PartialAligner()):
                    result = analyze_pronunciation(request)
                indices = original_indices or [1, 2, 3]
                self.assertEqual([phone.phone_index for phone in result.learner_phones],
                                 [indices[index - 1] for index in survivors])
                self.assertEqual([item["learner_phone_index"] for item in result.phone_assessments],
                                 [None if index == missing else indices[index - 1] for index in (1, 2, 3)])
                self.assertEqual(result.observations, [])
                path = root / "report.TextGrid"
                write_textgrid(result, path)
                grid = parselmouth.read(str(path))
                for time in (0.03, 0.09, 0.16):
                    interval = call(grid, "Get interval at time", 3, time)
                    self.assertEqual(call(grid, "Get label of interval", 3, interval), "证据不足")

    def test_legacy_out_of_audio_boundary_is_not_scored(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            learner = Path(directory) / "learner.wav"
            write_tone(reference, 16000, 220.0)
            write_tone(learner, 16000, 900.0)
            result = analyze_pronunciation(AnalysisRequest(
                reference_object=str(reference), learner_object=str(learner),
                language="en", phonemes=[PhoneSpec("a", reference_start=0.0,
                    reference_end=9.0, learner_start=0.0, learner_end=0.2)],
                profile_id=None))
        self.assertEqual(result.errors, [])
        self.assertTrue(any("超出音频范围" in warning for warning in result.warnings))
        self.assertEqual(result.phone_assessments[0]["status"], "insufficient_evidence")

    def test_null_alignment_metadata_uses_defaults(self) -> None:
        request = request_from_dict(
            {
                "reference_object": "reference.wav",
                "learner_object": "learner.wav",
                "phonemes": [
                    {
                        "ipa": "a",
                        "alignment_confidence": None,
                        "alignment_source": None,
                    }
                ],
            }
        )

        self.assertEqual(request.phonemes[0].alignment_confidence, 1.0)
        self.assertEqual(request.phonemes[0].alignment_source, "")

    def test_missing_reference_boundaries_are_aligned(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "two_tones.wav"
            write_two_tones(audio)
            request = AnalysisRequest(
                reference_object=str(audio),
                learner_object=str(audio),
                language="test",
                phonemes=[PhoneSpec("a"), PhoneSpec("b")],
            )
            with patch("praat_ai.pronunciation.build_aligner", return_value=FixedBoundaryAligner()):
                result = analyze_pronunciation(request)

        self.assertEqual(len(result.reference_phones), 2)
        self.assertEqual(result.reference_phones[0].end, 0.2)
        self.assertEqual(result.errors, [])

    def test_same_wav_uses_aligned_reference_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "two_tones.wav"
            write_two_tones(audio)
            request = AnalysisRequest(
                reference_object=str(audio),
                learner_object=str(audio),
                language="test",
                phonemes=_equal_segments(["a", "b"], 1.0),
            )
            with patch("praat_ai.pronunciation.build_aligner", return_value=FixedBoundaryAligner()):
                result = analyze_pronunciation(request)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.reference_phones[0].end, 0.2)
        self.assertEqual(result.learner_phones[0].end, 0.2)

    def test_same_wav_uses_supplied_learner_boundaries_when_aligner_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "two_tones.wav"
            write_two_tones(audio)
            phones = _equal_segments(["a", "b"], 1.0)
            phones[0].learner_start, phones[0].learner_end = 0.0, 0.2
            phones[1].learner_start, phones[1].learner_end = 0.2, 1.0
            result = analyze_pronunciation(
                AnalysisRequest(
                    reference_object=str(audio),
                    learner_object=str(audio),
                    language="test",
                    phonemes=phones,
                )
            )

        self.assertEqual(result.errors, [])
        self.assertEqual(result.reference_phones[0].end, 0.2)
        self.assertEqual(result.learner_phones[0].end, 0.2)

    def test_identical_wavs_at_distinct_paths_share_supplied_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            learner = Path(directory) / "learner.wav"
            write_two_tones(reference)
            shutil.copyfile(reference, learner)
            phones = _equal_segments(["a", "b"], 1.0)
            phones[0].learner_start, phones[0].learner_end = 0.0, 0.2
            phones[1].learner_start, phones[1].learner_end = 0.2, 1.0
            result = analyze_pronunciation(
                AnalysisRequest(
                    reference_object=str(reference),
                    learner_object=str(learner),
                    language="test",
                    phonemes=phones,
                )
            )

        self.assertEqual(result.errors, [])
        self.assertEqual(result.reference_phones[0].end, 0.2)

    def test_ui_estimates_stay_estimates_after_request_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "two_tones.wav"
            write_two_tones(audio)
            original = AnalysisRequest(
                reference_object=str(audio),
                learner_object=str(audio),
                language="test",
                phonemes=_equal_segments(["a", "b"], 1.0),
            )
            request = request_from_dict(original.to_dict())
            with patch("praat_ai.pronunciation.build_aligner", return_value=FixedBoundaryAligner()):
                result = analyze_pronunciation(request)

        self.assertEqual(result.reference_phones[0].end, 0.2)
        self.assertEqual(result.errors, [])

    def test_supplied_boundaries_are_preserved_on_both_sides(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "two_tones.wav"
            write_two_tones(audio)
            request = AnalysisRequest(
                reference_object=str(audio),
                learner_object=str(audio),
                language="test",
                phonemes=[
                    PhoneSpec("a", reference_start=0.0, reference_end=0.3, learner_start=0.0, learner_end=0.3),
                    PhoneSpec("b", reference_start=0.3, reference_end=1.0, learner_start=0.3, learner_end=1.0),
                ],
            )
            with patch("praat_ai.pronunciation.build_aligner", return_value=FixedBoundaryAligner()) as build:
                result = analyze_pronunciation(request)

        self.assertEqual(result.reference_phones[0].end, 0.3)
        self.assertEqual(result.learner_phones[0].end, 0.3)
        self.assertEqual(result.errors, [])
        build.assert_not_called()

    def test_same_audio_reuses_reference_boundaries_when_both_sides_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "two_tones.wav"
            write_two_tones(audio)
            request = AnalysisRequest(
                reference_object=str(audio),
                learner_object=str(audio),
                language="test",
                phonemes=[
                    PhoneSpec("a", reference_start=0.0, reference_end=0.3, learner_start=0.0, learner_end=0.2),
                    PhoneSpec("b", reference_start=0.3, reference_end=1.0, learner_start=0.2, learner_end=1.0),
                ],
            )
            with patch("praat_ai.pronunciation.build_aligner") as build:
                result = analyze_pronunciation(request)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.learner_phones[0].end, result.reference_phones[0].end)
        build.assert_not_called()

    def test_identical_files_reuse_reference_boundaries_when_both_sides_conflict(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            learner = Path(directory) / "learner.wav"
            write_two_tones(reference)
            shutil.copyfile(reference, learner)
            request = AnalysisRequest(
                reference_object=str(reference),
                learner_object=str(learner),
                language="test",
                phonemes=[
                    PhoneSpec("a", reference_start=0.0, reference_end=0.3, learner_start=0.0, learner_end=0.2),
                    PhoneSpec("b", reference_start=0.3, reference_end=1.0, learner_start=0.2, learner_end=1.0),
                ],
            )
            result = analyze_pronunciation(request)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.learner_phones[0].end, result.reference_phones[0].end)

    def test_different_spectra_produce_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.wav"
            learner = root / "learner.wav"
            write_tone(reference, 16000, 220.0)
            write_tone(learner, 16000, 900.0)

            result = analyze_pronunciation(
                AnalysisRequest(
                    reference_object=str(reference),
                    learner_object=str(learner),
                    language="test",
                    phonemes=[
                        PhoneSpec(
                            "a",
                            reference_start=0.0,
                            reference_end=0.2,
                        )
                    ],
                    error_threshold=1.0,
                    # Restored Parselmouth tracks can rank ahead of spectrum;
                    # this test checks spectral evidence, not the top-3 cap.
                    maximum_errors_per_phone=10,
                )
            )
            self.assertGreater(len(result.errors), 0)
            self.assertIn(
                "spectral_centroid",
                {error.feature for error in result.errors},
            )


if __name__ == "__main__":
    unittest.main()
