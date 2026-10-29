"""``pitch_statistics`` 极值与质量筛查的回归测试。

统计约束（实现见 ``praat_ai/pitch_quality.py``）：

① 平均、最低、最高共用同一个过滤集合 ⇒ ``minimum <= mean <= maximum``；
② 时刻只取区间内的帧中心 ⇒ 峰值时刻落在 ``[from, to]``；
③ 一个可信帧都没有时不报极值，也不退回未过滤的原始均值；
④ 相对能量门（峰值 −15 dB）之后才算中位带；连续段短于 3 帧的候选不进统计；
⑤ 首尾来自可靠段，不强行填到用户给的范围两端；
⑥ 事实行固定以「基频质量：」开头，状态是 已筛查 / 待复核 / 未核验。

行为用例用独立的 parselmouth 进程跑**真实生成的 Praat 脚本**（合成音，不含任何私人
录音）；没有 parselmouth 时自动跳过。
"""


import native_praat
import re
import tempfile
import unittest
from pathlib import Path

from praat_ai import pitch_quality, tools
from praat_ai.tools import _common

try:  # 真机回归需要 praat 引擎；CI 缺库时只跑结构化用例。
    import parselmouth
except Exception:  # noqa: BLE001 - 任何导入失败都按「本机没有 praat」处理
    parselmouth = None


SOUND_CONTEXT = "id\tclass\tname\tselected\n1\tSound\tprobe\t1\n"
PITCH_CONTEXT = "id\tclass\tname\tselected\n2\tPitch\tprobe\t1\n"
# 有 Sound 的那一支（能量门 + 连续段 + 中位带）才是要守的结构。
LINES = _common._pitch_extreme_lines(sound="1", floor=75.0, ceiling=600.0)
SCRIPT = "\n".join(LINES)
FRAME_LINE = next(line for line in LINES if "Get value in frame" in line)
MEDIAN_LINE = next(line for line in LINES if line.startswith("median = Get quantile"))

#: 真实事故区间（0.783–1.175 秒）的帧表：9 帧 555–576 Hz（倍频误判）+ 8 帧 79–91 Hz，
#: 中间没有任何帧；中位数 323.236 ⇒ 过滤区间 161.6–484.9 Hz，命中 0 帧。
DOUBLE_PEAK = {77: 575.851, 78: 569.788, 79: 561.094, 80: 555.234, 81: 563.686,
               82: 572.295, 83: 574.986, 84: 573.150, 85: 574.980,
               88: 91.239, 89: 89.715, 90: 87.196, 91: 85.995, 92: 84.637,
               93: 83.134, 94: 80.719, 95: 78.753}


def block_index(lines, needle, start=0):
    for index in range(start, len(lines)):
        if needle in lines[index]:
            return index
    raise AssertionError(f"脚本里找不到 {needle!r}")


