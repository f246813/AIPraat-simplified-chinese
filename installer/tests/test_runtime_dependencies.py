"""Wheel compatibility, offline dependency resolution, and runtime failures."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
import copy

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("runtime_dependencies", ROOT / "ai/runtime_dependencies.py")
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)


def wheel(folder, name="numpy", version="2.0.0", tag="py3-none-any", dependencies=(), python=">=3.10"):
    filename = Path(folder) / f"{name}-{version}-{tag}.whl"
    dist = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(filename, "w") as archive:
        archive.writestr(dist+"/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\nRequires-Python: {python}\n" + "".join(f"Requires-Dist: {item}\n" for item in dependencies))
        archive.writestr(dist+"/WHEEL", f"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: {tag}\n")
        archive.writestr(name+"/__init__.py", "installed=True\n")
        archive.writestr(dist+"/RECORD", "")
    return filename


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="AIPraat wheel 中文 ")
        self.folder = Path(self.temp.name)
        self.row = next(row for row in runtime.requirements(ROOT/"ai/requirements.txt") if row["id"] == "numpy")

    def tearDown(self):
        self.temp.cleanup()

    def test_requirements_include_all_required_and_openai_extra(self):
        rows = runtime.manifest(ROOT/"ai/requirements.txt")
        self.assertEqual(len(rows), 13)
        self.assertEqual({row["id"] for row in rows}, set(runtime.LABELS) | {"tk", "webview2"})
        self.assertEqual(next(row["requirement"] for row in rows if row["id"] == "pydantic-ai-slim"), "pydantic-ai-slim[openai]==2.52.0")

    def ready_report(self):
        return dict(python=sys.executable, device="cpu", supported=True, completed=True,
                    items=[dict(row, ok=True) for row in runtime.manifest(ROOT/"ai/requirements.txt")])

    def test_repair_plan_targets_only_failed_items_preserving_extras_and_constraints(self):
        report=self.ready_report()
        for row in report["items"]:
            row["ok"]=row["id"] not in ("torch", "pydantic-ai-slim", "tk")
        with patch.object(runtime,"detect") as detect:
            plan=runtime.repair_plan(ROOT/"ai/requirements.txt", "cpu", report)
        detect.assert_not_called()
        self.assertEqual(plan["requirements"], ["pydantic-ai-slim[openai]==2.52.0"])
        self.assertEqual(plan["torch"], "torch>=2.3")
        self.assertTrue(plan["tk"])
        self.assertFalse(plan["webview2"])
        self.assertIn("pydantic-graph==2.52.0",plan["constraints"])

    def test_complete_environment_generates_empty_repair_list(self):
        with patch.object(runtime,"detect") as detect:
            plan=runtime.repair_plan(ROOT/"ai/requirements.txt", "cpu", self.ready_report())
        detect.assert_not_called()
        self.assertEqual(plan["requirements"],[])
        self.assertEqual(plan["torch"],"")
        self.assertFalse(plan["tk"] or plan["webview2"])

    def test_other_python_device_or_incomplete_report_is_redetected(self):
        original=self.ready_report()
        bad=[dict(original,python="other/python.exe"),dict(original,device="cuda"),
             dict(original,completed=False),dict(original,items=original["items"][:-1])]
        changed=copy.deepcopy(original);changed["items"][0]["requirement"]="numpy>=1"
        bad.append(changed)
        for report in bad:
            with patch.object(runtime,"detect",return_value=original) as detect:
                runtime.repair_plan(ROOT/"ai/requirements.txt", "cpu", report)
                detect.assert_called_once_with(ROOT/"ai/requirements.txt", "cpu", smoke=False)

    def test_reject_wrong_package_version_architecture_and_python(self):
        bad = [wheel(self.folder, name="transformers"), wheel(self.folder, version="1.0.0"),
               wheel(self.folder, tag="cp313-cp313-win_amd64"), wheel(self.folder, tag="cp312-cp312-win32"),
               wheel(self.folder, version="2.1.0", python=">=3.13")]
        for file in bad:
            with self.subTest(file=file.name), self.assertRaises(ValueError):
                runtime.validate_wheel(file, self.row)

    def test_filename_cannot_masquerade_as_other_package(self):
        file = wheel(self.folder, name="transformers").rename(self.folder/"numpy-2.0.0-py3-none-any.whl")
        with self.assertRaisesRegex(ValueError, "元数据"):
            runtime.validate_wheel(file, self.row)

    def test_offline_url_dependency_is_rejected_before_pip(self):
        file = wheel(self.folder, dependencies=["example @ https://example.invalid/example.whl"])
        with self.assertRaisesRegex(ValueError, "URL"):
            runtime.validate_wheel(file, self.row)

    def test_supported_python_is_310_to_312_x64(self):
        for minor in (10, 11, 12):
            self.assertTrue(runtime.supported((3, minor), 64, "AMD64"))
        for args in (((3, 13), 64, "AMD64"), ((3, 14), 64, "AMD64"), ((3, 12), 32, "AMD64"), ((3, 12), 64, "ARM64")):
            self.assertFalse(runtime.supported(*args))

    def test_import_dll_and_webview_failure_prevents_readiness(self):
        with patch.object(runtime, "supported", return_value=True), patch.object(runtime.metadata, "distribution") as distribution, patch.object(runtime.subprocess, "run") as run:
            distribution.return_value.version = "2.52.0"
            run.return_value = subprocess.CompletedProcess([], 1, "", "DLL load failed / WebView2 missing")
            result = runtime.detect(ROOT/"ai/requirements.txt")
            self.assertFalse(result["ready"])
            self.assertTrue(result["completed"])
            self.assertEqual(len(result["items"]), 13)
            self.assertTrue(all(not row["ok"] for row in result["items"]))

    def test_dependency_folder_resolves_missing_indirect_package_offline(self):
        # An isolated pip environment ensures this test never modifies the host.
        env = self.folder/"env"
        subprocess.run([sys.executable, "-m", "venv", str(env)], check=True, capture_output=True)
        python = env/"Scripts/python.exe"
        packages = self.folder/"packages"
        deps = self.folder/"dependencies"/"nested"
        packages.mkdir(); deps.mkdir(parents=True)
        selected = wheel(packages, dependencies=["aipraat-test-dependency>=1.0"])
        plan = self.folder/"plan.json"
        plan.write_text(json.dumps({"packages": {"numpy": str(selected)}, "dependency_folder": str(deps.parent), "dependency_folders": [str(deps)]}), encoding="utf-8")
        command = [str(python), "-I", str(ROOT/"ai/runtime_dependencies.py"), "--local-plan", str(plan)]
        failed = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("aipraat-test-dependency", failed.stdout+failed.stderr)
        wheel(deps, name="aipraat_test_dependency", version="1.0.0")
        success = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(success.returncode, 0, success.stdout+success.stderr)
        loaded = subprocess.run([str(python), "-I", "-c", "import numpy,aipraat_test_dependency; assert numpy.installed and aipraat_test_dependency.installed"], capture_output=True)
        self.assertEqual(loaded.returncode, 0, loaded.stderr)


if __name__ == "__main__":
    unittest.main()
