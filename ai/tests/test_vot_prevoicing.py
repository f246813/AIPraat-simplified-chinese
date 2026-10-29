"""Exercise the actual chat VOT script in Praat, including signed landmarks."""
from pathlib import Path
import os
import re
import subprocess
import tempfile
import unittest

from praat_ai import tools


PRAAT = Path(os.environ.get("PRAAT_AI_PRAAT_EXECUTABLE") or
             Path(__file__).resolve().parents[2] / "Praat.exe")


@unittest.skipUnless(PRAAT.is_file(), "需要真实 Praat；可设置 PRAAT_AI_PRAAT_EXECUTABLE")
class VotAcousticTests(unittest.TestCase):
    praat = PRAAT

    def measure(self, formula, **arguments):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            context = tools.ToolContext((tools.ObjectRow(1, "Sound", "fixture", True),),
                                        folder / "result.tsv", folder / "state.txt")
            source = folder / "vot.praat"
            source.write_text(
                'seed = random_initializeWithSeedUnsafelyButPredictably (5489)\n' +
                'Create Sound from formula: "fixture", 1, 0, 0.7, 44100, ~ ' + formula + '\n' +
                tools.render("vot", {"from": .1, "to": .55, **arguments}, context) +
                '\nselect all\nappendFileLine: ' + tools.quote(folder / "count.txt") + ', numberOfSelected ()\n',
                encoding="utf-8")
            proc = subprocess.run([str(self.praat), "--no-pref-files", "--no-plugins", "--FULL-TRUST",
                                   "--run", str(source)], capture_output=True, timeout=30)
            self.assertEqual(proc.returncode, 0, proc.stderr.decode("utf-8", "replace"))
            self.assertEqual(context.state_path.read_text(encoding="utf-8-sig").strip(), "done")
            self.assertEqual((folder / "count.txt").read_text(encoding="utf-8-sig").strip(), "1")
            return context.result_path.read_text(encoding="utf-8-sig")

    @staticmethod
    def signal(pre_start=None, pre_end=.3):
        pre = "0" if pre_start is None else (
            f"if x >= {pre_start} and x < {pre_end} then 0.025*sin(2*pi*220*x) else 0 fi")
        # Deterministic broadband release, then a 220 Hz vowel with a 30 ms lag.
        return (f"({pre}) + (if x >= 0.3 and x < 0.33 then "
                "randomGauss (0, 0.25) "
                "else 0 fi) + (if x >= 0.33 then 0.4*sin(2*pi*220*x) else 0 fi)")

    def vot(self, result):
        match = re.search(r"VOT 估计值 = (-?[\d.]+) 秒", result)
        self.assertIsNotNone(match, result)
        return float(match[1])

    def test_positive_lag_and_no_invented_clear_vowel(self):
        result = self.measure(self.signal())
        self.assertGreater(self.vot(result), .005)
        self.assertLess(self.vot(result), .05)
        self.assertIn("清晰元音起始：未自动判定", result)
        self.assertIn("爆破后稳定起声候选间隔", result)

    def test_weak_prevoicing_and_positive_post_release_interval_coexist(self):
        result = self.measure(self.signal(.2), vowel=.33)
        self.assertLess(self.vot(result), -.05)
        self.assertGreater(self.vot(result), -.14)
        self.assertIn("预浊起始候选", result)
        self.assertIn("Burst-to-clear-vowel interval", result)
        self.assertRegex(result, r"Burst-to-clear-vowel interval = [23]\d\.\d 毫秒")

    def test_continuous_prevoicing_is_not_discarded_at_release(self):
        result = self.measure(self.signal(.2, .55))
        self.assertLess(self.vot(result), -.05)

    def test_onset_at_left_edge_is_unavailable(self):
        result = self.measure(self.signal(.1))
        self.assertNotIn("VOT 估计值 =", result)
        self.assertIn("截断", result)
        self.assertIn("爆破后稳定起声候选间隔", result)

    def test_interrupted_prevoicing_keeps_a_negative_candidate_pending_association(self):
        result = self.measure(self.signal(.14, .23))
        self.assertLess(self.vot(result), 0)
        self.assertIn("归属待确认", result)

    def test_prevoicing_longer_than_150_ms_is_not_clipped(self):
        result = self.measure(self.signal(.12), **{"from": .02})
        self.assertLess(self.vot(result), -.15)

    def test_silence_does_not_produce_vot(self):
        result = self.measure("0")
        self.assertNotIn("VOT 估计值 =", result)
        self.assertIn("找不到爆破瞬态", result)

    def test_manual_negative_zero_and_clear_vowel_interval(self):
        for onset, expected in ((.2, "-0.1000"), (.3, "0")):
            with self.subTest(onset=onset):
                result = self.measure("0", burst=.3, voicing=onset, vowel=.313)
                self.assertIn(f"VOT = {expected} 秒", result)
                self.assertIn("Burst-to-clear-vowel interval = 13.0 毫秒", result)

    def test_invalid_clear_vowel_is_not_reported_as_an_interval(self):
        result = self.measure("0", burst=.3, voicing=.2, vowel=.1)
        self.assertIn("VOT = -0.1000 秒", result)
        self.assertNotIn("Burst-to-clear-vowel interval =", result)
        self.assertIn("清晰元音地标无效", result)

    def test_short_positive_lag_is_not_negative_prevoicing(self):
        result = self.measure(self.signal().replace("0.33", "0.313"))
        self.assertGreaterEqual(self.vot(result), 0)
        self.assertNotIn("预浊起始候选：", result)


class VotCompatibilityTests(unittest.TestCase):
    def test_compatibility_templates_and_schema_match_active_package(self):
        import ast
        root = Path(tools.__file__).resolve().parent

        def functions(path):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            return {n.name: ast.dump(n, include_attributes=False) for n in tree.body
                    if isinstance(n, ast.FunctionDef)}, tree

        active, _ = functions(root / "measurements.py")
        legacy, tree = functions(root.parent / "tools.py")
        for name in ("_build_vot", "_build_vot_explicit", "_build_vot_auto",
                     "_vot_landmark_lines", "_vot_prevoicing_lines"):
            with self.subTest(name=name):
                self.assertEqual(active[name], legacy[name])
        assignment = next(n for n in tree.body if isinstance(n, ast.AnnAssign)
                          and isinstance(n.target, ast.Name) and n.target.id == "TOOL_PARAMETERS")
        value = next(v for k, v in zip(assignment.value.keys, assignment.value.values)
                     if isinstance(k, ast.Constant) and k.value == "vot")
        from praat_ai.tools import schema
        active_source = ast.parse((root / "schema.py").read_text(encoding="utf-8"))
        active_assignment = next(n for n in active_source.body if isinstance(n, ast.AnnAssign)
                                 and isinstance(n.target, ast.Name) and n.target.id == "TOOL_PARAMETERS")
        active_value = next(v for k, v in zip(active_assignment.value.keys, active_assignment.value.values)
                            if isinstance(k, ast.Constant) and k.value == "vot")
        self.assertEqual(ast.dump(value), ast.dump(active_value))
        self.assertIn("vowel", schema.TOOL_PARAMETERS["vot"]["properties"])


if __name__ == "__main__":
    unittest.main()