class ScriptStructureTests(unittest.TestCase):
    """生成器的文本口径：一个过滤集合、帧中心回退、无可信帧不报极值、能量门在前。"""

    def test_screening_runs_before_the_median_band(self):
        """先门后带：能量门与连续段必须在算中位带之前，否则中位数会被底噪占领。"""

        gate = block_index(LINES, "gateThreshold = gatePeak")
        energy = block_index(LINES, "frameEnergy[frameIndex] = 1")
        band = block_index(LINES, "qualityFloor = qualityMedian * 0.5")
        self.assertLess(gate, energy)
        self.assertLess(energy, band)

    def test_three_statistics_share_one_filter_condition(self):
        conditions = [line for line in LINES if "frameValue <> undefined" in line]
        self.assertEqual(len(conditions), 1, conditions)
        condition = conditions[0]
        self.assertIn("frameValue >= floorValue", condition)
        self.assertIn("frameValue <= ceilingValue", condition)
        # 能量门与连续段是同一个 if 里的另两个条件（不是第二套过滤集合）。
        self.assertIn("frameEnergy[pitchFrame] = 1", condition)
        self.assertIn("frameRun[pitchFrame] = 1", condition)

    def test_extremes_and_mean_are_updated_inside_the_same_branch(self):
        start = block_index(LINES, "frameValue <> undefined")
        end = next(index for index in range(start + 1, len(LINES))
                   if LINES[index].strip() == "endif" and LINES[index].startswith("        endif"))
        body = LINES[start + 1:end]
        for needle in ("pitchTotal = pitchTotal + frameValue",
                       "pitchCount = pitchCount + 1",
                       "highest = frameValue",
                       "lowest = frameValue"):
            with self.subTest(needle=needle):
                self.assertTrue(any(needle in line for line in body), body)

    def test_extremes_are_published_only_when_a_trustworthy_frame_survives(self):
        guard = block_index(LINES, "if pitchCount > 0")
        self.assertIn("        maximum = highest", LINES[guard + 1:guard + 3])
        self.assertIn("        minimum = lowest", LINES[guard + 1:guard + 4])
        # 「已按倍频误判剔除」的说法同样只在有可信帧时成立。
        note_guard = next(line for line in LINES if line.startswith("if pitchCount > 0 and median"))
        self.assertIn("rawMaximum > median * 1.5", note_guard)

    def test_median_band_uses_the_gated_median_when_available(self):
        # 原始分位数仍然保留（已有 Pitch 对象的退回带），但门后有候选时一律用门后中位数。
        self.assertIn(MEDIAN_LINE, LINES)
        self.assertIn("if qualityMedian <> undefined", LINES)
        self.assertIn("    median = qualityMedian", LINES)
        self.assertIn("    ceilingValue = median * 1.500000", LINES)
        self.assertIn("    floorValue = median * 0.500000", LINES)

    def test_no_trustworthy_frames_statement_is_explicit(self):
        joined = "".join(LINES)
        caveat = next(line for line in LINES if line.startswith('    pitchLimit$ = "（'))
        self.assertIn("if pitchCount = 0", joined)
        self.assertIn("没有通过质量筛查的基频帧", joined)
        self.assertIn("不输出平均/最低/最高", joined)
        self.assertIn("能量门", joined)
        self.assertIn("0.5–1.5 倍", joined)
        # 这条说明属于「一个可信帧都没有」的分支，不是普通成功分支的附言；
        # 也不能再提「原始均值可以参考」——没有可信帧时任何均值都不许打印。
        self.assertLess(joined.index("if pitchCount = 0"), joined.index(caveat.strip()))
        self.assertNotIn("原始均值", joined)
        self.assertNotIn("仅作参考", joined)

    def test_mean_is_never_taken_from_the_unfiltered_average(self):
        # 生成器只暴露 pitchCount/mean；没有可信帧时调用方必须走「没有可信统计」分支，
        # 不能退回 Praat 的 rawMean（2026-10-19 的 333.788 Hz）。
        joined = "".join(LINES)
        self.assertNotIn("mean = rawMean", joined)
        self.assertIn("if pitchCount > 0", joined)

    def test_peak_time_uses_frame_centers_inside_the_window(self):
        self.assertIn("    firstCenter = Get time from frame number: firstFrame", LINES)
        self.assertIn("    lastCenter = Get time from frame number: lastFrame", LINES)
        self.assertIn("    if firstCenter < tmin", LINES)
        self.assertIn("    if lastCenter > tmax", LINES)
        # 时刻必须来自帧本身（区间内帧中心），不能拿 tmax 凑。
        self.assertIn(
            '                maxtime = Get time from frame number: pitchFrame', LINES)

    def test_duplicate_generators_in_tools_py_stay_in_sync(self):
        root = Path(tools.__file__).resolve().parent          # …/praat_ai/tools

        def block(path, name):
            text = Path(path).read_text(encoding="utf-8").splitlines()
            start = next(index for index, line in enumerate(text)
                         if line.startswith(f"def {name}"))
            end = next((index for index in range(start + 1, len(text))
                        if text[index].startswith("def ")), len(text))
            return [line.strip() for line in text[start:end] if line.strip() and not line.startswith("@aipraat-")]

        for name in ("_pitch_extreme_lines", "_build_pitch_statistics", "_build_pitch",
                     "_point_query_lines", "_build_measure"):
            path = root / ("measurements.py" if name in {"_build_pitch_statistics", "_build_pitch",
                                                         "_build_measure"} else "_common.py")
            with self.subTest(function=name):
                live = block(path, name)
                legacy = block(root.parent / "tools.py", name)
                self.assertEqual(live, legacy)

    def test_generated_plugin_artifact_uses_the_same_predicate(self):
        """``ai/plugin/praat_ai/praatAiMeasure.praat`` 是生成物，也要带上同一套口径。

        插件不建临时 Intensity（它的宿主脚本没有 Sound→Intensity 这一步，见
        ai/tools/build_plugin.py），所以它保留原来的中位带谓词；对话侧的 measure
        走共享质量筛查。这里守的是「插件那份生成物没有退回裸 Get maximum/minimum」。
        """

        plugin = (Path(tools.__file__).resolve().parent.parent.parent
                  / "plugin" / "praat_ai" / "praatAiMeasure.praat").read_text(encoding="utf-8")
        self.assertEqual(
            plugin.count("if f <> undefined and f >= floorValue and f <= ceilingValue"), 3, plugin[:200])
        self.assertEqual(plugin.count("if firstCenter < start"), 3)
        self.assertEqual(plugin.count("if lastCenter > end"), 3)
        self.assertNotIn("if f <> undefined and f >= floorValue;", plugin)
        self.assertNotIn("if f <> undefined and f <= ceilingValue and f > highest;", plugin)


