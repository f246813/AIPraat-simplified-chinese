"""Installer/path-dialog/PowerShell runtime contract. Never downloads models."""
from __future__ import annotations

import argparse
import email
import importlib.metadata as metadata
import json
import os
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime, timezone

LABELS = {
    "numpy": "NumPy", "pillow": "Pillow", "pywebview": "pywebview",
    "pythonnet": "pythonnet", "torch": "PyTorch", "transformers": "Transformers",
    "safetensors": "Safetensors", "praat-parselmouth": "praat-parselmouth",
    "pydantic-ai-slim": "pydantic-ai-slim", "pydantic-graph": "pydantic-graph",
    "jsonschema": "jsonschema",
}
CHECKS = {
    "numpy": "import numpy as n; assert (n.array([1,2])+1).tolist()==[2,3]",
    "pillow": "from PIL import Image; assert Image.new('RGB',(2,2)).size==(2,2)",
    "pywebview": "import webview; assert callable(webview.create_window)",
    "pythonnet": "import clr; clr.AddReference('System.Windows.Forms'); clr.AddReference('System.Drawing'); from System.Windows.Forms import Form; f=Form(); f.Dispose()",
    "torch": "import torch; d=DEVICE; assert d=='cpu' or torch.cuda.is_available(), 'CUDA device unavailable'; x=torch.tensor([1.,2.],device=d); assert (x+x).sum().item()==6; print('device='+d)",
    "transformers": "from transformers import AutoProcessor, AutoModelForCTC",
    "safetensors": "from safetensors.numpy import save,load; import numpy as n; assert load(save({'x':n.array([1.],dtype='float32')}))['x'][0]==1",
    "praat-parselmouth": "import parselmouth; import numpy as n; s=parselmouth.Sound(n.zeros(160),sampling_frequency=16000); assert s.n_samples==160",
    "pydantic-ai-slim": "import pydantic_ai; from pydantic_ai.models.openai import OpenAIChatModel; import openai",
    "pydantic-graph": "from pydantic_graph import Graph, BaseNode",
    "jsonschema": "import jsonschema; jsonschema.validate({'x':1},{'type':'object'})",
    "tk": "import tkinter; r=tkinter.Tk(); r.withdraw(); print(r.tk.call('info','patchlevel')); r.destroy()",
}


def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def requirements(path):
    result = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("-r "):
            result.extend(requirements(Path(path).parent / line[3:].strip()))
        else:
            match = re.fullmatch(r"([\w.-]+)(\[[\w,.-]+\])?([^;]*)(?:;.*)?", line)
            if not match:
                raise ValueError(f"Unsupported requirement: {line}")
            name, extras, spec = match.groups()
            name = canonical(name)
            result.append(dict(id=name, label=LABELS[name], required=True,
                               requirement=name+(extras or "")+spec.strip(), spec=spec.strip()))
    return result


def manifest(path):
    rows = requirements(path)
    rows += [dict(id="webview2", label="WebView2 Runtime", required=True,
                  requirement="Microsoft Evergreen x64", spec=""),
             dict(id="tk", label="Tcl/Tk", required=True, requirement="可创建 Tk 窗口", spec="")]
    return rows


def supported(version=None, bits=None, machine=None):
    version = version or sys.version_info[:2]
    bits = bits or struct.calcsize("P") * 8
    machine = machine or platform.machine()
    return tuple(version[:2]) in ((3, 10), (3, 11), (3, 12)) and bits == 64 and machine.lower() in ("amd64", "x86_64")


def packaging_api():
    try:
        from packaging.requirements import Requirement
        from packaging.specifiers import SpecifierSet
        from packaging.utils import parse_wheel_filename
        from packaging.tags import sys_tags
    except ImportError:
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.specifiers import SpecifierSet
        from pip._vendor.packaging.utils import parse_wheel_filename
        from pip._vendor.packaging.tags import sys_tags
    return Requirement, SpecifierSet, parse_wheel_filename, sys_tags


def satisfies(version, spec):
    try:
        return packaging_api()[1](spec).contains(version)
    except ImportError:
        # The empty/no-pip environment still needs useful detection results.
        parts = tuple(int(x) for x in version.split("+")[0].split("."))
        for op, value in re.findall(r"(>=|==|<)([\d.]+)", spec):
            target = tuple(int(x) for x in value.split("."))
            size = max(len(parts), len(target))
            left, right = parts+(0,)*(size-len(parts)), target+(0,)*(size-len(target))
            if not {">=": left >= right, "==": left == right, "<": left < right}[op]:
                return False
        return True


