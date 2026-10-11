"""时间参数别名必须在**所有**接受范围的工具里一致生效。

2026-09-29 回归：云端 Prompt（``qwen.CLOUD_WORKFLOW_INSTRUCTIONS``）要求模型对整段请求
写 ``from=0`` / 终点写实际 duration，而 ``pitch_statistics``、``measure`` 等一批工具的
schema 里也只有 ``from``/``to``。但 ``extract_part`` 和 ``textgrid_set_interval`` 的
schema 写的是 ``start``/``end``：

- ``extract_part`` 认 ``from``/``to``/``finish`` 别名（``_number`` 的默认值是**先求值**的，
  ``arguments.get("start", arguments.get("from"))`` 真的能把 ``from`` 取出来），
- ``textgrid_set_interval`` 只把 ``end``/``finish`` 当别名，``from``/``to`` 完全没接，
  于是一句「把 0.2–0.6 秒标成 a」会直接报「标注区间需要 end 参数」——模型按 Prompt
  给的时间被丢掉。

这里的测试把「两个工具接受同一套别名」钉住，并顺手覆盖「非数字别名值必须报错」，
避免它退回默认值静默换范围。纯 Python 断言足够，不需要真 Praat。
"""

import tempfile
import unittest
from pathlib import Path

from praat_ai import tools


class RangeAliasTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def context(self, *rows):
        return tools.ToolContext(
            tuple(rows), self.base / "result.txt", self.base / "state.txt"
        )

    def sound(self):
        return self.context(tools.ObjectRow(1, "Sound", "tone", True))

    def grid(self):
        return self.context(tools.ObjectRow(1, "TextGrid", "grid", True))

    def assignments(self, script, names=("t1", "t2", "tmin", "tmax")):
        """脚本里 ``名字 = 数值`` 的真实赋值（按出现顺序）。"""

        found = {}
        for line in script.splitlines():
            for name in names:
                prefix = f"{name} = "
                if line.startswith(prefix):
                    found.setdefault(name, []).append(line[len(prefix):])
        return found

    def assert_time(self, script, name, value):
        values = self.assignments(script).get(name)
        self.assertIsNotNone(values, f"脚本里没有 {name} 的赋值：\n{script}")
        self.assertIn(f"{value:.6f}", values, f"{name} 没有取到 {value}：{script}")

    # ---- extract_part：from/to 与 finish 都是 start/end 的别名 -------------

    def test_extract_part_uses_nonzero_from_to_aliases(self):
        script = tools.render("extract_part", {"from": 0.2, "to": 0.7}, self.sound())
        self.assert_time(script, "t1", 0.2)
        self.assert_time(script, "t2", 0.7)

    def test_extract_part_uses_finish_alias_for_end(self):
        script = tools.render(
            "extract_part", {"object": 1, "start": 0.1, "finish": 0.4}, self.sound()
        )
        self.assert_time(script, "t1", 0.1)
        self.assert_time(script, "t2", 0.4)

    def test_extract_part_reports_contradicting_start_and_from(self):
        """``start`` 与 ``from`` 给了不同的值就是自相矛盾：以前 start 静默胜出、from 被丢掉。

        2026-10-07：别名折叠只在工具没声明另一个键时发生，声明过的键交给 build 做冲突检查。
        """

        with self.assertRaises(tools.ToolError) as caught:
            tools.render("extract_part", {"start": 0.3, "from": 0.9, "end": 0.6}, self.sound())
        self.assertIn("矛盾", str(caught.exception))
        script = tools.render("extract_part", {"start": 0.3, "from": 0.3, "end": 0.6}, self.sound())
        self.assert_time(script, "t1", 0.3)

    # ---- textgrid_set_interval：必须是同一套别名 --------------------------

    def test_textgrid_set_interval_accepts_from_to_aliases(self):
        script = tools.render(
            "textgrid_set_interval", {"from": 0.2, "to": 0.6, "label": "a"}, self.grid()
        )
        self.assert_time(script, "t1", 0.2)
        self.assert_time(script, "t2", 0.6)

    def test_textgrid_set_interval_still_accepts_start_end(self):
        script = tools.render(
            "textgrid_set_interval", {"start": 0.2, "end": 0.6, "label": "a"}, self.grid()
        )
        self.assert_time(script, "t1", 0.2)
        self.assert_time(script, "t2", 0.6)

    def test_textgrid_set_interval_uses_finish_alias_for_end(self):
        script = tools.render(
            "textgrid_set_interval",
            {"start": 0.1, "finish": 0.5, "label": "a"},
            self.grid(),
        )
        self.assert_time(script, "t2", 0.5)

    def test_both_tools_agree_on_the_alias_set(self):
        """同一对 ``from``/``to`` 在截取和标注里必须落到同一个区间。"""

        arguments = {"from": 0.2, "to": 0.6}
        extract = tools.render("extract_part", dict(arguments), self.sound())
        annotate = tools.render(
            "textgrid_set_interval", {**arguments, "label": "a"}, self.grid()
        )
        self.assertEqual(
            (self.assignments(extract)["t1"], self.assignments(extract)["t2"]),
            (self.assignments(annotate)["t1"], self.assignments(annotate)["t2"]),
        )

    # ---- 统计类工具的 from/to（走 _range_lines）--------------------------

    def test_statistics_use_nonzero_from_to_aliases(self):
        for name in (
            "pitch_statistics",
            "intensity_statistics",
            "formant_statistics",
            "harmonicity_statistics",
            "measure",
        ):
            with self.subTest(tool=name):
                arguments = {"from": 0.2, "to": 0.8}
                if name == "measure":
                    arguments["parameter"] = "mean_pitch"
                script = tools.render(name, arguments, self.sound())
                self.assert_time(script, "tmin", 0.2)
                self.assert_time(script, "tmax", 0.8)

    def test_statistics_still_accept_start_end_aliases(self):
        script = tools.render(
            "pitch_statistics", {"start": 0.2, "end": 0.8}, self.sound()
        )
        self.assert_time(script, "tmin", 0.2)
        self.assert_time(script, "tmax", 0.8)

    # ---- 明确给了非法值时要报错，不能悄悄退回默认 -------------------------

    def test_non_numeric_alias_value_is_rejected(self):
        cases = (
            ("extract_part", {"from": "开头", "to": 0.5}),
            ("extract_part", {"start": 0.1, "finish": "结束"}),
            ("textgrid_set_interval", {"from": "开头", "to": 0.5, "label": "a"}),
            ("textgrid_set_interval", {"start": 0.1, "finish": "结束", "label": "a"}),
        )
        for name, arguments in cases:
            with self.subTest(tool=name, arguments=arguments):
                context = self.grid() if name == "textgrid_set_interval" else self.sound()
                with self.assertRaises(tools.ToolError):
                    tools.render(name, arguments, context)

    def test_range_beyond_object_duration_is_not_silently_swapped(self):
        """给了越界的 from/to 之后不许退回整段，必须让脚本自己失败。"""

        for name, extra in (
            ("extract_part", {}),
            ("textgrid_set_interval", {"label": "a"}),
        ):
            with self.subTest(tool=name):
                context = self.grid() if name == "textgrid_set_interval" else self.sound()
                script = tools.render(name, {"from": 2.0, "to": 3.0, **extra}, context)
                self.assert_time(script, "t1", 2.0)
                self.assertNotIn("t1 = 0.000000", script.splitlines())


if __name__ == "__main__":
    unittest.main()
