"""AI 后端真实验收（**默认不联网**）：云端模型 + 真实 Praat 计算，逐案例看后端交付。

这不是单元测试，也不替代单元测试。它把真实链路接起来跑一遍：

    load_config()（只读，不改配置）
      → cloud_agent.run_cloud_turn / run_dialogue_turn（真·CloudSession、真 Agent）
      → chat._execute_action → 工具模板生成 Praat 脚本
      → native_praat.run（同一个 Sound 对象列表，id 从 1 开始）真算
      → 读回真实结果文件 / 完成标记
      → 记录 goal / status / report / attempts / evidence / metrics / 请求数
         / 真实 HTTP 次数 / 自动 checks

几条硬约束（刻意写死，避免「跑一次就把额度花光」）：

* 默认 dry-run：只打印计划（案例、模型名、账本余额），**不发任何请求**；
  只有显式 ``--send`` 才真的发。
* ``--max-requests`` 默认 50，且不允许大于 50。
* ``--ledger`` 是持久计数文件：**重复运行不重置**。每个真实 HTTP 发送
  （包括 SDK 重试、工具返修、定点数值返修）在**发出去之前**先落盘记账；
  发送失败也照样计数（保守计数）。
* 只允许一路在发：钩子检测到并发发送直接报错；脚本本身也只从主线程跑。

**不会打印 API key、base URL 或生成的 Praat 脚本**；报告里也不写。

用法（仓库根目录，用装了 numpy/praat-parselmouth 的解释器）::

    D:/Praat-work/venv-ai-modern/Scripts/python.exe ai/tests/verify_ai_dialogue_acceptance.py
    D:/Praat-work/venv-ai-modern/Scripts/python.exe ai/tests/verify_ai_dialogue_acceptance.py --send
    ... --send --case pitch --case derived --output D:/tmp/report.json
    ... --send --ledger D:/tmp/ledger.json --max-requests 10

``--send`` 会产生极少量费用；脚本不会替你决定要不要花这笔钱。

自然性（「像不像人说的话」）**不在这里自动判定**：每个案例都标
``manual_review: pending``，靠长度或关键词通过不算通过。
"""

from __future__ import annotations

import native_praat
import unittest

import argparse
import json
import os
import re
import sys
import tempfile
import threading
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from unittest.mock import patch

TESTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TESTS_DIR.parent))
sys.path.insert(0, str(TESTS_DIR))

from praat_ai import answer_contract, chat, cloud_agent, cloud_metrics, delivery_text, tools  # noqa: E402
from praat_ai.config import api_is_active, load_config  # noqa: E402
from praat_ai.dialogue_policy import dialogue_kind  # noqa: E402
from praat_ai.escape_policy import AnalysisState, Budget  # noqa: E402
from praat_ai.session_context import DELIVERY_NOTE, PhaseContext  # noqa: E402
from praat_ai.workflow_policy import WorkflowPolicy, explicit_action  # noqa: E402


#: 临时音频：1 秒 220 Hz 正弦；另加一条 1 秒 200 Hz 作参考（差值案例用）。
TONE_HZ = 220.0
REFERENCE_HZ = 200.0
SAMPLE_RATE = 44100
#: 实测频率的接受窗口（纯音 220 Hz，Praat 自相关提取稳定落在 ±1 Hz 内；
#: 留宽是为了不因为版本/设置抖动误报，不是为了放过错误测量）。
PITCH_BAND = (205.0, 235.0)
REFERENCE_BAND = (185.0, 215.0)
#: 220 与 200 的差值。
EXPECTED_DELTA = TONE_HZ - REFERENCE_HZ
DELTA_TOLERANCE = 0.02
#: 1 秒音频的总时长。
EXPECTED_DURATION = 1.0
DURATION_TOLERANCE = 0.01

DEFAULT_MAX_REQUESTS = 50
MAX_REQUESTS_CAP = 50
LEDGER_VERSION = 1

#: 交付文本里绝不该出现的内部/占位痕迹（`delivery_text` 已覆盖一部分，这里补
#: 脚本、路径与建模层术语；命中任何一条就算泄漏）。
LEAK_TOKENS = (
    "{{calc", "{{q:", "{{", "selectObject", "appendFileLine", "plusObject",
    "tmin", "tmax", "rangeNote", "pydantic", "openai", "chat/completions",
    "/v1/", "tool_call", "tool_json", "system prompt", "系统提示词",
    "TODO", "PLACEHOLDER", "placeholder", "lorem ipsum",
    "示例脚本", "模拟数据", "假设的测量值", "假数据",
)

#: 进度/观测里允许出现的技术细节（只用于 evidence，不会当交付正文）。
OBSERVATION_LIMIT = 400
REPORT_LIMIT = 6000


# ---------------------------------------------------------------------------
# 案例定义
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CaseSpec:
    """一个验收案例：一句话目标 + 这条案例该怎么判。"""

    name: str
    goal: str
    #: 期望的路由类型：'dialogue' 走 run_dialogue_turn，'tools' 走 run_cloud_turn。
    route: str
    #: 只给 whole 案例：模拟「编辑器里还留着旧选区」，检查整段请求是否尊重全范围。
    selection: tuple[float, float] | None = None
    #: 这条案例允许出现的工具（空集表示不该执行任何工具）。
    allow_tools: bool = True
    #: 期望的对话类型（`dialogue_kind` 的返回值），None 表示不作要求。
    expect_kind: str | None = None
    #: 期望实测频率落在这个窗口（None 表示这条案例不期望基频）。
    band: tuple[float, float] | None = None
    #: 期望报告里同时出现这两条真实测量值（对象 1 / 对象 2）。
    expects_two_objects: bool = False
    #: 这条案例在交付正文里必须出现的关键词（任一词命中即算说到点上）。
    must_mention: tuple[str, ...] = ()
    #: 多轮案例：第一轮交付后把正式历史接进上下文，再用 ``followup_goal`` 追问一轮。
    followup: bool = False
    #: 第二轮（追问）的目标；``followup`` 为 True 时必填。
    followup_goal: str = ""
    note: str = ""


