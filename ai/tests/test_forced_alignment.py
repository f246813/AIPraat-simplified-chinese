import contextlib
from types import SimpleNamespace
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from praat_ai import forced_alignment
from praat_ai.config import MfaAlignmentConfig, Wav2Vec2AlignmentConfig
from praat_ai.forced_alignment import (
    AlignmentError,
    AlignmentBackend,
    AlignmentResult,
    CompositeAligner,
    MfaAligner,
    ProportionalAligner,
    Wav2Vec2Aligner,
    _ctc_forced_align,
    ctc_span_quality,
    parse_mfa_textgrid,
)
from praat_ai.models import AlignedPhone, PhoneSpec


class _FakeTensor:
    def to(self, device):
        return self


class _FakeArray:
    def cpu(self):
        return self

    def numpy(self):
        return np.zeros((60, 3))


class _Indexable:
    def __getitem__(self, item):
        del item
        return _FakeArray()


class _FakeTorch:
    def inference_mode(self):
        return contextlib.nullcontext()

    def log_softmax(self, logits, dim):
        del logits, dim
        return _Indexable()


@contextlib.contextmanager
def stubbed_wav2vec2(spans, confidence=0.78, duration=0.892):
    """A Wav2Vec2Aligner whose model, tokenizer and CTC path are replaced by fixtures."""
    aligner = Wav2Vec2Aligner(Wav2Vec2AlignmentConfig(enabled=True, model="unit-test-model"))
    sound = SimpleNamespace(samples=np.zeros(16000, dtype=np.float32), sample_rate=16000, duration=duration)
    with patch.object(forced_alignment, "read_wav", return_value=sound), \
         patch.object(Wav2Vec2Aligner, "available", lambda self: True), \
         patch.object(Wav2Vec2Aligner, "_load", lambda self: None), \
         patch.object(Wav2Vec2Aligner, "_target_ids", lambda self, phones: [1] * len(phones)), \
         patch.object(forced_alignment, "_ctc_forced_align", return_value=(spans, confidence)):
        aligner._processor = lambda *args, **kwargs: {"input_values": _FakeTensor()}
        aligner._model = lambda values: SimpleNamespace(logits=object())
        aligner._torch = _FakeTorch()
        yield aligner


class FixedAligner(AlignmentBackend):
    def __init__(self, name: str, offsets: list[float], confidence: float):
        self.name = name
        self.offsets = offsets
        self.confidence = confidence

    def available(self) -> bool:
        return True

    def align(self, audio_path, phones, language, transcript=""):
        del audio_path, language, transcript
        return AlignmentResult(
            [
                AlignedPhone(
                    index,
                    phone.ipa,
                    offset,
                    offset + 0.1,
                    self.confidence,
                    self.name,
                )
                for index, (phone, offset) in enumerate(
                    zip(phones, self.offsets),
                    start=1,
                )
            ],
            self.name,
            self.confidence,
        )


