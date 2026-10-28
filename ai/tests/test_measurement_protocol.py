"""Quantity definitions and the actual pronunciation delivery path."""
import json
import math
from pathlib import Path
import tempfile
import unittest

import numpy as np
import parselmouth
from parselmouth.praat import call

from praat_ai.acoustic_events import analyse_vot
from praat_ai.measurement_protocol import bind_tracks, compatibility, compare_trajectories, valid_samples, valid_value
from praat_ai.models import AnalysisRequest, AnalysisResult, FeatureTrack, PhoneFeatureTracks, PhoneSpec, ScalarMeasurement
from praat_ai.pronunciation import analyze_pronunciation, compare_phone_evidence, explanation_evidence
from praat_ai.pronunciation_rules import PROFILES, evaluate_phone_rules
from praat_ai.report import write_json, write_overlay_png, write_textgrid
from praat_ai.report_guards import _quantities
from praat_ai.tutor_report import render_report


def phone(ipa="t", values=None):
    return PhoneFeatureTracks(1, ipa, .1, .55,
        {} if values is None else {"f1": FeatureTrack("f1", [.15, .25, .35], values, "Hz")},
        alignment_source="provided")


def scalar(target, onset, *, status="validated"):
    acoustic = analyse_vot(dict(scope_start=.1, scope_end=.55, source_start=0, source_end=.7,
        burst=.3, pre_start=onset if onset < .3 else None, post_start=onset if onset >= .3 else None),
        dict(burst=.3, voicing=onset), dict(role="fixture"))
    measure = acoustic.measures[0]
    target.measurements["vot"] = ScalarMeasurement("vot_ms", measure.value, "ms", status,
        analysis=dict(measurement_id=f"fixture-{onset}", acoustic=acoustic.to_dict()))


