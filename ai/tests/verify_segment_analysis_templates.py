"""Run generated VOT templates in Praat and verify the shared C++ TSV contract."""

# 注意（2026-10-17 第 3 轮）：这条脚本校验的是**已暂停**的「统一 VOT」原生路线
# （`739cdd86` 暂停开发、只保留共享修复）。它断言的是 `test/fon/segmentAcousticVOT.praat`
# 模板、原生动作 `Write VOT analysis to file...` 以及核心里的 analyseVOT/TSV 契约；
# 其中原生动作在本仓库**只声明未实现**（没有 `praat_addAction` 注册），所以这条脚本
# 现在必然失败——它是「恢复原生路线时要做成什么样」的对照，**不是**当前验收门槛。
# 保留实现的验收入口是 `ai/tests/test_segment_analysis_tools.py` 与
# `ai/tests/test_chat_tools.py`（0/0 = 未指定、单向参数报错、手工相减 + appendFileLine）。

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from praat_ai import tools  # noqa: E402


PROJECT = Path(__file__).resolve().parents[2]
PRAAT = Path(os.environ.get("PRAAT_EXE", str(PROJECT / "Praat.exe")))
CORE_SCRIPT = PROJECT / "test/fon/segmentAcousticCore.praat"
VOT_SCRIPT = PROJECT / "test/fon/segmentAcousticVOT.praat"
CORE_ANALYSIS_HEADER = PROJECT / "fon/SegmentAcousticAnalysis.h"
CORE_ANALYSIS_SOURCE = PROJECT / "fon/SegmentAcousticAnalysis.cpp"
CORE_TEST_SOURCE = PROJECT / "fon/Praat_tests.cpp"
CORE_TEST_ENUMS = PROJECT / "fon/Praat_tests_enums.h"
FONED_MAKEFILE = PROJECT / "foned/Makefile"
FONED_MESON = PROJECT / "foned/meson.build"
SOUND_ANALYSIS_AREA_SOURCE = PROJECT / "foned/SoundAnalysisArea.cpp"
SOUND_ACTION_SOURCE = PROJECT / "fon/praat_Sound.cpp"
SOUND_EDITOR_SOURCE = PROJECT / "foned/SoundEditor.cpp"
SOUND_EDITOR_HEADER = PROJECT / "foned/SoundEditor.h"
FUNCTION_EDITOR_SOURCE = PROJECT / "foned/FunctionEditor.cpp"
FUNCTION_EDITOR_HEADER = PROJECT / "foned/FunctionEditor.h"
PRAAT_TEST_SOURCE = PROJECT / "fon/Praat_tests.cpp"
PRAAT_TEST_ENUMS = PROJECT / "fon/Praat_tests_enums.h"
EDITOR_SOURCE = PROJECT / "foned/SegmentAcousticEditor.cpp"
EDITOR_HEADER = PROJECT / "foned/SegmentAcousticEditor.h"
VOT_SOUND = (
    'Create Sound from formula: "vot-fixture", 1, 0, 0.6, 44100, '
    '~ if x < 0.30 then 0 else if x < 0.33 then 0.3 * randomGauss (0, 1) '
    'else 0.5 * sin (2 * pi * 220 * x) fi fi'
)


def run_praat(script: Path, label: str) -> str:
    if not PRAAT.is_file():
        raise RuntimeError(f"Praat executable is missing: {PRAAT}")
    process = subprocess.run(
        [str(PRAAT), "--FULL-TRUST", "--run", str(script)],
        capture_output=True,
        timeout=60,
    )
    output = (process.stdout + process.stderr).decode("utf-16-le", "replace").replace("\x00", "")
    if process.returncode:
        raise RuntimeError(f"{label} failed with {process.returncode}:\n{output}")
    return output


