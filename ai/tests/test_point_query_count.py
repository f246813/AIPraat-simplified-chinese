"""点查询的时刻数按区间长度自适应，不再写死 8 个。

2026-10-20 用户反馈：「8 个时刻太少了，应该根据选区长度来定」。原来
``_query_times`` 固定 ``maximum = 8``，3 秒的区间也只能点 8 个时刻；现在按
每 50 ms 一个时刻算（最少 8、最多 64），并且 ``time`` 可以写 ``"auto"``
让工具在区间里均匀取点。
"""

import re
import tempfile
import unittest
from pathlib import Path

from praat_ai import tools
from praat_ai.workflow_policy import measurement_scope

POINT = re.compile(r"^time = (\d+\.\d{6})$", re.MULTILINE)


class PointQueryCountTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def context(self, *rows):
        return tools.ToolContext(tuple(rows), self.base / "result.txt", self.base / "state.txt")

    def sound(self, start=None, end=None):
        return tools.ObjectRow(
            1, "Sound", "录音", True, selection_start=start, selection_end=end
        )

    def test_short_range_keeps_the_floor_at_eight_points(self):
        self.assertEqual(
            tools._query_point_count({"from": 0.0, "to": 0.2}, self.sound()), tools.MIN_POINTS
        )

    def test_longer_range_allows_more_points(self):
        row = self.sound()
        # 1 秒 → 每 50 ms 一个，21 个；3 秒 → 61 个；10 秒封顶 64 个。
        self.assertEqual(tools._query_point_count({"from": 0.0, "to": 1.0}, row), 21)
        self.assertEqual(tools._query_point_count({"from": 0.0, "to": 3.0}, row), 61)
        self.assertEqual(
            tools._query_point_count({"from": 0.0, "to": 10.0}, row), tools.MAX_POINTS
        )

    def test_editor_selection_decides_the_count(self):
        self.assertEqual(tools._query_point_count({}, self.sound(0.5, 1.5)), 21)

    def test_unknown_range_does_not_reinstate_the_eight_point_cap(self):
        self.assertEqual(tools._query_point_count({}, self.sound()), tools.MAX_POINTS)

    def test_nine_times_pass_in_a_long_range_and_fail_in_a_short_one(self):
        times = ",".join(f"{index / 8:.3f}" for index in range(9))
        script = tools.render("intensity", {"time": times}, self.context(self.sound(0.0, 2.0)))
        self.assertEqual(len(POINT.findall(script)), 9)
        with self.assertRaisesRegex(tools.ToolError, "区间长度自适应"):
            tools.render("intensity", {"time": times}, self.context(self.sound(0.0, 0.2)))

    def test_auto_samples_the_selection_evenly(self):
        script = tools.render("intensity", {"time": "auto"}, self.context(self.sound(1.0, 2.0)))
        values = [float(item) for item in POINT.findall(script)]
        self.assertEqual(len(values), 21)
        self.assertAlmostEqual(values[0], 1.0, places=6)
        self.assertAlmostEqual(values[-1], 2.0, places=6)
        self.assertAlmostEqual(values[1] - values[0], 0.05, places=6)

    def test_auto_without_any_range_uses_duration_expressions(self):
        script = tools.render("intensity", {"time": "auto"}, self.context(self.sound()))
        self.assertIn("time = timeOrigin + duration * 0.000000", script)
        self.assertIn("time = timeOrigin + duration * 1.000000", script)
        self.assertEqual(
            len(re.findall(r"^time = timeOrigin \+ duration \* ", script, re.MULTILINE)),
            tools.UNKNOWN_SPAN_POINTS,
        )

    def test_missing_time_still_queries_the_single_midpoint(self):
        script = tools.render("intensity", {}, self.context(self.sound(0.4, 0.8)))
        self.assertEqual(POINT.findall(script), ["0.600000"])

    def test_pitch_point_query_follows_the_same_rule(self):
        times = ",".join(f"{index / 8:.3f}" for index in range(9))
        self.context(self.sound(0.0, 2.0))
        tools.render("pitch", {"time": times}, self.context(self.sound(0.0, 2.0)))
        with self.assertRaisesRegex(tools.ToolError, "区间长度自适应"):
            tools.render("pitch", {"time": times}, self.context(self.sound(0.0, 0.2)))

    def test_schema_describes_the_adaptive_rule(self):
        for name in ("pitch", "formant_frequency", "formant_bandwidth", "intensity"):
            with self.subTest(tool=name):
                description = tools.tool_parameters(name)["properties"]["time"]["description"]
                self.assertIn("auto", description)
                self.assertIn(str(tools.MAX_POINTS), description)
                self.assertNotIn("一次最多 8 个", description)
                self.assertIn("time", tools.TOOL_MAP[name].signature)

    def test_workflow_scope_does_not_report_auto_expressions_as_times(self):
        self.assertEqual(measurement_scope("intensity", {"time": "auto"}, self.sound()), {
            "kind": "query_points", "requested_times": []
        })
        self.assertEqual(measurement_scope("intensity", {"time": "0.25"}, self.sound()), {
            "kind": "query_points", "requested_times": [0.25]
        })


if __name__ == "__main__":
    unittest.main()