CASES: dict[str, CaseSpec] = {
    "knowledge": CaseSpec(
        name="knowledge",
        goal=("一般知识问题：日语里的 /ɯ/ 和 /u/ 有什么区别？"
              "请用发音原理讲一下怎么纠正，不需要测量我的录音。"),
        route="dialogue",
        allow_tools=False,
        expect_kind="concept",
        must_mention=("ɯ", "u"),
        note="自身语言学知识纠音，不许调用任何测量工具，也不许假装测过录音。",
    ),
    "concept": CaseSpec(
        name="concept",
        goal="基频和共振峰有什么区别？请解释一下原理，不需要测量我的录音。",
        route="dialogue",
        allow_tools=False,
        expect_kind="concept",
        must_mention=("基频", "共振峰"),
        note="概念解释走对话直答，不启动工具阶梯。",
    ),
    "pitch": CaseSpec(
        name="pitch",
        goal="请测量对象 1 在 0.1 到 0.8 秒之间的平均基频。",
        route="tools",
        band=PITCH_BAND,
        must_mention=("基频",),
        note="真算：原生 Praat 执行工具脚本，实测值必须落在 220 Hz 附近。",
    ),
    "duration": CaseSpec(
        name="duration",
        goal="请测量对象 1 的总时长。",
        route="tools",
        must_mention=("时长",),
        note="真算总时长（1 秒音频），交付正文必须带上这个实测值。",
    ),
    "mixed": CaseSpec(
        name="mixed",
        goal="介绍一下你都能测哪些声学参数，同时测一下对象 1 的平均基频。",
        route="tools",
        band=PITCH_BAND,
        must_mention=("基频", "共振峰", "时长", "强度"),
        note="能力介绍 + 真测量同一句：两件事都要做到。",
    ),
    "whole": CaseSpec(
        name="whole",
        goal="请对整段声音做平均基频统计，给我全段的平均值。",
        route="tools",
        selection=(0.100, 0.500),
        band=PITCH_BAND,
        must_mention=("基频",),
        note="编辑器里留着旧选区（0.100–0.500），整段请求不得静默只用那一小段。",
    ),
    "derived": CaseSpec(
        name="derived",
        goal="分别测量对象 1 和对象 2 的平均基频，然后告诉我两者的差值。",
        route="tools",
        band=PITCH_BAND,
        expects_two_objects=True,
        must_mention=("差", "平均基频"),
        note="两个对象都真测（220 Hz 与 200 Hz），差值必须是推导出来的。",
    ),
    "no_data": CaseSpec(
        name="no_data",
        goal="帮我测一下当前这段声音的平均基频。",
        route="tools",
        allow_tools=False,
        note="Praat 里没有对象：必须如实说测不了，并且不能编造测量值。",
    ),
    "knowledge_followup": CaseSpec(
        name="knowledge_followup",
        goal=("一般知识问题：日语里的 /ɯ/ 和 /u/ 有什么区别？"
              "请用发音原理讲一下怎么纠正，不需要测量我的录音。"),
        route="dialogue",
        allow_tools=False,
        expect_kind="concept",
        must_mention=("ɯ", "u"),
        followup=True,
        followup_goal="那我今天该怎么练？给我两三条具体的练习建议。",
        note="多轮（2 次对话）：第一轮知识讲解交付后，把用户实际看到的正文按正式历史"
             "接进对话上下文（mark_formal → payload → restore → sync_formal），第二轮"
             "追问练习建议；只检查第二轮真实请求体带的是上一轮实际交付的正文，且既有"
             "消息一个字不改（追加式前缀）。",
    ),
}

CASE_ORDER = ("knowledge", "concept", "pitch", "duration", "mixed", "whole", "derived",
              "no_data", "knowledge_followup")
#: 每个案例的请求量粗估（只用于 dry-run 打印，不参与硬限制）。
CASE_ESTIMATE = {
    "knowledge": "1–2 次",
    "concept": "1–2 次",
    "pitch": "2–4 次",
    "duration": "2–4 次",
    "mixed": "2–5 次",
    "whole": "2–4 次",
    "derived": "3–6 次",
    "no_data": "1–3 次",
    "knowledge_followup": "2–5 次（两轮对话各 1–2 次 + 可能的守卫纠正）",
}


class LedgerLimitExceeded(RuntimeError):
    """账本额度用尽：在真正发出去之前就停下。"""


class SendGuardViolation(RuntimeError):
    """发送钩子发现并发发送：只允许一路在发。"""


# ---------------------------------------------------------------------------
# 持久账本
# ---------------------------------------------------------------------------

def default_state_directory() -> Path:
    """账本与报告放哪儿：优先 PI_SCRATCH_DIR，其次系统临时目录。"""

    root = os.environ.get("PI_SCRATCH_DIR", "").strip()
    base = Path(root) if root else Path(tempfile.gettempdir())
    return base / "ai-dialogue-acceptance"


def default_ledger_path() -> Path:
    return default_state_directory() / "ledger.json"


def default_output_path() -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return default_state_directory() / f"report-{stamp}.json"


def read_ledger_summary(path: Path, limit: int = DEFAULT_MAX_REQUESTS) -> dict[str, Any]:
    """只读地看一眼账本（dry-run 用；不创建文件、不改内容）。"""

    path = Path(path)
    data: dict[str, Any] = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if not isinstance(data, dict):
            data = {}
    attempts = int(data.get("attempts_total") or 0)
    stored = data.get("limit")
    effective = min(stored, limit) if isinstance(stored, int) and stored > 0 else limit
    return {
        "path": str(path),
        "exists": path.is_file(),
        "limit": effective,
        "attempts_total": attempts,
        "remaining": max(0, effective - attempts),
        "runs": len(data.get("runs") or []),
    }


class SendLedger:
    """跨运行持久累加的发送计数。

    每次 ``reserve`` 都在**发请求之前**落盘（write + flush + fsync + 原子替换）。
    进程就算下一秒被杀掉，这次尝试也已经记上了——保守计数，宁可多记。
    """

    def __init__(self, path: Path | str, limit: int = DEFAULT_MAX_REQUESTS):
        if not 1 <= int(limit) <= MAX_REQUESTS_CAP:
            raise ValueError(f"limit 必须在 1–{MAX_REQUESTS_CAP} 之间")
        self.path = Path(path)
        self._lock = threading.Lock()
        self.data = self._load(int(limit))

    # -- 载入/落盘 ---------------------------------------------------------
    def _load(self, limit: int) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        if self.path.is_file():
            try:
                loaded = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                raw = loaded
        stored = raw.get("limit")
        # 账本里记的额度只会收紧，不会被后来的 --max-requests 放宽。
        effective = min(stored, limit) if isinstance(stored, int) and stored > 0 else limit
        counts = raw.get("case_counts")
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return {
            "version": LEDGER_VERSION,
            "limit": effective,
            "attempts_total": int(raw.get("attempts_total") or 0),
            "failed_attempts": int(raw.get("failed_attempts") or 0),
            "case_counts": dict(counts) if isinstance(counts, dict) else {},
            "runs": list(raw.get("runs") or []),
            "created_at": str(raw.get("created_at") or now),
            "updated_at": now,
        }

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data["updated_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        payload = json.dumps(self.data, ensure_ascii=False, indent=2)
        temporary = self.path.with_name(self.path.name + ".tmp")
        with open(temporary, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, self.path)

    # -- 查询/记账 ---------------------------------------------------------
    @property
    def limit(self) -> int:
        return int(self.data["limit"])

    @property
    def attempts_total(self) -> int:
        return int(self.data["attempts_total"])

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.attempts_total)

    def summary(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "limit": self.limit,
            "attempts_total": self.attempts_total,
            "remaining": self.remaining,
            "case_counts": dict(self.data["case_counts"]),
            "runs": len(self.data["runs"]),
            "created_at": self.data["created_at"],
        }

    def reserve(self, case: str) -> int:
        """占一次发送额度，并**先落盘**。额度用尽时抛 LedgerLimitExceeded。"""

        with self._lock:
            if self.attempts_total >= self.limit:
                raise LedgerLimitExceeded(
                    f"账本额度已用尽（{self.attempts_total}/{self.limit}）："
                    f"{self.path}。这个文件不会自动重置；要用新的额度请换一个 --ledger。"
                )
            self.data["attempts_total"] = self.attempts_total + 1
            counts = self.data["case_counts"]
            counts[case] = int(counts.get(case) or 0) + 1
            self._persist()
            return self.attempts_total

    def note_failure(self, count: int = 1) -> None:
        with self._lock:
            self.data["failed_attempts"] = int(self.data["failed_attempts"]) + int(count)
            self._persist()

    def record_run(self, record: Mapping[str, Any]) -> None:
        with self._lock:
            self.data["runs"].append(dict(record))
            self._persist()


# ---------------------------------------------------------------------------
# 发送钩子：硬限制 + 发前记账，且保留原有指标钩子
# ---------------------------------------------------------------------------

def new_counters(case: str = "") -> dict[str, Any]:
    return {
        "case": case,
        "sends": 0,
        "responses": 0,
        "statuses": [],
        "in_flight": 0,
        "limit_hit": False,
        "violation": "",
        "threads": set(),
        "guard": threading.Lock(),
    }