class PitchStatisticsBehaviourTests(unittest.TestCase):
    """用 parselmouth 跑真实生成的脚本（合成音 + 可控帧表）。"""

    def setUp(self):
        if parselmouth is None:
            self.skipTest("本机没有 parselmouth，跳过 Praat 行为回归")
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def render(self, arguments, context_text=SOUND_CONTEXT):
        result = self.base / "chat_result.tsv"
        if result.exists():
            result.unlink()
        context = tools.ToolContext(
            tools.parse_object_context(context_text), result, self.base / "chat_state.txt")
        return tools.render("pitch_statistics", arguments, context), result

    def read_result(self, result):
        raw = result.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:      # 库版 Praat 用 UTF-16 写 appendFileLine
            text = raw.decode("utf-16")
        return [line for line in text.splitlines() if line.strip() and not line.startswith("@aipraat-")]

    def run_praat(self, formula, duration, arguments, doctor=None):
        script, result = self.render(arguments)
        if doctor is not None:
            script = doctor(script)
        prefix = 'Create Sound from formula: "probe", 1, 0, %r, 44100, ~ %s\n' % (duration, formula)
        native_praat.run(prefix + script)
        rows = self.read_result(result)
        self.assertEqual(len(rows), 2, rows)       # 一行质量事实 + 一行实测
        self.assertTrue(pitch_quality.is_quality_row(rows[0]), rows)
        return rows[1], rows[0]

    def parse(self, row):
        numbers = dict(re.findall(r"(平均|最低|最高|最高点出现在) ([0-9.]+)", row))
        return {key: float(value) for key, value in numbers.items()}

    def test_steady_tone_keeps_min_mean_max_ordered(self):
        row, quality = self.run_praat("0.3*sin(2*pi*200*x)", 0.5, {})
        values = self.parse(row)
        self.assertEqual(set(values), {"平均", "最低", "最高", "最高点出现在"})
        for key in ("平均", "最低", "最高"):
            self.assertAlmostEqual(values[key], 200.0, delta=1.0)
        self.assertLessEqual(values["最低"], values["平均"])
        self.assertLessEqual(values["平均"], values["最高"])
        self.assertGreaterEqual(values["最高点出现在"], 0.0)
        self.assertLessEqual(values["最高点出现在"], 0.5)
        self.assertNotIn("没有可信", row)
        self.assertTrue(quality.startswith("基频质量：已筛查"), quality)

    def test_peak_time_stays_inside_a_narrow_window(self):
        glide = "0.3*sin(2*pi*(150+166.6667*x)*x)"
        row, _ = self.run_praat(glide, 0.6, {"from": 0.0, "to": 0.392})
        values = self.parse(row)
        self.assertLessEqual(values["最高点出现在"], 0.392 + 1e-9, row)
        self.assertGreaterEqual(values["最低"], 0.0)
        self.assertLessEqual(values["最低"], values["平均"])
        self.assertLessEqual(values["平均"], values["最高"])

    def inject_frames(self, script, frames, *, variable="frameValue", median=323.236,
                      n_frames=114, raw_mean=None):
        """把逐帧取值换成给定帧表；中位数、帧数（以及可选的 rawMean）一起换掉。"""

        table = "\n".join(
            [f"{variable} = undefined"]
            + [line for frame, value in frames.items()
               for line in (f"if pitchFrame = {frame}", f"    {variable} = {value!r}", "endif")])
        pattern = re.compile(r'^.*\b%s = Get value in frame: pitchFrame, "Hertz".*$'
                             % re.escape(variable), re.MULTILINE)
        script, replaced = pattern.subn(table, script)
        self.assertGreaterEqual(replaced, 1, "帧取值行没替换成功")
        script, replaced = re.subn(re.escape(MEDIAN_LINE), f"median = {median}", script)
        self.assertGreaterEqual(replaced, 1, "中位数行没替换成功")
        script, replaced = re.subn("nFrames = Get number of frames", f"nFrames = {n_frames}",
                                   script)
        self.assertGreaterEqual(replaced, 1, "nFrames 行没替换成功")
        if raw_mean is not None:
            script, replaced = re.subn(r'rawMean = Get mean: tmin, tmax, "Hertz"',
                                       f"rawMean = {raw_mean!r}", script)
            self.assertGreaterEqual(replaced, 1, "rawMean 行没替换成功")
        return script

    def force_all_frames_usable(self, script):
        """把能量门/连续段/中位带的外壳去掉：只测「双峰帧表」这一层的口径。"""

        script = re.sub(r"^.*frameEnergy\[pitchFrame\] = 1.*$",
                        "if frameValue <> undefined and frameRun[pitchFrame] = 1"
                        ' and frameValue >= floorValue and frameValue <= ceilingValue',
                        script, count=1, flags=re.MULTILINE)
        self.assertNotIn("frameEnergy[pitchFrame]", script)
        # 帧表注入后 qualityMedian 没有意义，也别让它把 median 覆盖回门后中位数。
        script = re.sub(r"^if qualityMedian <> undefined$", "if 0 = 1", script, count=1,
                        flags=re.MULTILINE)
        return script

    def test_mocked_double_peak_reports_no_trustworthy_statistics(self):
        # rawMean 换成真机记录的 333.788：没有可信帧时它连数字都不许出现。
        def doctor(script):
            script = self.inject_frames(script, DOUBLE_PEAK, raw_mean=333.788)
            return self.force_all_frames_usable(script)

        row, quality = self.run_praat(
            "0.3*sin(2*pi*200*x)", 1.175, {"from": 0.783, "to": 1.175}, doctor=doctor)
        self.assertIn("没有可信的基频统计", row)
        self.assertIn("没有通过质量筛查的基频帧", row)
        self.assertIn("不输出平均/最低/最高", row)
        # 一个 Hz 数值都不许出现：333.788（未过滤的 rawMean）不能当已测值交给报告。
        self.assertIsNone(re.search(r"[0-9.]+\s*Hz", row), row)
        self.assertNotIn("333.788", row)
        self.assertNotIn("原始平均", row)
        self.assertNotIn("）：平均", row)
        self.assertNotIn("，最低 ", row)
        self.assertNotIn("，最高 ", row)
        self.assertTrue(quality.startswith("基频质量：待复核"), quality)

    def test_mocked_octave_outlier_is_dropped_and_order_holds(self):
        frames = {k: value for k, value in zip(range(30, 40),
                                              (198.0, 199.0, 200.0, 201.0, 202.0,
                                               199.5, 200.5, 198.5, 201.5, 200.0))}
        frames[45] = 575.851
        # rawMean 换成一个明显更大的数：报告里只许出现过滤后的平均（200），
        # 原始均值只能作为「剔除了多少」的对照说明。
        def doctor(script):
            script = self.inject_frames(script, frames, median=200.0, n_frames=60,
                                        raw_mean=260.0)
            return self.force_all_frames_usable(script)

        row, _ = self.run_praat(
            "0.3*sin(2*pi*200*x)", 1.0, {"from": 0.2, "to": 0.6}, doctor=doctor)
        values = self.parse(row)
        self.assertAlmostEqual(values["最低"], 198.0, places=3)
        self.assertAlmostEqual(values["最高"], 202.0, places=3)
        self.assertLessEqual(values["最低"], values["平均"])
        self.assertLessEqual(values["平均"], values["最高"])
        self.assertLessEqual(values["最高"], 200.0 * 1.5)
        self.assertLessEqual(values["最高点出现在"], 0.6)
        self.assertGreaterEqual(values["最高点出现在"], 0.2)
        # 有可信帧时仍然说明剔除了多少（原始均值只作这条说明里的对照，不是测量值）。
        self.assertAlmostEqual(values["平均"], 200.0, places=3)
        self.assertIn("原始平均", row)
        self.assertIn("260.0", row)

    def test_window_shorter_than_one_frame_step_reports_no_statistics(self):
        row, quality = self.run_praat("0.3*sin(2*pi*200*x)", 0.6,
                                     {"from": 0.396, "to": 0.397})
        self.assertIn("没有可信的基频统计", row)
        self.assertIsNone(re.search(r"[0-9.]+\s*Hz", row), row)
        self.assertNotIn("）：平均", row)
        self.assertTrue(quality.startswith("基频质量：待复核"), quality)

    def test_noise_lead_and_tail_do_not_become_pitch(self):
        """底噪前后 + 中间 100 Hz：首尾必须来自可靠段，不能落在噪声上。"""

        formula = ("if x < 0.2 or x > 0.9 then 0.004*randomGauss(0,1) else 0.3*sin(2*pi*100*x) endif")
        row, quality = self.run_praat(formula, 1.1, {})
        values = self.parse(row)
        self.assertAlmostEqual(values["平均"], 100.0, delta=2.0)
        self.assertGreaterEqual(values["最低"], 90.0, row)
        self.assertLessEqual(values["最高"], 110.0, row)
        self.assertGreaterEqual(values["最高点出现在"], 0.2)
        self.assertLessEqual(values["最高点出现在"], 0.9)
        self.assertTrue(quality.startswith("基频质量：已筛查"), quality)

    def test_quiet_low_voice_survives_with_a_matching_floor(self):
        """55 Hz 真低音 + floor 35：不能被当成「贴下限的伪迹」删掉。"""

        row, quality = self.run_praat(
            "0.3*sin(2*pi*55*x)", 1.0, {"pitch_floor": 35, "pitch_ceiling": 600})
        values = self.parse(row)
        self.assertAlmostEqual(values["平均"], 55.0, delta=1.0)
        self.assertTrue(quality.startswith("基频质量：已筛查"), quality)

    def test_pitch_object_without_the_waveform_is_unverified(self):
        """已有 Pitch 对象没有原始 Sound：数值照给，但事实行必须写「未核验」。"""

        def doctor(script):
            return "To Pitch: 0, 75, 600\nRename: \"probe\"\nselectObject: 1\nRemove\n" + script

        result = self.base / "pitch_result.tsv"
        context = tools.ToolContext(
            tools.parse_object_context(PITCH_CONTEXT), result, self.base / "pitch_state.txt")
        script = tools.render("pitch_statistics", {"object": 2}, context)
        self.assertNotIn("To Intensity", script)
        native_praat.run(
            'Create Sound from formula: "probe", 1, 0, 0.5, 44100, ~ 0.3*sin(2*pi*200*x)\n'
            "To Pitch: 0, 75, 600\nRename: \"probe\"\nselectObject: 1\nRemove\n" + script)
        rows = self.read_result(result)
        self.assertEqual(len(rows), 2, rows)
        self.assertTrue(rows[0].startswith("基频质量：未核验"), rows[0])
        # 未核验时不许假装做过能量门。
        self.assertIn("能量门与连续段筛查都没做", rows[0])
        self.assertIn("平均", rows[1])