def read_rows(path: Path) -> dict[str, list[str]]:
    text = path.read_text(encoding="utf-8-sig")
    lines = text.splitlines()
    expected_header = (
        "schema_version\tpraat_version\tsource\tsource_object_id\tsource_file\tsource_start_s"
        "\tsource_end_s\tsource_duration_s\tsource_sample_rate_hz\tsource_channels\tsource_kind"
        "\tanalysis_kind\tparameters\tlanguage\tipa\tspeaker_id\tneighboring_vowel"
        "\tburst_time_s\tvoicing_time_s\tmetric_id\tvalue\tunit\tstatus\treason"
    )
    if not lines or lines[0] != expected_header:
        raise AssertionError(f"{path.name}: unexpected or missing schema header")
    rows: dict[str, list[str]] = {}
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) != 24:
            raise AssertionError(f"{path.name}: malformed TSV row {line!r}")
        rows[fields[19]] = fields
    return rows


def assert_metric(rows: dict[str, list[str]], metric_id: str, *, unit: str) -> list[str]:
    if metric_id not in rows:
        raise AssertionError(f"missing metric {metric_id!r}")
    row = rows[metric_id]
    if row[0] != "1" or row[11] != "VOT" or row[21] != unit:
        raise AssertionError(f"wrong schema, analysis kind, or unit: {row!r}")
    return row


def verify_generated_template(root: Path, name: str, arguments: dict[str, object]) -> dict[str, list[str]]:
    context = tools.ToolContext(
        tools.parse_object_context("id\tclass\tname\tselected\n1\tSound\tvot-fixture\t1\n"),
        root / f"{name}-chat-result.tsv",
        root / f"{name}-chat-state.txt",
    )
    template = tools.render("vot", {"object": 1, **arguments}, context)
    if "Write VOT analysis to file" not in template or "readFile$" not in template:
        raise AssertionError(f"{name}: the template did not call and read the C++ result action")
    if "Filter (pass Hann band)" in template or "To Pitch (ac)" in template:
        raise AssertionError(f"{name}: Python still duplicates the acoustic estimator")
    script = root / f"{name}.praat"
    script.write_text(VOT_SOUND + "\n" + template, encoding="utf-8")
    run_praat(script, name)
    return read_rows(context.result_path.with_suffix(".vot.tsv"))


def verify_comparison_editor_removed() -> None:
    if EDITOR_SOURCE.exists() or EDITOR_HEADER.exists():
        raise AssertionError("comparison editor files are still present")

    build_sources = FONED_MAKEFILE.read_text(encoding="utf-8") + FONED_MESON.read_text(encoding="utf-8")
    if "SegmentAcousticEditor.cpp" in build_sources:
        raise AssertionError("foned build files still register SegmentAcousticEditor")

    sound_area_source = SOUND_ANALYSIS_AREA_SOURCE.read_text(encoding="utf-8")
    removed_editor_items = ("SegmentAcousticEditor", "menu_cb_segmentVOT", "辅音分析", "目标/参照比较")
    remaining = [item for item in removed_editor_items if item in sound_area_source]
    if remaining:
        raise AssertionError(f"Sound editor still references comparison workflow: {remaining!r}")


def verify_vot_only_api() -> None:
    core_code = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CORE_ANALYSIS_HEADER, CORE_ANALYSIS_SOURCE)
    )
    removed_api = (
        "AnalysisKind",
        "compatibilityKeys",
        "ComparisonResult",
        "MetricComparison",
        "FrequencyOverlay",
        "TimeSeries",
        "FrequencySeries",
        "compareCompatibleMetrics",
        "normalizeTimeSeriesForOverlay",
        "interpolateCommonFrequencyGrid",
        "confirmVOTBoundaries",
        "TargetReferenceSegment",
        "SegmentAnalysisSelection",
    )
    remaining = [item for item in removed_api if item in core_code]
    if remaining:
        raise AssertionError(f"comparison/overlay API remains in the VOT core: {remaining!r}")

    required_vot_api = ("analyseVOT", "AnalysisResult_toTsv", "writeSegmentAnalysisTsvAtomically")
    missing = [item for item in required_vot_api if item not in core_code]
    if missing:
        raise AssertionError(f"VOT result APIs are missing from the core: {missing!r}")

    test_code = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (CORE_TEST_SOURCE, CORE_TEST_ENUMS, VOT_SCRIPT)
    )
    if "CHECK_SEGMENT_COMPARISON" in test_code or "CheckSegmentComparison" in test_code:
        raise AssertionError("comparison-only C++ tests remain registered or invoked")


