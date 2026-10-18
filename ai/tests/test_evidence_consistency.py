"""阶段 1–2 回归：缺测与证据不足不得被表达成「无差异」或「正常结论」。

本模块先复现设计文档里三个已复现缺陷（未修复的代码上必须失败）：

① 双方声学特征全为空时，旧式对比仍写「未发现超过旧式声学对比阈值的差异」；
② 学习者特征全为未定义值时，旧式路径产出 severity=1.0、双方 0.0 的偏差条目；
③ 本机规划器的最终交付绕过报告守卫，把没有证据支撑的 999 Hz 原样交给用户。

还覆盖阶段 2 的依赖/缺测状态（「依赖未安装」≠「已安装但提取失败」）、
缺测原因在交付物里可见，以及「一段音频只提取一次基频/共振峰」。

除了「一次提取」用例需要真实 praat-parselmouth 以外，其余都是纯逻辑，
所以在没有 Parselmouth 的解释器（例如 Python 3.14）上也能跑；该用例会
如实报 skipped，不报通过。
"""

import math
import re
import tempfile
import unittest
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from praat_ai import chat, report_guards
from praat_ai.audio import SoundSamples, extract_phone_tracks
from praat_ai.models import AnalysisRequest, FeatureTrack, PhoneFeatureTracks, PhoneSpec
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.report import write_textgrid
from test_agent_loop import FakeClient, Recorder, answer, run, tool_call

try:  # 纯逻辑用例不依赖它；只有「一次提取」用例需要。
    import parselmouth
except ImportError:  # pragma: no cover - Python 3.13+ 的 requirements 不装 Parselmouth。
    parselmouth = None

HAS_PARSELMOUTH = parselmouth is not None

TRACK_SAMPLES = {
    "f1": ([700.0, 710.0], "Hz"),
    "f2": ([1200.0, 1210.0], "Hz"),
    "f3": ([2500.0, 2510.0], "Hz"),
    "pitch": ([120.0, 121.0], "Hz"),
    "intensity": ([60.0, 61.0], "dB"),
    "rms": ([0.1, 0.11], "amplitude"),
    "spectral_centroid": ([3000.0, 3100.0], "Hz"),
    "high_frequency_ratio": ([0.4, 0.5], "ratio"),
    "zcr": ([0.2, 0.3], "ratio"),
}


def track_phone(ipa, index=1, start=0.0, end=0.2):
    return PhoneFeatureTracks(
        index,
        ipa,
        start,
        end,
        {
            name: FeatureTrack(name, [start, end], list(samples), unit)
            for name, (samples, unit) in TRACK_SAMPLES.items()
        },
        1.0,
        "provided",
    )


def legacy_analysis(left, right, language="ja"):
    """旧式对比路径（``profile_id=None``）的离线夹具：不读真实音频、不跑对齐。"""

    request = AnalysisRequest(
        "reference.wav",
        "learner.wav",
        language,
        [
            PhoneSpec(
                phone.ipa,
                reference_start=phone.start,
                reference_end=phone.end,
                learner_start=other.start,
                learner_end=other.end,
            )
            for phone, other in zip(left, right)
        ],
        profile_id=None,
    )
    audio = [SoundSamples(np.zeros(6400), 16000), SoundSamples(np.ones(6400), 16000)]
    with patch("praat_ai.audio.read_wav", side_effect=audio), patch(
        "praat_ai.pronunciation.extract_phone_tracks", side_effect=[left, right]
    ):
        return analyze_pronunciation(request)


def textgrid_labels(path: Path) -> list[str]:
    """按写出的 TextGrid 正文取回每个区间标签，不依赖 Parselmouth。"""

    return [
        line.split("=", 1)[1].strip().strip('"')
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip().startswith("text =")
    ]




def write_tone(path: Path, *, seconds: float, frequency: float = 220.0, sample_rate: int = 16000) -> None:
    """写一小段真实 PCM WAV：这些用例要真的走读取/提取，不用假音频对象。"""

    times = np.arange(int(sample_rate * seconds)) / sample_rate
    samples = (12000 * np.sin(2 * math.pi * frequency * times)).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def unmeasurable_reasons(assessment: dict) -> str:
    return "；".join(
        str(check.get("reason") or "")
        for check in assessment.get("checks") or []
        if check.get("status") == "insufficient_evidence"
    )


