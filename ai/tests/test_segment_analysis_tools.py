"""VOT adapters must hand boundaries and result serialization to Praat's C++ core."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from praat_ai import tools


class SegmentAnalysisToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        self.context = tools.ToolContext(
            tools.parse_object_context(
                "id\tclass\tname\tselected\tsel_start\tsel_end\n"
                "1\tSound\ttone\t1\t0.250000\t0.500000\n"
            ),
            root / "chat_result.tsv",
            root / "chat_state.txt",
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_zero_boundaries_mean_unspecified_and_fall_back_to_estimation(self) -> None:
        """两个时刻都是 0 表示「没给」：走自动估计，而不是报「浊音起始必须晚于爆破」。

        统一 VOT 设计期间这里曾经要求把 0.0 当显式人工值交给原生动作，那条路线已暂停
        （见 tools._build_vot 的说明与 git 739cdd86）；现在的契约是：0/0 = 未指定。
        """

        script = tools._build_vot(
            {"object": "Sound tone", "burst": 0.0, "voicing": 0.0}, self.context
        )

        self.assertIn("appendFileLine", script)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)
        self.assertNotIn("浊音起始必须晚于", script)

    def test_prevoiced_boundaries_are_rejected_with_a_clear_reason(self) -> None:
        """先浊后爆（prevoiced，负 VOT）目前会被拦下，并说明为什么（不是静默出错）。

        统一 VOT 设计期间这条输入是交给原生动作处理的（fon/Praat_tests.cpp 里有 prevoiced
        夹具），那条路线已暂停（git 739cdd86）。保留实现只做「浊音起始 − 爆破」的手工相减，
        voicing <= burst 属于参数错误，不会悄悄给出负 VOT。
        这里与 test_chat_tools 的保留契约（必须晚于）保持一致。
        """

        with self.assertRaises(tools.ToolError) as caught:
            tools._build_vot(
                {"object": "Sound tone", "burst": 0.30, "voicing": 0.28}, self.context
            )

        message = str(caught.exception)
        self.assertIn("必须晚于", message)
        self.assertIn("已暂停", message)

    def test_omitted_boundaries_select_candidate_estimation(self) -> None:
        """没给时刻时按圈选/范围做估计，并注明范围出处与「估计值」的性质。"""

        script = tools._build_vot({"object": "Sound tone"}, self.context)

        self.assertIn("tmin = 0.250000", script)
        self.assertIn("tmax = 0.500000", script)
        self.assertIn("按编辑器圈选 0.250–0.500 秒", script)
        self.assertIn("appendFileLine", script)

    def test_candidate_export_keeps_the_selected_absolute_interval(self) -> None:
        script = tools._build_vot({"object": "Sound tone"}, self.context)

        self.assertIn("tmin = 0.250000", script)
        self.assertIn("tmax = 0.500000", script)
        # 结果写在结果文件里（appendFileLine），不再调用已暂停的原生 TSV 动作。
        self.assertIn("appendFileLine", script)
        self.assertNotIn("Write VOT analysis to file", script)

    def test_one_sided_boundary_fails_before_script_dispatch(self) -> None:
        with self.assertRaises(tools.ToolError):
            tools._build_vot(
                {"object": "Sound tone", "burst": 0.30}, self.context
            )

    def test_longsound_needs_a_sound_first(self) -> None:
        """自动估计要读样本：LongSound 必须先存成 Sound（脚本估算不做隐式转换）。"""

        long_context = tools.ToolContext(
            tools.parse_object_context("id\tclass\tname\tselected\n1\tLongSound\tlong\t1\n"),
            self.context.result_path,
            self.context.state_path,
        )

        with self.assertRaises(tools.ToolError) as caught:
            tools._build_vot(
                {"object": "LongSound long", "burst": 0.30, "voicing": 0.32},
                long_context,
            )

        self.assertIn("Sound", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