def webview_version():
    import winreg
    key = r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in (winreg.KEY_WOW64_32KEY, winreg.KEY_WOW64_64KEY):
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as handle:
                    value = winreg.QueryValueEx(handle, "pv")[0]
                    if value and value != "0.0.0.0":
                        return value
            except OSError:
                pass
    raise RuntimeError("WebView2 Evergreen Runtime 未安装")


def frontend_smoke():
    # Exercise the same pywebview + pythonnet + EdgeChromium stack as the frontend.
    import webview
    loaded = []
    window = webview.create_window("AIPraat 运行库启动检查", html="<html><body id='ready'>AIPraat runtime ready</body></html>", width=420, height=160)
    def check():
        if window.evaluate_js("document.getElementById('ready').textContent") == "AIPraat runtime ready":
            loaded.append(True)
        window.destroy()
    window.events.loaded += check
    with tempfile.TemporaryDirectory(prefix="AIPraat-webview-") as storage:
        webview.start(gui="edgechromium", private_mode=True, storage_path=storage)
    if not loaded:
        raise RuntimeError("新前端窗口未能加载 HTML/执行 JavaScript")


def child_check(item, device, smoke):
    if item == "webview2":
        print(webview_version(), flush=True)
        if smoke:
            frontend_smoke()
    else:
        exec("DEVICE="+repr(device)+"\n"+CHECKS[item], {})


def write_json(path, data):
    if path:
        temp = str(path)+".tmp"
        Path(temp).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)


def detect(path, device="cpu", smoke=False, output=None):
    rows = manifest(path)
    result = dict(python=sys.executable, version=platform.python_version(), architecture=platform.machine(),
                  device=device, supported=supported(), ready=False, completed=False, items=[])
    result["message"] = "Python 3.10–3.12 x64（推荐 3.12）" if not result["supported"] else ""
    for row in rows:
        item = dict(row, installed="", ok=False, status="未检测")
        result["items"].append(item)
        if not result["supported"]:
            item["status"] = "不支持所选 Python：需要 3.10–3.12 x64"
            continue
        print("正在检测 "+row["label"], flush=True)
        try:
            if row["id"] in LABELS:
                distribution = metadata.distribution(row["id"])
                item["installed"] = distribution.version
                if not satisfies(distribution.version, row["spec"]):
                    raise RuntimeError("版本不满足 "+row["requirement"])
            command = [sys.executable, "-I", str(Path(__file__).resolve()), "--child", row["id"], "--device", device]
            if smoke:
                command.append("--smoke")
            run = subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=90)
            if row["id"] == "webview2" and run.stdout.strip():
                first = run.stdout.strip().splitlines()[0]
                if re.fullmatch(r"\d+(?:\.\d+)+", first):
                    item["installed"] = first
            if run.returncode:
                raise RuntimeError((run.stderr or run.stdout or f"进程退出码 {run.returncode}").strip()[-2000:])
            if row["id"] in ("tk", "webview2"):
                item["installed"] = run.stdout.strip().splitlines()[0]
            item["ok"], item["status"] = True, "已就绪"
        except metadata.PackageNotFoundError:
            item["status"] = "未安装"
        except Exception as error:
            item["status"] = str(error)
        print(row["label"]+": "+item["status"], flush=True)
        write_json(output, result)
    result["completed"] = True
    result["checked_at"] = datetime.now(timezone.utc).isoformat()
    result["smoke"] = smoke
    result["ready"] = result["supported"] and all(row["ok"] for row in result["items"])
    write_json(output, result)
    return result


def repair_plan(path, device, before=None):
    rows=manifest(path)
    expected={row["id"]:row["requirement"] for row in rows}
    actual={row.get("id"):row.get("requirement") for row in (before or {}).get("items",[])}
    same_python=os.path.normcase(os.path.abspath((before or {}).get("python", ""))) == os.path.normcase(os.path.abspath(sys.executable))
    if not (same_python and (before or {}).get("device")==device and
            (before or {}).get("completed") and actual==expected and len(before["items"])==len(rows)):
        before=detect(path, device, smoke=False)
    if not before.get("supported"):
        raise ValueError("需要 Python 3.10–3.12 x64")
    failed={row["id"] for row in before["items"] if not row.get("ok")}
    return dict(requirements=[row["requirement"] for row in rows if row["id"] in failed & (set(LABELS)-{"torch"})],
                torch=next((row["requirement"] for row in rows if row["id"]=="torch" and "torch" in failed), ""),
                constraints=[row["id"]+row["spec"] for row in rows if row["id"] in LABELS],
                tk="tk" in failed, webview2="webview2" in failed)


