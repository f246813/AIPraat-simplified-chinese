"""Full 15-item acceptance in a private scratch venv with read-only seed deps.
Only the selected NumPy/SciPy/pyloudnorm are installed in scratch. Existing
original dependencies are exposed read-only via a local .pth; bytecode writes
are disabled. No production installs, junctions, hardlinks or user pip config.
Usage: python this.py --seed-python <complete prior Python 3.12> --wheelhouse <cp312 wheels>
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def execute(command, log, expected=0, timeout=360):
    result = subprocess.run([str(x) for x in command], capture_output=True, encoding="utf-8", errors="replace",
                            env=dict(os.environ, PYTHONIOENCODING="utf-8"), timeout=timeout)
    log.write_text(result.stdout+result.stderr, encoding="utf-8")
    print(result.stdout, end="")
    print(result.stderr, end="")
    assert result.returncode == expected, (result.returncode, command, log)
    return result


def main(seed, wheelhouse):
    seed = Path(seed).resolve()
    wheelhouse = Path(wheelhouse).resolve()
    output = ROOT/"scratch/normalization-runtime/full312"
    output.mkdir(parents=True, exist_ok=True)
    # Reuse only our explicitly marked development clone on subsequent runs.
    marker = output/"PRIVATE-DEVELOPMENT-ENV.txt"
    env = output/"venv-shared"
    if env.exists() and not marker.is_file():
        raise RuntimeError("Refusing to use an unmarked existing environment")
    seed_info = json.loads(subprocess.check_output([str(seed), "-I", "-c",
        "import sys,sysconfig,json;print(json.dumps(dict(base=sys.base_prefix,site=sysconfig.get_path('purelib'),version=list(sys.version_info[:2]))))"], text=True))
    assert seed_info["version"] == [3,12], seed_info
    seed_site = Path(seed_info["site"])
    original_numpy = seed_site/"numpy/__init__.py"
    seed_numpy_hash = hashlib.sha256(original_numpy.read_bytes()).hexdigest()
    before_seed = subprocess.check_output([str(seed), "-I", "-m", "pip", "list", "--format=json", "--disable-pip-version-check"], text=True)
    try:
        if not env.exists():
            execute([seed, "-I", "-m", "venv", env], output/"create-venv.log")
            marker.write_text("Private scratch venv; original dependencies borrowed read-only.\n", encoding="utf-8")
        # A byte-for-byte full copy was also tried, but this machine's .NET
        # policy rejects Python.Runtime.dll from the workspace (0x80131515).
        # Do not weaken CLR/security policy: retain existing DLL locations and
        # install ONLY the changed dependency stack into our private venv.
        (env/"Lib/site-packages/seed-readonly.pth").write_text(
            "import sys,site; sys.dont_write_bytecode=True; site.addsitedir("+repr(str(seed_site))+")\n", encoding="utf-8")
        python = env/"Scripts/python.exe"
        assets = output/"assets"
        assets.mkdir(exist_ok=True)
        for source in (ROOT/"ai/runtime_dependencies.py", ROOT/"ai/requirements.txt", ROOT/"ai/requirements-wav2vec2.txt", ROOT/"installer/src/ConfigurePython.ps1"):
            shutil.copy2(source, assets/source.name)
        # Only the scratch venv's pip config changes. --isolated honors site config.
        (env/"pip.ini").write_text("[global]\nindex-url = https://pypi.org/simple\nno-cache-dir = true\ntimeout = 20\nretries = 1\n", encoding="utf-8")
        base = [python, "-I", assets/"runtime_dependencies.py"]
        # First bring only the three new/changed packages to the chosen versions
        # using actual wheels; all original packages come from the seed COPY.
        execute([python,"-I","-m","pip","--isolated","install","--disable-pip-version-check","--no-index","--force-reinstall","--no-deps","--find-links",wheelhouse,
                 "numpy==2.2.6","scipy==1.14.1","pyloudnorm==0.2.0"], output/"bootstrap-offline.log")
        execute(base+["--smoke","--output",output/"baseline.json"], output/"baseline.log")
        baseline = json.loads((output/"baseline.json").read_text(encoding="utf-8"))
        assert baseline["ready"] and baseline["smoke"] and len(baseline["items"]) == 15
        print("PASS full312 baseline: 15/15 and real frontend smoke")
        site = env/"Lib/site-packages"
        numpy_stamp = (site/"numpy/__init__.py").stat().st_mtime_ns
        meter = site/"pyloudnorm/meter.py"
        for mode in ("Local", "Online"):
            # Import still works; a known-reference failure must drive reinstall.
            meter.write_bytes(meter.read_bytes()+b"\nMeter.integrated_loudness = lambda self, data: -23.0\n")
            before = output/(mode+"-before.json")
            execute(base+["--output",before], output/(mode+"-before.log"), expected=1)
            data = json.loads(before.read_text(encoding="utf-8"))
            assert [x["id"] for x in data["items"] if not x["ok"]] == ["pyloudnorm"]
            plan = dict(device="cpu", before=data)
            if mode == "Local":
                plan.update(packages={"pyloudnorm":str(wheelhouse/"pyloudnorm-0.2.0-py3-none-any.whl")},dependency_folder=str(wheelhouse))
            plan_file = output/(mode+"-plan.json")
            plan_file.write_text(json.dumps(plan,ensure_ascii=False),encoding="utf-8")
            report = output/(mode+"-result.json")
            result = execute(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",assets/"ConfigurePython.ps1",
                     "-PythonPath",python,"-Mode",mode,"-PlanPath",plan_file,"-ResultPath",report,"-LogPath",output/(mode+"-transcript.log"),"-NoPause"],output/(mode+"-flow.log"))
            final = json.loads(report.read_text(encoding="utf-8"))
            assert final["ready"] and final["completed"] and final["smoke"]
            assert len(final["items"]) == 15 and all(row["ok"] for row in final["items"])
            assert result.stdout.count("正在检测 ") == 15
            assert (site/"numpy/__init__.py").stat().st_mtime_ns == numpy_stamp
            assert "--force-reinstall" in result.stdout
            if mode == "Local":
                assert "Downloading " not in result.stdout
            print("PASS full312 shipping "+mode+": targeted same-version repair, 15/15, real frontend smoke, healthy NumPy unchanged")
        # Complete env Online action must not silently reinstall anything.
        ready_plan=output/"complete-plan.json"
        ready_plan.write_text(json.dumps(dict(device="cpu",before=final)),encoding="utf-8")
        ready_result=output/"complete-result.json"
        result=execute(["powershell.exe","-NoProfile","-ExecutionPolicy","Bypass","-File",assets/"ConfigurePython.ps1",
                "-PythonPath",python,"-Mode","Online","-PlanPath",ready_plan,"-ResultPath",ready_result,
                "-LogPath",output/"complete-transcript.log","-NoPause"],output/"complete-flow.log")
        assert "pip install " not in result.stdout
        assert json.loads(ready_result.read_text(encoding="utf-8"))["ready"]
        print("PASS full312 complete Online action: no pip install, 15/15 frontend recheck")
        (output/"acceptance.json").write_text(json.dumps(dict(python=str(python),version="3.12",cases=4,passed=4,failed=0,
                seed_read_only=True,environment_type="scratch venv with read-only original dependencies, not clean install",
                full_manifest=True,frontend_smoke=True,shipping_local=True,shipping_online=True),indent=2),encoding="utf-8")
    finally:
        after_seed = subprocess.check_output([str(seed),"-I","-m","pip","list","--format=json","--disable-pip-version-check"],text=True)
        assert before_seed == after_seed, "Seed dependency list changed"
        assert hashlib.sha256(original_numpy.read_bytes()).hexdigest() == seed_numpy_hash, "Seed NumPy modified"
        print("PASS seed package inventory and NumPy hash unchanged")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser=argparse.ArgumentParser()
    parser.add_argument("--seed-python",required=True)
    parser.add_argument("--wheelhouse",required=True)
    args=parser.parse_args()
    main(args.seed_python,args.wheelhouse)