def verify_vot_action_contract() -> None:
    source = SOUND_ACTION_SOURCE.read_text(encoding="utf-8")
    compact = re.sub(r"\s+", " ", source)
    for object_class in ("Sound", "LongSound"):
        hidden = (
            f'praat_addAction1 (class{object_class}, 0, U"Write VOT analysis to file...", nullptr, '
            f'GuiMenu_DEPTH_1 | GuiMenu_HIDDEN, WRITE_ONE__{object_class}_VOT_TSV);'
        )
        if compact.count(hidden) != 1:
            raise AssertionError(f"{object_class} must register exactly one hidden AI TSV action")

    if 'U"VOT..."' in compact:
        raise AssertionError("the VOT form must be exposed from the SoundEditor toolbar, not the Objects menu")

    editor_source = SOUND_EDITOR_SOURCE.read_text(encoding="utf-8")
    editor_compact = re.sub(r"\s+", " ", editor_source)
    function_editor_header = FUNCTION_EDITOR_HEADER.read_text(encoding="utf-8")
    function_editor_source = FUNCTION_EDITOR_SOURCE.read_text(encoding="utf-8")
    sound_editor_header = SOUND_EDITOR_HEADER.read_text(encoding="utf-8")
    sound_analysis_source = SOUND_ANALYSIS_AREA_SOURCE.read_text(encoding="utf-8")
    function_editor_header_compact = re.sub(r"\s+", " ", function_editor_header)
    if "VOT" in editor_compact or "vot" in editor_compact.lower():
        raise AssertionError("SoundEditor must not expose or implement an editor-specific VOT command")
    if "virtual void v_createMenusAfterFunctionAreas () { }" in function_editor_header_compact:
        raise AssertionError("FunctionEditor must not retain the VOT-only post-area menu hook")
    menu_creation = function_editor_source.split("void structFunctionEditor :: v_createMenus ()", 1)[1]
    area_menus = menu_creation.find("area -> v_createMenus ();")
    ai_menus = menu_creation.find("createAiMenus (this);")
    ai_menu_builder = function_editor_source.split("void createAiMenus (FunctionEditor me)", 1)[1]
    alignment_menu = ai_menu_builder.find('window, U"Alignment"')
    if min(area_menus, ai_menus, alignment_menu) < 0 or not area_menus < ai_menus:
        raise AssertionError("AI menus must remain after the SoundEditor function-area menus")
    if 'Editor_addMenu (our functionEditor(), U"Pulses", 0)' not in re.sub(r"\s+", " ", sound_analysis_source):
        raise AssertionError("SoundAnalysisArea must retain its independent Pulses menu")
    if "v_createMenusAfterFunctionAreas" in function_editor_source + function_editor_header + sound_editor_header:
        raise AssertionError("VOT-only post-area menu hook remains in FunctionEditor or SoundEditor")
    if "vot" not in tools.TOOL_MAP:
        raise AssertionError("AI VOT tool must remain registered")

    if "AnalysisResult_toInfoSummary" not in CORE_ANALYSIS_HEADER.read_text(encoding="utf-8"):
        raise AssertionError("VOT Info summary formatter is not part of the core contract")
    if "CHECK_SEGMENT_VOT_INFO_SUMMARY" not in PRAAT_TEST_ENUMS.read_text(encoding="utf-8"):
        raise AssertionError("VOT Info summary regression is not registered")
    if "CHECK_SEGMENT_VOT_INFO_SUMMARY" not in PRAAT_TEST_SOURCE.read_text(encoding="utf-8"):
        raise AssertionError("VOT Info summary regression is missing")