class ForcedAlignmentTests(unittest.TestCase):
    def parse_labels(self, labels, expected):
        text = 'name = "phones"\n' + "\n".join(
            f'xmin = {index / 10}\nxmax = {(index + 1) / 10}\ntext = "{label}"'
            for index, label in enumerate(labels))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.TextGrid"
            path.write_text(text, encoding="utf-8")
            return parse_mfa_textgrid(path, [PhoneSpec(ipa) for ipa in expected])

    def test_repeated_extra_tokens_match_ordered_requested_sequence(self):
        for expected, starts in ((["a", "s"], [0.0, 0.1]),
                                 (["a", "s", "a"], [0.0, 0.1, 0.2]),
                                 (["s", "a", "s"], [0.1, 0.2, 0.3]),
                                 (["a", "s", "a", "s"], [0.0, 0.1, 0.2, 0.3])):
            with self.subTest(expected=expected):
                result = self.parse_labels(["a", "s", "a", "s"], expected)
                self.assertEqual([phone.ipa for phone in result.phones], expected)
                self.assertEqual([phone.start for phone in result.phones], starts)

    def test_unmatched_or_reordered_tokens_are_not_relabelled(self):
        for labels, expected in ((["s", "a"], ["a", "s"]),
                                 (["a", "s", "a", "s"], ["s", "i", "s"])):
            with self.subTest(labels=labels, expected=expected):
                with self.assertRaises(AlignmentError):
                    self.parse_labels(labels, expected)

    def test_repeated_tokens_can_match_noncontiguous_subsequence(self):
        result = self.parse_labels(["a", "s", "a", "s"], ["a", "a", "s"])
        self.assertEqual([phone.start for phone in result.phones], [0.0, 0.2, 0.3])

    def test_extra_tokens_and_silence_preserve_unicode_ipa(self):
        result = self.parse_labels(["sil", "s", "ɯ̥ː", "sp", "ɴ", "s"], ["ɯ̥ː", "ɴ"])
        self.assertEqual([phone.start for phone in result.phones], [0.2, 0.4])
        self.assertEqual([phone.ipa for phone in result.phones], ["ɯ̥ː", "ɴ"])

    def test_textgrid_parser_reads_phone_tier(self) -> None:
        text = """
        item [1]:
            class = "IntervalTier"
            name = "phones"
            intervals: size = 2
            intervals [1]:
                xmin = 0.0
                xmax = 0.1
                text = "m"
            intervals [2]:
                xmin = 0.1
                xmax = 0.2
                text = "a"
        """
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.TextGrid"
            path.write_text(text, encoding="utf-8")
            result = parse_mfa_textgrid(
                path,
                [PhoneSpec("m"), PhoneSpec("a")],
            )
        self.assertEqual(len(result.phones), 2)
        self.assertAlmostEqual(result.phones[1].start, 0.1)

    def test_ctc_alignment_finds_token_spans(self) -> None:
        log_probabilities = np.full((8, 3), -10.0)
        for frame in range(0, 3):
            log_probabilities[frame, 1] = 0.0
        for frame in range(3, 5):
            log_probabilities[frame, 0] = 0.0
        for frame in range(5, 8):
            log_probabilities[frame, 2] = 0.0
        spans, confidence = _ctc_forced_align(
            log_probabilities,
            [1, 2],
            blank_token_id=0,
        )
        self.assertEqual(spans[0], [0, 1, 2])
        self.assertEqual(spans[1], [5, 6, 7])
        self.assertGreater(confidence, 0.9)

    def test_composite_aligner_merges_boundaries(self) -> None:
        aligner = CompositeAligner(
            [
                FixedAligner("mfa", [0.0], 0.9),
                FixedAligner("wav2vec2", [0.02], 0.8),
            ],
            agreement_threshold_sec=0.04,
        )
        result = aligner.align("unused.wav", [PhoneSpec("a")], "test")
        self.assertGreaterEqual(result.phones[0].start, 0.0)
        self.assertLess(result.phones[0].start, 0.02)
        self.assertIn("dual:", result.source)

    def test_mfa_command_uses_configured_model(self) -> None:
        aligner = MfaAligner(
            MfaAlignmentConfig(
                enabled=True,
                executable="mfa",
                acoustic_model="mandarin_mfa",
            )
        )
        command = aligner.build_command(
            Path("corpus"),
            Path("dictionary.txt"),
            Path("output"),
        )
        self.assertIn("mandarin_mfa", command)
        self.assertIn("long_textgrid", command)


class DegenerateAlignmentTests(unittest.TestCase):
    """A high frame probability must not turn a timing-free path into an alignment."""

    def test_one_frame_per_phone_has_no_duration_information(self):
        quality = ctc_span_quality([[0], [20], [40], [60], [80]], 52, 0.892)
        self.assertTrue(quality["degenerate"])
        self.assertEqual(quality["median_frames"], 1.0)
        self.assertAlmostEqual(quality["covered_seconds"], 5 / 52 * 0.892, places=6)
        self.assertLess(quality["covered_fraction"], 0.12)

    def test_normal_spans_are_not_flagged(self):
        quality = ctc_span_quality([list(range(offset, offset + 6)) for offset in range(0, 30, 6)], 60, 1.0)
        self.assertFalse(quality["degenerate"])
        self.assertEqual(quality["median_frames"], 6.0)
        self.assertAlmostEqual(quality["covered_fraction"], 0.5)

    def test_empty_alignment_is_degenerate(self):
        self.assertTrue(ctc_span_quality([], 60, 1.0)["degenerate"])

    def test_degenerate_ctc_alignment_is_rejected(self):
        with stubbed_wav2vec2([[0], [20]]) as aligner:
            with self.assertRaises(AlignmentError) as raised:
                aligner.align(Path("unused.wav"), [PhoneSpec("a"), PhoneSpec("n")], "ja")
        self.assertIn("退化", str(raised.exception))

    def test_usable_ctc_alignment_is_returned(self):
        spans = [list(range(0, 5)), list(range(20, 25))]
        with stubbed_wav2vec2(spans, confidence=0.9) as aligner:
            result = aligner.align(Path("unused.wav"), [PhoneSpec("a"), PhoneSpec("n")], "ja")
        self.assertEqual(len(result.phones), 2)
        self.assertGreater(result.confidence, 0.7)
        self.assertAlmostEqual(result.phones[0].end - result.phones[0].start, 5 / 60 * 0.892, places=6)

    def test_degenerate_backend_falls_back_to_proportional_timing(self):
        specs = [PhoneSpec("a", reference_start=0.0, reference_end=0.2),
                 PhoneSpec("n", reference_start=0.2, reference_end=0.4)]
        with stubbed_wav2vec2([[0], [20]]) as aligner:
            result = CompositeAligner([aligner, ProportionalAligner()]).align(Path("unused.wav"), specs, "ja")
        self.assertEqual(result.source, "proportional")
        self.assertTrue(any("退化" in warning for warning in result.warnings), result.warnings)
        # Below the rules' 0.70 located-threshold: phone-level checks stay insufficient.
        self.assertTrue(all(phone.confidence < 0.7 for phone in result.phones))


if __name__ == "__main__":
    unittest.main()
