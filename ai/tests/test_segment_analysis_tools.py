"""Contracts for the active chat VOT script adapter."""

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

    def test_explicit_zero_landmarks_are_not_missing_values(self) -> None:
        script=tools._build_vot({'object':'Sound tone','burst':0.0,'voicing':0.0},self.context)
        self.assertIn('vot = t2 - t1',script)
        self.assertNotIn('Filter (pass Hann band)',script)
        self.assertIn('t1 = 0.000000000',script)
        self.assertIn('t2 = 0.000000000',script)

    def test_prevoiced_and_simultaneous_boundaries_are_accepted(self) -> None:
        for voicing in (.28, .30):
            script = tools._build_vot(
                {"object": "Sound tone", "burst": .30, "voicing": voicing}, self.context)
            self.assertIn("vot = t2 - t1", script)
            self.assertIn(f"t2 = {voicing:.6f}", script)

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
        # The active adapter serializes script results, not native TSV rows.
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