@contextmanager
def install_send_guard(ledger: SendLedger, counters: dict[str, Any], case: str):
    """把硬限制挂在**最终 HTTP 发送**那一步上。

    ``cloud_metrics.observe_request`` 就是 ``create_model`` 装进 httpx 的
    ``event_hooks['request']``（见 ``cloud_agent.create_model``）：请求真正发出去
    之前它一定会被调用。这里包一层而不是换掉它——原来那份指标钩子照常执行，
    缓存指纹、用量统计一条都不少。
    """

    original_request = cloud_metrics.observe_request
    original_response = cloud_metrics.observe_response
    counters["case"] = case

    async def guarded_request(request):
        with counters["guard"]:
            counters["in_flight"] += 1
            concurrent = counters["in_flight"]
        if concurrent > 1:
            with counters["guard"]:
                counters["in_flight"] -= 1
            counters["violation"] = "检测到并发的云端发送"
            raise SendGuardViolation("只允许一路云端请求在发：检测到并发发送")
        try:
            # 发之前先记账：这一步失败（额度用尽）就不会有 HTTP 出去。
            try:
                ledger.reserve(str(counters['case']))
            except LedgerLimitExceeded:
                counters["limit_hit"] = True
                raise
            counters["sends"] += 1
            counters["threads"].add(threading.current_thread().name)
            return await original_request(request)
        finally:
            with counters["guard"]:
                counters["in_flight"] -= 1

    async def guarded_response(response):
        counters["responses"] += 1
        status = getattr(response, "status_code", None)
        if isinstance(status, int):
            counters["statuses"].append(status)
        return await original_response(response)

    with ExitStack() as stack:
        stack.enter_context(patch.object(cloud_metrics, "observe_request", guarded_request))
        stack.enter_context(patch.object(cloud_metrics, "observe_response", guarded_response))
        yield counters


def model_factory(config):
    """真·``cloud_agent.create_model``，并把 SDK 重试显式压到 0。

    ``create_model`` 自己已经是 ``max_retries=0``（应用层只有一次有限重试）；
    这里再设一遍是保险：验收里绝不允许 SDK 在背后重放请求。
    """

    model = cloud_agent.create_model(config)
    client = getattr(model, "client", None)
    if client is not None and hasattr(client, "max_retries"):
        client.max_retries = 0
    return model


# ---------------------------------------------------------------------------
# 临时音频 + 真实 Praat 执行器
# ---------------------------------------------------------------------------

def load_sounds() -> list:
    """1 秒 220 Hz 正弦 + 1 秒 200 Hz 参考音（都在内存里，不碰用户录音）。"""

    import numpy as np
    import parselmouth

    time_axis = np.arange(SAMPLE_RATE, dtype=np.float64) / SAMPLE_RATE
    return [
        parselmouth.Sound(np.sin(2 * np.pi * hz * time_axis), sampling_frequency=SAMPLE_RATE)
        for hz in (TONE_HZ, REFERENCE_HZ)
    ]


def build_object_context(*, selection: tuple[float, float] | None = None,
                         empty: bool = False) -> str:
    """对象列表（6 列，和前端写出来的一样；id 从 1 开始）。"""

    header = "id\tclass\tname\tselected\tsel_start\tsel_end"
    if empty:
        return header + "\n"
    start, end = ("", "") if selection is None else (f"{selection[0]:.3f}", f"{selection[1]:.3f}")
    rows = [
        f"1\tSound\ttone220\t1\t{start}\t{end}",
        f"2\tSound\ttone200\t0\t\t",
    ]
    return header + "\n" + "\n".join(rows) + "\n"


def read_praat_text(path: Path) -> str:
    """读取 Praat 的 UTF-8 或带 BOM 的 UTF-16 结果文件。"""

    try:
        raw = Path(path).read_bytes()
    except OSError:
        return ""
    if raw.startswith(b"\xff\xfe"):
        return raw[2:].decode("utf-16-le", "replace")
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", "replace")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw[3:].decode("utf-8", "replace")
    return raw.decode("utf-8", "replace")


class PraatExecutor:
    """把一条脚本真的交给 Praat 算，并读回结果文件与完成标记。

    每次执行都把**同一个 Sound 对象列表**交给 ``native_praat.run``，
    所以脚本里的 ``selectObject: 1`` / ``2`` 永远指向那两条临时音频（id 从 1
    开始），和真实 Praat 对象列表的顺序一致。
    """

    def __init__(self, directory: Path, sounds: Sequence | None = None):
        self.directory = Path(directory)
        self.result_path = self.directory / "chat_result.tsv"
        self.state_path = self.directory / "chat_state.txt"
        self.sounds = list(sounds) if sounds is not None else []
        self.executions: list[dict[str, Any]] = []

    def __call__(self, script: str) -> tuple[bool, list[str], str]:
        import parselmouth

        for path in (self.result_path, self.state_path):
            path.unlink(missing_ok=True)
        started = time.monotonic()
        output = ""
        error = ""
        try:
            returned = native_praat.run(self.sounds, script, capture_output=True)
        except unittest.SkipTest:
            raise
        except Exception as failure:  # noqa: BLE001 - 执行失败要如实带回模型
            error = f"Praat 执行失败：{type(failure).__name__}: {failure}"
            returned = None
        if isinstance(returned, tuple) and len(returned) > 1:
            output = str(returned[1] or "")
        elif returned is not None:
            output = str(returned)
        results = [
            line.strip()
            for line in read_praat_text(self.result_path).splitlines()
            if line.strip()
        ]
        marker = read_praat_text(self.state_path).strip() if self.state_path.is_file() else ""
        done = marker == "done"
        self.executions.append({
            "ok": done,
            "done": done,
            "seconds": round(time.monotonic() - started, 2),
            "results": results,
            "console": output.strip()[:OBSERVATION_LIMIT],
            "error": error,
        })
        if not done:
            failure = error or output.strip()[:300] or "脚本没有写出完成标记。"
            return False, results, failure
        return True, results, ""


# ---------------------------------------------------------------------------
# 工具执行：只经过 chat._execute_action，不绕过任何工具检查
# ---------------------------------------------------------------------------

def make_execute_action(executor: PraatExecutor, context: tools.ToolContext,
                        attempts: list[dict[str, Any]]):
    """把 (name, arguments, round) 变成真实执行过的 ``AgentStep``。

    走的正是产品那条路：``chat._execute_action`` 负责工具自己的参数检查、
    对象检查与脚本模板渲染，只有检查都过了才会调用 ``executor``。
    """

    def execute_action(name, arguments, round_index):
        arguments = dict(arguments or {})
        before = len(executor.executions)
        step = chat._execute_action(
            {"tool": name, "arguments": arguments},
            context,
            executor,
            round_index,
            environment=None,
        )
        executed = len(executor.executions) - before
        attempts.append({
            "round": round_index,
            "tool": str(name),
            "arguments": _scrub_arguments(arguments),
            "ok": bool(step.ok),
            "executed": executed,
            "observation": str(step.observation)[:OBSERVATION_LIMIT],
            "results": [str(row) for row in (step.results or [])][:12],
        })
        return step

    return execute_action


def _scrub_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    """只保留判断用的参数；模型给的 ``script`` 字段一律不记（不写进报告）。"""

    kept: dict[str, Any] = {}
    for key, value in arguments.items():
        if key in {"script", "code"}:
            kept[key] = "[omitted]"
            continue
        if isinstance(value, (str, int, float, bool)) or value is None:
            kept[key] = value
        elif isinstance(value, (list, tuple)):
            kept[key] = [item for item in value if isinstance(item, (str, int, float, bool))][:12]
        else:
            kept[key] = str(value)[:80]
    return kept


