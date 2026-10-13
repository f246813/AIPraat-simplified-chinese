import json
import math
import tempfile
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import parselmouth
from parselmouth.praat import call

from praat_ai.audio import SoundSamples
from praat_ai.forced_alignment import parse_mfa_textgrid
from praat_ai.models import AnalysisRequest, FeatureTrack, PhoneFeatureTracks, PhoneSpec
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.pronunciation_rules import (
    PROFILES, applicable_features, evaluate_phone_rules, phone_class, resolve_profile,
)
from praat_ai.qwen import QwenClient
from praat_ai.config import QwenConfig
from praat_ai.report import write_json, write_overlay_png, write_textgrid
from praat_ai.tutor import request_from_dict
from praat_ai.tutor import run_tutor
from praat_ai.bridge import SelectedObject
from praat_ai.ui import show_tutor_form
from test_bridge_pipeline import write_tone


def track_phone(ipa, index=1, start=0.0, end=0.2, source="provided", confidence=1.0):
    values = {
        "f1": ([700.0, 710.0], "Hz"), "f2": ([1200.0, 1210.0], "Hz"),
        "f3": ([2500.0, 2510.0], "Hz"), "pitch": ([120.0, 121.0], "Hz"),
        "intensity": ([60.0, 61.0], "dB"), "rms": ([0.1, 0.11], "amplitude"),
        "spectral_centroid": ([3000.0, 3100.0], "Hz"),
        "high_frequency_ratio": ([0.4, 0.5], "ratio"), "zcr": ([0.2, 0.3], "ratio"),
    }
    return PhoneFeatureTracks(index, ipa, start, end,
        {name: FeatureTrack(name, [start, end], samples, unit) for name, (samples, unit) in values.items()},
        confidence, source)


