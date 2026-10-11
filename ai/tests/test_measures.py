"""B1：把声学测量参数做成「表」驱动（`ai/praat_ai/measures.tsv`）。

表里每一行 = 一个参数。加一个参数只要加一行，不用再写一份 Python 模板；
`verify_chat_templates.py` 会按这张表自动生成真机用例（见本文件最后一个
测试：表里每个参数都必须有对应用例）。
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from praat_ai import measures, tools

#: ``verify_chat_templates`` 是同事目录里的手工回归脚本（不是包的一部分）。
sys.path.insert(0, str(Path(__file__).resolve().parent))


SOUND_CONTEXT = "id\tclass\tname\tselected\n1\tSound\tSound tone\t1\n"
SELECTED_CONTEXT = (
    "id\tclass\tname\tselected\tsel_start\tsel_end\n"
    "1\tSound\tSound tone\t1\t0.250000\t0.500000\n"
)
GRID_CONTEXT = "id\tclass\tname\tselected\n1\tTextGrid\tTextGrid grid\t1\n"
PITCH_CONTEXT = "id\tclass\tname\tselected\n1\tPitch\tPitch tone\t1\n"


class MeasureTableTests(unittest.TestCase):
    """表格本身的一致性：任何一行写错都在这里拦下。"""

    def setUp(self) -> None:
        self.table = measures.load_table()

    def test_table_is_not_empty_and_sections_are_parsed(self) -> None:
        self.assertGreater(len(self.table.parameters()), 25)
        self.assertIn("pitch_floor", self.table.settings)
        self.assertIn("pitch", self.table.derivations)

    def test_parameter_names_are_unique(self) -> None:
        names = [entry.parameter for entry in self.table.entries]
        self.assertEqual(len(names), len(set(names)))

    def test_every_query_points_at_a_known_source(self) -> None:
        for entry in self.table.entries:
            if entry.kind != "query":
                continue
            for key in measures.source_keys(entry.source):
                self.assertTrue(
                    key == "sound"
                    or key in self.table.derivations
                    or key in {item.parameter for item in self.table.entries},
                    f"{entry.parameter} 的 source 里有未知的 {key}",
                )

    def test_derivations_are_listed_in_dependency_order(self) -> None:
        seen: set[str] = set()
        for key, derivation in self.table.derivations.items():
            source = derivation.source
            if source and source != "Sound":
                self.assertIn(
                    source,
                    seen,
                    f"{key} 依赖 {source}，但 {source} 写在它后面（表要按依赖顺序排）",
                )
            seen.add(key)

    def test_dedicated_rows_point_at_real_tools(self) -> None:
        parameters = self.table.parameters()
        for entry in self.table.entries:
            if entry.kind != "dedicated":
                continue
            self.assertIn(entry.tool, tools.TOOL_MAP, entry.parameter)
            json.loads(entry.arguments or "{}")
            self.assertIn(entry.parameter, parameters)

    def test_source_keys_split_on_plus(self) -> None:
        self.assertEqual(measures.source_keys("pointProcess+sound"), ["pointProcess", "sound"])
        self.assertEqual(measures.source_keys("sound"), ["sound"])

    def test_every_entry_has_a_chinese_label(self) -> None:
        for entry in self.table.entries:
            self.assertTrue(entry.label.strip(), entry.parameter)
            self.assertIn(entry.kind, {"query", "dedicated", "editor"})


class MeasureRenderingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        base = Path(self.directory.name)
        self.result = base / "chat_result.tsv"
        self.context = tools.ToolContext(
            tools.parse_object_context(SOUND_CONTEXT),
            self.result,
            base / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def render(self, **arguments: object) -> str:
        return tools.render("measure", arguments, self.context)

    def test_measure_tool_is_registered(self) -> None:
        self.assertIn("measure", tools.TOOL_MAP)
        self.assertIn("measure", tools.TOOL_PARAMETERS)

    def test_schema_offers_every_parameter(self) -> None:
        schema = tools.TOOL_PARAMETERS["measure"]
        parameter = schema["properties"]["parameter"]
        # 参数名不再写成 enum：enum 只认单个名字，会把说明书里明确允许的 "f1,f2"
        # 判成非法参数（2026-10-06 实测的对话损坏根因之一）。现在由参数表生成
        # pattern，本用例仍然要求「表里每个参数都收、拼错的仍然不收」。
        validator = Draft202012Validator({"type": "object", "properties": {"parameter": parameter}})
        for name in measures.load_table().parameters():
            with self.subTest(parameter=name):
                self.assertFalse(list(validator.iter_errors({"parameter": name})))
        self.assertTrue(list(validator.iter_errors({"parameter": "not_a_parameter"})))

    def test_schema_accepts_the_documented_multi_parameter_form(self) -> None:
        schema = tools.TOOL_PARAMETERS["measure"]
        validator = Draft202012Validator(
            {"type": "object", "properties": {"parameter": schema["properties"]["parameter"]}}
        )
        for written in ("f1,f2", "f1，f2", "f1、f2", "f1; f2", "mean_pitch,hnr,cpps"):
            with self.subTest(parameter=written):
                self.assertFalse(list(validator.iter_errors({"parameter": written})))
        # 说明书（summary / signature）和工具入口都必须认同一种写法。
        self.assertIn("f1,f2", tools.TOOL_MAP["measure"].signature)
        script = self.render(parameter="f1,f2")
        self.assertIn("第 1 共振峰", script)
        self.assertIn("第 2 共振峰", script)

    def test_pitch_parameters_share_one_derivation(self) -> None:
        script = self.render(parameter=["mean_pitch", "minimum_pitch", "sd_pitch"])
        self.assertEqual(script.count("To Pitch: 0, 75.000000, 600.000000"), 1)
        self.assertIn("平均基频", script)
        self.assertIn("最低基频", script)
        self.assertIn("基频标准差", script)
        # 临时对象用完要删掉，并把选中对象还给用户。
        self.assertIn("Remove", script)
        self.assertTrue(script.rstrip().splitlines()[-1].endswith('"done"'))

    def test_jitter_uses_a_point_process_and_cleans_up(self) -> None:
        script = self.render(parameter="local_jitter")
        self.assertIn("To PointProcess (periodic, cc): 75.000000, 600.000000", script)
        self.assertIn("Get jitter (local): tmin, tmax, 0.0001, 0.02, 1.3", script)
        self.assertIn("selectObject: 1", script)

    def test_shimmer_selects_both_the_point_process_and_the_sound(self) -> None:
        script = self.render(parameter="local_shimmer_percent")
        self.assertIn("plusObject: 1", script)
        self.assertIn("Get shimmer (local): tmin, tmax, 0.0001, 0.02, 1.3, 1.6", script)

    def test_multi_statement_rows_assign_the_value_variable(self) -> None:
        script = self.render(parameter="centre_of_gravity")
        self.assertIn("To Spectrum: \"yes\"", script)
        self.assertIn("value = Get centre of gravity: 2", script)

    def test_single_parameter_command_gets_the_value_prefix(self) -> None:
        script = self.render(parameter="rms")
        self.assertIn("value = Get root-mean-square: tmin, tmax", script)

    def test_unknown_parameter_names_are_reported(self) -> None:
        with self.assertRaises(tools.ToolError) as caught:
            self.render(parameter="sqrt_cpps")
        self.assertIn("mean_pitch", str(caught.exception))

    def test_measure_needs_a_sound_or_a_known_derived_object(self) -> None:
        grid = tools.ToolContext(
            tools.parse_object_context(GRID_CONTEXT),
            self.result,
            self.context.state_path,
        )
        with self.assertRaises(tools.ToolError) as caught:
            tools.render("measure", {"parameter": "mean_pitch"}, grid)
        self.assertIn("声音", str(caught.exception))

    def test_an_existing_pitch_object_is_reused(self) -> None:
        base = Path(self.directory.name)
        pitch = tools.ToolContext(
            tools.parse_object_context(PITCH_CONTEXT),
            self.result,
            base / "chat_state.txt",
        )
        script = tools.render("measure", {"parameter": "mean_pitch"}, pitch)
        self.assertIn("selectObject: 1", script)
        self.assertNotIn("To Pitch", script)
        self.assertNotIn("Remove", script)

    def test_dedicated_parameters_delegate_to_their_tool(self) -> None:
        table = measures.load_table()
        dedicated = [entry for entry in table.entries if entry.kind == "dedicated"]
        self.assertGreaterEqual(len(dedicated), 6)
        for entry in dedicated:
            with self.subTest(parameter=entry.parameter):
                delegated = self.render(parameter=entry.parameter)
                direct = tools.render(
                    entry.tool, json.loads(entry.arguments or "{}"), self.context
                )
                self.assertEqual(delegated, direct)

    def test_mixing_dedicated_and_table_parameters_is_refused(self) -> None:
        with self.assertRaises(tools.ToolError) as caught:
            self.render(parameter=["mean_pitch", "hl_ratio"])
        self.assertIn("hl_ratio", str(caught.exception))

    def test_editor_only_parameters_are_rejected_with_a_way_out(self) -> None:
        """编辑器专属参数不能在对象列表里算：以前写一行说明就算「成功测量」。

        2026-10-07：报告阶段会把那行中文说明当成一次有结果的测量，所以改成执行前拒绝，
        并指出改用什么参数。
        """

        with self.assertRaises(tools.ToolError) as caught:
            self.render(parameter="mean_autocorrelation")
        message = str(caught.exception)
        self.assertIn("编辑器", message)
        self.assertIn("hnr", message)

    def test_common_parameter_aliases_resolve(self) -> None:
        """用户/模型常说的名字要能对上规范名（以前只认精确名）。"""

        for alias, canonical in (("F0", "mean_pitch"), ("jitter local", "local_jitter"),
                                 ("CPPS", "cpps"), ("第 1 共振峰", "f1"),
                                 ("谐噪比", "hnr"), ("最高强度", "maximum_intensity")):
            with self.subTest(alias=alias):
                script = self.render(parameter=alias)
                self.assertIn("chat_state.txt", script)
                self.assertEqual(measures.load_table().canonical(alias), canonical)
        # 有歧义的说法不猜：还是按未知参数报错并列出可用参数。
        with self.assertRaises(tools.ToolError) as caught:
            self.render(parameter="共振峰")
        self.assertIn("f1", str(caught.exception))

    def test_range_and_editor_selection_are_reported(self) -> None:
        base = Path(self.directory.name)
        selected = tools.ToolContext(
            tools.parse_object_context(SELECTED_CONTEXT),
            self.result,
            base / "chat_state.txt",
        )
        script = tools.render("measure", {"parameter": "mean_intensity"}, selected)
        self.assertIn("tmin = 0.250000", script)
        self.assertIn("tmax = 0.500000", script)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)
        explicit = tools.render(
            "measure", {"parameter": "mean_intensity", "from": 0.1, "to": 0.2}, selected
        )
        self.assertIn("tmin = 0.100000", explicit)
        self.assertNotIn("按编辑器圈选", explicit)

    def test_parameters_without_a_time_range_do_not_claim_one(self) -> None:
        script = self.render(parameter="centre_of_gravity")
        self.assertNotIn("rangeNote$", script)
        self.assertNotIn("tmin", script)

    def test_pitch_extremes_are_guarded_against_octave_errors(self) -> None:
        """``maximum_pitch`` / ``minimum_pitch`` 不许用裸 Get maximum/minimum。

        和 ``pitch_statistics`` 同一个问题（2026-09-30 用户报的 598.5 Hz 峰值）：
        ``Get maximum`` 取区间内最高的**那一帧**，自相关在浊音起始常把真实基频
        听成 2–4 倍。实测 ``a.wav`` 裸最大值 496.65 Hz，真实只有 ~121.6 Hz。
        """

        for parameter, guard, variable in (
            ("maximum_pitch", "ceilingValue = median * 1.5", "highest"),
            ("minimum_pitch", "floorValue = median * 0.5", "lowest"),
        ):
            with self.subTest(parameter=parameter):
                script = self.render(parameter=parameter)
                self.assertNotIn('value = Get maximum: tmin', script)
                self.assertNotIn('value = Get minimum: tmin', script)
                self.assertIn("median = Get quantile: tmin, tmax, 0.5", script)
                self.assertIn(guard, script)
                self.assertIn(f"value = {variable}", script)

    def test_mean_pitch_uses_the_same_guarded_frames_as_the_extremes(self) -> None:
        """``mean_pitch`` 也不许用裸 ``Get mean``（2026-10-04）。

        裸 ``Get mean`` 会把最高/最低已经剔除的倍频误判帧算进平均，于是同一次测量
        给出「平均 139.8 Hz、最高 119.2 Hz」。平均必须和极值统计同一批限幅帧。
        """

        script = self.render(parameter="mean_pitch")
        self.assertNotIn('value = Get mean: tmin', script)
        self.assertIn("ceilingValue = median * 1.5", script)
        self.assertIn("floorValue = median * 0.5", script)
        self.assertIn("totalValue = totalValue + f", script)
        self.assertIn("value = totalValue / countValue", script)

    def test_every_parameter_renders(self) -> None:
        editor_only = {entry.parameter for entry in measures.load_table().entries
                       if entry.kind == 'editor'}
        for parameter in measures.load_table().parameters():
            if parameter in editor_only:
                continue    # 编辑器专属参数在执行前被拒绝，见上面那条用例
            with self.subTest(parameter=parameter):
                script = self.render(parameter=parameter)
                self.assertIn("chat_state.txt", script)
                self.assertNotIn("'", script)

    def test_catalog_text_mentions_the_measure_tool(self) -> None:
        text = tools.catalog_text()
        self.assertIn("- measure:", text)
        self.assertIn("mean_pitch", text)


class LiveVerificationCoverageTests(unittest.TestCase):
    """表里每个参数都必须能由真机用例跑到（防止加了一行没人验证）。"""

    def test_verify_chat_templates_covers_every_parameter(self) -> None:
        import verify_chat_templates

        covered: set[str] = set()
        for case in verify_chat_templates.CASES:
            raw = case.arguments.get("parameter")
            if not raw:
                continue
            covered.update(
                piece.strip() for piece in str(raw).split(",") if piece.strip()
            )
        expected = set(measures.load_table().parameters())
        self.assertEqual(expected - covered, set())
        self.assertLessEqual(covered - expected, set())


if __name__ == "__main__":
    unittest.main()