# ---------------------------------------------------------------------------
# 交付正文的自动检查（自然性一律交给人工）
# ---------------------------------------------------------------------------

def parse_hz_values(rows: Sequence[str]) -> list[float]:
    values = []
    for row in rows:
        for match in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:Hz|赫兹)", str(row), re.I):
            try:
                values.append(float(match[1]))
            except ValueError:
                continue
    return values


def values_in_band(rows: Sequence[str], band: tuple[float, float]) -> list[float]:
    low, high = band
    return [value for value in parse_hz_values(rows) if low <= value <= high]


def parse_seconds_values(rows: Sequence[str]) -> list[float]:
    values = []
    for row in rows:
        for match in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:秒|s\b)", str(row), re.I):
            try:
                values.append(float(match[1]))
            except ValueError:
                continue
    return values


def find_leaks(text: str, *, extra: Sequence[str] = (), secrets: Sequence[str] = ()):
    """交付正文里的泄漏：占位符、内部话术、脚本痕迹、凭证。

    复用产品的 ``delivery_text.find_answer_leaks``（占位式与内部术语），再补
    验收自己关心的痕迹。返回 ``[(kind, detail)]``；空列表表示没发现。
    """

    body = str(text or "")
    issues: list[tuple[str, str]] = []
    for leak in delivery_text.find_answer_leaks(body):
        issues.append((str(getattr(leak, "kind", "leak")), str(getattr(leak, "detail", ""))[:120]))
    lowered = body.casefold()
    for token in tuple(LEAK_TOKENS) + tuple(extra):
        if token and token.casefold() in lowered:
            issues.append(("token", token))
    for secret in secrets:
        if secret and len(secret) >= 4 and secret in body:
            issues.append(("credential", "[redacted]"))
    return issues


def mentions_all(text: str, words: Sequence[str]) -> bool:
    body = str(text or "")
    return all(word in body for word in words)


def mentions_any(text: str, words: Sequence[str]) -> list[str]:
    body = str(text or "")
    return [word for word in words if word in body]


def check(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"id": name, "ok": bool(ok), "detail": str(detail)[:300]}


def delivery_checks(spec: CaseSpec, status: str, sends: int, report: str) -> list[dict[str, Any]]:
    """Do not confuse an honest partial delivery with task completion."""
    expected = {'partial'} if spec.name == 'no_data' else {'complete'}
    checks = [check('status', status in expected, f'status={status}; expected={sorted(expected)}'),
              check('http_observed', sends >= 1 or spec.name == 'no_data',
                    f'actual_http_count={sends}; no_data may finish locally')]
    if spec.expects_two_objects:
        values = [value for value in parse_hz_values([report])
                  if abs(value - EXPECTED_DELTA) <= DELTA_TOLERANCE]
        unresolved = any(marker in report for marker in ('[推导未核对]', '[未通过核对的数值]'))
        checks.append(check('difference_stated', bool(values) and not unresolved,
                            f'差值候选 {values[:4]}; tolerance={DELTA_TOLERANCE} Hz'))
    return checks


CASE_ESTIMATE_HINT = "（请求数是应用层计数；actual_http_count 是钩子数到的真实 HTTP 发送）"


# ---------------------------------------------------------------------------
# 跑单个案例
# ---------------------------------------------------------------------------

def build_state(goal: str, context_text: str, config) -> AnalysisState:
    state = AnalysisState(
        goal,
        context_text,
        budget=Budget(context_tokens=config.api.max_context_tokens,
                      response_tokens=config.api.plan_max_tokens),
    )
    state.workflow = config.workflow or WorkflowPolicy()
    state.workflow_override = explicit_action(goal)
    state.deliveries = [goal]
    state.session_id = "acceptance"
    return state


# ---------------------------------------------------------------------------
# 多轮（follow-up）：上一轮实际交付的正文必须原样进入下一轮真实请求体
# ---------------------------------------------------------------------------

def _capture_request_bodies(bodies: list[str]):
    """在已有发送钩子**外面**再包一层：只把真实请求体抄一份。

    ``install_send_guard`` 已经把 ``cloud_metrics.observe_request`` 换成了带账本
    记账与并发检查的那一层（见 ``create_model`` 的 ``event_hooks['request']``）。
    这里继续往里包，调用链是「捕获 → 账本/并发 → 原始指标钩子」，所以
    ``SendLedger.reserve`` 一次都不会少，也没有任何绕过。
    """

    inner = cloud_metrics.observe_request

    async def capturing(request):
        try:
            bodies.append(bytes(request.content).decode("utf-8", "replace"))
        except Exception:  # noqa: BLE001 - 抄不到请求体不该影响发送本身
            bodies.append("")
        return await inner(request)

    return patch.object(cloud_metrics, "observe_request", capturing)


def request_messages(body: str) -> list[dict[str, Any]]:
    """从一个真实请求体里取出 ``messages``（这就是会被缓存命中的那段前缀）。"""

    try:
        payload = json.loads(body or "{}")
    except ValueError:
        return []
    if not isinstance(payload, Mapping):
        return []
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return []
    return [dict(item) for item in messages if isinstance(item, Mapping)]


def message_text(message: Mapping[str, Any]) -> str:
    """消息正文：字符串直接返回，分块内容摊平成文本。"""

    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(str(part.get("text") or "") for part in content if isinstance(part, Mapping))
    return ""


def messages_with_role(messages: Sequence[Mapping[str, Any]], role: str) -> list[Mapping[str, Any]]:
    return [item for item in messages if str(item.get("role")) == role]


def carries_delivery_note(messages: Sequence[Any]) -> bool:
    """上下文里是否已有「应用交付记录」（只有交付正文与原生末条不一致时才追加）。"""

    for message in messages or ():
        for part in getattr(message, "parts", None) or ():
            content = getattr(part, "content", "")
            if isinstance(content, str) and content.strip() == DELIVERY_NOTE.strip():
                return True
    return False


