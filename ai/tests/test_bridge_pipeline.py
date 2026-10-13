import math
import io
import json
import os
import shutil
import sys
import tempfile
import types
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np

from praat_ai.models import AnalysisRequest, AnalysisResult, PhoneSpec
from praat_ai.tutor import run_tutor


def write_tone(path: Path, frequency: float) -> None:
    sample_rate = 16000
    times = np.arange(int(sample_rate * 0.2)) / sample_rate
    samples = (12000 * np.sin(2 * math.pi * frequency * times)).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(samples.tobytes())


class BridgePipelineTests(unittest.TestCase):
    def test_cli_request_profile_errors_are_readable_and_do_not_start_model(self) -> None:
        from run_ai_tutor import main
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            helper = types.SimpleNamespace(get_selected=lambda: [
                {"id": 1, "name": "reference", "class": "Sound", "file": str(root / "reference.wav")},
                {"id": 2, "name": "learner", "class": "Sound", "file": str(root / "learner.wav")},
            ])
            for profile_id, expected in (("ja-JP-standard-v1", "与目标语言 en 不匹配"),
                                         ("unknown-profile", "未知纠音判据预设")):
                with self.subTest(profile_id=profile_id):
                    path = root / "request.json"
                    path.write_text(json.dumps(dict(reference_object=1, learner_object=2,
                        language="en", profile_id=profile_id, phonemes=[dict(ipa="a")])), encoding="utf-8")
                    stderr = io.StringIO()
                    with patch.dict(sys.modules, {"praat": helper}), patch.object(sys, "argv", [
                        "run_ai_tutor.py", "--request", str(path), "--config", str(root / "missing.json"),
                        "--no-gui"]), patch("sys.stderr", stderr), patch(
                        "praat_ai.tutor.QwenServerManager.ensure_started", return_value=False) as start_model:
                        self.assertEqual(main(), 1)
                    self.assertIn(expected, stderr.getvalue())
                    self.assertNotIn("Traceback", stderr.getvalue())
                    start_model.assert_not_called()

    def test_tutor_rejects_report_root_inside_temporary_import_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "output"
            output.mkdir()
            helper = types.SimpleNamespace(
                get_selected=lambda: [
                    {"id": 1, "name": "reference", "class": "Sound", "file": str(root / "reference.wav")},
                    {"id": 2, "name": "learner", "class": "Sound", "file": str(root / "learner.wav")},
                ],
                get_output_dir=lambda: str(output),
            )
            result = AnalysisResult(
                language="cmn",
                reference_name="reference",
                learner_name="learner",
                reference_path="reference.wav",
                learner_path="learner.wav",
                duration=0.2,
                errors=[],
                reference_phones=[],
                learner_phones=[],
            )
            conflicts = [output, output / "reports"]
            if os.name == "nt":
                conflicts.append(Path(str(output).upper()) / "reports")
            with patch.dict(sys.modules, {"praat": helper}), patch(
                "praat_ai.tutor.analyze_pronunciation", return_value=result
            ), patch("praat_ai.tutor.detect_gpu", return_value=None):
                for report_root in conflicts:
                    with self.subTest(report_root=report_root), patch.dict(
                        os.environ, {"PRAAT_AI_REPORT_DIR": str(report_root)}
                    ):
                        with self.assertRaisesRegex(ValueError, "temporary Praat output"):
                            run_tutor(
                                config_path=root / "missing-config.json",
                                request=AnalysisRequest(
                                    reference_object=1,
                                    learner_object=2,
                                    language="cmn",
                                    phonemes=[],
                                    qwen_explain=False,
                                    qwen_vision=False,
                                ),
                            )
                        self.assertEqual(list(output.iterdir()), [])

    def test_tutor_writes_all_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            reference = root / "reference.wav"
            learner = root / "learner.wav"
            output = root / "output"
            output.mkdir()
            reports = root / "reports"
            config = root / "ai_config.json"
            config.write_text(
                json.dumps(
                    {
                        "alignment": {
                            "backend": "auto",
                            "mfa": {"enabled": False},
                            "wav2vec2": {"enabled": False},
                        },
                        "server": {"auto_start": False},
                    }
                ),
                encoding="utf-8",
            )
            write_tone(reference, 220.0)
            write_tone(learner, 900.0)

            helper = types.SimpleNamespace(
                get_selected=lambda: [
                    {
                        "id": 1,
                        "name": "reference",
                        "class": "Sound",
                        "file": str(reference),
                    },
                    {
                        "id": 2,
                        "name": "learner",
                        "class": "Sound",
                        "file": str(learner),
                    },
                ],
                get_output_dir=lambda: str(output),
                call=lambda *args, **kwargs: None,
                run_praat_script=lambda *args, **kwargs: None,
                select=lambda *args, **kwargs: None,
                plus_select=lambda *args, **kwargs: None,
            )
            with patch.dict(sys.modules, {"praat": helper}), patch.dict(
                os.environ, {"PRAAT_AI_REPORT_DIR": str(reports)}
            ):
                progress: list[tuple[float, str]] = []
                outputs = run_tutor(
                    config_path=config,
                    request=AnalysisRequest(
                        reference_object=1,
                        learner_object=2,
                        language="test",
                        phonemes=[
                            PhoneSpec(
                                "a",
                                reference_start=0.0,
                                reference_end=0.2,
                            )
                        ],
                        error_threshold=1.0,
                        qwen_explain=False,
                        qwen_vision=False,
                    ),
                    progress=lambda fraction, message: progress.append(
                        (fraction, message)
                    ),
                )

            self.assertTrue(outputs.json_path.is_file())
            self.assertTrue(outputs.textgrid_path.is_file())
            self.assertIsNotNone(outputs.overlay_path)
            self.assertTrue(outputs.overlay_path.is_file())
            self.assertIn(reports, outputs.json_path.parents)
            self.assertIn(reports, outputs.textgrid_path.parents)
            self.assertIn(reports, outputs.overlay_path.parents)
            self.assertTrue((output / outputs.textgrid_path.name).is_file())
            self.assertTrue((output / outputs.overlay_path.name).is_file())
            self.assertEqual(sorted(path.suffix for path in output.iterdir()), [".TextGrid", ".png"])
            shutil.rmtree(output)
            self.assertTrue(outputs.json_path.is_file())
            self.assertTrue(outputs.textgrid_path.is_file())
            self.assertTrue(outputs.overlay_path.is_file())
            self.assertGreater(len(json.loads(outputs.json_path.read_text(encoding="utf-8"))["errors"]), 0)
            self.assertGreater(len(outputs.result.errors), 0)
            self.assertEqual(progress[0][0], 0.08)
            self.assertEqual(progress[-1], (1.0, "分析完成"))
            self.assertTrue(any("音素对齐" in message for _, message in progress))


if __name__ == "__main__":
    unittest.main()