def validate_wheel(path, row):
    Requirement, SpecifierSet, parse_filename, sys_tags = packaging_api()
    path = Path(path).resolve(strict=True)
    name, version, _, tags = parse_filename(path.name)
    if canonical(name) != row["id"]:
        raise ValueError(f"{row['label']} 不能选择 {name} 包")
    if not SpecifierSet(row["spec"]).contains(version):
        raise ValueError(f"{path.name} 不满足 {row['requirement']}")
    if not tags.intersection(set(sys_tags())):
        raise ValueError(f"{path.name} 与当前 Python 版本或 x64 架构不匹配")
    with zipfile.ZipFile(path) as wheel:
        files = [name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")]
        if len(files) != 1:
            raise ValueError(f"{path.name}: 无效 wheel 元数据")
        info = email.message_from_bytes(wheel.read(files[0]))
        if canonical(info["Name"] or "") != row["id"] or info["Version"] != str(version):
            raise ValueError(f"{path.name}: 文件名与包元数据不一致")
        if info["Requires-Python"] and not SpecifierSet(info["Requires-Python"]).contains(platform.python_version()):
            raise ValueError(f"{path.name}: Requires-Python 不支持当前解释器")
        for dep in info.get_all("Requires-Dist", []):
            if Requirement(dep).url:
                raise ValueError(f"{path.name}: 离线安装不支持 URL 依赖 {dep}，请补充本地 wheel")
    return path


def local_install(path, plan):
    rows = {row["id"]: row for row in requirements(path)}
    directories, targets = set(), []
    # Folder identification supplies the explicit subdirectories containing
    # wheels; pip must not silently miss indirect wheels below the chosen root.
    for selected in plan.get("dependency_folders", []):
        folder = Path(selected).resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("识别的依赖包目录不存在")
        directories.add(folder)
    if plan.get("dependency_folder"):
        folder = Path(plan["dependency_folder"]).resolve(strict=True)
        if not folder.is_dir():
            raise ValueError("依赖包文件夹不存在")
        directories.add(folder)
    for name, selected in plan.get("packages", {}).items():
        if name not in rows:
            raise ValueError("未知的运行库项："+name)
        wheel = validate_wheel(selected, rows[name])
        directories.add(wheel.parent)
        extras = "[openai]" if "[openai]" in rows[name]["requirement"] else ""
        targets.append(name+extras+" @ "+wheel.as_uri())
    # Also reject URL dependencies in indirect wheels before pip can resolve them.
    Requirement = packaging_api()[0]
    for folder in directories:
        for wheel_path in folder.glob("*.whl"):
            with zipfile.ZipFile(wheel_path) as wheel:
                for filename in wheel.namelist():
                    if filename.endswith(".dist-info/METADATA"):
                        info = email.message_from_bytes(wheel.read(filename))
                        for dep in info.get_all("Requires-Dist", []):
                            if Requirement(dep).url:
                                raise ValueError(f"{wheel_path.name}: URL 依赖不能用于离线安装：{dep}")
    if not targets:
        print("未选择 Python wheel；继续处理 Tk/WebView2 并复检。", flush=True)
        return
    with tempfile.TemporaryDirectory(prefix="AIPraat-pip-") as folder:
        constraints = Path(folder)/"constraints.txt"
        constraints.write_text("\n".join(row["id"]+row["spec"] for row in rows.values()), encoding="utf-8")
        command = [sys.executable, "-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check",
                   "--no-index", "--no-cache-dir", "--only-binary=:all:", "-c", str(constraints)]
        for directory in sorted(directories):
            command += ["--find-links", str(directory)]
        # pip names the missing indirect dependency; keep its full output for the UI.
        subprocess.run(command+targets, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default=str(Path(__file__).with_name("requirements.txt")))
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--manifest", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    parser.add_argument("--child", choices=tuple(CHECKS)+( "webview2",))
    parser.add_argument("--output")
    parser.add_argument("--local-plan")
    parser.add_argument("--repair-plan", action="store_true")
    parser.add_argument("--before-plan")
    args = parser.parse_args()
    if args.child:
        child_check(args.child, args.device, args.smoke)
    elif args.manifest:
        data = manifest(args.requirements)
        write_json(args.output, data)
        print(json.dumps(data, ensure_ascii=False))
    elif args.repair_plan:
        supplied=json.loads(Path(args.before_plan).read_text(encoding="utf-8-sig")) if args.before_plan else {}
        data=repair_plan(args.requirements,args.device,supplied.get("before"))
        write_json(args.output,data)
        if not args.output:
            print(json.dumps(data,ensure_ascii=False))
    elif args.local_plan:
        if not supported():
            raise RuntimeError("需要 Python 3.10–3.12 x64")
        local_install(args.requirements, json.loads(Path(args.local_plan).read_text(encoding="utf-8-sig")))
    else:
        result = detect(args.requirements, args.device, args.smoke, args.output)
        if not args.output:
            print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ready"] else 1
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