def run_followup_rounds(spec: CaseSpec, *, config, runtime, first_state: AnalysisState,
                        cancel: threading.Event, first_kind: str, second_kind: str,
                        first_deltas: list[str], second_deltas: list[str],
                        progress) -> dict[str, Any]:
    """两轮真实对话：把「第一轮实际交付的正文」按产品的方式接进第二轮上下文。

    走的就是产品那条路（``modern_execution`` 的做法）：第一轮结束后用用户实际看到
    的正文组 history、``context.mark_formal(history)``、
    ``PhaseContext.restore(context.payload())``、``restored.sync_formal(history)``，
    再把恢复后的 context 与 history 交给第二个 ``AnalysisState``（history 同时作为
    ``dialogue_context``）。两轮的真实请求体都由同一个发送钩子抄下来——账本记账、
    并发检查、原有指标钩子全都照旧。
    """

    data: dict[str, Any] = {
        "error": "",
        "first_kind": first_kind,
        "second_kind": second_kind,
        "first_state": first_state,
        "state": first_state,
        "first_report": "",
        "second_report": "",
        "first_stream": "",
        "second_stream": "",
        "first_sends": 0,
        "second_sends": 0,
        "first_messages": [],
        "second_messages": [],
        "note_present": False,
        "history": [],
    }
    bodies: list[str] = []
    with _capture_request_bodies(bodies):
        try:
            cloud_agent.run_dialogue_turn(
                config, first_state, kind=first_kind, cancel=cancel,
                on_text=first_deltas.append, progress=progress, runtime=runtime,
            )
        except Exception as failure:  # noqa: BLE001 - 真机脚本把失败如实带回
            data["error"] = f"第一轮：{type(failure).__name__}: {failure}"
        first_report = str(first_state.report or "")
        data["first_report"] = first_report
        data["first_stream"] = "".join(first_deltas)
        data["first_sends"] = len(bodies)
        data["first_messages"] = request_messages(bodies[0]) if bodies else []

        history = [{"role": "user", "content": spec.goal},
                   {"role": "assistant", "content": first_report}]
        data["history"] = history
        context = first_state.phase_contexts.get("dialogue") or first_state.phase_context
        restored = None
        if context is not None:
            context.mark_formal(history)
            payload = context.payload()
            restored = PhaseContext.restore(payload) if payload else None
            if restored is not None:
                restored.sync_formal(history)
                data["note_present"] = carries_delivery_note(restored.messages)
            else:
                data["note_present"] = carries_delivery_note(context.messages)
        if restored is None:
            data["error"] = data["error"] or "第一轮没有可恢复的对话上下文（payload 为空）"

        second_state = build_state(spec.followup_goal or spec.goal, first_state.context_text, config)
        second_state.dialogue_context = [dict(item) for item in history]
        second_state.phase_context = restored
        second_state.context_epoch = (int(getattr(restored, "epoch", 0) or 0)
                                      if restored is not None else 0)
        data["state"] = second_state
        try:
            cloud_agent.run_dialogue_turn(
                config, second_state, kind=second_kind, cancel=cancel,
                on_text=second_deltas.append, progress=progress, runtime=runtime,
            )
        except Exception as failure:  # noqa: BLE001
            note = f"第二轮：{type(failure).__name__}: {failure}"
            data["error"] = f"{data['error']}；{note}" if data["error"] else note
        data["second_report"] = str(second_state.report or "")
        data["second_stream"] = "".join(second_deltas)
        round_two = bodies[data["first_sends"]:]
        data["second_sends"] = len(round_two)
        data["second_messages"] = request_messages(round_two[0]) if round_two else []
    # 请求体只在本进程里判一次；原始字节不进报告，只有判完的结果写出去。
    return data


def followup_checks(data: Mapping[str, Any]) -> list[dict[str, Any]]:
    """多轮用例的自动检查：上一轮交付正文在下一轮真实请求体里的形态。

    只依据真实请求体里的 ``messages`` 和第一轮 ``on_text`` 的拼接结果判定，
    不用「产品说它接了」来证明它接了。
    """

    checks: list[dict[str, Any]] = []
    first_report = str(data.get("first_report") or "").strip()
    streamed = str(data.get("first_stream") or "").strip()
    first = list(data.get("first_messages") or [])
    second = list(data.get("second_messages") or [])

    # 1) 第一轮：用户看到的（on_text 拼接）就是交付的（state.report）。
    differing = next((index for index, (left, right)
                      in enumerate(zip(streamed, first_report)) if left != right), None)
    if differing is None and len(streamed) != len(first_report):
        differing = min(len(streamed), len(first_report))
    checks.append(check(
        "followup_first_delivery_matches_stream",
        bool(first_report) and streamed == first_report,
        f"第一轮 on_text {len(streamed)} 字 / state.report {len(first_report)} 字；"
        + (f"首处不同在第 {differing + 1} 字" if differing is not None else "逐字一致"),
    ))

    # 2) 第二轮请求体里，上一轮交付正文作为 assistant 消息**恰好一条**。
    assistant = messages_with_role(second, "assistant")
    copies = [index for index, message in enumerate(assistant)
              if first_report and message_text(message).strip() == first_report]
    checks.append(check(
        "followup_delivered_body_once",
        bool(first_report) and bool(second) and len(copies) == 1,
        f"第二轮请求 messages {len(second)} 条（assistant {len(assistant)} 条）；"
        f"等于第一轮交付正文的 assistant 消息 {len(copies)} 条（要求恰好 1 条）",
    ))

    # 3) 交付记录的形式：不一致时必须带「应用交付记录」并紧跟交付正文；一致时不得重复追加。
    note_positions = [index for index, message in enumerate(second)
                      if str(message.get("role")) == "user"
                      and DELIVERY_NOTE.strip() in message_text(message)]
    if data.get("note_present"):
        following = (message_text(second[note_positions[0] + 1]).strip()
                     if note_positions and note_positions[0] + 1 < len(second) else "")
        ok = len(note_positions) == 1 and following == first_report
        detail = (f"第一轮交付与原生末条不一致：交付记录出现 {len(note_positions)} 次（要求 1 次），"
                  f"紧跟的 assistant 正文与交付正文一致：{following == first_report}")
    else:
        ok = not note_positions
        detail = ("第一轮原生末条就是交付正文：本轮不需要追加交付记录"
                  f"（实际出现 {len(note_positions)} 次）")
    checks.append(check("followup_delivery_note_form", ok, detail))

    # 4) 正式回合的尾巴就是交付正文：上下文最后一个 assistant 回合必须等于交付正文，
    #    交付记录不能自己冒充成 assistant 正文。草稿可以留在历史里（交付记录已声明它
    #    不是最终交付），但「最后一条 assistant」只能是用户实际看到的那份正文。
    texts = [message_text(message).strip() for message in assistant]
    texts = [text for text in texts if text]
    note_as_assistant = [text[:40] for text in texts if DELIVERY_NOTE.strip() in text]
    tail = texts[-1] if texts else ""
    drafts = max(0, len(texts) - 1)
    checks.append(check(
        "followup_tail_is_delivery",
        bool(first_report) and tail == first_report and not note_as_assistant,
        f"最后一条 assistant 就是交付正文：{tail == first_report}；"
        f"其它 assistant 消息（草稿/纠正中间稿）{drafts} 条；"
        f"把交付记录写成 assistant 正文的 {len(note_as_assistant)} 条",
    ))

    # 5) 追加式上下文：第一轮请求 messages 是第二轮请求 messages 的前缀（缓存前缀不破）。
    prefix = bool(first) and bool(second) and second[:len(first)] == first
    checks.append(check(
        "followup_prefix_preserved",
        prefix,
        f"第一轮请求 messages {len(first)} 条 / 第二轮 {len(second)} 条；前缀完全一致：{prefix}"
        + ("" if first else "（第一轮没有抓到请求体）"),
    ))
    return checks


