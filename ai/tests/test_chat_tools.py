import tempfile
import unittest
from pathlib import Path

from praat_ai import tools


CONTEXT = (
    "id\tclass\tname\tselected\n"
    "1\tSound\tSound tone\t1\n"
    "2\tFormant\tFormant tone\t0\n"
)

MIXED_CONTEXT = (
    "id\tclass\tname\tselected\n"
    "1\tSound\tSound 思い出す\t1\n"
    "2\tSound\ttone\t0\n"
    "3\tTextGrid\tTextGrid 思い出す\t0\n"
    "4\tTable\tTable words\t0\n"
)

GRID_CONTEXT = "id\tclass\tname\tselected\n1\tTextGrid\tTextGrid grid\t1\n"

# 打开了编辑器、并在波形上拖选了 0.25–0.5 秒时的上下文（多出两列）。
SELECTED_CONTEXT = (
    "id\tclass\tname\tselected\tsel_start\tsel_end\n"
    "1\tSound\tSound tone\t1\t0.250000\t0.500000\n"
    "2\tSound\tSound tone2\t0\t\t\n"
)


class ArgumentContractTests(unittest.TestCase):
    """参数契约：同义写法、未知键、「只对整段」和破坏性操作的明确目标。

    2026-10-07 实测的这一组问题都是「静默做错事」：说明书里的 ``finish`` 从没被读过
    （``from``+``finish`` 变成整段），未知键被忽略（单位/范围约束消失），
    ``spectral_emphasis`` 的范围参数被无声丢掉，什么都没选中时 ``remove_object``
    会删掉对象列表的最后一行。
    """

    def context(self, text=CONTEXT):
        return tools.ToolContext(tools.parse_object_context(text), Path("result"), Path("state"))

    def test_finish_alias_actually_bounds_the_range(self):
        script = tools.render("pitch_statistics", {"from": 0.1, "finish": 0.7}, self.context())
        self.assertIn("tmax = 0.700000", script)
        self.assertIn("tmin = 0.100000", script)
        script = tools.render("measure", {"parameter": "mean_pitch", "from": 0.1, "finish": 0.4},
                              self.context())
        self.assertIn("tmax = 0.400000", script)

    def test_contradicting_range_aliases_are_rejected(self):
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("pitch_statistics", {"from": 0.1, "to": 0.5, "finish": 0.7}, self.context())
        self.assertIn("矛盾", str(caught.exception))

    def test_reversed_range_is_rejected_before_dispatch(self):
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("pitch_statistics", {"from": 1.0, "to": 0.5}, self.context())
        self.assertIn("起点必须早于终点", str(caught.exception))
        with self.assertRaises(tools.ToolError):
            tools.render("vot", {"from": 2.0, "to": 1.0}, self.context())

    def test_unknown_argument_names_are_reported(self):
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("pitch_statistics", {"from": 0.1, "to": 0.4, "pitch_flor": 100}, self.context())
        message = str(caught.exception)
        self.assertIn("pitch_flor", message)
        self.assertIn("pitch_floor", message)

    def test_whole_object_tools_refuse_a_range_and_say_what_to_do(self):
        for tool in ("spectral_emphasis", "hl_ratio", "hammarberg_index", "duration"):
            with self.subTest(tool=tool):
                with self.assertRaises(tools.ToolError) as caught:
                    tools.render(tool, {"from": 0.1, "to": 0.4}, self.context())
                self.assertIn("extract_part", str(caught.exception))

    def test_pitch_band_bounds_are_the_same_everywhere(self):
        for tool, args in (("pitch", {"pitch_ceiling": 3000}),
                           ("pitch_statistics", {"pitch_ceiling": 3000}),
                           ("measure", {"parameter": "mean_pitch", "pitch_ceiling": 3000}),
                           ("pitch", {"pitch_floor": 15}),
                           ("measure", {"parameter": "mean_pitch", "pitch_floor": 15})):
            with self.subTest(tool=tool, args=args):
                self.assertTrue(tools.render(tool, args, self.context()))

    def test_inverted_pitch_band_is_rejected(self):
        for tool, args in (("pitch", {"pitch_floor": 500, "pitch_ceiling": 100}),
                           ("pitch_statistics", {"pitch_floor": 500, "pitch_ceiling": 100}),
                           ("measure", {"parameter": "mean_pitch", "pitch_floor": 500,
                                        "pitch_ceiling": 100})):
            with self.subTest(tool=tool):
                with self.assertRaises(tools.ToolError) as caught:
                    tools.render(tool, args, self.context())
                self.assertIn("必须小于", str(caught.exception))

    def test_hnr_honours_the_requested_floor(self):
        plain = tools.render("measure", {"parameter": "hnr"}, self.context())
        lowered = tools.render("measure", {"parameter": "hnr", "pitch_floor": 40}, self.context())
        self.assertIn("To Harmonicity (cc): 0.01, 75.000000", plain)
        self.assertIn("To Harmonicity (cc): 0.01, 40.000000", lowered)

    def test_inverted_frequency_bands_are_rejected(self):
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("hl_ratio", {"low_from": 8000, "low_to": 100}, self.context())
        self.assertIn("起点必须小于终点", str(caught.exception))
        with self.assertRaises(tools.ToolError):
            tools.render("hammarberg_index", {"high_from": 5000, "high_to": 2000}, self.context())

    def test_destructive_tools_need_an_explicit_target(self):
        unselected = self.context("id\tclass\tname\tselected\n1\tSound\tSound tone\t0\n"
                                  "3\tIntensity\tIntensity tone\t0\n")
        for tool, args in (("remove_object", {}), ("rename_object", {"new_name": "x"})):
            with self.subTest(tool=tool):
                with self.assertRaises(tools.ToolError) as caught:
                    tools.render(tool, args, unselected)
                self.assertIn("明确的目标", str(caught.exception))
        # 唯一选中时照常工作。
        self.assertIn("selectObject: 1", tools.render("remove_object", {}, self.context()))

    def test_paths_are_escaped_exactly_once(self):
        script = tools.render("read_file", {"path": 'D:/in/my "q".wav'}, self.context())
        line = next(line for line in script.splitlines() if "Read from file" in line)
        self.assertIn('"D:/in/my ""q"".wav"', line)
        self.assertNotIn('""""', line)

    def test_snapshot_survives_tabs_and_non_finite_selection(self):
        rows = tools.parse_object_context("id\tclass\tname\tselected\n1\tSound\t新\t名字\t1\n")
        self.assertEqual((rows[0].name, rows[0].selected), ("新\t名字", True))
        nan_rows = tools.parse_object_context(
            "id\tclass\tname\tselected\n1\tSound\tS\t1\tnan\tinf\n")
        self.assertIsNone(nan_rows[0].selection)


