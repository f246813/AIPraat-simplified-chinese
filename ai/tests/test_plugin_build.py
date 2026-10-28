"""Praat 原生插件的生成物必须和参数表一致，菜单也必须指向真文件。"""

import re
import sys
import tempfile
import unittest
from pathlib import Path

from praat_ai import measures

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import build_plugin   # noqa: E402


PLUGIN = build_plugin.PLUGIN_SOURCE


def unescape_praat_literal(text: str) -> str:
    """把 ``praat_literal`` 的输出还原（测试用，验证转义是可逆的）。"""

    assert text.startswith('"') and text.endswith('"'), text
    return text[1:-1].replace('""', '"')


class GeneratedScriptTests(unittest.TestCase):
    def test_checked_in_script_matches_the_table(self) -> None:
        self.assertEqual(build_plugin.check_generated(), "")

    def test_every_parameter_has_a_block(self) -> None:
        script = build_plugin.render_measure_script()
        table = measures.load_table()
        for entry in table.queries():
            with self.subTest(parameter=entry.parameter):
                self.assertIn(f'parameter$ = "{entry.parameter}"', script)
                self.assertIn(f'"{entry.label}"', script)
        # 表里交给专用工具算的那些（hl_ratio 等）不在插件里：插件只做一行查询能
        # 表达的参数，多步逻辑留在对话前端（见 measures.tsv 的 @@ dedicated）。
        dedicated = [entry for entry in table.entries if entry.kind == "dedicated"]
        self.assertTrue(dedicated)
        for entry in dedicated:
            self.assertNotIn(f'parameter$ = "{entry.parameter}"', script)

    def test_script_uses_the_plugin_range_variables(self) -> None:
        script = build_plugin.render_measure_script()
        self.assertIn('real: "Start (s)", "0"', script)
        self.assertNotIn("tmin", script)
        self.assertNotIn("tmax", script)

    def test_pitch_extremes_are_guarded_against_octave_errors(self) -> None:
        """插件里的最高/最低基频也要防倍频误判（和会话模板同一处修正）。

        2026-09-30：``Get maximum`` 取区间内最高的那一帧，自相关在浊音起始常把
        真实基频听成 2–4 倍。会话里的 ``pitch_statistics`` 和 ``measure`` 都改成
        逐帧限幅了，插件这份生成物必须跟着走，否则两条路给出不同的数。
        """

        script = build_plugin.render_measure_script()
        self.assertNotIn('value = Get maximum: start, end, "Hertz"', script)
        self.assertNotIn('value = Get minimum: start, end, "Hertz"', script)
        self.assertIn("ceilingValue = median * 1.5", script)
        self.assertIn("floorValue = median * 0.5", script)
        # 插件没有 tmin/tmax（用 start/end），逐帧取值必须走 start/end。
        self.assertIn("Get frame number from time: start", script)

    def test_script_never_opens_the_info_window(self) -> None:
        """插件也不该往 Praat Info 里写（结果进 Table；guide §8.5 的约定）。"""

        script = build_plugin.render_measure_script()
        for line in script.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in (
                "writeInfoLine",
                "appendInfoLine",
                "printline",
                "clearinfo",
            ):
                self.assertNotIn(forbidden, stripped, stripped)

    def test_script_has_no_single_quote_interpolation(self) -> None:
        script = build_plugin.render_measure_script()
        for line in script.splitlines():
            if line.strip().startswith("#"):
                continue
            self.assertNotIn("'", line, line)


class MenuRegistrationTests(unittest.TestCase):
    def test_menu_commands_point_at_existing_scripts(self) -> None:
        setup = (PLUGIN / "setup.praat").read_text(encoding="utf-8")
        entries = re.findall(r'^\s*Add (?:menu|action) command:(.*)$', setup, re.M)
        self.assertGreaterEqual(len(entries), 4)
        for entry in entries:
            fields = [
                field.strip().strip('"')
                for field in re.split(r",(?=(?:[^\"]*\"[^\"]*\")*[^\"]*$)", entry)
            ]
            script = fields[-1]
            with self.subTest(script=script):
                # praatAiChat.praat 是安装时按本机路径生成的，源目录里只有模板。
                exists = (PLUGIN / script).is_file() or (PLUGIN / f"{script}.in").is_file()
                self.assertTrue(exists, f"菜单指向的脚本不存在：{script}")

    def test_editor_wrapper_passes_as_many_arguments_as_the_form_has_fields(self) -> None:
        body = build_plugin.render_measure_script()
        form = body.split("endform", 1)[0]
        fields = len(re.findall(r"^\s*(?:word|real|boolean):", form, re.M))
        wrapper = (PLUGIN / "praatAiMeasureEditor.praat").read_text(encoding="utf-8")
        call = re.search(r'runScript:\s*"praatAiMeasure\.praat"(.*)', wrapper)
        self.assertIsNotNone(call)
        arguments = [piece for piece in call.group(1).split(",") if piece.strip()]
        self.assertEqual(len(arguments), fields)


class ChatLauncherTests(unittest.TestCase):
    def test_praat_literal_is_reversible(self) -> None:
        for text in (r'D:\a b\c.py', 'say "hi"', "plain"):
            with self.subTest(text=text):
                # 反斜杠统一换成斜杠（和 tools.quote 一致：Praat 里两种都能用，
                # 但斜杠不会被当成转义）。
                self.assertEqual(
                    unescape_praat_literal(build_plugin.praat_literal(text)),
                    text.replace("\\", "/"),
                )

    def test_install_writes_the_expected_folder(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            target = build_plugin.install(
                project_directory=Path("D:/praat/praat-simplified-chinese"),
                python_executable="D:/py/venv/Scripts/python.exe",
                destination=Path(raw) / "plugin_praat_ai",
                quiet=True,
            )
            names = sorted(path.name for path in target.iterdir())
            self.assertIn("setup.praat", names)
            self.assertIn("praatAiMeasure.praat", names)
            self.assertIn("praatAiMeasureEditor.praat", names)
            self.assertIn("praatAiChat.praat", names)
            # 模板本身不装（装了会让 Praat 试着执行它）。
            self.assertNotIn("praatAiChat.praat.in", names)
            chat = (target / "praatAiChat.praat").read_text(encoding="utf-8")
            self.assertIn("runSystem:", chat)
            self.assertIn("praat-simplified-chinese/ai/start_ai_chat.py", chat)
            self.assertNotIn("@CHAT_COMMAND@", chat)
            self.assertNotIn("@PROJECT_DIRECTORY@", chat)
            # 装出来的测量脚本就是生成物本身。
            self.assertEqual(
                (target / "praatAiMeasure.praat").read_text(encoding="utf-8"),
                build_plugin.render_measure_script(),
            )

    def test_missing_generated_script_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            problem = build_plugin.check_generated(Path(raw))
            self.assertIn("不存在", problem)


if __name__ == "__main__":
    unittest.main()
