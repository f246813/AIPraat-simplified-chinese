"""Real wheel installs/repairs in NEW scratch venvs only; no production changes.
Usage: python installer/tests/verify_loudness_install.py <scratch wheelhouse> [external-index-url]
Online branch: unmodified shipping AST, injected localhost HTTP index transport.
External-index-url is explicit opt-in (e.g. https://pypi.org/simple).
"""
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import os
from threading import Thread
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("runtime", ROOT/"ai/runtime_dependencies.py")
runtime = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runtime)
REQUIREMENTS = "numpy>=2.0,<2.3\nscipy>=1.14.1,<1.15\npyloudnorm==0.2.0\n"


def run(args, success=True):
    result = subprocess.run([str(arg) for arg in args], capture_output=True, encoding="utf-8", errors="replace", timeout=180,
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="")
    assert (result.returncode == 0) == success, (result.returncode, args)
    return result


def main(wheelhouse, external_index=None):
    wheelhouse = Path(wheelhouse).resolve()
    wheels = {name: next(wheelhouse.glob(pattern)) for name, pattern in {
        "numpy": "numpy-*-cp311-cp311-win_amd64.whl", "scipy": "scipy-1.14.1-cp311-cp311-win_amd64.whl",
        "pyloudnorm": "pyloudnorm-0.2.0-py3-none-any.whl"}.items()}
    scratch = ROOT/"scratch/normalization-runtime"
    scratch.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="real-install-中文-", dir=scratch) as temp:
        folder = Path(temp)
        env = folder/"venv"
        run([sys.executable, "-m", "venv", env])
        python = env/"Scripts/python.exe"
        packages = env/"Lib/site-packages"
        req = folder/"requirements.txt"
        req.write_text(REQUIREMENTS, encoding="utf-8")
        plan = folder/"plan.json"
        report = folder/"report.json"
        base = [python, "-I", ROOT/"ai/runtime_dependencies.py", "--requirements", req]
        # Missing real SciPy and missing real NumPy must be named; pip download
        # must fail before any selected wheel is installed.
        for missing in ("scipy", "numpy"):
            partial_house = folder/("missing-"+missing)
            partial_house.mkdir()
            for name, wheel in wheels.items():
                if name != missing:
                    shutil.copy2(wheel, partial_house/wheel.name)
            plan.write_text(json.dumps({"packages": {"pyloudnorm": str(partial_house/wheels["pyloudnorm"].name)}, "dependency_folder": str(partial_house)}), encoding="utf-8")
            result = run(base+["--local-plan", plan], success=False)
            assert missing in result.stdout+result.stderr
            assert not (packages/"pyloudnorm").exists() and not (packages/"numpy").exists()
            print("PASS missing real wheel closure before installation:", missing)
        def local(name):
            plan.write_text(json.dumps({"packages": {name: str(wheels[name])}, "dependency_folder": str(wheelhouse)}), encoding="utf-8")
            run(base+["--local-plan", plan])
        def check(ok):
            # Full selected manifest + native Tk/WebView registry detection;
            # deliberately not a claim of complete app/frontend smoke readiness.
            run(base+["--output", report], success=ok)
            data = json.loads(report.read_text(encoding="utf-8"))
            assert all(row["ok"] for row in data["items"] if row["id"] in wheels) == ok
            return data
        local("pyloudnorm")
        check(True)
        numpy_file = packages/"numpy/__init__.py"
        healthy_stamp = numpy_file.stat().st_mtime_ns
        for name, relative, corruption in (
            ("scipy", "scipy/signal/__init__.py", "raise ImportError('deliberate scratch ABI failure')\n"),
            ("scipy", "scipy/signal/__init__.py", "\ndef lfilter(*a,**kw):\n    import numpy as np\n    return np.zeros(4)\n"),
            ("pyloudnorm", "pyloudnorm/meter.py", "\nMeter.integrated_loudness = lambda self, data: -23.0\n")):
            broken = packages/relative
            if corruption.startswith("raise"):
                broken.write_text(corruption, encoding="utf-8")
            else:
                broken.write_text(broken.read_text(encoding="utf-8")+corruption, encoding="utf-8")
            before = check(False)
            row = next(r for r in before["items"] if r["id"] == name)
            assert row["installed"] in ("1.14.1", "0.2.0") and "功能检测失败" in row["status"]
            local(name)
            check(True)
            assert numpy_file.stat().st_mtime_ns == healthy_stamp
            print("PASS offline same-version targeted repair, healthy NumPy untouched:", name)
        # Serve a standard PEP 503 simple index over HTTP. This tests real pip
        # resolution and the shipping PS branch, independent of external service.
        index_root = folder/"index"
        index_root.mkdir()
        for name, wheel in wheels.items():
            shutil.copy2(wheel, index_root/wheel.name)
            page = index_root/"simple"/name
            page.mkdir(parents=True)
            (page/"index.html").write_text(f'<a href="../../{quote(wheel.name)}">{wheel.name}</a>', encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(SimpleHTTPRequestHandler, directory=str(index_root)))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            # Real missing-package install via Online branch.
            run([python, "-I", "-m", "pip", "--isolated", "uninstall", "-y", "pyloudnorm"])
            local_index = f"http://127.0.0.1:{server.server_port}/simple"
            cases = [(scenario, local_index) for scenario in ("missing", "functional", "complete")]
            if external_index:
                cases.append(("functional", external_index))
            for scenario, index_url in cases:
                if scenario == "functional":
                    meter_file = packages/"pyloudnorm/meter.py"
                    meter_file.write_text(meter_file.read_text(encoding="utf-8")+"\nMeter.integrated_loudness = lambda self, data: -23.0\n", encoding="utf-8")
                run(base+["--output", report], success=scenario == "complete")
                before = json.loads(report.read_text(encoding="utf-8"))
                # repair_plan runs in the selected scratch Python, not the host.
                plan.write_text(json.dumps({"before": before}), encoding="utf-8")
                repair = folder/"repair.json"
                run(base+["--repair-plan", "--before-plan", plan, "--output", repair])
                result = folder/(scenario+".json")
                output = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ROOT/"installer/tests/Test-LoudnessOnlineBranch.ps1", "-PythonPath", python, "-RepairPath", repair, "-Index", index_url, "-ResultPath", result])
                commands = json.loads(result.with_suffix(".commands.json").read_text(encoding="utf-8"))
                forced = [cmd for cmd in commands if "--force-reinstall" in cmd]
                assert len(forced) == (1 if scenario == "functional" else 0)
                if forced:
                    assert "--no-deps" in forced[0] and forced[0][-1] == "pyloudnorm==0.2.0"
                if scenario == "complete":
                    assert commands == []
                check(True)
                assert numpy_file.stat().st_mtime_ns == healthy_stamp
                print("PASS shipping Online branch HTTP index:", index_url, scenario)
        finally:
            server.shutdown()
            server.server_close()
        # Execute the FULL shipping PowerShell Local flow and its finally/smoke
        # recheck with the real 15-item manifest. This intentionally incomplete
        # dev env must NOT become globally ready merely because loudness works.
        assets = folder/"full-resources"
        assets.mkdir()
        for source in (ROOT/"ai/runtime_dependencies.py", ROOT/"ai/requirements.txt", ROOT/"ai/requirements-wav2vec2.txt", ROOT/"installer/src/ConfigurePython.ps1"):
            shutil.copy2(source, assets/source.name)
        full_report = folder/"full-before.json"
        run([python, "-I", assets/"runtime_dependencies.py", "--output", full_report], success=False)
        plan.write_text(json.dumps({"packages": {"scipy": str(wheels["scipy"])}, "dependency_folder": str(wheelhouse), "before": json.loads(full_report.read_text(encoding="utf-8"))}), encoding="utf-8")
        final_report = folder/"full-final.json"
        result = run(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", assets/"ConfigurePython.ps1", "-PythonPath", python, "-Mode", "Local", "-PlanPath", plan, "-ResultPath", final_report, "-LogPath", folder/"full-flow.log", "-NoPause"], success=False)
        final = json.loads(final_report.read_text(encoding="utf-8"))
        assert final["completed"] and final["smoke"] and not final["ready"]
        assert len(final["items"]) == 15
        assert all(row["ok"] for row in final["items"] if row["id"] in wheels)
        assert any(not row["ok"] for row in final["items"] if row["id"] not in wheels)
        assert result.stdout.count("正在检测 ") == 15, "postinstall recheck did not run exactly once"
        assert "全部必需运行库已通过" not in result.stdout
        print("PASS full shipping Local/finally flow returns nonready 15-item smoke report for incomplete dev env")
    print("PASS real wheel offline/online scoped integration; no production environment touched")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