class MeasurePitchParameterTests(unittest.TestCase):
    """``measure`` 的八个基频参数与 ``pitch_statistics`` 共用同一套筛查。"""

    PITCH = "mean_pitch,minimum_pitch,maximum_pitch,median_pitch,sd_pitch,pitch_slope,pitch_start,pitch_end"

    def setUp(self):
        if parselmouth is None:
            self.skipTest("本机没有 parselmouth，跳过 Praat 行为回归")
        self.directory = tempfile.TemporaryDirectory()
        self.base = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def inject(self, script):
        table = "\n".join(
            ["frameValue = undefined"]
            + [line for frame, value in DOUBLE_PEAK.items()
               for line in (f"if pitchFrame = {frame}", f"    frameValue = {value!r}", "endif")])
        script, replaced = re.subn(
            r'^.*\bframeValue = Get value in frame: pitchFrame, "Hertz".*$', table, script,
            flags=re.MULTILINE)
        self.assertGreaterEqual(replaced, 1, "帧取值行没替换成功")
        script, replaced = re.subn(re.escape(MEDIAN_LINE), "median = 323.236", script)
        self.assertGreaterEqual(replaced, 1, "中位数行没替换成功")
        script, replaced = re.subn("nFrames = Get number of frames", "nFrames = 114", script)
        self.assertGreaterEqual(replaced, 1, "nFrames 行没替换成功")
        script = re.sub(r"^.*frameEnergy\[pitchFrame\] = 1.*$",
                        "if frameValue <> undefined and frameRun[pitchFrame] = 1"
                        ' and frameValue >= floorValue and frameValue <= ceilingValue',
                        script, count=1, flags=re.MULTILINE)
        self.assertNotIn("frameEnergy[pitchFrame]", script)
        script = re.sub(r"^if qualityMedian <> undefined$", "if 0 = 1", script,
                        flags=re.MULTILINE)
        return script

    def run_measure(self, formula, duration, arguments, doctor=None):
        result = self.base / "measure_result.tsv"
        if result.exists():
            result.unlink()
        context = tools.ToolContext(
            tools.parse_object_context(SOUND_CONTEXT), result, self.base / "measure_state.txt")
        script = tools.render("measure", arguments, context)
        if doctor is not None:
            script = doctor(script)
        prefix = 'Create Sound from formula: "probe", 1, 0, %r, 44100, ~ %s\n' % (duration, formula)
        native_praat.run(prefix + script)
        raw = result.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("utf-16")
        return [line for line in text.splitlines() if line.strip() and not line.startswith("@aipraat-")]

    def values(self, rows):
        found = re.findall(
            r"(平均基频|最低基频|最高基频|基频中位数|基频标准差|基频平均绝对斜率|起点基频|终点基频)"
            r"（[^）]*）= ([0-9.]+)", "\n".join(rows))
        return {name: float(value) for name, value in found}

    def test_three_pitch_parameters_are_ordered_on_a_steady_tone(self):
        rows = self.run_measure("0.3*sin(2*pi*200*x)", 0.5,
                                {"parameter": "mean_pitch,minimum_pitch,maximum_pitch"})
        self.assertEqual(len(rows), 4, rows)          # 一行质量事实 + 三个参数
        values = self.values(rows)
        self.assertEqual(set(values), {"平均基频", "最低基频", "最高基频"})
        for name, value in values.items():
            with self.subTest(parameter=name):
                self.assertAlmostEqual(value, 200.0, delta=1.0)
        self.assertLessEqual(values["最低基频"], values["平均基频"])
        self.assertLessEqual(values["平均基频"], values["最高基频"])
        quality = [row for row in rows if pitch_quality.is_quality_row(row)]
        self.assertEqual(len(quality), 1, rows)
        self.assertTrue(quality[0].startswith("基频质量：已筛查"), quality[0])

    def test_double_peak_gives_no_value_for_any_pitch_parameter(self):
        rows = self.run_measure("0.3*sin(2*pi*200*x)", 1.175,
                                {"parameter": self.PITCH, "from": 0.783, "to": 1.175},
                                doctor=self.inject)
        measured = [row for row in rows if not pitch_quality.is_quality_row(row)]
        self.assertEqual(len(measured), len(self.PITCH.split(",")), rows)
        for row in measured:
            with self.subTest(row=row):
                self.assertIn("无法计算", row)
                self.assertIsNone(re.search(r"[0-9.]+\s*Hz", row), row)
        # 八个参数都走同一批帧：都不给值，才不可能拼出「最低 555 > 最高 91」。
        joined = "\n".join(measured)
        self.assertNotIn("最低基频（0.783–1.175 秒）= ", joined)
        self.assertNotIn("最高基频（0.783–1.175 秒）= ", joined)


if __name__ == "__main__":
    unittest.main()