class ObjectContextTests(unittest.TestCase):
    def test_context_is_parsed(self) -> None:
        rows = tools.parse_object_context(CONTEXT)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].name, "Sound tone")
        self.assertTrue(rows[0].selected)

    def test_default_object_prefers_selection(self) -> None:
        rows = tools.parse_object_context(CONTEXT)
        context = tools.ToolContext(rows, Path("r.tsv"), Path("s.txt"))
        self.assertEqual(context.default_object().id, 1)

    def test_object_can_be_resolved_by_id_and_name(self) -> None:
        rows = tools.parse_object_context(CONTEXT)
        context = tools.ToolContext(rows, Path("r.tsv"), Path("s.txt"))
        self.assertEqual(context.resolve_object(2).class_name, "Formant")
        self.assertEqual(context.resolve_object("Sound tone").id, 1)

    def test_unknown_object_is_rejected(self) -> None:
        rows = tools.parse_object_context(CONTEXT)
        context = tools.ToolContext(rows, Path("r.tsv"), Path("s.txt"))
        with self.assertRaises(tools.ToolError):
            context.resolve_object("Sound missing")

    def test_editor_selection_columns_are_parsed(self) -> None:
        # Praat 打开编辑器后会多写 sel_start/sel_end 两列；旧的四列写法也要照旧能读。
        rows = tools.parse_object_context(SELECTED_CONTEXT)
        self.assertEqual(rows[0].selection, (0.25, 0.5))
        self.assertIsNone(rows[1].selection)
        legacy = tools.parse_object_context(CONTEXT)
        self.assertIsNone(legacy[0].selection)

    def test_cursor_without_a_dragged_range_is_not_a_selection(self) -> None:
        rows = tools.parse_object_context(
            "id\tclass\tname\tselected\tsel_start\tsel_end\n"
            "1\tSound\tSound tone\t1\t0.300000\t0.300000\n"
        )
        self.assertIsNone(rows[0].selection)


class ScriptRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.context = tools.ToolContext(
            tools.parse_object_context(CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def render(self, name: str, **arguments: object) -> str:
        return tools.render(name, arguments, self.context)

    def test_custom_pitch_conversion_rejects_known_harmonicity_source(self) -> None:
        context = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\n"
                "1\tSound\tSound tone\t0\n"
                "9\tHarmonicity\tHarmonicity tone\t1\n"
            ),
            self.context.result_path,
            self.context.state_path,
        )
        for script in (
            "selectObject: 9\nTo Pitch: 0, 75, 600",
            "To Pitch: 0, 75, 600",
        ):
            with self.subTest(script=script), self.assertRaisesRegex(tools.ToolError, "Sound"):
                tools.render(tools.CUSTOM_SCRIPT_TOOL, {}, context, custom_script=script)

    def test_custom_pitch_conversion_accepts_explicit_sound_source(self) -> None:
        script = tools.render(
            tools.CUSTOM_SCRIPT_TOOL, {}, self.context,
            custom_script="selectObject: 1\nTo Pitch: 0, 75, 600",
        )
        self.assertIn("To Pitch: 0, 75, 600", script)

    def test_formant_bandwidth_uses_object_id_and_double_quotes(self) -> None:
        script = self.render("formant_bandwidth", formant=2)
        self.assertIn("selectObject: 1", script)
        self.assertIn("To Formant (burg): 0, 5, 5500, 0.025, 50", script)
        self.assertIn(
            'value = Get bandwidth at time: 2, time, "hertz", "linear"', script
        )
        self.assertNotIn("'", script)

    def test_formant_query_accepts_explicit_time_and_unit(self) -> None:
        script = self.render("formant_frequency", formant=3, time=0.25, unit="Bark")
        self.assertIn("time = 0.250000", script)
        self.assertIn('value = Get value at time: 3, time, "Bark", "linear"', script)

    def test_existing_formant_object_is_not_reanalysed(self) -> None:
        script = self.render("formant_bandwidth", object=2)
        self.assertIn("selectObject: 2", script)
        self.assertNotIn("To Formant (burg)", script)
        self.assertNotIn("Remove", script)

    def test_pitch_and_intensity_templates(self) -> None:
        pitch = self.render("pitch", time=0.3)
        self.assertIn("To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000", pitch)
        self.assertIn('value = Get value at time: time, "Hertz", "linear"', pitch)
        intensity = self.render("intensity")
        self.assertIn('To Intensity: 100, 0, "yes"', intensity)
        self.assertIn('value = Get value at time: time, "cubic"', intensity)

    def test_every_script_ends_with_state_marker(self) -> None:
        base = Path(self.directory.name)
        context = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\n"
                "1\tSound\tSound tone\t1\n"
                "2\tSound\ttone2\t0\n"
                "3\tTextGrid\tTextGrid grid\t0\n"
            ),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )
        extra: dict[str, dict[str, object]] = {
            "rename_object": {"new_name": "测试"},
            "extract_part": {"start": 0.1, "end": 0.4},
            "save_sound": {"path": str(base / "ai-chat.wav")},
            "read_file": {"path": str(base / "ai-chat.wav")},
            "concatenate_sounds": {"object": 1, "object2": 2},
            "textgrid_info": {"object": 3},
            "textgrid_set_interval": {
                "object": 3,
                "start": 0.1,
                "end": 0.2,
                "label": "a",
            },
            "textgrid_insert_boundary": {"object": 3, "time": 0.5},
            "vot": {"object": 3, "burst": 0.3, "voicing": 0.42},
            "measure": {"parameter": "rms,mean_intensity"},
        }
        for tool in tools.TOOLS:
            arguments: dict[str, object] = dict(extra.get(tool.name, {}))
            script = tools.render(tool.name, arguments, context)
            last = [line for line in script.splitlines() if line.strip()][-1]
            self.assertIn("chat_state.txt", last, tool.name)
            self.assertIn('"done"', last, tool.name)

    def test_invalid_formant_number_is_rejected(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.render("formant_bandwidth", formant=0)

    def test_invalid_unit_is_rejected(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.render("formant_bandwidth", unit="meters")

    def test_rename_requires_new_name(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.render("rename_object")

    def test_object_creating_tools_report_the_new_id(self) -> None:
        """新建对象的结果行里要带 id：多步请求的下一步要靠它认出这个新对象。"""

        base = Path(self.directory.name)
        context = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\n"
                "1\tSound\tSound tone\t1\n"
                "2\tSound\ttone2\t0\n"
            ),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )
        cases = {
            "create_sound": {"duration": 0.2, "frequency": 220},
            "extract_part": {"start": 0.1, "end": 0.4},
            "duplicate_object": {},
            "resample_sound": {"rate": 16000},
            "concatenate_sounds": {"object": 1, "object2": 2},
            "spectrogram": {},
        }
        for tool_name, arguments in cases.items():
            with self.subTest(tool=tool_name):
                script = tools.render(tool_name, arguments, context)
                self.assertIn("newId = ", script, tool_name)
                self.assertIn("fixed$ (newId, 0)", script, tool_name)


class BorrowedMeasurementTests(unittest.TestCase):
    """借用 chengafni 的测量脚本（公式照抄，真机验证在 verify_chat_templates）。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.result = base / "chat_result.tsv"
        self.context = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\n"
                "1\tSound\tSound tone\t1\n"
                "2\tTextGrid\tTextGrid grid\t0\n"
                "3\tIntensity\tIntensity tone\t0\n"
                "4\tPitch\tPitch tone\t0\n"
            ),
            self.result,
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def render(self, name: str, **arguments: object) -> str:
        return tools.render(name, arguments, self.context)

    def test_spectral_emphasis_follows_the_source_script(self) -> None:
        script = self.render("spectral_emphasis", object=1)
        self.assertIn("intensity = Get intensity (dB)", script)
        self.assertIn("To Pitch (filtered autocorrelation): 0, 75.000000, 600.000000", script)
        self.assertIn("Filter (pass Hann band): 0, cutoff, 20.000000", script)
        # 中间对象要删掉，最后回到用户原来选中的对象。
        self.assertIn("Remove", script)
        self.assertIn("selectObject: 1", script)

    def test_spectral_emphasis_rejects_explicit_wrong_object(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.render("spectral_emphasis", object=2)

    def test_hl_ratio_rejects_overlapping_bands(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.render("hl_ratio", object=1, low_to=5000, high_from=4000)
        script = self.render("hl_ratio", object=1)
        self.assertIn("To Spectrum", script)
        self.assertIn("lowBand = Get band energy: 0.000000, 4000.000000", script)

    def test_hammarberg_index_needs_sound_or_ltas(self) -> None:
        script = self.render("hammarberg_index", object=1)
        self.assertIn("To Ltas: 100.000000", script)
        self.assertIn('lowBand = Get maximum: 0.000000, 2000.000000, "Parabolic"', script)
        with self.assertRaises(tools.ToolError):
            self.render("hammarberg_index", object=2)

    def test_pitch_peak_latency_rejects_objects_without_pitch(self) -> None:
        script = self.render("pitch_peak_latency", object=1)
        # 峰值时刻走防倍频误判的稳健取法（见 2026-09-30 的 598.5 Hz 问题）。
        self.assertIn("peakTime = maxtime", script)
        self.assertIn("ceilingValue = median * 1.5", script)
        self.assertNotIn('peakTime = Get time of maximum', script)
        self.assertIn("latency = (peakTime - tmin) / (tmax - tmin)", script)
        # TextGrid 有时长但没有 To Pitch：要在这里拦住，而不是让 Praat 弹英文错误框。
        with self.assertRaises(tools.ToolError):
            self.render("pitch_peak_latency", object=2)
        # 已经是 Pitch 的直接用，不再做一次 To Pitch。
        self.assertNotIn("To Pitch", self.render("pitch_peak_latency", object=4))

    def test_peak_to_average_uses_rms_not_the_signed_mean(self) -> None:
        script = self.render("peak_to_average_ratio", object=1)
        self.assertIn('peak = Get maximum: tmin, tmax, "Sinc70"', script)
        self.assertIn("rms = Get root-mean-square: tmin, tmax", script)
        self.assertNotIn("Get mean: 1", script)

    def test_intensity_slope_local_and_global(self) -> None:
        local = self.render("intensity_slope", object=1)
        self.assertIn('Formula: "abs(self[col+1]-self[col])/dx"', local)
        self.assertIn("Down to Matrix", local)
        global_ = self.render("intensity_slope", object=1, method="global")
        self.assertIn("slope = (lastEl - firstEl) / (lastTime - firstTime)", global_)
        self.assertNotIn("Formula:", global_)

    def test_intensity_slope_rejects_bad_method_and_range(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.render("intensity_slope", object=1, method="whatever")
        # Intensity 对象没有「取片段」命令：别假装能按范围算。
        with self.assertRaises(tools.ToolError):
            self.render("intensity_slope", object=3, **{"from": 0.1, "to": 0.5})
        with self.assertRaises(tools.ToolError):
            self.render("intensity_slope", object=2)

    def test_intensity_slope_extracts_a_part_for_sound_ranges(self) -> None:
        script = self.render("intensity_slope", object=1, **{"from": 0.2, "to": 0.8})
        self.assertIn("extractedId = Extract part:", script)
        self.assertIn("if extractedId <> 0", script)

    def test_intensity_slope_whole_object_does_not_delete_anything(self) -> None:
        """整段分析时 extractedId = 0，收尾的删除要跳过（不能删错对象）。"""

        script = self.render("intensity_slope", object=1)
        self.assertIn("extractedId = 0", script)
        self.assertIn("if extractedId <> 0", script)


class ScriptValidationTests(unittest.TestCase):
    def test_single_quotes_become_double_quotes(self) -> None:
        script = tools.validate_script("selectObject('Sound', 'tone')")
        self.assertEqual(script.strip(), 'selectObject("Sound", "tone")')

    def test_markdown_fence_is_stripped(self) -> None:
        script = tools.validate_script("```praat\nselectObject: 1\n```")
        self.assertEqual(script.strip(), "selectObject: 1")

    def test_dangerous_commands_are_rejected(self) -> None:
        for snippet in (
            'runSystem: "cmd /c dir"',
            'deleteFile: "C:/tmp/a.txt"',
            "exit",
        ):
            with self.assertRaises(tools.ToolError, msg=snippet):
                tools.validate_script(snippet)

    def test_unbalanced_quotes_are_rejected(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools.validate_script('Rename: "abc')

    def test_custom_script_gets_result_marker(self) -> None:
        directory = tempfile.TemporaryDirectory()
        base = Path(directory.name)
        context = tools.ToolContext(
            tools.parse_object_context(CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )
        script = tools.render(
            tools.CUSTOM_SCRIPT_TOOL,
            {},
            context,
            custom_script="selectObject('Sound', 'tone')",
        )
        directory.cleanup()
        self.assertIn('selectObject("Sound", "tone")', script)
        self.assertIn("chat_state.txt", script)

    def test_custom_script_must_not_open_the_info_window(self) -> None:
        """模型写 writeInfoLine / print 会弹出「Praat Info」，脚本层要兜住，内容不丢。"""

        directory = tempfile.TemporaryDirectory()
        base = Path(directory.name)
        result = base / "chat_result.tsv"
        context = tools.ToolContext(
            tools.parse_object_context(CONTEXT), result, base / "chat_state.txt"
        )
        script = tools.render(
            tools.CUSTOM_SCRIPT_TOOL,
            {},
            context,
            custom_script=(
                'selectObject: 1\n'
                'writeInfoLine: "共振峰：", 500\n'
                "clearinfo\n"
                'printline 这行也要留着\n'
                'Rename: "新名字"\n'
            ),
        )
        directory.cleanup()
        # 可执行的行里不许再有会弹 Info 窗口的命令（被注释掉的 printtab/clearinfo 不算）。
        for line in script.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertIsNone(tools.INFO_LINE_PATTERN.match(line), line)
            self.assertIsNone(tools.INFO_OTHER_PATTERN.match(line), line)
        # 行式的输出改成写结果文件，模型想回给用户的那句话还在。
        self.assertIn('appendFileLine: "', script)
        self.assertIn('"共振峰：", 500', script)
        self.assertIn('Rename: "新名字"', script)
        # printline 的字面文字也要带过去，不再被注释掉。
        self.assertIn('appendFileLine: "', script)
        self.assertNotIn("这行也要留着", "\n".join(
            line for line in script.splitlines() if line.strip().startswith("#")
        ))
        self.assertIn("这行也要留着", script)
        # 只有「没有内容可保留」的命令才留注释，方便排查。
        self.assertIn("已省略只影响 Info 窗口的命令", script)

    def test_literal_output_commands_become_result_lines(self) -> None:
        """printline / print / echo 在 Praat 里写的是字面文字，整段抄进结果文件。"""

        script = tools.neutralize_info_commands(
            'printline 第一行\nprint 接着写\necho 说一句"带引号"的话\n',
            Path("D:/x/result.tsv"),
        )
        self.assertEqual(
            script,
            'appendFileLine: "D:/x/result.tsv", "第一行"\n'
            'appendFileLine: "D:/x/result.tsv", "接着写"\n'
            'appendFileLine: "D:/x/result.tsv", "说一句""带引号""的话"\n',
        )

    def test_value_output_commands_become_result_lines(self) -> None:
        """appendInfo / writeInfo 带的是值列表，原样换成 appendFileLine。"""

        script = tools.neutralize_info_commands(
            'appendInfo: "F1 = ", f1, " Hz"\nwriteInfo: 1, 2, 3\n',
            Path("D:/x/result.tsv"),
        )
        self.assertEqual(
            script,
            'appendFileLine: "D:/x/result.tsv", "F1 = ", f1, " Hz"\n'
            'appendFileLine: "D:/x/result.tsv", 1, 2, 3\n',
        )

    def test_meaningless_info_commands_are_commented_out(self) -> None:
        """printtab 只写制表符、clearinfo 只清空 Info 窗口：没有内容可保留。"""

        script = tools.neutralize_info_commands(
            "printtab\nclearinfo\n", Path("D:/x/result.tsv")
        )
        self.assertNotIn("appendFileLine", script)
        self.assertEqual(len(script.strip().splitlines()), 2)
        for line in script.strip().splitlines():
            self.assertTrue(line.startswith("# praat-ai:"), line)

    def test_write_info_line_without_arguments_stays_valid(self) -> None:
        script = tools.neutralize_info_commands(
            "writeInfoLine:\n", Path("D:/x/result.tsv")
        )
        self.assertEqual(script.strip(), 'appendFileLine: "D:/x/result.tsv", ""')

    def test_append_file_line_lines_are_untouched(self) -> None:
        original = 'appendFileLine: "D:/x/result.tsv", "ok"\n'
        self.assertEqual(
            tools.neutralize_info_commands(original, Path("D:/x/result.tsv")), original
        )


class ObjectMatchingTests(unittest.TestCase):
    """模型给的对象写法常常和 Praat 列表里的名字不完全一样。"""

    def setUp(self) -> None:
        self.context = tools.ToolContext(
            tools.parse_object_context(MIXED_CONTEXT),
            Path("r.tsv"),
            Path("s.txt"),
        )

    def test_name_without_class_prefix_matches(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.context.resolve_object("思い出す")
        self.assertEqual(self.context.resolve_object("Sound 思い出す").id, 1)

    def test_short_name_matches_full_name(self) -> None:
        self.assertEqual(self.context.resolve_object("tone").id, 2)

    def test_chinese_id_forms_are_accepted(self) -> None:
        for text in ("3", "3 号", "#3", "id 3"):
            self.assertEqual(self.context.resolve_object(text).id, 3, text)

    def test_ambiguous_name_is_rejected_with_candidates(self) -> None:
        with self.assertRaises(tools.ToolError) as caught:
            self.context.resolve_object("Sound")
        self.assertIn("多个对象", str(caught.exception))

    def test_unknown_name_still_fails(self) -> None:
        with self.assertRaises(tools.ToolError):
            self.context.resolve_object("Sound nothing")


class ToolGuardTests(unittest.TestCase):
    """对象类型不匹配时要给出中文原因，而不是让 Praat 抛英文错误。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.context = tools.ToolContext(
            tools.parse_object_context(MIXED_CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_play_requires_sound(self) -> None:
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("play", {"object": 3}, self.context)
        self.assertIn("声音", str(caught.exception))

    def test_duration_rejects_objects_without_time(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools.render("duration", {"object": 4}, self.context)

    def test_object_info_skips_duration_for_tables(self) -> None:
        script = tools.render("object_info", {"object": 4}, self.context)
        self.assertNotIn("Get total duration", script)
        self.assertIn("没有时长", script)

    def test_object_info_reports_sound_properties(self) -> None:
        script = tools.render("object_info", {"object": 2}, self.context)
        self.assertIn("Get total duration", script)
        self.assertIn("Get number of channels", script)
        self.assertIn("Get sampling frequency", script)

    def test_object_info_writes_the_selection_share_of_the_duration(self) -> None:
        """2026-10-04：圈选段要带上占时长的百分比。

        报告阶段没有工具、只有证据数字；「选段在词里哪个位置」必须能靠算术定，
        否则模型会凭印象把含爆破点的选段说成「词首辅音之前」。
        """

        context = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\tsel_start\tsel_end\n"
                "1\tSound\tSound あなた\t1\t0.446194\t0.671104\n"
            ),
            Path(self.directory.name) / "chat_result.tsv",
            Path(self.directory.name) / "chat_state.txt",
        )
        script = tools.render("object_info", {}, context)
        self.assertIn("selStart = 0.446194", script)
        self.assertIn("selEnd = 0.671104", script)
        self.assertIn("；圈选 ", script)
        self.assertIn("fixed$ (selStart / duration * 100, 0)", script)
        self.assertIn("fixed$ (selEnd / duration * 100, 0)", script)
        # 没有圈选的对象不许凭空写一段占比。
        plain = tools.render("object_info", {}, self.context)
        self.assertNotIn("圈选 ", plain)


class QueryRobustnessTests(unittest.TestCase):
    """越界时间和 --undefined-- 结果都要变成中文说明。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.context = tools.ToolContext(
            tools.parse_object_context(MIXED_CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_pitch_preserves_outside_request_and_explains_missing_value(self) -> None:
        script = tools.render("pitch", {"time": 5.0}, self.context)
        self.assertIn("queryOutside = time < domainStart or time > domainEnd", script)
        self.assertIn("未改用其他时刻", script)
        self.assertIn("if value = undefined", script)
        self.assertIn("没有周期性声源", script)

    def test_formant_query_explains_undefined(self) -> None:
        script = tools.render("formant_bandwidth", {"formant": 2}, self.context)
        self.assertIn("if value = undefined", script)
        self.assertIn("没有可用的共振峰数据", script)

    def test_intensity_query_explains_undefined(self) -> None:
        script = tools.render("intensity", {}, self.context)
        self.assertIn("if value = undefined", script)
        self.assertIn("没有强度数据", script)


class NewToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)
        self.context = tools.ToolContext(
            tools.parse_object_context(MIXED_CONTEXT),
            self.base / "chat_result.tsv",
            self.base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_formant_can_query_several_numbers_at_once(self) -> None:
        script = tools.render(
            "formant_frequency",
            {"formant": "1,2", "time": 0.4},
            self.context,
        )
        self.assertIn('Get value at time: 1, time, "hertz", "linear"', script)
        self.assertIn('Get value at time: 2, time, "hertz", "linear"', script)
        # 「频率和带宽」一次问完，不再需要两个工具。
        self.assertIn('Get bandwidth at time: 2, time, "hertz", "linear"', script)
        self.assertIn("第 2 共振峰（", script)
        self.assertIn("：频率 ", script)

    def test_invalid_formant_number_message_is_chinese(self) -> None:
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("formant_frequency", {"formant": "0,2"}, self.context)
        self.assertIn("共振峰编号", str(caught.exception))

    def test_create_sound_builds_pure_tone_and_silence(self) -> None:
        tone = tools.render(
            "create_sound",
            {"duration": 1.5, "frequency": 220, "amplitude": 0.4},
            self.context,
        )
        self.assertIn("Create Sound as pure tone", tone)
        self.assertIn("220.000000", tone)
        silence = tools.render("create_sound", {"frequency": 0}, self.context)
        self.assertIn("Create Sound from formula", silence)
        self.assertIn("~ 0", silence)

    def test_concatenate_needs_two_different_sounds(self) -> None:
        script = tools.render(
            "concatenate_sounds",
            {"object": 1, "object2": 2},
            self.context,
        )
        self.assertIn("selectObject: 1", script)
        self.assertIn("plusObject: 2", script)
        self.assertIn("Concatenate", script)
        with self.assertRaises(tools.ToolError):
            tools.render("concatenate_sounds", {"object": 1, "object2": 1}, self.context)
        with self.assertRaises(tools.ToolError):
            tools.render("concatenate_sounds", {"object": 1}, self.context)

    def test_concatenate_rejects_non_sound(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools.render(
                "concatenate_sounds",
                {"object": 1, "object2": 3},
                self.context,
            )

    def test_extract_part_clamps_and_validates(self) -> None:
        script = tools.render(
            "extract_part",
            {"object": 1, "start": 0.2, "end": 0.5},
            self.context,
        )
        self.assertIn('Extract part: t1, t2, "rectangular", 1, "no"', script)
        self.assertIn("if t2 > duration", script)
        # Praat 的 from / to / end 都是保留字，模板里不能拿来当变量名。
        for line in script.splitlines():
            name = line.split("=")[0].strip()
            self.assertNotIn(name, {"from", "to", "end"})
        with self.assertRaises(tools.ToolError):
            tools.render("extract_part", {"start": 0.5}, self.context)
        with self.assertRaises(tools.ToolError):
            tools.render("extract_part", {"start": 0.5, "end": 0.2}, self.context)

    def test_save_sound_requires_absolute_wav_path(self) -> None:
        target = self.base / "out" / "a"
        script = tools.render(
            "save_sound",
            {"object": 1, "path": str(target)},
            self.context,
        )
        self.assertIn(f'Save as WAV file: "{target.as_posix()}.wav"', script)
        with self.assertRaises(tools.ToolError):
            tools.render("save_sound", {"object": 1}, self.context)
        with self.assertRaises(tools.ToolError):
            tools.render("save_sound", {"object": 1, "path": "a.wav"}, self.context)
        with self.assertRaises(tools.ToolError):
            tools.render(
                "save_sound",
                {"object": 3, "path": str(self.base / "a.wav")},
                self.context,
            )

    def test_save_sound_creates_missing_folder(self) -> None:
        target = self.base / "新建目录" / "更深" / "out.wav"
        script = tools.render(
            "save_sound",
            {"object": 1, "path": str(target)},
            self.context,
        )
        self.assertTrue(target.parent.is_dir())
        self.assertIn("Save as WAV file", script)

    def test_resample_requires_integer_rate(self) -> None:
        script = tools.render("resample_sound", {"rate": 16000}, self.context)
        self.assertIn("Resample: 16000, 50", script)
        with self.assertRaises(tools.ToolError):
            tools.render("resample_sound", {"rate": 16000.5}, self.context)

    def test_duplicate_object_uses_copy(self) -> None:
        script = tools.render("duplicate_object", {"object": 1}, self.context)
        self.assertIn('Copy: "', script)

    def test_pitch_statistics_uses_mean_min_max(self) -> None:
        script = tools.render(
            "pitch_statistics",
            {"object": 1, "from": 0.1, "to": 0.6},
            self.context,
        )
        self.assertIn('Get mean: tmin, tmax, "Hertz"', script)
        # 最低/最高不再直接问 Praat（裸 Get minimum/maximum 会被倍频误判劫持），
        # 改成逐帧、按中位数的 0.5–1.5 倍限幅后再取极值。
        self.assertNotIn('minimum = Get minimum', script)
        self.assertNotIn('maximum = Get maximum', script)
        self.assertIn("minimum = lowest", script)
        self.assertIn("maximum = highest", script)
        self.assertIn("tmin = 0.100000", script)
        self.assertIn("tmax = 0.600000", script)

    def test_intensity_statistics_uses_energy_mean(self) -> None:
        script = tools.render("intensity_statistics", {"object": 1}, self.context)
        self.assertIn('Get mean: tmin, tmax, "energy"', script)

    def test_point_queries_accept_several_times(self) -> None:
        for name in ("pitch", "intensity", "formant_frequency"):
            script = tools.render(name, {"time": "0.25,0.75"}, self.context)
            self.assertIn("time = 0.250000", script, name)
            self.assertIn("time = 0.750000", script, name)
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("pitch", {"time": "abc"}, self.context)
        self.assertIn("时间只能是", str(caught.exception))
        # 2026-10-20：上限按区间长度自适应（最少 8、最多 64），12 个时刻现在能用；
        # 超过上限仍然拒绝，别把脚本撑成一份数据点清单。
        script = tools.render(
            "pitch", {"time": ",".join(str(index / 10) for index in range(12))}, self.context
        )
        self.assertEqual(script.count("time = 0.300000"), 1)
        with self.assertRaises(tools.ToolError):
            tools.render(
                "pitch",
                {"time": ",".join(str(index / 100) for index in range(70))},
                self.context,
            )

    def test_formant_statistics_reports_each_formant_separately(self) -> None:
        script = tools.render("formant_statistics", {"formant": "1,2"}, self.context)
        self.assertIn('Get mean: 1, tmin, tmax, "hertz"', script)
        self.assertIn('Get mean: 2, tmin, tmax, "hertz"', script)
        # 每条共振峰都要有自己的结果行，不能共用被覆盖的变量。
        self.assertEqual(script.count("第 1 共振峰平均"), 1)
        self.assertEqual(script.count("第 2 共振峰平均"), 1)

    def test_harmonicity_statistics_uses_harmonicity_analysis(self) -> None:
        script = tools.render("harmonicity_statistics", {}, self.context)
        self.assertIn("To Harmonicity (cc): 0.01, 75, 0.1, 1", script)
        self.assertIn("Get mean: tmin, tmax", script)
        self.assertIn("谐噪比 HNR", script)

    def test_spectrogram_requires_sound(self) -> None:
        script = tools.render("spectrogram", {}, self.context)
        self.assertIn('To Spectrogram: 0.005000, 5000.000000, 0.002000, 20.000000, "Gaussian"', script)
        self.assertIn("Get number of frames", script)
        with self.assertRaises(tools.ToolError):
            tools.render("spectrogram", {"object": 3}, self.context)

    def test_spectrogram_name_does_not_shadow_the_source_object(self) -> None:
        # 2 号对象的简称是 tone，模型可能把这个名字当成新名字传进来。
        script = tools.render(
            "spectrogram",
            {"object": 2, "name": "tone"},
            self.context,
        )
        self.assertIn('Rename: "tone 频谱图"', script)

    def test_pitch_statistics_reports_time_of_maximum(self) -> None:
        script = tools.render("pitch_statistics", {}, self.context)
        # 最高点时刻必须来自「稳健的逐帧循环」，不能是裸的 Get time of maximum：
        # 自相关在浊音起始常把真实基频听成 2–4 倍（2026-09-30 用户报的 598.5 Hz）。
        self.assertNotIn("maxtime = Get time of maximum", script)
        self.assertIn("maxtime = Get time from frame number: pitchFrame", script)
        self.assertIn("最高点出现在", script)


class TextGridToolTests(unittest.TestCase):
    """TextGrid 的查看与标注（真机命令在 verify_chat_templates.py 里跑过）。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.context = tools.ToolContext(
            tools.parse_object_context(GRID_CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_info_lists_tiers_and_intervals(self) -> None:
        script = tools.render("textgrid_info", {}, self.context)
        self.assertIn("Get number of tiers", script)
        # if 条件里不能直接调用命令，必须先赋值。
        self.assertIn("isInterval = Is interval tier: tier", script)
        self.assertIn("if isInterval = 1", script)
        self.assertIn("Get label of interval: tier, part", script)
        self.assertIn("Get number of points: tier", script)
        self.assertIn("Get label of point: tier, part", script)

    def test_info_limits_the_listing(self) -> None:
        script = tools.render("textgrid_info", {"maximum_intervals": 5}, self.context)
        self.assertIn("if shown < 5", script)
        self.assertIn("未列出", script)

    def test_set_interval_inserts_boundaries_and_writes_label(self) -> None:
        script = tools.render(
            "textgrid_set_interval",
            {"start": 0.2, "end": 0.5, "label": "a"},
            self.context,
        )
        self.assertIn("t1 = 0.200000", script)
        self.assertIn("t2 = 0.500000", script)
        self.assertIn("Insert boundary: 1, t1", script)
        self.assertIn("Insert boundary: 1, t2", script)
        self.assertIn('Set interval text: 1, part, "a"', script)
        # 已经有边界时不能再插入，否则 Praat 会直接报错。
        self.assertIn("needBoundary1 = 1", script)
        self.assertIn("needBoundary2 = 1", script)
        self.assertIn("boundaryTime", script)
        self.assertIn("tier = 1", script)

    def test_set_interval_requires_label_and_range(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools.render("textgrid_set_interval", {"start": 0.2, "end": 0.5}, self.context)
        with self.assertRaises(tools.ToolError):
            tools.render("textgrid_set_interval", {"label": "a"}, self.context)
        with self.assertRaises(tools.ToolError):
            tools.render(
                "textgrid_set_interval",
                {"start": 0.5, "end": 0.2, "label": "a"},
                self.context,
            )

    def test_insert_boundary_reports_existing_boundary(self) -> None:
        script = tools.render("textgrid_insert_boundary", {"time": 0.5}, self.context)
        self.assertIn("Insert boundary: 1, t1", script)
        self.assertIn("已经有边界", script)
        with self.assertRaises(tools.ToolError):
            tools.render("textgrid_insert_boundary", {}, self.context)

    def test_textgrid_tools_reject_other_objects(self) -> None:
        sound_context = tools.ToolContext(
            tools.parse_object_context(MIXED_CONTEXT),
            self.context.result_path,
            self.context.state_path,
        )
        for name, arguments in (
            ("textgrid_info", {"object": 1}),
            ("textgrid_set_interval", {"object": 1, "start": 0.1, "end": 0.2, "label": "a"}),
            ("textgrid_insert_boundary", {"object": 1, "time": 0.5}),
        ):
            # 明确编号指向 Sound 时不能偷偷换成另一个 TextGrid。
            with self.assertRaises(tools.ToolError, msg=name):
                tools.render(name, arguments, sound_context)

    def test_class_fallback_needs_a_unique_candidate(self) -> None:
        two_grids = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\n"
                "1\tSound\tSound tone\t1\n"
                "2\tTextGrid\tTextGrid a\t0\n"
                "3\tTextGrid\tTextGrid b\t0\n"
            ),
            self.context.result_path,
            self.context.state_path,
        )
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("textgrid_info", {"object": 1}, two_grids)
        self.assertIn("TextGrid", str(caught.exception))

    def test_tier_number_is_validated(self) -> None:
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("textgrid_insert_boundary", {"time": 0.5, "tier": 0}, self.context)
        self.assertIn("层号", str(caught.exception))
        with self.assertRaises(tools.ToolError):
            tools.render("textgrid_set_interval", {"tier": "第一层"}, self.context)


class VotToolTests(unittest.TestCase):
    """VOT 必须按给定的两个时刻算，不能拿别的测量值顶替。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.grid_context = tools.ToolContext(
            tools.parse_object_context(GRID_CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )
        self.sound_context = tools.ToolContext(
            tools.parse_object_context("id\tclass\tname\tselected\n1\tSound\tSound tone\t1\n"),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_explicit_zero_times_do_not_become_missing_parameters(self) -> None:
        script=tools.render('vot',{'burst':0,'voicing':0},self.sound_context)
        self.assertIn('vot = t2 - t1',script)
        self.assertNotIn('Filter (pass Hann band)',script)

    def test_only_one_time_is_still_an_error(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools.render("vot", {"burst": 0.3}, self.sound_context)

    def test_non_numeric_time_is_rejected_in_chinese(self) -> None:
        with self.assertRaises(tools.ToolError) as raised:
            tools.render("vot", {"burst": "爆破", "voicing": 0.4}, self.sound_context)
        self.assertIn("必须是数字", str(raised.exception))

    def test_manual_textgrid_landmarks_leave_existing_intervals_intact(self) -> None:
        script = tools.render("vot", {"burst": 0.30, "voicing": 0.42}, self.grid_context)
        self.assertIn("vot = t2 - t1", script)
        self.assertIn("fixed$ (vot * 1000, 1)", script)
        self.assertNotIn("Insert boundary", script)
        self.assertNotIn("Set interval text", script)
        self.assertIn("@aipraat-acoustic-v1", script)

    def test_sound_path_only_subtracts(self) -> None:
        script = tools.render(
            "vot",
            {"burst": 0.30, "voicing": 0.42},
            self.sound_context,
        )
        self.assertIn("vot = t2 - t1", script)
        self.assertNotIn("Insert boundary", script)
        self.assertIn("未做自动检测", script)

    def test_missing_or_bad_times_are_rejected(self) -> None:
        # 两个时刻都不给 = 自动估计模式；只给一个才是参数错误。
        auto = tools.render("vot", {}, self.sound_context)
        self.assertIn("自动", auto)
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("vot", {"burst": 0.3}, self.grid_context)
        self.assertIn("voicing", str(caught.exception))
        script = tools.render("vot", {"burst": 0.4, "voicing": 0.3}, self.grid_context)
        self.assertIn("t2 = 0.300000", script)
        self.assertIn("预浊起始", script)

    def test_onset_alias_works(self) -> None:
        script = tools.render(
            "vot",
            {"burst": 0.1, "onset": 0.25},
            self.sound_context,
        )
        self.assertIn("t2 = 0.250000", script)

    def test_auto_mode_detects_within_the_given_range(self) -> None:
        script = tools.render(
            "vot",
            {"from": 0.25, "to": 0.5},
            self.sound_context,
        )
        self.assertIn("tmin = 0.250000", script)
        self.assertIn("tmax = 0.500000", script)
        # 爆破和浊音起始分开测：前者看 2–8 kHz 带通包络的上升沿，后者看谐噪比。
        self.assertIn("Filter (pass Hann band): 2000, 8000, 100", script)
        self.assertIn('To Intensity: 2000, 0.001, "yes"', script)
        self.assertIn('To Harmonicity (cc): 0.002, 75.000000', script)
        self.assertIn('To Pitch (filtered autocorrelation): 0.002, 75.000000', script)
        self.assertIn("burstTime", script)
        self.assertIn("burstRise", script)
        self.assertIn("firstVoicedTime", script)
        self.assertIn("VOT 估计值", script)
        self.assertIn("爆破与浊音起始分开估计", script)
        self.assertIn("请对着语图核对", script)

    def test_editor_selection_note_survives_the_model_echoing_it(self) -> None:
        # 模型把圈选原样抄进 from/to 时，回话里仍要写清楚这段范围是圈出来的。
        context = tools.ToolContext(
            tools.parse_object_context(SELECTED_CONTEXT),
            self.sound_context.result_path,
            self.sound_context.state_path,
        )
        script = tools.render("vot", {"from": 0.25, "to": 0.5}, context)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)
        self.assertNotIn("未指定范围", script)

    def test_auto_mode_warns_when_the_range_holds_two_phonemes(self) -> None:
        # 圈大了（后面还有第二个音素）时不能静默只报第一个。
        script = tools.render("vot", {"from": 0.25, "to": 1.0}, self.sound_context)
        self.assertIn("secondVoicingTime", script)
        self.assertIn("第 2 段浊音", script)
        self.assertIn("建议收紧范围", script)

    def test_auto_mode_uses_the_range_dragged_in_the_editor(self) -> None:
        context = tools.ToolContext(
            tools.parse_object_context(SELECTED_CONTEXT),
            self.sound_context.result_path,
            self.sound_context.state_path,
        )
        script = tools.render("vot", {}, context)
        self.assertIn("tmin = 0.250000", script)
        self.assertIn("tmax = 0.500000", script)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)
        self.assertNotIn("未指定范围", script)

    def test_explicit_range_beats_the_editor_selection(self) -> None:
        context = tools.ToolContext(
            tools.parse_object_context(SELECTED_CONTEXT),
            self.sound_context.result_path,
            self.sound_context.state_path,
        )
        script = tools.render("vot", {"from": 0.6, "to": 0.8}, context)
        self.assertIn("tmin = 0.600000", script)
        self.assertIn("tmax = 0.800000", script)
        self.assertNotIn("按编辑器圈选", script)

    def test_auto_mode_needs_a_sound_not_a_textgrid(self) -> None:
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("vot", {"from": 0.1, "to": 0.3}, self.grid_context)
        self.assertIn("Sound", str(caught.exception))


class EditorSelectionRangeTests(unittest.TestCase):
    """拖出来的选区要能接到所有"按一段时间统计"的工具上，而不是只有 VOT。"""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.selected = tools.ToolContext(
            tools.parse_object_context(SELECTED_CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )
        self.plain = tools.ToolContext(
            tools.parse_object_context(CONTEXT),
            base / "chat_result.tsv",
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_statistics_tools_use_the_editor_selection(self) -> None:
        for name in (
            "pitch_statistics",
            "intensity_statistics",
            "formant_statistics",
            "harmonicity_statistics",
        ):
            script = tools.render(name, {}, self.selected)
            self.assertIn("tmin = 0.250000", script, name)
            self.assertIn("tmax = 0.500000", script, name)
            self.assertIn("按编辑器圈选 0.250–0.500 秒", script, name)
            self.assertIn("appendFileLine:", script, name)
            # 回话里必须带范围出处，不能只写数字。pitch_statistics 还会在末尾追加
            # 一句「剔除了倍频误判」的说明（见 2026-09-30 的 598.5 Hz 问题），
            # 所以这里只要求 rangeNote$ 出现在这一行、且它是最后一个内容项之前。
            written = [
                line for line in script.splitlines() if "chat_result.tsv" in line
            ]
            self.assertTrue(written, name)
            self.assertIn("rangeNote$", written[-1], name)

    def test_statistics_note_survives_the_model_echoing_the_selection(self) -> None:
        # 模型把 sel_start/sel_end 抄成 from/to 时，回话里仍要写清范围出处。
        script = tools.render(
            "formant_statistics",
            {"formant": 1, "from": 0.25, "to": 0.5},
            self.selected,
        )
        self.assertIn("tmin = 0.250000", script)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)

    def test_explicit_range_beats_the_editor_selection(self) -> None:
        script = tools.render(
            "intensity_statistics",
            {"from": 0.6, "to": 0.8},
            self.selected,
        )
        self.assertIn("tmin = 0.600000", script)
        self.assertIn("tmax = 0.800000", script)
        self.assertNotIn("按编辑器圈选", script)

    def test_without_a_selection_the_whole_object_is_still_the_default(self) -> None:
        script = tools.render("pitch_statistics", {}, self.plain)
        self.assertIn("tmin = domainStart", script)
        self.assertIn("tmax = domainEnd", script)
        self.assertNotIn("按编辑器圈选", script)
        self.assertIn('rangeNote$ = ""', script)

    def test_point_query_falls_back_to_the_selection_middle(self) -> None:
        # 圈了 0.25–0.5 再问「这一刻的基频」：问的应该是圈里那一段，不是整个对象的中点。
        for name in ("pitch", "intensity", "formant_frequency"):
            script = tools.render(name, {}, self.selected)
            self.assertIn("time = 0.375000", script, name)
        explicit = tools.render("pitch", {"time": 0.9}, self.selected)
        self.assertIn("time = 0.900000", explicit)
        self.assertNotIn("time = 0.375000", explicit)

    def test_extract_part_uses_the_editor_selection(self) -> None:
        script = tools.render("extract_part", {}, self.selected)
        self.assertIn("t1 = 0.250000", script)
        self.assertIn("t2 = 0.500000", script)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)
        # 没圈选也没有 end 时仍然报错，不能猜一个范围。
        with self.assertRaises(tools.ToolError):
            tools.render("extract_part", {}, self.plain)

    def test_extract_part_honours_the_finish_alias(self) -> None:
        # finish 是 end 的别名；以前取值漏了别名，会误报「开始时间必须小于结束时间」。
        script = tools.render(
            "extract_part",
            {"object": 1, "start": 0.1, "finish": 0.4},
            self.plain,
        )
        self.assertIn("t2 = 0.400000", script)

    def test_textgrid_label_uses_the_editor_selection(self) -> None:
        # 在 TextGrid 编辑器里拖一段再让 AI 标注：范围就用拖出来的那一段。
        context = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\tsel_start\tsel_end\n"
                "1\tTextGrid\tTextGrid grid\t1\t0.250000\t0.500000\n"
            ),
            self.plain.result_path,
            self.plain.state_path,
        )
        script = tools.render("textgrid_set_interval", {"label": "a"}, context)
        self.assertIn("t1 = 0.250000", script)
        self.assertIn("t2 = 0.500000", script)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)
        # 没圈选时照旧报错，不能自己编一个区间。
        with self.assertRaises(tools.ToolError):
            tools.render("textgrid_set_interval", {"label": "a"}, self.plain)


class PythonScriptRejectionTests(unittest.TestCase):
    """模型把 Python 当 Praat 脚本时会带来莫名其妙的报错，必须提前拦下。"""

    def test_python_scripts_are_rejected(self) -> None:
        samples = (
            'import numpy as np\nsound = 1\n',
            "from praat import *\n",
            "def make_sound():\n    return 1\n",
            'print("hello")\n',
            'os.system("dir")\n',
        )
        for sample in samples:
            with self.assertRaises(tools.ToolError, msg=sample):
                tools.validate_script(sample)

    def test_praat_scripts_are_still_accepted(self) -> None:
        script = tools.validate_script(
            'selectObject: 1\nRename: "测试"\nappendInfoLine: "ok"\n'
        )
        self.assertIn("Rename", script)


if __name__ == "__main__":
    unittest.main()