class PhoneRuleTests(unittest.TestCase):
    def test_classes_come_from_panphon_for_english_and_japanese(self):
        expected = {"i": "vowel", "aɪ": "vowel", "ɯ̥": "vowel", "s": "fricative",
                    "ʃ": "fricative", "t": "stop", "tʃ": "affricate", "t͡ʃ": "affricate",
                    "dʒ": "affricate", "m": "nasal", "ŋ": "nasal", "ɹ": "approximant"}
        for language in ("en", "ja"):
            for ipa, category in expected.items():
                with self.subTest(language=language, ipa=ipa):
                    self.assertEqual(phone_class(ipa, language), category)

    def test_japanese_lengths_devoicing_and_internal_labels_are_retained(self):
        expected = {"aː": "vowel", "i̥": "vowel", "I": "vowel", "U": "vowel",
                    "N": "nasal", "cl": "geminate", "kː": "geminate", "kk": "geminate"}
        for ipa, category in expected.items():
            self.assertEqual(phone_class(ipa, "ja"), category)
        self.assertEqual(phone_class("N", "en"), "unknown")

    def test_unknown_symbols_are_not_silently_deleted(self):
        for ipa in ("a?", "not-IPA", "", "ː"):
            self.assertEqual(phone_class(ipa, "en"), "unknown")

    def test_each_phone_class_selects_relevant_features(self):
        self.assertEqual(applicable_features("i", "en")[1], ("f1", "f2"))
        self.assertEqual(applicable_features("s", "en")[1], ("spectral_centroid", "high_frequency_ratio", "zcr"))
        self.assertEqual(applicable_features("p", "en")[1], ("vot",))
        self.assertEqual(applicable_features("cl", "ja")[1], ())
        self.assertEqual(applicable_features("ɹ", "en")[1], ("f2", "f3"))

    def test_vowels_ignore_loudness_pitch_and_fricative_spectrum(self):
        left, right = track_phone("i"), track_phone("i")
        for name in ("pitch", "intensity", "rms", "spectral_centroid"):
            right.tracks[name].values = [9999.0, 9999.0]
        observations, _ = evaluate_phone_rules(PROFILES[0], [left], [right])
        self.assertEqual({item.feature for item in observations}, {"f1", "f2"})
        self.assertTrue(all(item.direction == "无明显差异" for item in observations))
        self.assertTrue(all(item.issue_type == "observation" and item.score == 0 for item in observations))

    def test_fricatives_do_not_compare_formants_or_require_f0(self):
        left, right = track_phone("s"), track_phone("s")
        right.tracks["pitch"].values = [math.nan, math.nan]
        right.tracks["f1"].values = [9999.0, 9999.0]
        observations, _ = evaluate_phone_rules(PROFILES[0], [left], [right])
        self.assertEqual({item.feature for item in observations}, {"spectral_centroid", "high_frequency_ratio", "zcr"})

    def test_missing_stop_vot_is_recorded_as_unavailable(self):
        observations, assessments = evaluate_phone_rules(PROFILES[0], [track_phone("p")], [track_phone("p")])
        self.assertEqual(observations, [])
        check = next(item for item in assessments[0]["checks"] if item["feature"] == "vot")
        self.assertEqual(check["status"], "insufficient_evidence")
        self.assertIn("未估造", check["reason"])

    def test_valid_vot_measurements_are_compared_only_for_stops(self):
        left, right = track_phone("t"), track_phone("t")
        left.tracks["vot"] = FeatureTrack("vot", [0.0, 0.2], [30.0, 30.0], "ms")
        right.tracks["vot"] = FeatureTrack("vot", [0.0, 0.2], [70.0, 70.0], "ms")
        observations, _ = evaluate_phone_rules(PROFILES[0], [left], [right])
        self.assertEqual([(item.feature, item.reference_value, item.learner_value) for item in observations], [("vot", 30.0, 70.0)])

    def test_devoiced_japanese_vowel_does_not_become_deletion(self):
        left, right = track_phone("i̥"), track_phone("i̥")
        right.tracks["pitch"].values = [math.nan, math.nan]
        right.tracks["f1"].values = [0.0, math.nan]
        right.tracks["f2"].values = [math.nan, math.nan]
        observations, assessments = evaluate_phone_rules(PROFILES[1], [left], [right])
        self.assertEqual(observations, [])
        self.assertEqual(assessments[0]["status"], "insufficient_evidence")
        self.assertNotIn("deletion", json.dumps(assessments))

    def test_missing_nonfinite_and_zero_formants_are_not_normal_values(self):
        left, right = track_phone("a"), track_phone("a")
        left.tracks.pop("f1")
        right.tracks["f2"].values = [0.0, math.nan, math.inf]
        observations, assessments = evaluate_phone_rules(PROFILES[0], [left], [right])
        self.assertEqual(observations, [])
        self.assertEqual(assessments[0]["status"], "insufficient_evidence")

    def test_zero_spectral_centroid_is_valid_but_negative_is_not(self):
        left, right = track_phone("s"), track_phone("s")
        left.tracks["spectral_centroid"].values = [0.0, 0.0, math.nan, -1.0]
        right.tracks["spectral_centroid"].values = [0.0, 1e-9, math.inf, -1.0]
        observations, _ = evaluate_phone_rules(PROFILES[0], [left], [right])
        centroid = next(item for item in observations if item.feature == "spectral_centroid")
        self.assertEqual(centroid.reference_value, 0.0)
        self.assertEqual(centroid.evidence["reference_valid_frames"], 2)
        self.assertEqual(centroid.evidence["learner_valid_frames"], 2)

    def test_retired_minimum_duration_is_ignored_in_old_json(self):
        request = AnalysisRequest(1, 2, "en", [PhoneSpec("a")])
        payload = request.to_dict()
        payload["minimum_error_duration_sec"] = "retired"
        recovered = request_from_dict(payload)
        self.assertNotIn("minimum_error_duration_sec", recovered.to_dict())
        self.assertFalse(hasattr(recovered, "minimum_error_duration_sec"))

    def test_retired_minimum_duration_is_ignored_in_old_config(self):
        from praat_ai.config import load_config
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(json.dumps({"analysis": {"minimum_error_duration_sec": 0.5,
                "default_error_threshold": 2.0}}), encoding="utf-8")
            config = load_config(path)
        self.assertNotIn("minimum_error_duration_sec", config.to_dict()["analysis"])
        self.assertEqual(config.analysis.default_error_threshold, 2.0)

    def test_mismatched_units_are_not_compared(self):
        left, right = track_phone("a"), track_phone("a")
        right.tracks["f1"].unit = "kHz"
        observations, assessments = evaluate_phone_rules(PROFILES[0], [left], [right])
        self.assertEqual([item.feature for item in observations], ["f2"])
        self.assertEqual(assessments[0]["status"], "partial")

    def test_proportional_low_quality_and_invalid_boundaries_do_not_produce_observations(self):
        for change in (dict(alignment_source="proportional"), dict(alignment_source="ui_equal_estimate"),
                       dict(alignment_confidence=0.4), dict(start=0.2), dict(end=math.nan)):
            for side in ("reference", "learner"):
                left, right = track_phone("a"), track_phone("a")
                for key, value in change.items():
                    setattr(left if side == "reference" else right, key, value)
                observations, assessments = evaluate_phone_rules(PROFILES[0], [left], [right])
                self.assertEqual(observations, [])
                self.assertEqual(assessments[0]["status"], "insufficient_evidence")

    def test_overlapping_boundaries_do_not_support_phone_evidence(self):
        phones = [track_phone("a", end=0.3), track_phone("s", 2, 0.2, 0.4)]
        observations, assessments = evaluate_phone_rules(PROFILES[0], phones, phones)
        self.assertEqual(observations, [])
        self.assertTrue(all(item["status"] == "insufficient_evidence" for item in assessments))

    def test_mismatched_phone_identity_is_not_paired(self):
        observations, assessments = evaluate_phone_rules(PROFILES[0], [track_phone("a")], [track_phone("s")])
        self.assertEqual(observations, [])
        self.assertEqual(assessments[0]["status"], "insufficient_evidence")

    def test_pairing_uses_positions_and_reference_labels(self):
        left = [track_phone("a", 10), track_phone("s", 20, 0.2, 0.4)]
        right = [track_phone("a", 1), track_phone("s", 3, 0.2, 0.4)]
        observations, assessments = evaluate_phone_rules(PROFILES[0], left, right)
        self.assertEqual({item.phone_index for item in observations}, {10, 20})
        self.assertEqual([item["learner_phone_index"] for item in assessments], [1, 3])
        duration = [item for item in observations if item.feature == "relative_duration"]
        self.assertEqual(len(duration), 2)
        self.assertEqual(duration[0].evidence["neighbor_phone_indices"], [20])

    def test_missing_middle_phone_does_not_shift_repeated_phones(self):
        left = [track_phone("a"), track_phone("s", 2, 0.2, 0.4),
                track_phone("a", 3, 0.4, 0.6)]
        right = [track_phone("a"), track_phone("a", 3, 0.4, 0.6)]
        observations, assessments = evaluate_phone_rules(PROFILES[0], left, right)
        self.assertEqual(observations, [])
        self.assertTrue(all(item["status"] == "insufficient_evidence" for item in assessments))

    def test_unequal_sequences_report_only_matching_original_phone_identity(self):
        left = [track_phone("a"), track_phone("i", 2, 0.2, 0.4),
                track_phone("u", 3, 0.4, 0.6)]
        for surviving_index in (2, 3):
            right = [track_phone("a"), track_phone("u", surviving_index, 0.4, 0.6)]
            with self.subTest(surviving_index=surviving_index):
                observations, assessments = evaluate_phone_rules(PROFILES[0], left, right)
                self.assertEqual(observations, [])
                self.assertEqual([item["learner_phone_index"] for item in assessments],
                                 [1, None, 3 if surviving_index == 3 else None])

    def test_explicit_phone_identity_survives_request_round_trip(self):
        request = AnalysisRequest(1, 2, "en", [PhoneSpec("a", phone_index=41)])
        recovered = request_from_dict(request.to_dict())
        self.assertEqual(recovered.phonemes[0].phone_index, 41)

    def test_unreliable_neighbor_is_not_removed_before_pairing(self):
        left = [track_phone("a"), track_phone("s", 2, 0.2, 0.4),
                track_phone("i", 3, 0.4, 0.6)]
        right = [track_phone("a", 11), track_phone("s", 13, 0.2, 0.4, confidence=0.1),
                 track_phone("i", 15, 0.4, 0.6)]
        observations, assessments = evaluate_phone_rules(PROFILES[0], left, right)
        self.assertEqual({item.phone_index for item in observations}, {1, 3})
        self.assertFalse(any(item.feature == "relative_duration" for item in observations))
        self.assertEqual(assessments[1]["status"], "insufficient_evidence")

    def test_uniform_speech_rate_change_preserves_relative_duration(self):
        left = [track_phone("aː", end=0.4), track_phone("n", 2, 0.4, 0.6)]
        right = [track_phone("aː", end=0.8), track_phone("n", 2, 0.8, 1.2)]
        observations, _ = evaluate_phone_rules(PROFILES[1], left, right)
        duration = [item for item in observations if item.feature == "relative_duration"]
        self.assertEqual(len(duration), 2)
        self.assertAlmostEqual(duration[0].reference_value, duration[0].learner_value)
        self.assertEqual(duration[0].rule_id, "ja.long_vowel.relative_duration")
        self.assertEqual(duration[0].ipa, "aː")
        self.assertFalse(duration[0].evidence["mora_structure_available"])

    def test_japanese_geminate_duration_has_own_rule_and_no_pitch_judgment(self):
        left = [track_phone("cl", end=0.2), track_phone("a", 2, 0.2, 0.4)]
        right = [track_phone("cl", end=0.1), track_phone("a", 2, 0.1, 0.3)]
        observations, _ = evaluate_phone_rules(PROFILES[1], left, right)
        closure = next(item for item in observations if item.ipa == "cl")
        self.assertEqual(closure.rule_id, "ja.geminate.relative_duration")
        self.assertEqual(closure.direction, "相对偏短")
        self.assertEqual(closure.issue_type, "observation")

    def test_geminate_without_acoustic_tracks_is_duration_only(self):
        phones = [track_phone("cl"), track_phone("cl", 2, 0.2, 0.4)]
        for phone in phones:
            phone.tracks.clear()
        observations, assessments = evaluate_phone_rules(PROFILES[1], phones, phones)
        self.assertEqual({item.feature for item in observations}, {"relative_duration"})
        self.assertTrue(all(item["status"] == "duration_only" for item in assessments))
        self.assertTrue(all(any(check["feature"] == "phone_acoustics" and
                               check["status"] == "not_applicable"
                               for check in item["checks"]) for item in assessments))

    def test_profile_selection_is_explicit_and_language_checked(self):
        self.assertIsNone(resolve_profile(None, "en"))
        self.assertIsNone(resolve_profile(None, "cmn"))
        self.assertEqual(resolve_profile(PROFILES[0].id, "en-US"), PROFILES[0])
        self.assertEqual(resolve_profile(PROFILES[1].id, "jpn"), PROFILES[1])
        for profile, language in (("unknown", "en"), (PROFILES[0].id, "cmn")):
            with self.assertRaises(ValueError):
                resolve_profile(profile, language)

    def test_new_and_legacy_request_json_round_trip(self):
        request = AnalysisRequest(1, 2, "ja", [PhoneSpec("aː")], profile_id=PROFILES[1].id)
        recovered = request_from_dict(json.loads(json.dumps(request.to_dict())))
        self.assertEqual(recovered.profile_id, request.profile_id)
        self.assertEqual(recovered.phonemes[0].ipa, "aː")
        payload = request.to_dict()
        payload.pop("profile_id")
        self.assertIsNone(request_from_dict(payload).profile_id)

    def test_mfa_parser_preserves_unicode_ipa(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "phones.TextGrid"
            path.write_text('name = "phones"\nxmin = 0\nxmax = 0.2\ntext = "ɯ̥ː"\n', encoding="utf-8")
            result = parse_mfa_textgrid(path, [PhoneSpec("ɯ̥ː")])
        self.assertEqual(result.phones[0].ipa, "ɯ̥ː")


class RuleIntegrationTests(unittest.TestCase):
    def test_tutor_form_selects_profiles_and_disables_legacy_threshold(self):
        import tkinter as tk
        from tkinter import ttk

        def descendants(parent):
            return [parent, *(child for widget in parent.winfo_children() for child in descendants(widget))]

        for selection, profile in enumerate(PROFILES, start=1):
            root = tk.Tk()
            root.withdraw()
            failures = []

            def submit():
                try:
                    widgets = descendants(root)
                    combo = next(widget for widget in widgets if isinstance(widget, ttk.Combobox)
                                 and "旧式声学对比" in widget.cget("values"))
                    combo.current(selection)
                    combo.event_generate("<<ComboboxSelected>>")
                    entry = next(widget for widget in widgets if isinstance(widget, ttk.Entry) and widget.get() == "1.25")
                    self.assertEqual(str(entry.cget("state")), "disabled")
                    root.update_idletasks()  # Flush queued ttk theme events before form teardown.
                    next(widget for widget in widgets if getattr(widget, "_text", None) == "开始分析")._command()
                except Exception as error:
                    failures.append(error)
                    root.destroy()

            root.after(50, submit)
            sounds = [SelectedObject(1, "reference", "Sound", Path("reference.wav")),
                      SelectedObject(2, "learner", "Sound", Path("learner.wav"))]
            with patch("praat_ai.ui.ui_windows.create_root", return_value=root), patch(
                "praat_ai.ui.read_wav", return_value=SoundSamples(np.zeros(6400), 16000)):
                values = show_tutor_form(sounds)
            self.assertEqual(failures, [])
            self.assertEqual(values.request.profile_id, profile.id)
            self.assertEqual(values.request.language, profile.language)

    def test_tutor_generates_persistent_new_reports_without_model_or_api(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            reference, learner = root / "reference.wav", root / "learner.wav"
            write_tone(reference, 220.0)
            write_tone(learner, 900.0)
            helper = types.SimpleNamespace(
                get_selected=lambda: [
                    dict(id=1, name="reference", **{"class": "Sound"}, file=str(reference)),
                    dict(id=2, name="learner", **{"class": "Sound"}, file=str(learner)),
                ], get_output_dir=lambda: str(output))
            request = AnalysisRequest(1, 2, "en", [
                PhoneSpec("a", reference_start=0, reference_end=0.1, learner_start=0, learner_end=0.1),
                PhoneSpec("s", reference_start=0.1, reference_end=0.2, learner_start=0.1, learner_end=0.2),
            ], profile_id=PROFILES[0].id, qwen_explain=False, qwen_vision=False)
            with patch.dict(sys.modules, {"praat": helper}), patch.dict(
                os.environ, {"PRAAT_AI_REPORT_DIR": str(root / "reports")}
            ), patch("praat_ai.tutor.detect_gpu", return_value=None), patch(
                "praat_ai.tutor.QwenServerManager.ensure_started"
            ) as start_model:
                outputs = run_tutor(config_path=root / "missing-config.json", request=request)
            start_model.assert_not_called()
            self.assertEqual(outputs.result.errors, [])
            self.assertGreater(len(outputs.result.observations), 0)
            payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["profile_snapshot"]["id"], PROFILES[0].id)
            grid = parselmouth.read(str(output / outputs.textgrid_path.name))
            self.assertEqual(call(grid, "Get number of tiers"), 4)
            self.assertTrue((output / outputs.overlay_path.name).is_file())

    def result(self, *, coarse=False, overflowing=False):
        left = [track_phone("aː"), track_phone("s", 2, 0.2, 0.4)]
        right = [track_phone("aː"), track_phone("s", 2, 0.2, 0.4)]
        right[0].tracks["f1"].values = [900.0, 910.0]
        left[0].tracks["f2"].values = [1200.0, math.nan, 1210.0]
        if coarse:
            for phone in left + right:
                phone.alignment_source = "proportional"
        if overflowing:
            left[1].end = 0.5
        return self.analyze_tracks(left, right, PROFILES[1].id)

    def analyze_tracks(self, left, right, profile_id):
        request = AnalysisRequest("reference.wav", "learner.wav", "ja", [
            PhoneSpec(phone.ipa, reference_start=phone.start, reference_end=phone.end,
                      learner_start=other.start, learner_end=other.end)
            for phone, other in zip(left, right)], profile_id=profile_id)
        audio = [SoundSamples(np.zeros(6400), 16000), SoundSamples(np.ones(6400), 16000)]
        with patch("praat_ai.audio.read_wav", side_effect=audio), patch(
            "praat_ai.pronunciation.extract_phone_tracks", side_effect=[left, right]):
            return analyze_pronunciation(request)

    def test_duration_only_status_reaches_json_and_textgrid(self):
        left = [track_phone("cl"), track_phone("cl", 2, 0.2, 0.4)]
        right = [track_phone("cl"), track_phone("cl", 2, 0.2, 0.4)]
        for phone in left + right:
            phone.tracks.clear()
        result = self.analyze_tracks(left, right, PROFILES[1].id)
        payload = result.to_dict()
        self.assertEqual(payload["check_statuses"]["phone_acoustics"], "not_applicable")
        self.assertEqual(payload["check_statuses"]["relative_duration"], "observed")
        self.assertTrue(all(item["status"] == "duration_only" for item in payload["phone_assessments"]))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.TextGrid"
            write_textgrid(result, path)
            grid = parselmouth.read(str(path))
            self.assertEqual(call(grid, "Get label of interval", 3, 1), "仅生成时长观察（实验性）")
            self.assertNotIn("已生成声学观察", path.read_text(encoding="utf-8"))

    def test_textgrid_status_follows_position_with_different_track_indices(self):
        left = [track_phone("cl", 10), track_phone("a", 20, 0.2, 0.4)]
        right = [track_phone("cl", 1), track_phone("a", 3, 0.2, 0.4)]
        right[1].tracks["f1"].values = [900.0, 910.0]
        result = self.analyze_tracks(left, right, PROFILES[1].id)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.TextGrid"
            write_textgrid(result, path)
            grid = parselmouth.read(str(path))
            self.assertEqual(call(grid, "Get label of interval", 3, 1), "仅生成时长观察（实验性）")
            self.assertEqual(call(grid, "Get label of interval", 3, 2), "已生成声学观察（实验性）")
            from PIL import Image
            png_path = path.with_suffix(".png")
            self.assertTrue(write_overlay_png(result, png_path))
            with Image.open(png_path) as image:
                colors = {color for _, color in image.crop((0, 210, 1280, 360)).getcolors(1280 * 150)}
            self.assertIn((96, 165, 250), colors)  # Reference F1 curve.
            self.assertIn((245, 158, 11), colors)  # Learner F1 curve.

    def test_neighbor_bounds_and_overlap_are_checked_with_gapped_indices(self):
        for start, end, invalid_first in ((0.1, 0.4, True), (0.2, 0.5, False)):
            with self.subTest(start=start, end=end):
                left = [track_phone("a"), track_phone("s", 2, 0.2, 0.4)]
                right = [track_phone("a"), track_phone("s", 3, start, end)]
                result = self.analyze_tracks(left, right, PROFILES[1].id)
                self.assertEqual(result.phone_assessments[1]["status"], "insufficient_evidence")
                self.assertFalse(any(item.feature == "relative_duration" for item in result.observations))
                if invalid_first:
                    self.assertEqual(result.observations, [])
                else:
                    self.assertEqual({item.phone_index for item in result.observations}, {1})

    def test_report_does_not_borrow_state_for_renumbered_partial_tracks(self):
        for missing, labels in ((2, ["a", "i", "u"]), (1, ["?", "i", "u"])):
            with self.subTest(missing=missing):
                left = [track_phone(label, index + 1, start, end)
                        for index, (label, (start, end)) in enumerate(zip(
                            labels, [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6)]))]
                right = [track_phone(phone.ipa, position + 1, phone.start, phone.end)
                         for position, phone in enumerate(phone for phone in left if phone.phone_index != missing)]
                result = self.result()
                result.reference_phones, result.learner_phones = left, right
                result.duration = 0.6
                result.observations, result.phone_assessments = evaluate_phone_rules(PROFILES[1], left, right)
                # Exercise old serialized assessments that used positional keys.
                for position, item in enumerate(result.phone_assessments):
                    item["learner_phone_index"] = position + 1 if position < len(right) else None
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "report.TextGrid"
                    write_textgrid(result, path)
                    grid = parselmouth.read(str(path))
                    for time in (0.1, 0.3, 0.5):
                        interval = call(grid, "Get interval at time", 3, time)
                        self.assertEqual(call(grid, "Get label of interval", 3, interval), "证据不足")

    def test_duration_cannot_mask_missing_applicable_acoustic_frames(self):
        left = [track_phone("cl"), track_phone("a", 2, 0.2, 0.4)]
        right = [track_phone("cl"), track_phone("a", 2, 0.2, 0.4)]
        for phone in left + right:
            phone.tracks.clear()
        result = self.analyze_tracks(left, right, PROFILES[1].id)
        self.assertEqual(result.check_statuses["phone_acoustics"], "not_evaluated")
        self.assertEqual(result.phone_assessments[0]["status"], "duration_only")
        self.assertEqual(result.phone_assessments[1]["status"], "partial")

    def test_legacy_skips_out_of_audio_pairs_on_either_side(self):
        for side in ("reference", "learner"):
            with self.subTest(side=side):
                left, right = [track_phone("a")], [track_phone("a")]
                right[0].tracks["f1"].values = [900.0, 910.0]
                self.assertTrue(self.analyze_tracks(left, right, None).errors)
                (left if side == "reference" else right)[0].end = 9.0
                result = self.analyze_tracks(left, right, None)
                self.assertEqual(result.errors, [])
                self.assertTrue(any("超出音频范围" in warning for warning in result.warnings))
                self.assertEqual(result.phone_assessments[0]["status"], "insufficient_evidence")
                with tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "legacy.TextGrid"
                    write_textgrid(result, path)
                    grid = parselmouth.read(str(path))
                    self.assertIn("证据不足", call(grid, "Get label of interval", 1, 1))
                    self.assertNotIn("未发现超过", path.read_text(encoding="utf-8"))

    def test_invalid_pair_does_not_prevent_valid_legacy_comparison(self):
        left = [track_phone("a"), track_phone("a", 2, 0.2, 9.0)]
        right = [track_phone("a"), track_phone("a", 2, 0.2, 0.4)]
        for phone in right:
            phone.tracks["f1"].values = [900.0, 910.0]
        result = self.analyze_tracks(left, right, None)
        self.assertEqual({item.phone_index for item in result.errors}, {1})
        self.assertEqual(result.phone_assessments[0]["phone_index"], 2)

    def test_mixed_duration_and_acoustics_keep_distinct_phone_statuses(self):
        left = [track_phone("cl"), track_phone("a", 2, 0.2, 0.4)]
        right = [track_phone("cl"), track_phone("a", 2, 0.2, 0.4)]
        result = self.analyze_tracks(left, right, PROFILES[1].id)
        self.assertEqual(result.check_statuses["phone_acoustics"], "observed")
        self.assertEqual([item["status"] for item in result.phone_assessments],
                         ["duration_only", "observed"])

    def test_analysis_dispatch_and_status_reach_json(self):
        result = self.result()
        self.assertEqual(result.errors, [])
        self.assertGreater(len(result.observations), 0)
        payload = result.to_dict()
        self.assertEqual(payload["analysis_mode"], "phone_rules")
        self.assertFalse(payload["profile_snapshot"]["thresholds_calibrated"])
        self.assertEqual(payload["check_statuses"]["independent_phone_recognition"], "not_evaluated")
        self.assertIsNone(payload["reference_phones"][0]["tracks"]["f2"]["values"][1])
        json.dumps(payload, allow_nan=False)

    def test_out_of_audio_boundaries_are_not_observations(self):
        result = self.result(overflowing=True)
        self.assertEqual(result.phone_assessments[1]["status"], "insufficient_evidence")
        self.assertFalse(any(item.phone_index == 2 for item in result.observations))
        self.assertEqual(result.check_statuses["phone_acoustics"], "partial")

    def test_new_report_textgrid_and_png_preserve_evidence_and_duration(self):
        result = self.result()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_json(result, root / "report.json")
            write_textgrid(result, root / "report.TextGrid")
            self.assertTrue(write_overlay_png(result, root / "report.png"))
            grid = parselmouth.read(str(root / "report.TextGrid"))
            self.assertEqual(call(grid, "Get number of tiers"), 4)
            self.assertEqual(call(grid, "Get label of interval", 1, 1), "aː")
            self.assertIn("未执行独立音素识别", call(grid, "Get label of interval", 2, 1))
            self.assertIn("实验性", call(grid, "Get label of interval", 3, 1))
            self.assertGreater(call(grid, "Get number of points", 4), 0)
            self.assertGreater((root / "report.png").stat().st_size, 0)

    def test_coarse_report_has_no_false_normal_or_error_conclusion(self):
        result = self.result(coarse=True)
        self.assertEqual(result.observations, [])
        self.assertEqual(result.errors, [])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.TextGrid"
            write_textgrid(result, path)
            grid = parselmouth.read(str(path))
            self.assertEqual(call(grid, "Get number of points", 4), 0)
            self.assertEqual(call(grid, "Get label of interval", 3, 1), "证据不足")
            self.assertNotIn("正常", path.read_text(encoding="utf-8"))
            self.assertTrue(write_overlay_png(result, path.with_suffix(".png")))

    def test_qwen_receives_status_at_tail_with_stable_system_prefix(self):
        result = self.result().to_dict()
        client = QwenClient(QwenConfig())
        messages = []
        with patch.object(client, "chat", side_effect=lambda value, **kwargs: messages.append(value) or "说明"):
            for suffix in ("首次", "继续", "复查"):
                client.explain_errors({"goal": suffix}, result)
        self.assertEqual(messages[0][0], messages[1][0])
        self.assertEqual(messages[1][0], messages[2][0])
        for payload in messages:
            self.assertIn("independent_phone_recognition", payload[-1]["content"])
            self.assertIn("观察不等于发音错误", payload[-1]["content"])

    def test_qwen_vision_gets_observations_and_status_instead_of_empty_errors(self):
        client = QwenClient(QwenConfig())
        result = self.result().to_dict()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "chart.png"
            path.write_bytes(b"offline image fixture")
            with patch.object(client, "chat", return_value="说明") as chat:
                client.vision_explain(path, {}, result)
        content = chat.call_args.args[0][-1]["content"][1]["text"]
        self.assertIn("observations", content)
        self.assertIn("not_evaluated", content)
        self.assertIn("不能判定发音对错", content)


if __name__ == "__main__":
    unittest.main()