class NoMeasurementIsNotNoDifferenceTests(unittest.TestCase):
    """缺陷①：什么都没测出来时，交付物不能说「没有超过阈值的差异」。"""

    def test_empty_feature_tracks_are_delivered_as_unevaluated(self) -> None:
        left, right = [track_phone("a")], [track_phone("a")]
        for phone in left + right:
            phone.tracks.clear()

        result = legacy_analysis(left, right)

        self.assertEqual(result.errors, [])
        self.assertEqual(result.phone_assessments[0]["status"], "insufficient_evidence")
        payload = result.to_dict()
        self.assertEqual(payload["check_statuses"].get("phone_acoustics"), "not_evaluated")
        self.assertEqual(payload["phone_assessments"][0]["status"], "insufficient_evidence")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.TextGrid"
            write_textgrid(result, path)
            text = path.read_text(encoding="utf-8")
            labels = textgrid_labels(path)

        self.assertNotIn("未发现超过", text)
        self.assertTrue(labels)
        self.assertIn("证据不足", labels[0])
        self.assertIn("未评估", labels[0])


class UnmeasurableIsNotSeverityOneTests(unittest.TestCase):
    """缺陷②：量不出来不是「严重度 1.0 的偏差」，也不能填 0.0/0.0 当测量值。"""

    def test_undefined_learner_values_are_not_a_deviation(self) -> None:
        left, right = [track_phone("a")], [track_phone("a")]
        for track in right[0].tracks.values():
            track.values = [math.nan, math.nan]

        result = legacy_analysis(left, right)

        self.assertEqual(result.errors, [])
        for error in result.errors:
            self.assertNotEqual(error.severity, 1.0)
        assessment = result.phone_assessments[0]
        self.assertEqual(assessment["status"], "insufficient_evidence")
        self.assertIn("无法可靠测量", unmeasurable_reasons(assessment))
        payload = result.to_dict()
        self.assertEqual(payload["errors"], [])
        self.assertEqual(payload["phone_assessments"][0]["status"], "insufficient_evidence")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.TextGrid"
            write_textgrid(result, path)
            text = path.read_text(encoding="utf-8")
            labels = textgrid_labels(path)

        self.assertNotIn("未发现超过", text)
        self.assertIn("证据不足", labels[0])
        self.assertIn("无法可靠测量", labels[0])


class LocalDeliveryGuardTests(unittest.TestCase):
    """缺陷③：本机规划器的最终交付必须走与云端同一份证据守卫。"""

    def test_unsupported_number_cannot_reach_the_user(self) -> None:
        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.5}),
                answer("0.5 秒处基频 999 Hz。"),
            ]
        )
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])

        outcome = run(client, execute)

        self.assertNotIn("999", outcome.reply)
        self.assertIn(report_guards.UNVERIFIED_NUMBER, outcome.reply)
        self.assertEqual(outcome.delivery_guard, "rejected_masked")
        self.assertTrue(any("核对" in note for note in outcome.notes))

    def test_supported_number_is_delivered_unchanged(self) -> None:
        client = FakeClient(
            [
                tool_call("pitch", {"time": 0.5}),
                answer("0.5 秒处基频 220 Hz。"),
            ]
        )
        execute = Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")])

        outcome = run(client, execute)

        self.assertEqual(outcome.reply, "0.5 秒处基频 220 Hz。")
        self.assertEqual(outcome.delivery_guard, "verified")

    def test_both_delivery_paths_use_one_guard_entry(self) -> None:
        from praat_ai import cloud_agent

        self.assertIs(
            cloud_agent.report_guards.guard_delivered_text,
            report_guards.guard_delivered_text,
        )
        calls: list[str] = []
        original = report_guards.guard_delivered_text

        def spy(state, text, **kwargs):
            calls.append("guard")
            return original(state, text, **kwargs)

        with patch.object(report_guards, "guard_delivered_text", spy):
            run(
                FakeClient(
                    [
                        tool_call("pitch", {"time": 0.5}),
                        answer("0.5 秒处基频 999 Hz。"),
                    ]
                ),
                Recorder([(True, ["基频（0.500 秒处）= 220.000 Hz"], "")]),
            )
            cloud_agent.rescued_report("平均基频 999 Hz。", "数值“999 Hz”未匹配", ["999 Hz"])

        self.assertEqual(len(calls), 2)


