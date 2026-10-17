"""阶段 3 回归：双音频的可选 AI 解释必须走与云端同一份证据守卫。

设计文档点名的缺口：纠音报告的可选 AI 解释直接调用文字接口交付，没经过云端对话已有的
证据守卫；本机/云端两种 provider 都一样。本模块先把缺口写成测试（未修复时必须失败）：

① 解释里的数值没有对上本次测量 → 必须遮蔽并标记，不得原样交付；
② 本次对比不完整（证据不足／缺依赖）时 → 解释必须带上与 JSON 状态一致的结论等级说明，
   不能给出无条件结论；
③ 视觉解释失败必须留原因，不得静默丢弃；
④ 两条解释路径（text / vision）都经 `report_guards.guard_delivered_text` 这一个入口。

除端到端那一条需要真实音频与 Pillow 之外，其余都是纯逻辑。
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
from praat_ai.pronunciation import conclusion_caveat, explanation_evidence
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

        self.assertIn("not_evaluated", caveat)
        self.assertIn("dependency_missing", caveat)
        self.assertIn("不构成独立音素识别", caveat)
        self.assertIn("学习者 F1 均值 1100 Hz。", guarded)
        self.assertTrue(guarded.endswith(caveat))
        self.assertEqual(status, "verified")

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
