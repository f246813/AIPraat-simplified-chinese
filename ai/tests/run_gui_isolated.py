"""Run the Tk/GUI test modules and the backend modules in two separate processes.

Why: a GUI test module that creates and destroys a Tk interpreter leaves the
destroyed ``tkapp`` in a reference cycle.  Collected on the main thread that is
harmless; collected by a *backend* module's worker thread it deletes the async
handler from the wrong thread and the process aborts with::

    Tcl_AsyncDelete: async handler deleted by the wrong thread

The GUI modules each end with ``tearDownModule()`` that runs ``gc.collect()`` on
the main thread, which is enough for the in-process mixed run.  This script is
the belt-and-braces variant: two processes simply cannot share that object
graph.

Dry run by default -- it only prints the plan::

    python ai/tests/run_gui_isolated.py

Add ``--run`` to execute both groups::

    python ai/tests/run_gui_isolated.py --run
    python ai/tests/run_gui_isolated.py --run --backend test_modern_execution
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

# Modules that create a real Tk interpreter in this process.
GUI_MODULES = [
    "test_dialogue_ui",
    "test_api_capability_status",
    "test_api_settings",
    "test_api_transition",
    "test_chat_message_render",
    "test_chat_process_fold",
    "test_analysis_stop",
    "test_ui_widgets",
    "test_ime",
    "test_dialogue_fastpath",
    "test_escape_gui",
]

# Backend modules that start worker threads (cloud runtime, cancellation, ...).
BACKEND_MODULES = ["test_modern_execution", "test_phase_context"]

TESTS_DIR = Path(__file__).resolve().parent
AI_DIR = TESTS_DIR.parent
REPO_ROOT = AI_DIR.parent


def environment() -> dict[str, str]:
    """PYTHONPATH with the package dirs *appended* to whatever is already there."""

    parts = [str(AI_DIR), str(TESTS_DIR)]
    existing = os.environ.get("PYTHONPATH", "")
    if existing:
        parts.append(existing)
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return env


def command(modules: list[str]) -> list[str]:
    return [sys.executable, "-m", "unittest", *modules, "-q"]


def describe(label: str, modules: list[str]) -> None:
    print(f"[{label}] {len(modules)} module(s): {' '.join(modules)}")
    print(f"[{label}] {' '.join(command(modules))}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true", help="actually run the two groups")
    parser.add_argument("--gui", nargs="+", default=GUI_MODULES, help="override the GUI module list")
    parser.add_argument("--backend", nargs="+", default=BACKEND_MODULES,
                        help="override the backend module list")
    parser.add_argument("--timeout", type=float, default=1800.0, help="per-group timeout in seconds")
    args = parser.parse_args(argv)

    if not args.run:
        print("dry run (add --run to execute); cwd =", REPO_ROOT)
        describe("gui", args.gui)
        describe("backend", args.backend)
        return 0

    env = environment()
    exit_code = 0
    for label, modules in (("gui", args.gui), ("backend", args.backend)):
        print(f"=== {label}: {' '.join(modules)}", flush=True)
        completed = subprocess.run(
            command(modules), cwd=REPO_ROOT, env=env, timeout=args.timeout
        )
        print(f"=== {label}: exit {completed.returncode}", flush=True)
        if completed.returncode == -1073740791:   # 0xC0000409, abort() on Windows
            print(f"=== {label}: aborted (0xC0000409 / Tcl_AsyncDelete)")
        if completed.returncode != 0:
            exit_code = completed.returncode
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
