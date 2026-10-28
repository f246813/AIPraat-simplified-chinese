"""共享基频质量筛查（``praat_ai/pitch_quality.py``）的行为测试。

筛查约束：

- 先做相对能量门，再算候选带，再看连续段；首尾/统计/斜率共用同一批有效帧；
- 斜率不跨无声间隔；
- 事实行稳定可解析（``基频质量：已筛查 / 待复核 / 未核验``），帧数与阈值不算实测基频；
- 输入的若是已有 Pitch 对象（没有原始 Sound），只能写「未核验」；
- 55 Hz 这类真实低音不能被当成伪迹删掉，也不做自动倍频纠正。

这些用例全部用合成信号，不含任何私人录音；没有 parselmouth 时自动跳过真机部分。
"""


import native_praat
import math
import re
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from praat_ai import evidence_quality, measures, pitch_quality, tools

try:
    import parselmouth
except Exception:  # noqa: BLE001 - 任何导入失败都按「本机没有 praat」处理
    parselmouth = None


SOUND_CONTEXT = "id\tclass\tname\tselected\n1\tSound\tprobe\t1\n"


class ContractTests(unittest.TestCase):
    """模块口径：事实行前缀、参数映射、常量都是「数据」而不是散在各处的字面量。"""

    def test_quality_prefix_is_shared_with_evidence_quality(self):
        self.assertEqual(pitch_quality.QUALITY_PREFIX, evidence_quality.QUALITY_PREFIX)
        for state in ("已筛查", "待复核", "未核验"):
            with self.subTest(state=state):
                self.assertTrue(
                    pitch_quality.is_quality_row(f"{pitch_quality.QUALITY_PREFIX}{state}；…"))
        self.assertFalse(pitch_quality.is_quality_row("平均基频 = 120 Hz"))

    def test_report_summary_names_every_state_and_threshold(self):
        summary = pitch_quality.report_summary()
        for needle in ("已筛查", "待复核", "未核验", "15", "0.5", "1.5", "3"):
            with self.subTest(needle=needle):
                self.assertIn(needle, summary)

    def test_measure_mapping_covers_every_pitch_parameter(self):
        table = measures.load_table()
        for parameter, variable in pitch_quality.MEASURE_VALUES.items():
            with self.subTest(parameter=parameter):
                entry = table.find(parameter)
                self.assertEqual(pitch_quality.measure_value(parameter), variable)
                self.assertIn("pitch", measures.source_keys(entry.source))
                # 参数表里那一行仍然存在（插件与默认参数还靠它），只是对话侧改走筛查。
                self.assertTrue(entry.command)
        self.assertEqual(
            set(pitch_quality.MEASURE_VALUES),
            {"mean_pitch", "minimum_pitch", "maximum_pitch", "median_pitch",
             "sd_pitch", "pitch_slope", "pitch_slope_octave_free", "pitch_start", "pitch_end"},
        )
        self.assertIsNone(pitch_quality.measure_value("hnr"))

    def test_energy_gate_is_relative_and_never_a_fixed_f0(self):
        lines = pitch_quality.screen_lines(sound="1", floor=75.0, ceiling=600.0)
        joined = "\n".join(lines)
        # 门限来自区间峰值，不是写死的样本值。
        self.assertIn("gatePeak = Get maximum: tmin, tmax", joined)
        self.assertIn("gateThreshold = gatePeak - 15.0", joined)
        # 强度分析的基频下限跟着 pitch floor 走（低音才不会被 Intensity 自己滤掉）。
        self.assertIn("To Intensity: 75.000000", joined)
        self.assertIn("To Intensity: 50.000000",
                      "\n".join(pitch_quality.screen_lines(sound="1", floor=35.0, ceiling=600.0)))

    def test_screening_order_and_single_frame_set(self):
        lines = pitch_quality.screen_lines(sound="1", floor=75.0, ceiling=600.0)
        joined = "\n".join(lines)
        gate = next(index for index, line in enumerate(lines)
                    if line.strip() == "gateThreshold = gatePeak - 15.0")
        band = next(index for index, line in enumerate(lines)
                    if line.startswith("    qualityFloor = qualityMedian * 0.5"))
        self.assertLess(gate, band)
        # 统计、首尾、峰值、中位数都只读 frameUsed[]/usedValue[]。
        for needle in ("usedTotal = usedTotal + usedValue[frameIndex]",
                       "usedStart = usedValue[frameIndex]",
                       "usedEnd = usedValue[frameIndex]",
                       "usedMaxTime = frameFirst + (frameIndex - 1) * frameStep"):
            with self.subTest(needle=needle):
                self.assertIn(needle, joined)
        # 斜率只在相邻两帧都有效时累加：不跨无声间隔。
        self.assertIn("if frameUsed[frameIndex] = 1 and frameUsed[frameIndex + 1] = 1", joined)
        self.assertIn("usedSlope = usedSlopeSum / (usedSlopePairs * frameStep)", joined)

    def test_low_pitch_is_only_flagged_never_deleted(self):
        lines = pitch_quality.screen_lines(sound="1", floor=50.0, ceiling=300.0)
        joined = "\n".join(lines)
        # 贴近下限只计数（贴显式下限 50 × 1.25 = 62.5 Hz），不参与 frameUsed 的判定。
        self.assertIn("nearFloorCount = nearFloorCount + 1", joined)
        used_gate = next(line for line in lines if "frameUsed[frameIndex] = 1 and qualityFloor" in line
                         or line.strip().startswith("if frameEnergy[frameIndex] = 1 and frameRun[frameIndex] = 1"))
        self.assertNotIn("nearFloor", used_gate)
        # 也不做自动倍频纠正：只数跳变处，不改 usedValue。
        self.assertIn("jumpCount = jumpCount + 1", joined)
        self.assertNotIn("usedValue[frameIndex] = usedValue[frameIndex] * 2", joined)

    def test_pitch_object_input_has_no_intensity_step(self):
        lines = pitch_quality.screen_lines(sound=None, floor=75.0, ceiling=600.0)
        self.assertNotIn("To Intensity", "\n".join(lines))
        self.assertEqual(pitch_quality.cleanup_lines(pitch=None, sound=False), [])
        self.assertEqual(pitch_quality.cleanup_lines(pitch="pitchId", sound=False),
                         ["selectObject: pitchId", "Remove"])

    def test_evidence_quality_reads_the_three_states(self):
        def evidence(rows, tool="pitch_statistics", arguments=None):
            return SimpleNamespace(rows=rows, tool=tool, arguments=arguments or {},
                                   source=SOUND_CONTEXT)

        screened = evidence([f"{pitch_quality.QUALITY_PREFIX}已筛查；参数：下限 75.0 Hz"])
        suspect = evidence([f"{pitch_quality.QUALITY_PREFIX}待复核；待复核原因：贴近显式下限"])
        unverified = evidence([f"{pitch_quality.QUALITY_PREFIX}未核验；没有原始 Sound"])
        mixed = evidence([f"{pitch_quality.QUALITY_PREFIX}已筛查",
                          f"{pitch_quality.QUALITY_PREFIX}待复核"])
        self.assertEqual(evidence_quality.pitch_quality(screened)["status"], "screened")
        self.assertEqual(evidence_quality.pitch_quality(suspect)["status"], "suspect")
        self.assertEqual(evidence_quality.pitch_quality(unverified)["status"], "unverified")
        self.assertEqual(evidence_quality.pitch_quality(mixed)["status"], "suspect")
        self.assertTrue(evidence_quality.can_interpret_pitch(screened))
        self.assertFalse(evidence_quality.can_interpret_pitch(unverified))
        # 事实行本身不算实测值：阈值/帧数不进 quantity 列表。
        quantities = [row for row in screened.rows if not evidence_quality.is_quality_row(row)]
        self.assertEqual(quantities, [])


