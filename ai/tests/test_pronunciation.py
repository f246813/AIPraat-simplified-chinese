import unittest
import math

from praat_ai.models import (
    AnalysisRequest,
    FeatureTrack,
    PhoneFeatureTracks,
    PhoneSpec,
)
from praat_ai.pronunciation import compare_phone


class PronunciationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.request = AnalysisRequest(
            reference_object=1,
            learner_object=2,
            language="cmn",
            phonemes=[PhoneSpec("a", reference_start=0.0, reference_end=0.1)],
            error_threshold=1.0,
        )

    def test_similar_phone_has_no_error(self) -> None:
        reference = PhoneFeatureTracks(
            1,
            "a",
            0.0,
            0.1,
            {"f1": FeatureTrack("f1", [0.0, 0.1], [700.0, 710.0], "Hz", 1.0, 100.0)},
        )
        learner = PhoneFeatureTracks(
            1,
            "a",
            0.0,
            0.1,
            {"f1": FeatureTrack("f1", [0.0, 0.1], [705.0, 715.0], "Hz", 1.0, 100.0)},
        )
        self.assertEqual(compare_phone(self.request, reference, learner), [])

    def test_shifted_phone_has_error(self) -> None:
        reference = PhoneFeatureTracks(
            1,
            "a",
            0.0,
            0.1,
            {"f1": FeatureTrack("f1", [0.0, 0.1], [700.0, 700.0], "Hz", 1.0, 100.0)},
        )
        learner = PhoneFeatureTracks(
            1,
            "a",
            0.0,
            0.1,
            {"f1": FeatureTrack("f1", [0.0, 0.1], [1100.0, 1100.0], "Hz", 1.0, 100.0)},
        )
        errors = compare_phone(self.request, reference, learner)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].feature, "f1")

    def test_nonfinite_learner_samples_do_not_enter_dtw(self) -> None:
        reference = PhoneFeatureTracks(1, "a", 0.0, 0.1, {
            "f1": FeatureTrack("f1", [0.0, 0.1], [700.0, 700.0], "Hz", 1.0, 100.0)})
        learner = PhoneFeatureTracks(1, "a", 0.0, 0.1, {
            "f1": FeatureTrack("f1", [0.0, 0.05, 0.1], [1100.0, math.nan, 1100.0], "Hz", 1.0, 100.0)})
        errors = compare_phone(self.request, reference, learner)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].learner_value, 1100.0)
        self.assertTrue(math.isfinite(errors[0].score))
        self.assertTrue(math.isnan(learner.tracks["f1"].values[1]))


if __name__ == "__main__":
    unittest.main()