def run_case(spec: CaseSpec, *, config, runtime, ledger: SendLedger,
             counters: dict[str, Any], workdir: Path) -> dict[str, Any]:
    """跑一个案例，返回它的完整记录（不含脚本、key、base URL）。"""

    empty = spec.name == "no_data"
    raw_context = build_object_context(selection=spec.selection, empty=empty)
    # 产品就是这么做的：整段请求会先把编辑器旧选区清掉（chat._request_context）。
    context_text = chat._request_context(raw_context, spec.goal)
    context = tools.ToolContext(
        tools.parse_object_context(context_text),
        workdir / "chat_result.tsv",
        workdir / "chat_state.txt",
    )
    executor = PraatExecutor(workdir, load_sounds() if not empty else [])
    attempts: list[dict[str, Any]] = []
    execute_action = make_execute_action(executor, context, attempts)
    progress_lines: list[str] = []
    deltas: list[str] = []
    state = build_state(spec.goal, context_text, config)
    cancel = threading.Event()
    secrets = [str(getattr(config.api, "api_key", "") or ""),
               str(getattr(config.api, "base_url", "") or "")]

    kind = dialogue_kind(spec.goal)
    second_kind = dialogue_kind(spec.followup_goal) if spec.followup else None
    before_sends = counters["sends"]
    error = ""
    followup: dict[str, Any] | None = None
    started = time.monotonic()
    try:
        if spec.followup:
            if kind is None:
                error = "dialogue_kind 没有把这句话判成对话（会误进工具阶梯）"
            first_deltas: list[str] = []
            second_deltas: list[str] = []
            followup = run_followup_rounds(
                spec, config=config, runtime=runtime, first_state=state, cancel=cancel,
                first_kind=kind or spec.expect_kind or "concept",
                second_kind=second_kind or "advice",
                first_deltas=first_deltas, second_deltas=second_deltas,
                progress=progress_lines.append,
            )
            deltas.extend(first_deltas)
            deltas.extend(second_deltas)
            state = followup["state"]
            error = followup["error"] or error
        elif spec.route == "dialogue":
            if kind is None:
                error = "dialogue_kind 没有把这句话判成对话（会误进工具阶梯）"
            cloud_agent.run_dialogue_turn(
                config, state,
                kind=kind or "concept",
                cancel=cancel,
                on_text=deltas.append,
                progress=progress_lines.append,
                runtime=runtime,
            )
        else:
            cloud_agent.run_cloud_turn(
                config, state,
                execute_action=execute_action,
                cancel=cancel,
                progress=progress_lines.append,
                runtime=runtime,
            )
    except Exception as failure:  # noqa: BLE001 - 真机脚本要把失败如实带回
        error = f"{type(failure).__name__}: {failure}"
    elapsed = round(time.monotonic() - started, 2)
    sends = counters["sends"] - before_sends
    recent = counters["statuses"][-sends:] if sends else []
    failed_sends = [status for status in recent if status >= 400]
    if failed_sends:
        ledger.note_failure(len(failed_sends))

    report = _scrub_text(str(state.report or "").strip(), workdir, secrets)
    results = [row for execution in executor.executions for row in execution["results"]]
    ok_attempts = [item for item in attempts if item["ok"] and item["executed"]]
    leaks = find_leaks(report, secrets=secrets)
    # 多轮案例的第一轮正文（知识讲解）也要过泄漏检查，并且是 knowledge_answer /
    # 冲突检查的对象（追问那轮只判「有没有接住上一轮的交付」）。
    first_report = (_scrub_text(str(followup["first_report"] or ""), workdir, secrets).strip()
                    if followup is not None else report)
    if followup is not None and first_report != report:
        leaks = [*leaks, *find_leaks(first_report, secrets=secrets)]

    checks: list[dict[str, Any]] = []
    routed = spec.route == "tools" or kind is not None
    checks.append(check("routing", routed, f"route={spec.route} kind={kind}"))
    if spec.expect_kind is not None:
        checks.append(check("dialogue_kind", kind == spec.expect_kind,
                            f"dialogue_kind={kind}（期望 {spec.expect_kind}）"))
    checks.extend(delivery_checks(spec, state.status, sends, report))
    checks.append(check("report_present", bool(report), f"{len(report)} 字（只判非空，不判自然性）"))
    checks.append(check("no_credential_leak",
                        not [item for item in leaks if item[0] == "credential"],
                        "; ".join(f"{kind_}:{detail}" for kind_, detail in leaks)))
    checks.append(check("no_internal_jargon", not leaks,
                        "; ".join(f"{kind_}:{detail}" for kind_, detail in leaks)))

    if followup is not None:
        # 多轮用例：只看「上一轮实际交付的正文」在下一轮真实请求体里的形态。
        checks.extend(followup_checks(followup))

    if not spec.allow_tools:
        checks.append(check("no_tool_executed", not executor.executions and not attempts,
                            f"工具尝试 {len(attempts)} 次，真执行 {len(executor.executions)} 次"))
        if spec.name in {"knowledge", "concept", "knowledge_followup"}:
            checks.append(check("knowledge_answer",
                                mentions_all(first_report, spec.must_mention),
                                f"命中 {mentions_any(first_report, spec.must_mention)}"
                                f"/{list(spec.must_mention)}"))
            conflicts = answer_contract.knowledge_issues(spec.goal, first_report)
            checks.append(check('known_knowledge_conflict', not conflicts, '; '.join(conflicts)))
        if spec.name == "no_data":
            honest = bool(re.search(r"没有|无|空|请先|需要先|打开|选中|缺少|找不到", report)) and \
                bool(re.search(r"对象|音|录音|声音", report))
            checks.append(check("honest_no_measurement", honest and not results,
                                f"结果行 {len(results)} 条；正文提到缺失/下一步：{honest}"))
            checks.append(check("no_fabricated_measurement", not re.search(r"实测|测量结果[:：]?\s*\d", report),
                                f"出现实测声明：{bool(re.search(r'实测', report))}"))
    else:
        checks.append(check("registered_tool_used",
                            any(item["tool"] != tools.CUSTOM_SCRIPT_TOOL for item in ok_attempts),
                            "工具：" + ", ".join(sorted({item["tool"] for item in attempts})) or "（没有调用）"))
        checks.append(check("tool_executed", bool(ok_attempts),
                            f"成功执行 {len(ok_attempts)}/{len(attempts)} 次；真 Praat 执行 {len(executor.executions)} 次"))
        checks.append(check("praat_completed",
                            bool(executor.executions) and all(item["done"] for item in executor.executions),
                            f"{sum(1 for item in executor.executions if item['done'])}/{len(executor.executions)} 个脚本写出完成标记"))
        if spec.band is not None:
            hits = values_in_band(results, spec.band)
            checks.append(check("measured_frequency_range", bool(hits),
                                f"窗口 {spec.band}；实测 {parse_hz_values(results)[:6]}"))
            # 交付正文必须自己带上这个实测值（不是只有结果行里有）。
            checks.append(check("report_cites_measurement",
                                bool(values_in_band([report], spec.band)),
                                f"正文里的 Hz 值 {parse_hz_values([report])[:6]}"))
        if spec.name == "duration":
            durations = [value for value in parse_seconds_values(results)
                         if abs(value - EXPECTED_DURATION) <= DURATION_TOLERANCE]
            checks.append(check("measured_duration", bool(durations),
                                f"期望 {EXPECTED_DURATION}s；实测 {parse_seconds_values(results)[:6]}"))
            checks.append(check("report_cites_duration",
                                any(abs(value - EXPECTED_DURATION) <= DURATION_TOLERANCE
                                    for value in parse_seconds_values([report])),
                                f"正文里的秒值 {parse_seconds_values([report])[:6]}"))
        if spec.name == "whole":
            used_selection = any("按编辑器圈选" in row for row in results)
            checks.append(check("full_range_respected", not used_selection and bool(results),
                                "命中编辑器旧选区" if used_selection else "没有按旧选区收窄"))
        if spec.expects_two_objects:
            primary = values_in_band(results, PITCH_BAND)
            reference = values_in_band(results, REFERENCE_BAND)
            checks.append(check("both_objects_measured", bool(primary) and bool(reference),
                                f"220 附近 {primary[:3]}；200 附近 {reference[:3]}"))
        if spec.name == "mixed":
            checks.append(check("capability_mentioned",
                                bool(re.search(r"能|可以|支持", report)) and bool(mentions_any(report, spec.must_mention)),
                                f"命中 {mentions_any(report, spec.must_mention)}"))

    auto_passed = all(item["ok"] for item in checks)
    record: dict[str, Any] = {
        "case": spec.name,
        "goal": spec.goal,
        "route": spec.route,
        "dialogue_kind": kind,
        "note": spec.note,
        "status": state.status,
        "report": report[:REPORT_LIMIT],
        "attempts": attempts,
        "evidence": [{"tool": evidence.tool, "rows": list(evidence.rows)[:8]}
                     for evidence in (state.evidence or [])][:8],
        "metrics": _metrics_projection(state, sends, recent),
        "requests": int(getattr(state, "requests", 0) or 0),
        "actual_http_count": int(sends),
        "executions": executor.executions,
        "progress": [_scrub_text(line, workdir, secrets)[:200] for line in progress_lines][:12],
        "streamed_text_chars": len("".join(deltas)),
        "checks": checks,
        "auto_passed": auto_passed,
        "manual_review": {
            "status": "pending",
            "reason": "自然性、语气与教学有效性必须人工读正文判断；长度或关键词命中不算通过。",
            "items": ["是否像人说的话（不生硬、不模板腔）", "纠正/结论是否有依据且不夸大", "是否回答了用户真正问的那件事"],
        },
        "coverage_gaps": _coverage_gaps(checks),
        "error": error,
        "elapsed_seconds": elapsed,
    }
    if followup is not None:
        record["rounds"] = _rounds_projection(spec, followup, report, workdir, secrets)
    if not auto_passed:
        record["failed_checks"] = [item["id"] for item in checks if not item["ok"]]
    return record


