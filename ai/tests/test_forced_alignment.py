import tempfile
import unittest
from pathlib import Path

import numpy as np

from praat_ai.config import MfaAlignmentConfig
from praat_ai.forced_alignment import (
    AlignmentError,
    AlignmentBackend,
    AlignmentResult,
    CompositeAligner,
    MfaAligner,
    _ctc_forced_align,
    parse_mfa_textgrid,
)
from praat_ai.models import AlignedPhone, PhoneSpec


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


if __name__ == "__main__":
    unittest.main()
