"""Optional tutor explanations share the evidence guard used by cloud reports.

Unmatched values are masked, incomplete comparisons retain status, and visual
explanation failures retain a reason. The end-to-end case uses isolated audio
and Pillow; other cases exercise protocol logic.
"""

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from praat_ai import report_guards
from praat_ai.models import (
    AnalysisRequest,
    AnalysisResult,
    FeatureTrack,
    PhoneFeatureTracks,
    PhoneSpec,
    PronunciationError,
)
from praat_ai.pronunciation import conclusion_caveat, conclusion_gaps, explanation_evidence
from praat_ai.qwen import QwenError
from praat_ai.tutor import guard_explanation, run_tutor

try:
    import parselmouth  # noqa: F401
    import PIL  # noqa: F401
    HAS_IMAGE_TOOLS = True
except ImportError:  # pragma: no cover - 缺依赖的解释器上如实跳过
    HAS_IMAGE_TOOLS = False


def track(phone_index, ipa, start, end, values):
    return PhoneFeatureTracks(
        phone_index,
        ipa,
        start,
        end,
        {
            name: FeatureTrack(name, [start, end], list(samples), unit)
            for name, (samples, unit) in values.items()
        },
        1.0,
        "provided",
    )


MEASURED = {
    "f1": ([700.0, 700.0], "Hz"),
    "pitch": ([120.0, 120.0], "Hz"),
}


def analysis(
    *,
    learner_f1=(1100.0, 1100.0),
    learner_pitch=(120.0, 120.0),
    check_statuses=None,
    errors=None,
):
    reference = track(1, "a", 0.0, 0.2, MEASURED)
    learner = track(1, "a", 0.0, 0.2, {
        "f1": (list(learner_f1), "Hz"),
        "pitch": (list(learner_pitch), "Hz"),
    })
    error = PronunciationError(
        phone_index=1,
        ipa="a",
        start=0.0,
        end=0.2,
        feature="f1",
        score=4.0,
        severity=1.0,
        direction="偏高",
        message="/a/ 的 f1 偏高：参考 700.000Hz，学习者 1100.000Hz。",
        reference_value=700.0,
        learner_value=1100.0,
        unit="Hz",
        alignment_confidence=1.0,
    )
    return AnalysisResult(
        language="ja",
        reference_name="reference.wav",
        learner_name="learner.wav",
        reference_path="reference.wav",
        learner_path="learner.wav",
        duration=0.2,
        errors=list(errors) if errors is not None else [error],
        reference_phones=[reference],
        learner_phones=[learner],
        warnings=[],
        phone_assessments=[dict(phone_index=1, ipa="a", status="observed", checks=[
            dict(feature="f1", status="observed", reason="")])],
        check_statuses=check_statuses if check_statuses is not None else {
            "phone_acoustics": "observed", "pitch_formant_measurement": "ok"},
    )


class ExplanationEvidenceTests(unittest.TestCase):
    def test_evidence_carries_both_sides_measured_values(self):
        result = analysis()
        state = explanation_evidence(result)

        self.assertEqual(len(state.evidence), 2)
        rows = " ".join(row for item in state.evidence for row in item.rows)
        self.assertIn("700.000", rows)
        self.assertIn("1100.000", rows)
        self.assertIn("120.000", rows)
        self.assertTrue(all(item.kind == "measurement" for item in state.evidence))
        self.assertTrue(report_guards.report_context_data(state))