def _metrics_projection(state: AnalysisState, sends: int,
                        statuses: Sequence[int] = ()) -> dict[str, Any]:
    metrics = dict(getattr(state, "metrics", {}) or {})
    timings = list(metrics.get("request_timings") or [])
    phases = metrics.get("phases") or {}
    return {
        "requests": int(getattr(state, "requests", 0) or 0),
        "actual_http_count": int(sends),
        "http_statuses": [int(status) for status in statuses][-12:],
        "timings_count": len(timings),
        "retry_timings": sum(1 for item in timings if item.get("retry")),
        "total_seconds": metrics.get("total_seconds"),
        "first_text_seconds": metrics.get("first_text_seconds"),
        "finish_state": metrics.get("finish_state"),
        "route_reason": metrics.get("route_reason"),
        "output_tokens": metrics.get("output_tokens"),
        'request_timings': [{key: item.get(key) for key in (
            'request', 'phase', 'status', 'http_status', 'retry', 'elapsed_seconds',
            'input_tokens_total', 'cache_read_tokens', 'cache_write_tokens', 'output_tokens')}
            for item in timings],
        'phases': {name: {key: value.get(key) for key in (
            'requests', 'retries', 'elapsed_seconds', 'input_tokens_total', 'cache_read_tokens',
            'cache_write_tokens', 'observable_requests', 'cache_coverage')}
            for name, value in phases.items()},
        "guard_blocks": metrics.get("guard_blocks") or {},
        "guard_repairs": metrics.get("guard_repairs") or {},
        "report_verified": metrics.get("report_verified"),
        "effective_thinking_level": metrics.get("effective_thinking_level"),
        'knowledge_issues': metrics.get('knowledge_issues') or [],
        'knowledge_delivery': metrics.get('knowledge_delivery'),
    }


def _rounds_projection(spec: CaseSpec, data: Mapping[str, Any], report: str,
                       workdir: Path, secrets: Sequence[str]) -> list[dict[str, Any]]:
    """多轮案例的分轮记录：每轮的目标、状态、正文长度与真实 HTTP 次数。"""

    first_state = data.get("first_state")
    second_state = data.get("state")
    return [
        {
            "round": 1,
            "kind": data.get("first_kind"),
            "goal": spec.goal,
            "status": getattr(first_state, "status", ""),
            "requests": int(getattr(first_state, "requests", 0) or 0),
            "actual_http_count": int(data.get("first_sends") or 0),
            "streamed_text_chars": len(str(data.get("first_stream") or "")),
            "report": _scrub_text(str(data.get("first_report") or ""), workdir, secrets)[:REPORT_LIMIT],
            "request_messages": len(data.get("first_messages") or []),
        },
        {
            "round": 2,
            "kind": data.get("second_kind"),
            "goal": spec.followup_goal,
            "status": getattr(second_state, "status", ""),
            "requests": int(getattr(second_state, "requests", 0) or 0),
            "actual_http_count": int(data.get("second_sends") or 0),
            "streamed_text_chars": len(str(data.get("second_stream") or "")),
            "report": report[:REPORT_LIMIT],
            "request_messages": len(data.get("second_messages") or []),
            "delivery_note_expected": bool(data.get("note_present")),
            "history_messages": len(data.get("history") or []),
        },
    ]


def _coverage_gaps(checks: Sequence[Mapping[str, Any]]) -> list[str]:
    return [str(item["id"]) for item in checks if not item["ok"]]


def _scrub_text(text: str, workdir: Path, secrets: Sequence[str] = ()) -> str:
    """去掉不该出现在输出里的东西：凭证、临时目录绝对路径。"""

    body = str(text or "")
    for secret in secrets:
        if secret and len(secret) >= 4:
            body = body.replace(secret, "[redacted]")
    if workdir:
        body = body.replace(str(workdir), "<tmp>").replace(str(workdir).replace("\\", "/"), "<tmp>")
    return body

# ---------------------------------------------------------------------------
# dry-run / 主流程
# ---------------------------------------------------------------------------

def describe_environment(config) -> dict[str, Any]:
    """环境摘要：不含 key、不含 base URL。"""

    info: dict[str, Any] = {
        "python": sys.version.split()[0],
        "executable": Path(sys.executable).name,
        "api_active": api_is_active(config),
        "model": getattr(config.api, "model", ""),
        "thinking_level": getattr(config.api, "thinking_level", ""),
        "request_timeout_sec": getattr(config.api, "request_timeout_sec", None),
        "max_context_tokens": getattr(config.api, "max_context_tokens", None),
        "plan_max_tokens": getattr(config.api, "plan_max_tokens", None),
        "audio_input_enabled": getattr(config.api, "audio_input_enabled", None),
        "workflow_preset": getattr(config.workflow, "preset", None) if config.workflow else None,
    }
    try:
        import numpy

        info["numpy"] = numpy.__version__
    except ImportError:  # pragma: no cover - 依赖缺失要如实报
        info["numpy"] = None
    try:
        import parselmouth

        info["parselmouth"] = getattr(parselmouth, "VERSION", None) or getattr(parselmouth, "__version__", "?")
    except ImportError:  # pragma: no cover
        info["parselmouth"] = None
    return info


def selected_cases(names: Sequence[str] | None) -> list[CaseSpec]:
    if not names:
        return [CASES[name] for name in CASE_ORDER]
    ordered = [name for name in CASE_ORDER if name in set(names)]
    return [CASES[name] for name in ordered]