def main() -> None:
    verify_vot_action_contract()
    core_output_dir = Path.home()
    core_prefix = f"praat-segment-acoustic-core-{uuid.uuid4().hex}"
    core_files = [core_output_dir / f"{core_prefix}-{name}.tsv" for name in ("zero", "negative", "candidates", "longsound")]
    legacy_temp_path = Path(f"{core_files[0]}.tmp")
    core_wave = core_output_dir / f"{core_prefix}.wav"
    with tempfile.TemporaryDirectory(prefix="praat-segment-analysis-") as temporary:
        root = Path(temporary)
        core_script = root / "segmentAcousticCore.praat"
        core_script.write_text(
            CORE_SCRIPT.read_text(encoding="utf-8").replace(
                "praat-segment-acoustic-core", core_prefix
            ),
            encoding="utf-8",
        )
        try:
            core_files[0].write_text("previous successful result", encoding="utf-8")
            legacy_temp_path.write_text("unrelated pre-existing temp file", encoding="utf-8")
            run_praat(core_script, "C++ Sound/LongSound object actions")
            run_praat(VOT_SCRIPT, "C++ VOT acoustic regression")
            verify_comparison_editor_removed()
            verify_vot_only_api()

            core_zero = read_rows(core_files[0])
            zero = assert_metric(core_zero, "vot_ms", unit="ms")
            if float(zero[20]) != 0.0 or zero[22] != "measured":
                raise AssertionError(f"explicit zero was not preserved: {zero!r}")
            if not zero[2] or not zero[3] or zero[5:12] != ["0", "1", "1", "44100", "1", "Sound", "VOT"]:
                raise AssertionError(f"single-source export lost source identity or analysis range: {zero!r}")
            if zero[17:19] != ["0", "0"]:
                raise AssertionError(f"single-source export lost the explicit manual boundaries: {zero!r}")
            if legacy_temp_path.read_text(encoding="utf-8") != "unrelated pre-existing temp file":
                raise AssertionError("analysis export overwrote an unrelated pre-existing .tmp file")

            core_negative = read_rows(core_files[1])
            negative = assert_metric(core_negative, "vot_ms", unit="ms")
            if abs(float(negative[20]) + 20.0) > 1e-8 or negative[22] != "measured":
                raise AssertionError(f"negative VOT was not preserved: {negative!r}")

            long_sound = read_rows(core_files[3])
            long_negative = assert_metric(long_sound, "vot_ms", unit="ms")
            if abs(float(long_negative[20]) + 20.0) > 1e-8:
                raise AssertionError(f"LongSound action changed absolute VOT: {long_negative!r}")

            core_candidate = read_rows(core_files[2])
            missing_candidate = assert_metric(core_candidate, "vot_candidate_ms", unit="ms")
            if missing_candidate[20] or missing_candidate[22] != "unavailable" or not missing_candidate[23]:
                raise AssertionError(f"missing candidate lacks an unavailable reason: {missing_candidate!r}")

            generated_zero = verify_generated_template(root, "template-zero", {"burst": 0.0, "voicing": 0.0})
            zero = assert_metric(generated_zero, "vot_ms", unit="ms")
            if float(zero[20]) != 0.0 or zero[22] != "measured":
                raise AssertionError(f"AI template lost explicit zero: {zero!r}")

            generated_negative = verify_generated_template(
                root, "template-negative", {"burst": 0.30, "voicing": 0.28}
            )
            negative = assert_metric(generated_negative, "vot_ms", unit="ms")
            if abs(float(negative[20]) + 20.0) > 1e-8 or negative[22] != "measured":
                raise AssertionError(f"AI template lost negative VOT: {negative!r}")

            generated_auto = verify_generated_template(root, "template-candidates", {})
            burst = assert_metric(generated_auto, "burst_time_candidate", unit="s")
            vot = assert_metric(generated_auto, "vot_candidate_ms", unit="ms")
            if burst[22] not in {"warning", "unavailable"} or vot[22] not in {"warning", "unavailable"}:
                raise AssertionError(f"candidate values are not marked for review: {burst!r}, {vot!r}")
            if (burst[22] == "unavailable" and not burst[23]) or (vot[22] == "unavailable" and not vot[23]):
                raise AssertionError(f"unavailable candidate has no reason: {burst!r}, {vot!r}")
        finally:
            for path in core_files:
                path.unlink(missing_ok=True)
            legacy_temp_path.unlink(missing_ok=True)
            core_wave.unlink(missing_ok=True)

    print("SEGMENT_ANALYSIS_TEMPLATE_PASS: VOT contract and AI template bridge")


if __name__ == "__main__":
    main()