class AcousticExtractionStatusTests(unittest.TestCase):
    """阶段 2：依赖状态可区分、缺测原因可见、一段音频只提取一次。"""

    def test_dependency_missing_differs_from_failed_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tone.wav"
            write_tone(path, seconds=0.3)
            phones = [PhoneSpec("a", reference_start=0.05, reference_end=0.25,
                                learner_start=0.05, learner_end=0.25, phone_index=1)]
            with patch.dict("sys.modules", {"parselmouth": None}):
                missing = {}
                extract_phone_tracks(path, phones, learner=True, diagnostics=missing)

            class Broken:
                def __init__(self, *_args, **_kwargs):
                    raise RuntimeError("boom")

            with patch.dict("sys.modules", {"parselmouth": SimpleNamespace(Sound=Broken)}):
                failed = {}
                extract_phone_tracks(path, phones, learner=True, diagnostics=failed)

        self.assertEqual(missing["pitch_formant_status"], "dependency_missing")
        self.assertEqual(failed["pitch_formant_status"], "extraction_failed")
        self.assertIn("parselmouth", missing["pitch_formant_reason"])
        self.assertIn("boom", failed["pitch_formant_reason"])
        self.assertTrue(missing["pitch_formant_reason"] != failed["pitch_formant_reason"])
        # 两种情形都不许被当成「测到了，只是没差异」。
        self.assertEqual(missing["pitch_formant_extractions"], 0)
        self.assertEqual(failed["pitch_formant_extractions"], 0)

    def test_missing_reason_reaches_the_deliverable(self) -> None:
        """真实音频、真实提取，只是解释器里没有 praat-parselmouth：原因必须写进交付物。"""

        with tempfile.TemporaryDirectory() as directory:
            reference = Path(directory) / "reference.wav"
            learner = Path(directory) / "learner.wav"
            write_tone(reference, seconds=0.6)
            write_tone(learner, seconds=0.6)
            request = AnalysisRequest(
                str(reference),
                str(learner),
                "ja",
                [
                    PhoneSpec("a", reference_start=0.05, reference_end=0.25,
                              learner_start=0.05, learner_end=0.25, phone_index=1),
                    PhoneSpec("a", reference_start=0.3, reference_end=0.5,
                              learner_start=0.3, learner_end=0.5, phone_index=2),
                ],
                profile_id=None,
            )
            with patch.dict("sys.modules", {"parselmouth": None}):
                result = analyze_pronunciation(request)

        payload = result.to_dict()
        # 频谱类特征仍然比得到，所以音位层是 observed；「基频/共振峰这一族没测」是另一个
        # 独立状态（pitch_formant_measurement）。两者分开，正好说明状态没有被合并掉。
        self.assertEqual(payload["check_statuses"]["phone_acoustics"], "observed")
        self.assertEqual(payload["check_statuses"].get("pitch_formant_measurement"), "dependency_missing")
        reasons = [warning for warning in payload["warnings"] if "基频/共振峰测量" in warning]
        self.assertEqual(len(reasons), 2)  # 参考侧与学习者侧各一条，不是一句笼统的说明。
        self.assertTrue(all("praat-parselmouth" in warning for warning in reasons))
        self.assertEqual(
            [key for key in payload["check_statuses"] if "pitch_formant" in key],
            ["pitch_formant_measurement"],
        )

    @unittest.skipUnless(HAS_PARSELMOUTH, "需要 praat-parselmouth（Python 3.13 及以上不安装）")
    def test_one_audio_is_measured_once_and_matches_per_phone_extraction(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "tone.wav"
            sample_rate = 16000
            times = np.arange(int(sample_rate * 0.5)) / sample_rate
            samples = (12000 * np.sin(2 * math.pi * 220.0 * times)).astype("<i2")
            with wave.open(str(path), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(sample_rate)
                handle.writeframes(samples.tobytes())

            from praat_ai.audio import read_wav

            windows = [(0.05, 0.15), (0.2, 0.3), (0.35, 0.45)]
            phones = [
                PhoneSpec("a", reference_start=start, reference_end=end, learner_start=start,
                          learner_end=end, phone_index=index)
                for index, (start, end) in enumerate(windows, start=1)
            ]

            opened: list[str] = []
            real_sound = parselmouth.Sound

            def counting_sound(*args, **kwargs):
                opened.append(str(args[0] if args else kwargs.get("file_path", "")))
                return real_sound(*args, **kwargs)

            diagnostics = {}
            with patch.object(parselmouth, "Sound", counting_sound):
                tracks = extract_phone_tracks(path, phones, learner=True, diagnostics=diagnostics)

            self.assertEqual(len(opened), 1)
            self.assertEqual(diagnostics["pitch_formant_status"], "ok")
            self.assertEqual(diagnostics["pitch_formant_extractions"], 1)

            # 逐音素重新读取整段音频（旧行为）必须给出同样的数值。
            for track, (start, end) in zip(tracks, windows):
                sound = real_sound(str(path))
                pitch = sound.to_pitch(time_step=0.01, pitch_floor=75.0, pitch_ceiling=600.0)
                formant = sound.to_formant_burg(
                    time_step=0.01, max_number_of_formants=5.0, maximum_formant=5500.0,
                    window_length=0.025, pre_emphasis_from=50.0,
                )
                for time in track.tracks["pitch"].times:
                    self.assertAlmostEqual(
                        track.tracks["pitch"].values[track.tracks["pitch"].times.index(time)],
                        float(pitch.get_value_at_time(time)),
                        places=9,
                    )
                    self.assertAlmostEqual(
                        track.tracks["f1"].values[track.tracks["pitch"].times.index(time)],
                        float(formant.get_value_at_time(1, time)),
                        places=9,
                    )


if __name__ == "__main__":
    unittest.main()
