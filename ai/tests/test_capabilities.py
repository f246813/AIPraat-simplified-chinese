"""阶段 3：分析依赖在**使用点**被检测，并把「缺什么 / 去哪儿装」收到同一个口径。

``runtime_dependencies.detect`` 是安装向导的体检（每项起子进程做加载与参考功能检查），
不适合在纠音窗口里随手调用；`praat_ai.capabilities` 只做进程内 import + 版本比对。
这里覆盖三件事：

① 检测本身（缺包 / 版本落在 requirements 区间之外 / 版本区间来自 requirements.txt）；
② 「使用点」拿到的确实是同一份结论（audio 的基频/共振峰、共振峰校准、响度归一化）；
③ 前端契约 ``tutor.capabilities`` 的负载形状与文案。
"""

import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from praat_ai import capabilities
from praat_ai.bridge import SelectedObject
from praat_ai.ui import TutorFormApplication

FIX_HINT = "配置运行库"


class CapabilityDetectionTests(unittest.TestCase):
    def test_this_interpreter_is_ready_and_specs_come_from_requirements(self):
        """当前验收解释器上三项能力都应就绪，且版本区间来自 requirements.txt。"""

        report = capabilities.analysis_capabilities()

        self.assertEqual(set(report["features"]), {"formants", "pitch", "loudness"})
        self.assertEqual(Path(report["requirements"]).name, "requirements.txt")
        self.assertTrue(report["ready"])
        self.assertEqual(report["message"], "")
        for feature in report["features"].values():
            self.assertTrue(feature["ready"], feature["reason"])
            self.assertEqual((feature["reason"], feature["hint"]), ("", ""))

    def test_missing_parselmouth_blocks_formants_and_pitch_but_not_loudness(self):
        """缺 praat-parselmouth 只影响基频/共振峰；响度不该被连坐。"""

        with patch.dict("sys.modules", {"parselmouth": None}):
            pitch = capabilities.check("pitch")
            blocker = capabilities.blocker("pitch")

            self.assertFalse(pitch["ready"])
            self.assertIn("praat-parselmouth", pitch["reason"])
            self.assertIn(FIX_HINT, pitch["hint"])
            self.assertFalse(capabilities.check("formants")["ready"])
            self.assertTrue(capabilities.check("loudness")["ready"])
            self.assertEqual(capabilities.blocker("loudness"), "")

        self.assertIn("缺少分析依赖", blocker)
        self.assertIn("praat-parselmouth", blocker)
        self.assertIn(FIX_HINT, blocker)

    def test_version_outside_requirements_is_refused_from_the_same_source(self):
        """版本门控读 requirements.txt：既不写死数字，也不接受区间外的「同类新版」。"""

        versions = {"numpy": "2.2.6", "scipy": "1.15.0", "pyloudnorm": "0.2.0"}
        with patch("importlib.metadata.version", side_effect=lambda name: versions[name]):
            status = capabilities.check("loudness")

        self.assertFalse(status["ready"])
        self.assertIn("版本不匹配", status["reason"])
        self.assertIn("scipy>=1.14.1,<1.15", status["reason"])

        forged = capabilities.check("loudness", specs={"numpy": ">=99"})
        self.assertFalse(forged["ready"])
        self.assertIn("numpy>=99", forged["reason"])
        self.assertIn("版本不匹配", forged["reason"])

    def test_unknown_capability_and_intentional_absence_on_newer_python(self):
        """未知能力项要报错；较新解释器上 parselmouth 是**故意**不装的，提示要说清原因。"""

        with self.assertRaises(ValueError):
            capabilities.check("not-a-feature")

        fake_sys = SimpleNamespace(version_info=(3, 14), executable="python", path=[])
        with patch.dict("sys.modules", {"parselmouth": None}), patch(
            "praat_ai.capabilities.sys", fake_sys
        ):
            reason = capabilities.check("pitch")["reason"]

        self.assertIn("Python 3.13 及以上不安装", reason)
        self.assertNotIn(FIX_HINT, reason)  # 装不上的东西不该让人去反复装


class CapabilityUsePointTests(unittest.TestCase):
    def test_audio_extraction_and_calibration_share_the_same_hint(self):
        """两处使用点（基频/共振峰提取、共振峰校准）都用同一句可执行提示。"""

        from praat_ai.audio import _open_parselmouth
        from praat_ai.normalization import import_formant_calibration

        with patch.dict("sys.modules", {"parselmouth": None}):
            analysis, status, reason = _open_parselmouth(Path("unused.wav"))
            with self.assertRaises(ValueError) as caught:
                import_formant_calibration(
                    "a.wav", "b.TextGrid", speaker_label="s", language="ja", profile_id="cmn"
                )

        self.assertIsNone(analysis)
        self.assertEqual(status, "dependency_missing")
        self.assertIn("praat-parselmouth", reason)
        self.assertIn(FIX_HINT, reason)
        self.assertIn("基频/共振峰", reason)
        self.assertIn(FIX_HINT, str(caught.exception))

    def test_tutor_capabilities_payload_is_the_frontend_contract(self):
        """前端 `tutor.capabilities`：能力 id 与归一化开关同名，逐包给出状态与提示。"""

        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "config.json"
            config.write_text(json.dumps(dict(api=dict(api_key="keep-secret"))), encoding="utf-8")
            app = TutorFormApplication(
                [SelectedObject(4, "ref", "Sound", Path("ref.wav")),
                 SelectedObject(9, "learner", "Sound", Path("learner.wav"))],
                config,
            )
            app.window = Mock()
            report = app.rpc("tutor.capabilities", {})

        self.assertTrue(report["ready"])
        self.assertEqual(set(report["features"]), {"formants", "pitch", "loudness"})
        self.assertEqual(
            [row["id"] for row in report["features"]["loudness"]["packages"]],
            ["numpy", "scipy", "pyloudnorm"],
        )
        self.assertEqual(report["features"]["formants"]["label"], "共振峰校准与 F1/F2 观察")


if __name__ == "__main__":
    unittest.main()