class ScreenBehaviourTests(unittest.TestCase):
    """跑真实生成的脚本（合成音）。"""

    def setUp(self):
        if parselmouth is None:
            self.skipTest("本机没有 parselmouth，跳过 Praat 行为回归")
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def run_tool(self, tool, arguments, formula, duration, context_text=SOUND_CONTEXT):
        result = self.base / f"{tool}_result.tsv"
        if result.exists():
            result.unlink()
        context = tools.ToolContext(
            tools.parse_object_context(context_text), result, self.base / f"{tool}_state.txt")
        script = tools.render(tool, arguments, context)
        prefix = 'Create Sound from formula: "probe", 1, 0, %r, 44100, ~ %s\n' % (duration, formula)
        native_praat.run(prefix + script)
        raw = result.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-16")
        rows = [line for line in text.splitlines() if line.strip() and not line.startswith("@aipraat-")]
        quality = [row for row in rows if pitch_quality.is_quality_row(row)]
        self.assertEqual(len(quality), 1, rows)
        return rows, quality[0]

    def test_noise_before_and_after_the_tone_is_not_reported_as_pitch(self):
        formula = ("if x < 0.2 or x > 0.9 then 0.004*randomGauss(0,1) "
                   "else 0.3*sin(2*pi*100*x) endif")
        rows, quality = self.run_tool("pitch_statistics", {}, formula, 1.1)
        self.assertTrue(quality.startswith("基频质量：已筛查"), quality)
        values = {key: float(value) for key, value in
                  re.findall(r"(平均|最低|最高) ([0-9.]+)", rows[1])}
        self.assertAlmostEqual(values["平均"], 100.0, delta=2.0)
        self.assertGreater(values["最低"], 90.0, rows[1])
        self.assertLess(values["最高"], 110.0, rows[1])
        # 帧数、门限只出现在事实行里，不混进实测那一行。
        self.assertNotIn("基频质量", rows[1])
        self.assertIn("有效 ", quality)

    def test_point_query_returns_undefined_inside_the_noise(self):
        formula = ("if x < 0.3 then 0.004*randomGauss(0,1) "
                   "else 0.3*sin(2*pi*150*x) endif")
        rows, _ = self.run_tool("pitch", {"time": "0.1,0.6"}, formula, 1.0)
        self.assertIn("没有可用的基频", rows[1], rows)
        self.assertRegex(rows[2], r"= 1[45][0-9]\.[0-9]+ Hz")

    def test_slope_does_not_cross_a_silent_gap(self):
        # 两段不同基频中间隔着 0.2 秒无声：若斜率跨缺口累加，|140-100|/0.01 ≈ 4000 Hz/s。
        formula = ("if x < 0.2 then 0.3*sin(2*pi*100*x) else "
                   "(if x > 0.4 then 0.3*sin(2*pi*140*x) else 0 fi) fi")
        rows, quality = self.run_tool(
            "measure", {"parameter": "pitch_slope,mean_pitch"}, formula, 0.6)
        slope = re.search(r"基频平均绝对斜率 = ([0-9.]+) Hz/s", "\n".join(rows))
        self.assertIsNotNone(slope, rows)
        self.assertLess(float(slope.group(1)), 100.0, rows)
        self.assertTrue(quality.startswith("基频质量："), quality)

    def test_explicit_50_300_parameters_are_kept_in_the_receipt(self):
        rows, quality = self.run_tool(
            "pitch_statistics", {"pitch_floor": 50, "pitch_ceiling": 300},
            "0.3*sin(2*pi*100*x)", 0.5)
        self.assertIn("下限 50.0 Hz", quality)
        self.assertIn("上限 300.0 Hz", quality)
        # 默认仍然是 75/600：没有悄悄改成 50 或男性舒适区。
        _, default_quality = self.run_tool("pitch_statistics", {}, "0.3*sin(2*pi*200*x)", 0.5)
        self.assertIn("下限 75.0 Hz", default_quality)
        self.assertIn("上限 600.0 Hz", default_quality)

    def test_quality_line_never_reports_band_edges_as_measurements(self):
        """失败面：待复核时只给原因，不给「已测值」措辞。"""

        rows, quality = self.run_tool("pitch_statistics", {}, "0", 0.4)
        self.assertTrue(quality.startswith("基频质量：待复核"), quality)
        self.assertNotIn("已筛查", quality)
        self.assertIn("无法计算", "\n".join(rows))
        measured = [row for row in rows if not pitch_quality.is_quality_row(row)]
        self.assertTrue(measured)
        self.assertNotIn("基频质量", measured[0])

    def test_sine_glide_is_not_reported_as_a_flat_line(self):
        glide = "0.3*sin(2*pi*(150+166.6667*x)*x)"
        rows, _ = self.run_tool("pitch_statistics", {}, glide, 0.6)
        values = {key: float(value) for key, value in
                  re.findall(r"(平均|最低|最高) ([0-9.]+)", rows[1])}
        self.assertGreater(values["最高"] - values["最低"], 100.0, rows[1])
        self.assertLessEqual(values["最低"], values["平均"])
        self.assertLessEqual(values["平均"], values["最高"])
        self.assertFalse(math.isclose(values["最低"], values["最高"], rel_tol=0.05))


if __name__ == "__main__":
    unittest.main()