class GuardedExplanationTests(unittest.TestCase):
    def test_fabricated_number_is_masked_and_measured_number_survives(self):
        result = analysis()
        text = "学习者 F1 均值 1100 Hz，参考 F1 均值 700 Hz；本次音高 999 Hz。"

        guarded, status = guard_explanation(result, text)

        self.assertNotIn("999", guarded)
        self.assertIn(report_guards.UNVERIFIED_NUMBER, guarded)
        self.assertIn("1100 Hz", guarded)
        self.assertIn("700 Hz", guarded)
        self.assertEqual(status, "rejected_masked")

    def test_measured_explanation_keeps_its_text(self):
        result = analysis()
        text = "学习者 F1 均值 1100 Hz，参考 F1 均值 700 Hz。"

        guarded, status = guard_explanation(result, text)

        self.assertEqual(guarded, text)
        self.assertEqual(status, "verified")

    def test_gaps_force_a_conclusion_caveat(self):
        result = analysis(check_statuses={
            "phone_acoustics": "not_evaluated", "pitch_formant_measurement": "dependency_missing"})

        caveat = conclusion_caveat(result)
        guarded, status = guard_explanation(result, "学习者 F1 均值 1100 Hz。")

        # 用户读到的是人话：内部状态码只留在结构化字段里，不抄进结论等级说明。
        self.assertIn("音位声学对比没有评估", caveat)
        self.assertIn("缺少必要的组件", caveat)
        self.assertNotIn("not_evaluated", caveat)
        self.assertNotIn("dependency_missing", caveat)
        self.assertIn("本次缺测", caveat)
        self.assertNotIn("发音正确性判定", caveat)
        self.assertIn("学习者 F1 均值 1100 Hz。", guarded)
        self.assertTrue(guarded.endswith(caveat))
        self.assertEqual(status, "verified")

    def test_raw_statuses_stay_structured_next_to_the_caveat(self):
        statuses = {"phone_acoustics": "not_evaluated", "pitch_formant_measurement": "unknown"}
        result = analysis(check_statuses=statuses)

        gaps = conclusion_gaps(result)

        self.assertEqual([gap["check"] for gap in gaps],
                         ["phone_acoustics", "pitch_formant_measurement"])
        self.assertEqual([gap["status"] for gap in gaps], ["not_evaluated", "unknown"])
        self.assertEqual(gaps[0]["label"], "音位声学对比")
        self.assertEqual(result.check_statuses, statuses)

    def test_unknown_status_code_is_not_echoed(self):
        result = analysis(check_statuses={"phone_acoustics": "mystery_code_v9"})

        caveat = conclusion_caveat(result)

        self.assertNotIn("mystery_code_v9", caveat)
        self.assertIn("暂不可用或状态不明", caveat)

    def test_complete_comparison_has_no_caveat(self):
        result = analysis()

        self.assertEqual(conclusion_caveat(result), "")
        guarded, _ = guard_explanation(result, "学习者 F1 均值 1100 Hz。")
        self.assertNotIn("本次对比不完整", guarded)

    def test_caveat_is_recorded_in_warnings(self):
        result = analysis(check_statuses={"phone_acoustics": "partial"})

        guard_explanation(result, "学习者 F1 均值 1100 Hz。")

        self.assertTrue(any("结论等级" in warning for warning in result.warnings), result.warnings)


class TutorExplanationDeliveryTests(unittest.TestCase):
    """端到端：run_tutor 的两条解释路径都必须经过共用的守卫入口。"""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.output = self.root / "output"
        self.output.mkdir()
        self.reference = self.root / "reference.wav"
        self.learner = self.root / "learner.wav"
        write_sine(self.reference, 220.0)
        write_sine(self.learner, 900.0)

    def _run(self, client, *, vision=False):
        helper = types.SimpleNamespace(
            get_selected=lambda: [
                dict(id=1, name="reference", **{"class": "Sound"}, file=str(self.reference)),
                dict(id=2, name="learner", **{"class": "Sound"}, file=str(self.learner)),
            ],
            get_output_dir=lambda: str(self.output))
        request = AnalysisRequest(
            1, 2, "en",
            [PhoneSpec("a", reference_start=0, reference_end=0.1,
                       learner_start=0, learner_end=0.1)],
            profile_id=None, qwen_explain=True, qwen_vision=vision)
        with patch.dict(sys.modules, {"praat": helper}), patch.dict(
            os.environ, {"PRAAT_AI_REPORT_DIR": str(self.root / "reports")}
        ), patch("praat_ai.tutor.detect_gpu", return_value=None), patch(
            "praat_ai.tutor.QwenServerManager.ensure_started"
        ), patch("praat_ai.tutor.QwenClient", return_value=client):
            return run_tutor(config_path=self.root / "missing-config.json", request=request)

    def test_text_explanation_is_masked_before_delivery(self):
        class Client:
            def available(self):
                return True

            def explain_errors(self, request, result):
                return "学习者 F1 均值 900 Hz，参考 F1 均值 220 Hz；另称基频 5000 Hz。"

            def vision_explain(self, *args, **kwargs):
                raise AssertionError("未请求视觉解释")

        calls = []
        original = report_guards.guard_delivered_text

        def spy(state, text, **kwargs):
            calls.append("guard")
            return original(state, text, **kwargs)

        with patch.object(report_guards, "guard_delivered_text", spy):
            outputs = self._run(Client())

        explanation = outputs.result.explanation
        self.assertIn(report_guards.UNVERIFIED_NUMBER, explanation)
        self.assertNotIn("5000", explanation)
        self.assertEqual(calls, ["guard"])
        payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["explanation_status"], "rejected_masked")
        self.assertEqual(payload["explanation"], explanation)

    @unittest.skipUnless(HAS_IMAGE_TOOLS, "需要 Pillow 与 parselmouth 才能渲染叠加图")
    def test_vision_failure_is_recorded_not_swallowed(self):
        class Client:
            def available(self):
                return True

            def explain_errors(self, request, result):
                return "学习者 F1 均值 900 Hz。"

            def vision_explain(self, *args, **kwargs):
                raise QwenError("视觉模型不可用：连接被拒绝")

        outputs = self._run(Client(), vision=True)

        self.assertTrue(
            any("视觉解释" in warning and "连接被拒绝" in warning
                for warning in outputs.result.warnings),
            outputs.result.warnings,
        )
        payload = json.loads(outputs.json_path.read_text(encoding="utf-8"))
        self.assertTrue(any("视觉解释" in warning for warning in payload["warnings"]))


def write_sine(path: Path, frequency: float, seconds: float = 0.3,
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


if __name__ == "__main__":
    unittest.main()
