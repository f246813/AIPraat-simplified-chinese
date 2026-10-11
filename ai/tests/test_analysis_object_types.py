"""Analysis tools must reject unsupported inputs before they reach Praat."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from praat_ai import tools


def _praat_executable() -> Path:
    """用和前端一样的顺序找 Praat，方便对新建出来的 ``Praat-fixed.exe`` 复验。

    ``PRAAT_AI_PRAAT_EXECUTABLE`` 优先（见 :mod:`praat_ai.praat_app`），否则退回
    仓库根目录的 ``Praat.exe``。
    """

    configured = os.getenv("PRAAT_AI_PRAAT_EXECUTABLE", "").strip()
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "Praat.exe"


PRAAT = _praat_executable()
SOUND = 'Create Sound from formula: "tone", 1, 0, 1, 44100, ~ 0.5 * sin (2*pi*220*x)'
ANALYSES = {
    "pitch": "Pitch",
    "pitch_statistics": "Pitch",
    "pitch_peak_latency": "Pitch",
    "intensity": "Intensity",
    "intensity_statistics": "Intensity",
    "intensity_slope": "Intensity",
    "formant_frequency": "Formant",
    "formant_bandwidth": "Formant",
    "formant_statistics": "Formant",
    "harmonicity_statistics": "Harmonicity",
    "hl_ratio": "Spectrum",
    "hammarberg_index": "Ltas",
}


class AnalysisObjectTypeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def context(self, *rows):
        return tools.ToolContext(tuple(rows), self.base / "result.txt", self.base / "state.txt")

    def test_explicit_harmonicity_never_receives_another_analysis_conversion(self):
        context = self.context(
            tools.ObjectRow(1, "Sound", "あなた", True),
            tools.ObjectRow(2, "Harmonicity", "あなた", False),
        )
        for name in ANALYSES:
            if name == "harmonicity_statistics":
                continue
            with self.subTest(tool=name), self.assertRaisesRegex(tools.ToolError, "Harmonicity"):
                tools.render(name, {"object": 2}, context)

    def test_each_analysis_rejects_unrelated_time_domain_objects(self):
        for name in ANALYSES:
            for class_name in ("TextGrid", "Spectrogram", "PointProcess", "PitchTier"):
                with self.subTest(tool=name, source=class_name), self.assertRaises(tools.ToolError):
                    tools.render(name, {}, self.context(tools.ObjectRow(7, class_name, "sample", True)))

    def test_wrong_default_selection_can_use_unique_sound(self):
        context = self.context(
            tools.ObjectRow(1, "Sound", "あなた", False),
            tools.ObjectRow(2, "Harmonicity", "あなた", True),
        )
        for name in ANALYSES:
            if name == "harmonicity_statistics":
                continue
            with self.subTest(tool=name):
                script = tools.render(name, {}, context)
                self.assertEqual(script.splitlines()[0], "selectObject: 1")

    def test_wrong_default_selection_with_multiple_sounds_is_ambiguous(self):
        context = self.context(
            tools.ObjectRow(1, "Sound", "one", False),
            tools.ObjectRow(2, "Sound", "two", False),
            tools.ObjectRow(3, "TextGrid", "grid", True),
        )
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("pitch_statistics", {}, context)
        self.assertIn("1", str(caught.exception))
        self.assertIn("2", str(caught.exception))

    def test_multiple_selected_supported_sources_and_unselected_sources_are_ambiguous(self):
        for selected in (True, False):
            with self.subTest(selected=selected), self.assertRaises(tools.ToolError):
                context = self.context(
                    tools.ObjectRow(1, "Sound", "one", selected),
                    tools.ObjectRow(2, "Sound", "two", selected),
                )
                tools.render("pitch_statistics", {}, context)

    def test_bare_same_name_is_ambiguous_but_class_name_resolves(self):
        context = self.context(
            tools.ObjectRow(1, "Sound", "あなた", True),
            tools.ObjectRow(2, "Harmonicity", "あなた", False),
        )
        with self.assertRaises(tools.ToolError):
            context.resolve_object("あなた")
        self.assertEqual(context.resolve_object("Sound あなた").id, 1)

    def test_class_specific_tools_do_not_replace_explicit_wrong_id(self):
        context = self.context(
            tools.ObjectRow(1, "Sound", "sound", True),
            tools.ObjectRow(2, "TextGrid", "grid", False),
        )
        for name, arguments in (
            ("spectrogram", {"object": 2}),
            ("resample_sound", {"object": 2}),
            ("extract_part", {"object": 2, "start": 0.1, "end": 0.2}),
            ("textgrid_info", {"object": 1}),
        ):
            with self.subTest(tool=name), self.assertRaises(tools.ToolError):
                tools.render(name, arguments, context)

    def test_longsound_is_rejected_for_sound_only_conversions(self):
        context = self.context(tools.ObjectRow(1, "LongSound", "long", True))
        calls = [(name, {}) for name in ANALYSES]
        calls.extend((name, {}) for name in ("spectrogram", "resample_sound", "spectral_emphasis", "peak_to_average_ratio", "vot"))
        calls.extend((
            ("concatenate_sounds", {"object2": 2}),
            ("measure", {"parameter": "mean_pitch"}),
        ))
        for name, arguments in calls:
            with self.subTest(tool=name), self.assertRaisesRegex(tools.ToolError, "LongSound"):
                tools.render(name, arguments, context)

    def test_measure_can_resolve_a_default_source_by_parameter_type(self):
        context = self.context(
            tools.ObjectRow(1, "Sound", "あなた", False),
            tools.ObjectRow(2, "Harmonicity", "あなた", True),
        )
        script = tools.render("measure", {"parameter": "mean_pitch"}, context)
        self.assertEqual(script.splitlines()[0], "selectObject: 1")
        with self.assertRaises(tools.ToolError):
            tools.render("measure", {"parameter": "mean_pitch", "object": 2}, context)

    def test_duration_rejects_classes_without_duration_command(self):
        for class_name in ("Excitation", "Polygon", "Cochleagram", "Manipulation", "IntervalTier"):
            with self.subTest(source=class_name), self.assertRaises(tools.ToolError):
                tools.render("duration", {}, self.context(tools.ObjectRow(1, class_name, "sample", True)))

    def test_pitch_statistics_guards_against_octave_errors_in_the_script(self):
        """最高/最低基频不能用裸 ``Get maximum``——倍频误判会一帧定结论。

        2026-09-30 用户报的「159.9 Hz 的段落冒出 598.5 Hz 峰值 @ 0.061 秒」：
        自相关在**浊音起始**常把真实基频听成 2–4 倍（实测 ``a.wav`` 裸最大值
        496.65 Hz，真实只有 ~121.6 Hz）。所以模板必须自己遍历帧、把候选限制在
        中位数的 1.5 倍以内，并且把「剔除了什么」写进结果。
        """

        script = tools.render(
            "pitch_statistics", {}, self.context(tools.ObjectRow(1, "Sound", "tone", True))
        )
        # 被报告出去的 maximum / minimum / maxtime 必须来自稳健的逐帧循环，
        # 不能是 Get maximum / Get time of maximum 的结果。
        self.assertNotIn('maximum = Get maximum', script)
        self.assertNotIn('minimum = Get minimum', script)
        self.assertNotIn('maxtime = Get time of maximum', script)
        self.assertIn('median = Get quantile: tmin, tmax, 0.5, "Hertz"', script)
        self.assertIn("ceilingValue = median * 1.5", script)
        self.assertIn("floorValue = median * 0.5", script)
        self.assertIn("maximum = highest", script)
        self.assertIn("maxtime = Get time from frame number: pitchFrame", script)
        # 2026-10-04：平均值必须和最高/最低统计同一批帧。裸 Get mean 会把被剔除的
        # 倍频误判帧也算进去，于是报告里出现「平均 139.8 Hz、最高 119.2 Hz」这种
        # 自相矛盾的数字，模型只能写「含误判帧」把它绕过去。
        self.assertIn('rawMean = Get mean: tmin, tmax, "Hertz"', script)
        self.assertIn("pitchTotal = pitchTotal + frameValue", script)
        self.assertIn("mean = pitchTotal / pitchCount", script)
        self.assertIn("meanNote$", script)
        # 裸最大值只允许出现在「剔除了什么」的披露里，而且必须报给用户看。
        self.assertIn("已按倍频误判剔除", script)
        self.assertIn('rawMaximum = Get maximum: tmin, tmax, "Hertz", "Parabolic"', script)

    def test_pitch_statistics_keeps_a_consistent_contract_for_pitch_objects(self):
        """显式给 Pitch 对象时，同一个模板也要能跑（不重复 To Pitch）。"""

        context = self.context(tools.ObjectRow(2, "Pitch", "tone", True))
        script = tools.render("pitch_statistics", {"object": 2}, context)
        self.assertNotIn("To Pitch:", script)
        self.assertIn("ceilingValue = median * 1.5", script)

    def test_plan_actions_preserves_invalid_arguments_instead_of_defaults(self):
        for raw in ([], "{broken", 7):
            with self.subTest(arguments=raw):
                action = tools.plan_actions({"tool": "pitch_statistics", "arguments": raw})[0]
                self.assertTrue(action.get("invalid_arguments"))
        action = tools.plan_actions({"actions": [{"id": "call_9", "tool": "pitch_statistics", "arguments": {}, "invalid_arguments": "参数 JSON 不完整"}]})[0]
        self.assertEqual(action.get("invalid_arguments"), "参数 JSON 不完整")
        self.assertEqual(action.get("id"), "call_9")

    @unittest.skipUnless(PRAAT.is_file(), "isolated Praat executable is unavailable")
    def test_supported_existing_analysis_objects_survive_real_queries(self):
        conversions = {
            "Pitch": "To Pitch: 0, 75, 600",
            "Intensity": 'To Intensity: 100, 0, "yes"',
            "Formant": "To Formant (burg): 0, 5, 5500, 0.025, 50",
            "Harmonicity": "To Harmonicity (cc): 0.01, 75, 0.1, 1",
            "Spectrum": 'To Spectrum: "yes"',
            "Ltas": "To Ltas: 100",
        }
        for name, class_name in ANALYSES.items():
            with self.subTest(tool=name):
                context = self.context(tools.ObjectRow(2, class_name, "tone", True))
                script = tools.render(name, {"object": 2}, context)
                self.run_praat(SOUND + "\n" + conversions[class_name] + "\n" + script +
                               'assert numberOfSelected () = 1\nassert selected () = 2\nselect all\nassert numberOfSelected () = 2\n')

    @unittest.skipUnless(PRAAT.is_file(), "isolated Praat executable is unavailable")
    def test_sound_statistics_cleanup_and_range_aliases_in_real_praat(self):
        for name in ("pitch_statistics", "intensity_statistics", "formant_statistics", "harmonicity_statistics", "measure"):
            with self.subTest(tool=name):
                context = self.context(tools.ObjectRow(1, "Sound", "tone", True))
                # parameter 只有 measure 收：别的工具多给一个不认识的键现在会被明确拒绝
                # （2026-10-07：未知参数键以前是静默忽略）。
                arguments = {"start": 0.2, "end": 0.7}
                if name == "measure":
                    arguments["parameter"] = "mean_pitch"
                self.run_praat(SOUND + "\n" + tools.render(name, arguments, context) +
                               'assert selected () = 1\nselect all\nassert numberOfSelected () = 1\n')
                result = context.result_path.read_text(encoding="utf-8-sig")
                self.assertIn("0.200–0.700", result)

    def test_unknown_arguments_are_refused_before_praat(self):
        """同一个请求里多给一个别的工具才用的键，要在执行前说清楚。"""

        context = self.context(tools.ObjectRow(1, "Sound", "tone", True))
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("pitch_statistics", {"start": 0.2, "end": 0.7, "parameter": "mean_pitch"}, context)
        self.assertIn("parameter", str(caught.exception))

    @unittest.skipUnless(PRAAT.is_file(), "isolated Praat executable is unavailable")
    def test_longsound_extract_and_info_use_real_supported_commands(self):
        wav = self.base / "input.wav"
        context = self.context(tools.ObjectRow(2, "LongSound", "input", True))
        prefix = SOUND + "\nSave as WAV file: " + tools.quote(wav) + "\nRemove\nOpen long sound file: " + tools.quote(wav) + "\n"
        for name, arguments in (("object_info", {}), ("extract_part", {"start": 0.1, "end": 0.4})):
            with self.subTest(tool=name):
                self.run_praat(prefix + tools.render(name, arguments, context))
                self.assertTrue(context.result_path.read_text(encoding="utf-8-sig").strip())

    @unittest.skipUnless(PRAAT.is_file(), "isolated Praat executable is unavailable")
    def test_read_file_accepts_multiple_imported_objects_and_reports_all_ids(self):
        collection = self.base / "objects.Collection"
        context = self.context()
        prefix = SOUND + '\nCreate TextGrid: 0, 1, "words", ""\nselect all\nSave as text file: ' + tools.quote(collection) + "\nRemove\n"
        self.run_praat(prefix + tools.render("read_file", {"path": str(collection)}, context) +
                       'assert numberOfSelected () = 2\nassert selected (1) = 3\nassert selected (2) = 4\n')
        result = context.result_path.read_text(encoding="utf-8-sig")
        self.assertIn("id 3", result)
        self.assertIn("id 4", result)

    @unittest.skipUnless(PRAAT.is_file(), "isolated Praat executable is unavailable")
    def test_extract_part_accepts_from_to_aliases(self):
        context = self.context(tools.ObjectRow(1, "Sound", "tone", True))
        self.run_praat(SOUND + "\n" + tools.render("extract_part", {"from": 0.2, "to": 0.7}, context) +
                       'length = Get total duration\nassert abs(length - 0.5) < 0.0001\n')

    @unittest.skipUnless(PRAAT.is_file(), "isolated Praat executable is unavailable")
    def test_invalid_range_does_not_silently_measure_entire_sound(self):
        context = self.context(tools.ObjectRow(1, "Sound", "tone", True))
        # 起点晚于终点：2026-10-07 起在 Python 层就拒绝（以前要投递一次、由脚本 exitScript）。
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("pitch_statistics", {"from": 0.7, "to": 0.2}, context)
        self.assertIn("起点必须早于终点", str(caught.exception))
        # 超出时长仍然留给 Praat 的钳制逻辑，但绝不能变成「按整段测量」。
        script = tools.render("pitch_statistics", {"from": 2.0, "to": 3.0}, context)
        result = self.execute_praat(SOUND + "\n" + script)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(context.state_path.exists())
        self.assertFalse(context.result_path.exists())

    @unittest.skipUnless(PRAAT.is_file(), "isolated Praat executable is unavailable")
    def test_missing_import_aborts_before_querying_an_old_object(self):
        context = self.context(tools.ObjectRow(1, "Sound", "tone", True))
        script = tools.render("read_file", {"path": str(self.base / "missing.wav")}, context)
        result = self.execute_praat(SOUND + "\n" + script + 'appendFileLine: ' + tools.quote(self.base / "continued.txt") + ', "wrong continuation"\n')
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(context.state_path.exists())
        self.assertFalse((self.base / "continued.txt").exists())

    def run_praat(self, script):
        completed = self.execute_praat(script)
        output = completed.stdout + completed.stderr
        encoding = "utf-16-le" if b"\x00" in output else "utf-8"
        self.assertEqual(completed.returncode, 0, output.decode(encoding, errors="replace"))
        self.assertEqual((self.base / "state.txt").read_text(encoding="utf-8-sig").strip(), "done")

    def execute_praat(self, script):
        for path in (self.base / "result.txt", self.base / "state.txt"):
            path.unlink(missing_ok=True)
        target = self.base / "test.praat"
        target.write_text(script, encoding="utf-8", newline="\n")
        return subprocess.run([str(PRAAT), "--no-pref-files", "--no-plugins", "--FULL-TRUST", "--run", str(target)], cwd=self.base,
                              capture_output=True, timeout=30)


if __name__ == "__main__":
    unittest.main()