def print_plan(cases: Sequence[CaseSpec], config, ledger_summary: Mapping[str, Any],
               output: Path) -> None:
    environment = describe_environment(config)
    print("AI 后端真实验收 — dry-run（不会发任何请求）")
    print(f"· Python {environment['python']}；numpy={environment['numpy']}；"
          f"parselmouth={environment['parselmouth']}")
    print(f"· 模型：{environment['model'] or '（未填）'}；思考档位：{environment['thinking_level']}；"
          f"API 可用：{environment['api_active']}")
    print(f"· 临时音频：1 秒 {TONE_HZ:.0f} Hz 正弦 + 1 秒 {REFERENCE_HZ:.0f} Hz 参考音"
          f"（{SAMPLE_RATE} Hz 采样，全部在 PI_SCRATCH_DIR 的临时目录里生成）")
    print(f"· 账本：{ledger_summary['path']}"
          f"（已用 {ledger_summary['attempts_total']}/{ledger_summary['limit']}，"
          f"余 {ledger_summary['remaining']}，历史运行 {ledger_summary['runs']} 次）")
    print(f"· 报告会写到：{output}")
    print("· 案例：")
    total_low = total_high = 0
    # 多轮案例的名字更长：列宽按最长名字对齐，别把目标挤到下一行看不清。
    name_width = max([10] + [len(spec.name) for spec in cases])
    for spec in cases:
        estimate = CASE_ESTIMATE[spec.name]
        low, _, high = estimate.partition("–")
        try:
            total_low += int(low.strip())
            total_high += int(str(high).replace("次", "").split("（")[0].strip() or low)
        except ValueError:
            pass
        print(f"    - {spec.name:<{name_width}} {spec.route:<8} {estimate:<8} {spec.goal}")
        print(f"      {spec.note}")
    print(f"· 预计真实请求：{total_low}–{total_high} 次（受账本余额 {ledger_summary['remaining']} 硬限制）")
    print("· 自然性判定：待人工复核（脚本只做自动检查，不用长度/关键词冒充通过）")
    print("要真的发（会产生费用）请加 --send。")


def run_selected(cases: Sequence[CaseSpec], *, config, output: Path,
                 ledger: SendLedger) -> tuple[int, dict[str, Any]]:
    from praat_ai.cloud_runtime import CloudRuntime

    runtime = CloudRuntime(model_factory=model_factory)
    records: list[dict[str, Any]] = []
    counters = new_counters()
    # 所有临时文件都在 PI_SCRATCH_DIR 下的一个临时目录里，跑完自动删。
    base = default_state_directory()
    base.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="ai-dialogue-acceptance-", dir=str(base)) as raw:
            for spec in cases:
                if ledger.remaining <= 0 or counters["limit_hit"]:
                    print(f"· 账本额度不足，跳过 {spec.name}（已用 {ledger.attempts_total}/{ledger.limit}）")
                    continue
                if counters["violation"]:
                    break
                print(f"· 跑 {spec.name} …", flush=True)
                workdir = Path(raw) / spec.name
                workdir.mkdir(parents=True, exist_ok=True)
                with install_send_guard(ledger, counters, spec.name):
                    record = run_case(spec, config=config, runtime=runtime,
                                      ledger=ledger, counters=counters, workdir=workdir)
                records.append(record)
                status = "OK  " if record["auto_passed"] else "FAIL"
                print(f"  {status} {spec.name}：status={record['status']}，"
                      f"请求 {record['requests']} 次 / 真实 HTTP {record['actual_http_count']} 次，"
                      f"工具执行 {len(record['executions'])} 次，{record['elapsed_seconds']}s")
                for item in record["checks"]:
                    if not item["ok"]:
                        print(f"       × {item['id']}：{item['detail']}")
    finally:
        runtime.close(wait=True)

    summary = {
        "cases_run": [record["case"] for record in records],
        "cases_passed": sum(1 for record in records if record["auto_passed"]),
        "cases_failed": [record["case"] for record in records if not record["auto_passed"]],
        "skipped": [spec.name for spec in cases if spec.name not in {r["case"] for r in records}],
        "limit_hit": bool(counters["limit_hit"]),
        "violation": counters["violation"],
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "mode": "send",
        "environment": describe_environment(config),
        "ledger": ledger.summary(),
        "cases": records,
        "summary": summary,
        "manual_review": {
            "status": "pending",
            "reason": "自动检查只覆盖实测数值、真实工具调用与泄漏；自然性必须人工读正文。",
        },
        "hint": CASE_ESTIMATE_HINT,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n· 报告：{output}")
    print(f"· 账本：{ledger.path}（本次累计 {ledger.attempts_total}/{ledger.limit}）")
    print(f"· 自动检查通过 {summary['cases_passed']}/{len(records)} 个案例")
    print("· 自然性：待人工复核（pending）——自动检查通过不代表回答像人说的话")
    if summary["limit_hit"]:
        print('· 注意：账本额度用尽，剩下的案例没有跑；需要获得新的调用授权后再继续。')
    if summary["violation"]:
        print(f"· 注意：{summary['violation']}（验收要求只允许一路发送）")
    return (0 if not summary["cases_failed"] and not summary["limit_hit"]
            and not summary["violation"] else 1), report


def _positive_limit(value: str) -> int:
    try:
        number = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("--max-requests 必须是整数") from error
    if number < 1:
        raise argparse.ArgumentTypeError("--max-requests 至少为 1")
    if number > MAX_REQUESTS_CAP:
        raise argparse.ArgumentTypeError(
            f"--max-requests 不允许大于 {MAX_REQUESTS_CAP}（现在给的是 {number}）")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_ai_dialogue_acceptance.py",
        description="AI 后端真实验收：默认 dry-run，只有 --send 才真的发请求。",
    )
    parser.add_argument("--send", action="store_true",
                        help="真的向配置好的云端模型发请求（会产生费用）")
    parser.add_argument("--case", action="append", dest="cases", choices=list(CASE_ORDER),
                        metavar="CASE", help=f"只跑指定案例，可重复；可选：{', '.join(CASE_ORDER)}")
    parser.add_argument("--output", type=Path, default=None,
                        help="把 JSON 报告写到这里（默认写到 PI_SCRATCH_DIR 下）")
    parser.add_argument("--ledger", type=Path, default=None,
                        help="持久发送计数文件（重复运行不重置）")
    parser.add_argument("--max-requests", type=_positive_limit, default=DEFAULT_MAX_REQUESTS,
                        help=f"账本累计上限，默认 {DEFAULT_MAX_REQUESTS}，不允许大于 {MAX_REQUESTS_CAP}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    options = parser.parse_args(list(argv) if argv is not None else None)

    if threading.current_thread() is not threading.main_thread():
        print("云端请求只允许从主线程发出：这个脚本不能在后台线程里跑。")
        return 2

    cases = selected_cases(options.cases)
    ledger_path = options.ledger or default_ledger_path()
    output = options.output or default_output_path()
    config = load_config()

    if not options.send:
        print_plan(cases, config, read_ledger_summary(ledger_path, options.max_requests), output)
        return 0

    if not api_is_active(config):
        print("当前不是 API 模式：先在「API 配置…」里启用云端模型（脚本不改你的配置）。")
        return 2
    if not native_praat.executable().is_file():
        print("需要原生 Praat；请通过 PRAAT_AI_PRAAT_EXECUTABLE 指定项目构建程序。")
        return 2
    missing = describe_environment(config).get("parselmouth")
    if not missing:
        print("这个解释器没有 praat-parselmouth：实测必须真算，不能跳过。"
              "改用装了它的 3.12 解释器（见 guide.md）。")
        return 2

    ledger = SendLedger(ledger_path, options.max_requests)
    if ledger.remaining <= 0:
        print(f"账本额度已用尽（{ledger.attempts_total}/{ledger.limit}）：{ledger.path}\n"
              "这个文件不会自动重置；要新额度请换一个 --ledger。")
        return 2
    print(f"· 开始：{len(cases)} 个案例；账本 {ledger.attempts_total}/{ledger.limit}"
          f"（余 {ledger.remaining}）；模型 {getattr(config.api, 'model', '')}")
    code, report = run_selected(cases, config=config, output=output, ledger=ledger)
    ledger.record_run({
        "at": report["generated_at"],
        "cases": report["summary"]["cases_run"],
        "passed": report["summary"]["cases_passed"],
        "failed": report["summary"]["cases_failed"],
        "sends": sum(record["actual_http_count"] for record in report["cases"]),
        "limit_hit": report["summary"]["limit_hit"],
    })
    return code


if __name__ == "__main__":
    raise SystemExit(main())