class MeasurementContractTests(unittest.TestCase):
    def test_quantity_domains_distinguish_zero_negative_and_missing(self):
        for name in ("zcr", "rms", "high_frequency_ratio", "vot", "f1_lobanov_z", "intensity"):
            self.assertTrue(valid_value(name, 0), name)
        for name in ("vot", "f1_lobanov_z", "intensity"):
            self.assertTrue(valid_value(name, -20), name)
        for name in ("pitch", "f1", "f2", "f3"):
            self.assertFalse(valid_value(name, 0), name)
            self.assertFalse(valid_value(name, -1), name)
        for invalid in (math.nan, math.inf, -math.inf):
            self.assertFalse(valid_value("vot", invalid))
        for name in ("zcr", "high_frequency_ratio"):
            self.assertFalse(valid_value(name, 1.1))

    def test_dtw_means_and_real_pairing_are_preserved_as_evidence(self):
        left, right = phone("a", [100., 200., 200.]), phone("a", [100., 100., 200.])
        bind_tracks([left], dict(role="reference", audio_id="a"))
        bind_tracks([right], dict(role="learner", audio_id="b"))
        observations, _ = evaluate_phone_rules(PROFILES[0], [left], [right])
        observation = next(o for o in observations if o.feature == "f1")
        self.assertNotAlmostEqual(observation.reference_value, sum(left.tracks["f1"].values)/3)
        self.assertEqual(observation.evidence["statistic"], "dtw_resampled_mean")
        self.assertEqual(observation.evidence["pairs"][0]["reference_time"], .15)
        result = AnalysisResult("en", "a", "b", "a", "b", .7, [], [left], [right], observations=observations)
        state = explanation_evidence(result)
        derived = [e for e in state.evidence if e.tool == "pronunciation_derivation"]
        self.assertEqual(len(derived), 2)
        self.assertEqual(_quantities("E", derived[0])[0].value, observation.reference_value)
        self.assertEqual(derived[0].acoustic["measurements"][0]["measurement_id"], observation.evidence["measurement_id"])

    def test_single_signed_vot_bypasses_curve_minimum_frames(self):
        for onset in (.2, .3, .34):
            with self.subTest(onset=onset):
                left, right = phone(), phone()
                scalar(left, onset)
                scalar(right, .35)
                observations, _ = evaluate_phone_rules(PROFILES[0], [left], [right])
                self.assertEqual(len(observations), 1)
                self.assertAlmostEqual(observations[0].reference_value, (onset-.3)*1000)
                self.assertNotEqual(observations[0].evidence["method"], "dtw")
                self.assertEqual(left.tracks, {})

    def test_missing_vot_and_duplicated_fake_curve_are_not_landmark_evidence(self):
        left, right = phone(), phone()
        for target in (left, right):
            target.tracks["vot"] = FeatureTrack("vot", [.1, .55], [30., 30.], "ms")
        observations, checks = evaluate_phone_rules(PROFILES[0], [left], [right])
        self.assertEqual(observations, [])
        self.assertIn("地标", checks[0]["checks"][0]["reason"])

    def test_provisional_vot_is_not_promoted_to_confirmed_diagnosis(self):
        left, right = phone(), phone()
        scalar(left, .2, status="provisional")
        scalar(right, .3)
        observations, assessments = evaluate_phone_rules(PROFILES[0], [left], [right])
        self.assertEqual(observations[0].evaluation_status, "provisional")
        self.assertEqual(observations[0].score, 0)
        self.assertIn("候选", observations[0].message)
        self.assertEqual(assessments[0]["checks"][0]["status"], "provisional")

    def test_unknown_energy_protocol_is_not_mixed_with_new_values(self):
        old = FeatureTrack("high_frequency_ratio", [0, .1], [0., 0.], "ratio")
        new = FeatureTrack("high_frequency_ratio", [0, .1], [.2, .2], "ratio", analysis=dict(protocol="energy-v2"))
        self.assertIn("版本未记录", compatibility(old, new))
        comparison = compare_phone_evidence(AnalysisRequest("a", "b", "en", []),
            PhoneFeatureTracks(1, "s", 0, .1, {old.name: old}), PhoneFeatureTracks(1, "s", 0, .1, {new.name: new}))
        self.assertFalse(comparison.errors)
        self.assertTrue(comparison.unmeasured)

    def test_no_old_error_threshold_applies_to_new_energy_ratio_or_recording_gain(self):
        for name, unit, metadata in (("high_frequency_ratio", "ratio", dict(protocol="energy-v2", algorithm="power")),
                                      ("rms", "amplitude", {}), ("intensity", "dB", {})):
            left = FeatureTrack(name, [0, .1], [0., 0.], unit, analysis=metadata.copy())
            right = FeatureTrack(name, [0, .1], [1., 1.], unit, analysis=metadata.copy())
            result = compare_phone_evidence(AnalysisRequest("a", "b", "en", []),
                PhoneFeatureTracks(1, "s", 0, .1, {name: left}), PhoneFeatureTracks(1, "s", 0, .1, {name: right}))
            self.assertEqual(result.errors, [])
            self.assertEqual(len(result.observations), 1)

    def test_scalar_json_textgrid_and_report_keep_zero_and_same_landmarks(self):
        left, right = phone(), phone()
        scalar(left, .3)
        scalar(right, .3)
        observations, assessments = evaluate_phone_rules(PROFILES[0], [left], [right])
        result = AnalysisResult("en", "a", "b", "a", "b", .7, [], [left], [right],
            profile_snapshot={"id": "fixture"}, observations=observations, phone_assessments=assessments)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            write_json(result, root/"report.json")
            write_textgrid(result, root/"report.TextGrid")
            payload = json.loads((root/"report.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["learner_phones"][0]["measurements"]["vot"]["value"], 0.)
            grid = parselmouth.read(str(root/"report.TextGrid"))
            tier = next(i for i in range(1, call(grid, "Get number of tiers")+1)
                        if call(grid, "Get tier name", i) == "VOT_1_events")
            self.assertEqual(call(grid, "Get number of points", tier), 1)
            mark = call(grid, "Get label of point", tier, 1)
            self.assertIn("release_burst", mark)
            self.assertIn("post_release_periodicity", mark)
            self.assertIn("0 ms", render_report(payload))
            self.assertTrue(write_overlay_png(result, root/"overlay.png"))


class PronunciationEntryTests(unittest.TestCase):
    def test_real_entry_runs_shared_vot_candidates_and_does_not_invent_clear_vowel(self):
        sample_rate = 44100
        time = np.arange(int(.7*sample_rate))/sample_rate
        rng = np.random.default_rng(5489)
        for onset in (None, .2):
            with self.subTest(onset=onset), tempfile.TemporaryDirectory() as temporary:
                signal = np.zeros_like(time)
                if onset is not None:
                    mask = (time >= onset) & (time < .3)
                    signal[mask] = .025*np.sin(2*np.pi*220*time[mask])
                mask = (time >= .3) & (time < .33)
                signal[mask] = rng.normal(0, .25, mask.sum())
                mask = time >= .33
                signal[mask] = .4*np.sin(2*np.pi*220*time[mask])
                path = Path(temporary)/"stop.wav"
                parselmouth.Sound(signal, sample_rate).save(str(path), "WAV")
                request = AnalysisRequest(str(path), str(path), "en", [PhoneSpec("t", reference_start=.1,
                    reference_end=.33, learner_start=.1, learner_end=.33, alignment_source="provided")],
                    profile_id=PROFILES[0].id, qwen_explain=False)
                request.sources = {role: dict(original_sound_domain=[5., 5.+signal.size/sample_rate], wav_to_sound_offset_sec=5.)
                                   for role in ('reference', 'learner')}
                result = analyze_pronunciation(request)
                target = result.learner_phones[0]
                self.assertNotIn("vot", target.tracks)
                measure = target.measurements["vot"]
                self.assertIsNotNone(measure.value, measure.reason)
                self.assertEqual(measure.status, "provisional")
                self.assertGreater(measure.value, 0) if onset is None else self.assertLess(measure.value, 0)
                self.assertIsNone(target.measurements["burst_to_clear_vowel_ms"].value)
                self.assertTrue(measure.analysis["measurement_id"].startswith("m-"))
                self.assertEqual(result.errors, [])
                self.assertEqual(result.sources['learner']['timeline_status'], 'mapped')
                self.assertEqual(measure.analysis['source']['wav_to_sound_offset_sec'], 5.)
                json.dumps(result.to_dict(), allow_nan=False)

    def test_model_summary_preserves_ids_without_mutating_raw_curves(self):
        from praat_ai.measurement_protocol import explanation_summary
        left = phone('a', [100., 200., 200.])
        bind_tracks([left], dict(role='reference', audio_id='fixture'))
        payload = dict(reference_phones=[left.to_dict()])
        summary = explanation_summary(payload)
        self.assertIn('values', payload['reference_phones'][0]['tracks']['f1'])
        self.assertNotIn('values', summary['reference_phones'][0]['tracks']['f1'])
        self.assertEqual(summary['reference_phones'][0]['tracks']['f1']['analysis']['measurement_id'],
                         payload['reference_phones'][0]['tracks']['f1']['analysis']['measurement_id'])

    def test_candidate_explanation_does_not_become_confirmed_diagnosis(self):
        from praat_ai.tutor import guard_explanation
        left, right = phone(), phone()
        scalar(left, .2, status='provisional')
        scalar(right, .3, status='provisional')
        result = AnalysisResult('en', 'a', 'b', 'a', 'b', .7, [], [left], [right])
        text, status = guard_explanation(result, 'VOT 已确认，证明发音错误。', caveat=False)
        self.assertEqual(status, 'rejected_masked')
        self.assertIn('自动候选', text)
