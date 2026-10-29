"""MFA execution must time out, honor cancellation and terminate its process tree.

Cancellation must propagate through pronunciation/tutor and never become a
proportional-alignment fallback or a completed report. These tests use isolated
Python subprocesses, not an MFA model or GUI Praat.
"""

import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from praat_ai.audio import SoundSamples
from praat_ai.config import AlignmentConfig, MfaAlignmentConfig
from praat_ai.forced_alignment import (
    AlignmentCancelled,
    AlignmentError,
    CompositeAligner,
    MfaAligner,
    ProportionalAligner,
)
from praat_ai.models import AlignedPhone, AlignmentResult, AnalysisRequest, PhoneSpec
from praat_ai.pronunciation import analyze_pronunciation
from praat_ai.tutor import run_tutor


def write_sine(path: Path, frequency: float = 220.0, seconds: float = 0.3,
               sample_rate: int = 16000) -> None:
    import math
    import wave

    times = np.arange(int(sample_rate * seconds)) / sample_rate
    samples = (12000 * np.sin(2 * math.pi * frequency * times)).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


def sleeping_command(seconds: float, marker: Path) -> list[str]:
    """真的会睡、睡完写标记文件的命令：标记文件没出现就说明它被杀了。"""

    code = (f"import time,pathlib;time.sleep({seconds});"
            f"pathlib.Path(r'{marker}').write_text('done')")
    return [sys.executable, "-c", code]


class ScriptedMfa(MfaAligner):
    """把 ``build_command`` 换成测试脚本，其余（临时目录、dictionary、进程管理）走真实现。"""

    def __init__(self, config: MfaAlignmentConfig, command: list[str]):
        super().__init__(config)
        self.command = list(command)

    def available(self) -> bool:  # 不需要真的装 MFA
        return True

    def build_command(self, corpus_dir, dictionary_path, output_dir) -> list[str]:
        return list(self.command)


class MfaTimeoutTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.audio = self.root / "learner.wav"
        write_sine(self.audio)
        self.marker = self.root / "finished.txt"

    def phones(self):
        return [PhoneSpec("a", learner_start=0.0, learner_end=0.2)]

    def test_timeout_fails_with_reason_and_kills_the_child(self):
        config = MfaAlignmentConfig(enabled=True, executable=sys.executable,
                                    acoustic_model="fixture", timeout_sec=1.0)
        aligner = ScriptedMfa(config, sleeping_command(30.0, self.marker))

        started = time.monotonic()
        with self.assertRaises(AlignmentError) as caught:
            aligner.align(self.audio, self.phones(), "ja", "")
        elapsed = time.monotonic() - started

        self.assertIn("超时", str(caught.exception))
        self.assertLess(elapsed, 20.0, "超时必须结束等待，不能挂住")
        time.sleep(1.0)
        self.assertFalse(self.marker.exists(), "超时后子进程必须被杀掉")

    def test_cancel_raises_a_cancellable_error_within_the_deadline(self):
        config = MfaAlignmentConfig(enabled=True, executable=sys.executable,
                                    acoustic_model="fixture", timeout_sec=60.0)
        cancel = threading.Event()
        aligner = ScriptedMfa(config, sleeping_command(30.0, self.marker))
        threading.Timer(0.4, cancel.set).start()

        started = time.monotonic()
        with self.assertRaises(AlignmentCancelled) as caught:
            aligner.align(self.audio, self.phones(), "ja", "", cancel=cancel)
        elapsed = time.monotonic() - started

        self.assertIn("取消", str(caught.exception))
        self.assertLess(elapsed, 20.0, "取消必须尽快结束等待，不能等到超时")
        time.sleep(1.0)
        self.assertFalse(self.marker.exists(), "取消后子进程必须被杀掉")

    def test_cancel_before_start_never_spawns_the_command(self):
        config = MfaAlignmentConfig(enabled=True, executable=sys.executable,
                                    acoustic_model="fixture", timeout_sec=60.0)
        cancel = threading.Event()
        cancel.set()
        aligner = ScriptedMfa(config, sleeping_command(30.0, self.marker))

        started = time.monotonic()
        with self.assertRaises(AlignmentCancelled):
            aligner.align(self.audio, self.phones(), "ja", "", cancel=cancel)

        self.assertLess(time.monotonic() - started, 5.0)
        self.assertFalse(self.marker.exists())

class CancelIsNotAFallbackTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.audio = Path(self.directory.name) / "learner.wav"
        write_sine(self.audio)

    def phones(self):
        return [PhoneSpec("a", learner_start=0.0, learner_end=0.1)]

    def test_composite_does_not_swallow_cancellation(self):
        class Cancelling:
            name = "cancelling"

            def available(self):
                return True

            def align(self, audio_path, phones, language, transcript="", *, cancel=None):
                raise AlignmentCancelled("已取消对齐。")

        composite = CompositeAligner([Cancelling(), ProportionalAligner()], strict=False)

        with self.assertRaises(AlignmentCancelled):
            composite.align(str(self.audio), self.phones(), "ja", "")

    def test_composite_still_falls_back_on_real_alignment_failure(self):
        class Broken:
            name = "broken"

            def available(self):
                return True

            def align(self, audio_path, phones, language, transcript="", *, cancel=None):
                raise AlignmentError("MFA 对齐失败。")

        composite = CompositeAligner([Broken(), ProportionalAligner()], strict=False)

        result = composite.align(str(self.audio), self.phones(), "ja", "")

        self.assertTrue(any("MFA 对齐失败" in warning for warning in result.warnings))
        self.assertEqual(result.source, "proportional")


class CancellationThreadingTests(unittest.TestCase):
    """取消信号必须从分析入口一路走到对齐层。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)

    def request(self):
        return AnalysisRequest(
            "reference.wav", "learner.wav", "ja",
            [PhoneSpec("a", reference_start=0.0, reference_end=0.1)],
            profile_id=None,
        )

    class RecordingAligner:
        """像 CompositeAligner 那样工作：记录拿到的 cancel，能取消就取消，否则给出夹具对齐。"""

        def __init__(self):
            self.cancel = "unset"
            self.called = False

        def align(self, audio_path, phones, language, transcript="", *, cancel=None):
            self.called = True
            self.cancel = cancel
            if cancel is not None and cancel.is_set():
                raise AlignmentCancelled("已取消分析。")
            return AlignmentResult(
                phones=[AlignedPhone(index, phone.ipa, phone.reference_start,
                                     phone.reference_end, 0.5)
                        for index, phone in enumerate(phones, start=1)],
                source="fixture",
                confidence=0.5,
                warnings=["夹具：没有真实对齐，只用请求边界。"],
            )

    def test_analysis_passes_the_cancel_event_to_the_aligner(self):
        aligner = self.RecordingAligner()
        cancel = threading.Event()
        audio = [SoundSamples(np.zeros(3200), 16000), SoundSamples(np.ones(3200), 16000)]
        with patch("praat_ai.audio.read_wav", side_effect=audio), patch(
            "praat_ai.pronunciation.build_aligner", return_value=aligner
        ), patch("praat_ai.pronunciation.extract_phone_tracks", side_effect=[[], []]):
            # 特征提取用空夹具：这条用例只关心取消信号有没有走到对齐层。
            result = analyze_pronunciation(self.request(), cancel=cancel)

        self.assertTrue(aligner.called)
        self.assertIs(aligner.cancel, cancel)
        self.assertTrue(any("夹具" in warning for warning in result.warnings), result.warnings)

    def test_cancelled_analysis_raises_instead_of_reporting_completion(self):
        aligner = self.RecordingAligner()
        cancel = threading.Event()
        cancel.set()
        audio = [SoundSamples(np.zeros(3200), 16000), SoundSamples(np.ones(3200), 16000)]
        with patch("praat_ai.audio.read_wav", side_effect=audio), patch(
            "praat_ai.pronunciation.build_aligner", return_value=aligner
        ):
            with self.assertRaises(AlignmentCancelled):
                analyze_pronunciation(self.request(), cancel=cancel)

    def test_run_tutor_cancelled_before_start_does_not_touch_praat(self):
        cancel = threading.Event()
        cancel.set()
        with patch("praat_ai.tutor.PraatBridge.from_praat",
                   side_effect=AssertionError("取消后不应再连接 Praat")):
            with self.assertRaises(AlignmentCancelled) as caught:
                run_tutor(config_path=self.root / "missing-config.json",
                          request=self.request(), cancel=cancel)

        self.assertIn("取消", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
