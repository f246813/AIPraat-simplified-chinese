"""Praat AI 对话窗口。

前端流程：

1. 读取 Praat 写出的对象列表（``runtime/chat_context.tsv``）。
2. Qwen 只负责选择工具并填参数，脚本由 :mod:`praat_ai.tools` 用固定模板生成。
3. 脚本通过 :mod:`praat_ai.sendpraat` 送到正在运行的 Praat 里执行（自己写
   ``Message.txt`` 再发 ``WM_APP``，不激活 Praat 的任何窗口）。
4. 脚本把数值结果写进 ``runtime/chat_result.tsv``，执行完成写
   ``runtime/chat_state.txt``；对话窗口轮询到完成标记后把结果读回来显示。

每条指令一个脚本文件（``runtime/commands/chat_command_<编号>.praat``）并且脚本开头
先写下自己的编号（``runtime/chat_started.txt``）：Praat 的 ``Message.txt`` 是全局
唯一的，超时指令还可能压在消息队列里，靠这两样才能保证「投递出去的那条」和
「真的执行了的那条」对得上（见 guide.md §8.5）。

注意：``An instance of Praat that is not me is already running.`` 是
``--send`` 转发脚本时的常规提示，不是错误，所以不显示给用户。
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
import time
import tkinter as tk
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from tkinter import ttk
from typing import Any, Callable, Mapping

from . import (
    api_settings,
    delivery,
    escape_policy,
    execution_core,
    escape_policy,
    parent_watch,
    praat_app,
    progress_popup,
    qwen,
    report_guards,
    sendpraat,
    tools,
    vot_request,
    ui_theme,
    ui_widgets,
    ui_windows,
)
from .config import api_is_active, default_config_path as config_path, load_config
from .presets import PresetError, active_preset, list_presets
from .server import QwenServerError, running_model_info


SEND_NOISE = (
    "An instance of Praat that is not me is already running.",
)

EXECUTION_TIMEOUT_SEC = 25.0

#: ``runtime/commands/`` 里最多留几个脚本文件（旧的才删，见 :func:`prune_command_scripts`）。
MAX_COMMAND_SCRIPTS = 40

#: 超过这个岁数的旧脚本文件才允许删：``Message.txt`` 里可能还引用着最近几个。
COMMAND_SCRIPT_TTL_SEC = 3600.0

COMPOSER_THINKING_CHOICES = (
    ("自动", "auto"),
    ("关闭", "off"),
    ("低", "low"),
    ("中", "medium"),
    ("高", "high"),
)


def context_ping_script() -> str:
    """一条不需要任何对象的脚本：Praat 每执行完一条命令都会刷新对象列表文件，
    所以用它既能确认 Praat 还在响应，又能把 ``chat_context.tsv`` 更新到最新。

    只能写文件：``appendInfoLine`` 这类命令会走 ``gui_information()``，把
    「Praat Info」窗口弹出来（这就是用户报的那个 bug，见 guide.md §8.5）。
    """

    return f'appendFileLine: {tools.quote(state_path())}, "done"\n'


def ai_directory() -> Path:
    """前端自己的目录（Praat 消息里的工作目录，相对路径按它解析）。"""

    return Path(__file__).resolve().parents[1]


def runtime_dir() -> Path:
    path = Path(__file__).resolve().parents[1] / "runtime"
    path.mkdir(parents=True, exist_ok=True)
    return path


def context_path() -> Path:
    return runtime_dir() / "chat_context.tsv"


def result_path() -> Path:
    return runtime_dir() / "chat_result.tsv"


def state_path() -> Path:
    return runtime_dir() / "chat_state.txt"


def script_path(request_id: str = "") -> Path:
    """这次请求的脚本路径。

    不带编号时返回老路径 ``runtime/chat_command.praat``（只用于兼容旧调用和排障）。
    带上编号就每条指令一个文件：``Message.txt`` 全局唯一，队列里可能还压着上一条
    超时消息；如果所有指令共用一个文件名，那条旧消息醒来时执行的会是**刚写进去的
    新脚本**。文件名分开之后，旧消息最多把它自己那条再跑一遍（用户要求的操作，
    而且 :func:`_wait_for_result` 能从编号看出来），不会把新指令执行成别的东西。
    """

    if not request_id:
        return runtime_dir() / "chat_command.praat"
    return command_dir() / f"chat_command_{request_id}.praat"


def command_dir() -> Path:
    path = runtime_dir() / "commands"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_request_id() -> str:
    """一条指令的编号（用它区分脚本文件、完成标记是谁写的）。"""

    return uuid.uuid4().hex[:12]


def request_id_from_path(path: Path | str) -> str:
    """从脚本文件名反推请求编号（诊断和单测用）；不是这种名字就返回空串。"""

    name = Path(path).name
    if name.startswith("chat_command_") and name.endswith(".praat"):
        return name[len("chat_command_") : -len(".praat")]
    return ""


def started_marker_path() -> Path:
    return runtime_dir() / "chat_started.txt"


def finished_marker_path() -> Path:
    return runtime_dir() / "chat_finished.txt"


def failure_path() -> Path:
    """Praat 报错时写下的原始错误（见 ``PraatAiControl_reportChatScriptFailure``）。

    脚本自己报错时 Praat **不再弹模态错误框**（那会挡住后面所有消息），而是把错误
    写到这里 + 结果文件里，并补一句完成标记；前端看到这个文件就把这一条当失败，
    而不是把错误行当成正常结果。
    """

    return runtime_dir() / "chat_failure.txt"


def script_preamble(request_id: str) -> str:
    """脚本开头那句「我这条指令开始执行了」。

    完成标记（``chat_state.txt``）里只有 ``done``，光看它分不清是谁写的。脚本一
    开始就把编号写进 ``chat_started.txt``，等待完成时对一下编号，就能认出「上一条
    超时指令刚刚才跑完」这种情况，而不是把它的数值当成本次结果（见
    :func:`_wait_for_result`）。
    """

    if not request_id:
        return ""
    return (
        f"# praat-ai 请求 {request_id}\n"
        f'appendFileLine: {tools.quote(str(started_marker_path()))}, "started {request_id}"\n'
    )


def prune_command_scripts() -> int:
    """删掉又老又旧的脚本文件，返回删了几个。

    只删「数量超出 :data:`MAX_COMMAND_SCRIPTS` **而且** 超过
    :data:`COMMAND_SCRIPT_TTL_SEC`」的那些：``Message.txt`` 里可能还引用着最近
    几个，删早了会让残留消息找不到文件（Praat 会弹一句「文件不存在」的错误框）。
    """

    try:
        entries = sorted(
            command_dir().glob("chat_command_*.praat"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return 0
    removed = 0
    cutoff = time.time() - COMMAND_SCRIPT_TTL_SEC
    for index, path in enumerate(entries):
        if index < MAX_COMMAND_SCRIPTS:
            continue
        try:
            if path.stat().st_mtime >= cutoff:
                continue
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def send_log_path() -> Path:
    return runtime_dir() / "chat_send.log"


def praat_executable() -> str:
    """正在跑的那个 Praat 的可执行文件；找不到返回空字符串。

    具体去哪儿找见 :mod:`praat_ai.praat_app`（环境变量 → 仓库根目录 → ``PATH``
    → 常见安装位置，带缓存和 30 秒重试）。
    """

    return praat_app.find_praat()


def object_context() -> str:
    path = context_path()
    if not path.is_file():
        return "（未读取到对象列表）"
    try:
        return path.read_text(encoding="utf-8").strip() or "（对象列表为空）"
    except OSError as error:
        return f"（读取对象列表失败：{error}）"


#: Praat 写在对象列表文件末尾的进程标记（见 sys/PraatAiControl.cpp）。
CONTEXT_PID_PATTERN = re.compile(r"^#\s*praat-pid=(\d+)\s*$", re.MULTILINE)


def context_pid(text: str | None = None) -> int:
    """对象列表是哪一次 Praat 进程写的（没有标记返回 0）。

    有了它，前端就不必每条消息都先投一条空脚本「刷一遍」对象列表（C5）：标记就是
    当前这个 Praat，说明文件是最新的。对方的 Praat 不写标记（老版本、或者别人的
    Praat），返回值是 0，调用方会退回「刷一次」的老行为。

    ``text=None``（默认）时读 ``chat_context.tsv``；传字符串时只解析这份文本。
    """

    if text is None:
        text = object_context()
    match = CONTEXT_PID_PATTERN.search(text or "")
    return int(match.group(1)) if match else 0


def selected_object_label() -> str:
    rows = tools.parse_object_context(object_context())
    if not rows:
        return "当前对象：（Praat 对象列表为空）"
    row = next((item for item in rows if item.selected), rows[-1])
    marker = "已选中" if row.selected else "最近对象"
    return f"当前对象：{row.name}（id {row.id}，{marker}）"


def _decode_console(data: bytes) -> str:
    if not data:
        return ""
    for encoding in ("utf-8", "utf-16-le", "gbk"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        if "\x00" not in text:
            return text
    return data.decode("utf-8", errors="replace").replace("\x00", "")


def _clean_send_output(text: str) -> str:
    lines = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if any(noise in line for noise in SEND_NOISE):
            continue
        lines.append(line)
    return "\n".join(lines)


def _clear_result_files() -> None:
    """每次投递前清掉上一次的结果和完成标记（等待时只看新的那个）。"""

    for path in (result_path(), state_path(), started_marker_path(), failure_path(), finished_marker_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _blocked_reason() -> str:
    return (
        f"Praat 在 {EXECUTION_TIMEOUT_SEC:.0f} 秒内没有执行这个脚本，"
        "也可能已经跑完但完成标记没能写回来。常见原因：Praat 里有没关掉的对话框或"
        "错误提示、编辑器正在播放或被模态窗口挡住。关掉那些窗口后再发一次即可；"
        "下一次请求会先做一次显式核对，不会把上一次的结果算到新请求上。"
    )


def _cancelled_reason() -> str:
    return (
        "已取消：不再等这一条的结果。脚本可能已经在 Praat 里跑完了，"
        "结果文件里会有它的输出。"
    )


def _read_started_marker() -> str:
    """最后一次开始执行的请求编号（``chat_started.txt`` 里最后一个 ``started`` 行）。"""

    try:
        lines = started_marker_path().read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        text = line.strip()
        if text.startswith("started "):
            return text.split(" ", 1)[1].strip()
    return ""


def _completion_state(request_id: str) -> tuple[bool, str]:
    """``chat_state.txt`` 出现没有？是不是**本次**请求写的？"""

    if not state_path().is_file():
        return False, ""
    if not request_id:
        return True, ""
    started = _read_started_marker()
    if started == request_id:
        if "# praat-protocol=2" in object_context():
            try:
                finished = finished_marker_path().read_text(encoding="utf-8").splitlines()
            except OSError:
                return False, ""
            if not finished or finished[-1].strip() != f"finished {request_id}":
                return False, ""
        return True, ""
    return False, (
        f"上一条超时指令（编号 {started or '未知'}）刚刚才执行完，"
        "已丢掉它的完成标记和结果，继续等本次指令。"
    )


def _discard_stale_output() -> None:
    for path in (state_path(), result_path()):
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def _wait_for_result(
    process: subprocess.Popen[bytes] | None,
    *,
    request_id: str = "",
    cancel: threading.Event | None = None,
) -> tuple[bool, str]:
    """轮询 ``chat_state.txt``；``process`` 只在那条 ``--send`` 兜底路径里非空。

    ``cancel``（C7）是对话窗口「停止」按钮置的那个事件：一置上就不再等，并且把
    排队中的 WM_APP 换成空脚本（否则它以后醒来会执行**下一条**指令的脚本）。
    """

    deadline = time.monotonic() + EXECUTION_TIMEOUT_SEC
    notes = ""
    while time.monotonic() < deadline:
        ready, stale_note = _completion_state(request_id)
        if ready:
            if process is not None:
                _close_send_process(process)
            return True, notes.strip()
        if cancel is not None and cancel.is_set():
            # 结果已经到了就不打断（上面的 ready 分支已经返回）；这里说明还没来。
            ready, _ = _completion_state(request_id)
            if ready:
                if process is not None:
                    _close_send_process(process)
                return True, notes.strip()
            if process is not None:
                _close_send_process(process)
            else:
                sendpraat.cancel_pending()
            return False, _join_notes(notes, _cancelled_reason())
        if stale_note:
            # 完成标记是别人的：连同它的结果一起丢掉，继续等本次指令。
            notes = stale_note
            _discard_stale_output()
        if process is not None and process.poll() is not None:
            break
        time.sleep(0.15)

    output = _clean_send_output(_read_send_log()) if process is not None else ""
    ready, _ = _completion_state(request_id)
    if ready:
        if process is not None:
            _close_send_process(process)
        return True, notes.strip()
    if process is None:
        # 自己投递时没有「发送进程」可看：这条 WM_APP 还排在 Praat 的消息队列里，
        # 得先把消息文件换成空脚本，否则它醒来时执行的会是下一条指令的脚本。
        # （正常情况下脚本开头那句「消息自消费」已经做过这件事，这里只是兜底。）
        sendpraat.cancel_pending()
        return False, _join_notes(notes, _blocked_reason())
    if process.poll() is None:
        _close_send_process(process)
        return False, _join_notes(output, notes, _blocked_reason())
    return False, _join_notes(output, notes)


def _join_notes(*parts: str) -> str:
    return "\n".join(part.strip() for part in parts if part and part.strip())


def render_action(
    action: Mapping[str, Any], context: tools.ToolContext
) -> tuple[str, str]:
    """渲染一个动作，返回 ``(脚本, 给用户的提示)``；提示为空表示没什么要说的。

    工具自己报错（参数不合法、对象找不到）而模型又同时给了 ``script`` 时，用模型
    那条脚本兜底，并把这件事告诉用户——这比直接失败好，也比悄悄换一条路好。
    """

    tool_name = str(action.get("tool", "") or "").strip()
    arguments = action.get("arguments") or {}
    custom_script = str(action.get("script", "") or "")
    if tool_name == tools.CUSTOM_SCRIPT_TOOL:
        return (
            tools.render(
                tool_name,
                {},
                context,
                custom_script=str(arguments.get("script", "") or custom_script),
            ),
            "",
        )
    try:
        return tools.render(tool_name, arguments, context), ""
    except tools.ToolError as error:
        if not custom_script.strip():
            raise
        return (
            tools.render(
                tools.CUSTOM_SCRIPT_TOOL,
                {},
                context,
                custom_script=custom_script,
            ),
            f"工具 {tool_name} 无法执行（{error}），改用模型给出的脚本。",
        )


#: 一次用户请求最多让模型规划几轮（每轮可以执行 1–3 个动作）。
#:
#: 一轮结束会把执行结果**回灌**给模型，让它接着做下一步或者把结果讲清楚；到上限
#: 还在调工具时就强制要一次纯文本回答。3 轮是权衡：多步请求够用，最坏情况也只多花
#: 一两次小模型调用（实测一次调用 0.3–1 秒）。
MAX_AGENT_ROUNDS = 3

#: 一次用户请求最多**实际执行**几个动作。小模型在多轮里会退化成重复调用同一个
#: 工具（实测「截取 0.2–0.5 秒」被它连做三遍），所以除了轮数还要卡总步数。
MAX_AGENT_STEPS = 5
API_AGENT_ROUNDS = 8
API_AGENT_STEPS = 20

# 分析所需的对象准备允许 API 模型在后续轮次完成。
ANALYSIS_PREPARATION_TOOLS = frozenset({
    "select_object", "extract_part", "duplicate_object", "spectrogram", "view_edit",
})


def _whole_recording_request(user_text: str) -> bool:
    """用户这句话是不是要求对**整段**对象做分析（而不是编辑器里圈出来的那一小段）。"""

    for match in re.finditer(
            r"整段|整个(?:声音|语音|音频|录音|波形)"
            r"|全部(?:声音|语音|音频)"
            r"|(?:the\s+)?(?:whole|entire|full)\s+(?:sound|audio|recording|utterance|file)",
            user_text,
            re.I,
        ):
        # Negation belongs to this clause, not another requested operation.
        prefix = re.split(r'[，,。；;！？!?\n]', user_text[:match.start()])[-1]
        negated = re.search(
            r'(?:不要|不用|不必|无需|别|而不是|而非|不是|并非|不需要).{0,24}$'
            r'|\b(?:not|do not|don[\'’]t|instead of|rather than)\b.{0,40}$', prefix, re.I)
        if not negated:
            return True
    return False


def _request_context(text: str, user_text: str) -> str:
    """整段请求覆盖编辑器旧选区；工具显式时间参数仍由工具层处理。

    对象列表是 6 列 TSV（``id / class / name / selected / sel_start / sel_end``）；
    只把后两列清空，别动前面的列。列数正好 6、且第一列是数字的行才会被改，其余行
    原样保留——行数、顺序、制表符和末尾换行都不变，所以 ``parse_object_context()``
    仍然读得出一模一样的对象，只是没有圈选。
    """

    if not _whole_recording_request(user_text):
        return text
    lines = []
    for line in text.splitlines():
        fields = line.split("\t")
        if len(fields) == 6 and fields[0].isdigit():
            fields[4:6] = ["", ""]
        lines.append("\t".join(fields))
    stripped = "\n".join(lines)
    # splitlines() 会把末尾换行吃掉，这里补回来，避免调用方拿到的文本长度悄悄变短。
    return stripped + "\n" if text.endswith("\n") else stripped


@dataclass(slots=True)
class AgentStep:
    """一次工具执行及回灌观察。execution 记录 delivery 模块的投递事实；空值表示执行器没有声明，调用方只能按说明判断。未投递与已投递但结果不明必须区分。"""

    round_index: int
    tool: str
    arguments: dict[str, Any]
    ok: bool
    observation: str
    script: str = ""
    results: list[str] = field(default_factory=list)
    note: str = ""
    execution: str = ""


@dataclass(slots=True)
class TurnOutcome:
    """一轮用户请求的结果（可能包含多步执行）。"""

    reply: str = ""
    results: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    steps: list[AgentStep] = field(default_factory=list)
    used_tools: bool = False
    failure: str = ""
    #: 最终交付的证据守卫状态（见 :func:`_guard_local_delivery`）：
    #: ``verified`` / ``rejected_masked`` / ``no_evidence`` / ``empty``。
    #: 空串表示这一轮没走到交付守卫（例如直接返回的固定提示）。
    delivery_guard: str = ""
    #: 这轮正文的来源：``model``（模型写的）／``summary``（本地用实测结果行拼的摘要）／
    #: ``static``（程序文案，例如取消、超时）。只有 ``model`` 的正文才是「模型的主张」，
    #: 才需要拿证据核对：实测结果行本身不是需要核对的主张。
    reply_source: str = ""

    @property
    def last_script(self) -> str:
        for step in reversed(self.steps):
            if step.script:
                return step.script
        return ""


def local_evidence_state(context_text: str, outcome: TurnOutcome, user_text: str) -> escape_policy.AnalysisState:
    """本机循环里用来核对最终正文的证据视图。

    证据的折法现在与云端共用 ``execution_core``（同一个 kind 口径：操作类不是测量），
    两条路径核对的是同一种东西，不再各写一份。
    """

    state = execution_core.evidence_from_steps(context_text, outcome.steps,
                                               is_mutating=tools.is_mutating)
    state.goal = str(user_text or "")
    return state


def _guard_local_delivery(outcome: TurnOutcome, *, context_text: str, user_text: str) -> None:
    """本机最终交付前走与云端同一份证据守卫（``report_guards.guard_delivered_text``）。

    本机规划器过去把模型写好的正文直接交付，云端报告已有的数值核对在本机路径上
    完全不生效：实测里证据只有 220 Hz 时，模型写的 999 Hz 照样到了用户面前。

    没有测量证据时不核对数值——没有被核对的对象，状态如实记为 ``no_evidence``，
    而不是把它算成通过。核对通过/被遮蔽分别记 ``verified`` / ``rejected_masked``。
    """

    reply = str(outcome.reply or "")
    if not reply.strip():
        outcome.delivery_guard = "empty"
        return
    if outcome.reply_source != "model":
        # 本地摘要/程序文案不是模型的主张：结果行是实测原文，遮蔽它等于丢证据。
        outcome.delivery_guard = "not_model_text"
        return
    state = local_evidence_state(context_text, outcome, user_text)
    if not report_guards.report_context_data(state):
        outcome.delivery_guard = "no_evidence"
        return
    text, status = report_guards.guard_delivered_text(state, reply)
    outcome.delivery_guard = status
    if text != reply:
        outcome.reply = text
        outcome.notes.append(
            "交付前数值核对：对不上本次实测的数值已标成 "
            + report_guards.UNVERIFIED_NUMBER
            + "，结论文字保留。"
        )


def _short_arguments(arguments: Mapping[str, Any], *, maximum: int = 60) -> str:
    if not arguments:
        return ""
    text = ", ".join(f"{key}={value}" for key, value in arguments.items())
    return text if len(text) <= maximum else text[: maximum - 1] + "…"


def _progress_text(step: AgentStep) -> str:
    head = step.tool + (f"（{_short_arguments(step.arguments)}）" if step.arguments else "")
    if step.ok:
        detail = "；".join(step.results) or "已执行"
        return f"第 {step.round_index} 轮：{head} → {detail}"
    return f"第 {step.round_index} 轮：{head} 未完成 → {step.observation}"


def _summary_of(outcome: TurnOutcome) -> str:
    """模型没给出文字时的兜底回答。"""

    if outcome.results:
        return "结果：" + "；".join(outcome.results) + "\n（模型这次没有给出说明。）"
    if outcome.failure:
        return "执行未完成：" + outcome.failure
    # 空响应需要明确说明没有回答或操作，不能显示完成。
    return (
        "模型这次没有返回任何内容：既没有回答，也没有给出要执行的操作。"
        "把要求再说具体一点（例如指明对象、时间点、要分析的量）会更稳。"
    )


def _action_signature(action: Mapping[str, Any]) -> str:
    """动作签名（工具名 + 参数），用来认出重复调用。"""

    return json.dumps(
        [str(action.get("tool", "")), action.get("arguments") or {}],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


#: 用户明确说了「还有下一步」的说法。第二轮之后要动对象，必须出现其中之一。
_FOLLOWUP_WORDS = (
    "然后",
    "再",
    "接着",
    "之后",
    "并且",
    "同时",
    "顺便",
    "第一步",
    "第二步",
    "先",
)


def wants_second_step(user_text: str) -> bool:
    """用户这句话是不是明确要求「不止一步」。

    「把前 0.3 秒截出来，**然后**把它改名」是两步；「打开这个声音的编辑器」只有一步。
    小模型（尤其 0.8B 预设）在第二轮之后爱顺手多做几步（实测会做频谱图、换掉选中
    对象），所以只有用户自己说要接着做，才允许后续轮次动对象。
    """

    text = (user_text or "").strip()
    return any(word in text for word in _FOLLOWUP_WORDS)


def _execute_action(
    action: Mapping[str, Any],
    context: tools.ToolContext,
    execute: Callable[[str], tuple[bool, list[str], str]],
    round_index: int,
    environment: tools.LocalEnvironment | None = None,
) -> AgentStep:
    """执行一个动作，把它变成一条可以回灌给模型的观察结果。

    工具自己拒绝请求（参数不合法、对象找不到）时**不再往上抛**：那是一条「这个动作
    没做成，原因是……」的观察结果，模型看到之后可以改参数重试（见 A4 的取舍）。

    ``environment`` 是给**本地工具**（例如 ``tools.RUN_SCRIPT_TOOL``）用的：
    「渲染脚本 → 投递」那套对它们不适用，它们要自己起批处理进程；没给环境时
    只回一句「这次没有可用的执行环境」，而不是崩掉（测试里直接调也会走到这）。
    """

    tool_name = str(action.get("tool", "") or "").strip()
    arguments = action.get("arguments") or {}
    if action.get("invalid_arguments"):
        return AgentStep(round_index, tool_name, {}, False, f"工具 {tool_name} 没有执行：{action['invalid_arguments']}")
    if not isinstance(arguments, Mapping):
        return AgentStep(round_index, tool_name, {}, False, f"工具 {tool_name} 没有执行：工具参数必须为 JSON 对象。")
    # 本地工具（跑现成 .praat 脚本那种）不走「渲染脚本 → 投递」，先分流。
    if tool_name in tools.LOCAL_TOOLS:
        return _execute_local_action(
            tool_name, arguments, context, environment, round_index
        )
    try:
        script, note = render_action(action, context)
    except tools.ToolError as error:
        observation = f"工具 {tool_name} 没有执行：{error}"
        return AgentStep(round_index, tool_name, dict(arguments), False, observation)
    ok, results, failure = execute(script)
    if tool_name == "vot":
        # Script completion alone is not measurement success (e.g. no burst).
        measured = any(
            re.match(r"^VOT(?: 估计值)?\s*=\s*[+-]?\d+(?:\.\d+)?\s*秒", line)
            for line in results
        )
        if ok and measured:
            results = [*results, "计算公式：VOT（毫秒）=（浊音起始时刻（秒）− 爆破时刻（秒））× 1000。"]
        else:
            ok = False
            failure = "VOT 测量未完成：" + (failure or "；".join(results) or "脚本没有返回 VOT 数值。")
    if ok:
        detail = "；".join(results) if results else "（脚本跑完了，但没有输出结果行）"
        observation = f"工具 {tool_name} 执行成功，结果：{detail}"
    else:
        observation = f"工具 {tool_name} 执行失败：{failure}"
    return AgentStep(
        round_index,
        tool_name,
        dict(arguments),
        ok,
        observation,
        script,
        results,
        note,
    )


def _execute_local_action(
    tool_name: str,
    arguments: Mapping[str, Any],
    context: tools.ToolContext,
    environment: tools.LocalEnvironment | None,
    round_index: int,
) -> AgentStep:
    """跑一个**本地工具**：它自己在 Python 这边干活（例如起批处理跑现成脚本）。

    和模板工具一样，工具自己拒绝请求时只回一条「没有执行 + 原因」的观察结果；
    ``environment`` 缺失（有人在测试里直接调这个函数）时说清楚，而不是崩。
    """

    local = tools.LOCAL_TOOLS[tool_name]
    if environment is None:
        observation = (
            f"工具 {tool_name} 没有执行：它要在对话窗口里跑，这次没有可用的执行环境。"
        )
        return AgentStep(round_index, tool_name, dict(arguments), False, observation)
    try:
        ok, results, failure = local.run(arguments, context, environment)
    except tools.ToolError as error:
        observation = f"工具 {tool_name} 没有执行：{error}"
        return AgentStep(round_index, tool_name, dict(arguments), False, observation)
    if ok:
        detail = "；".join(results) if results else "（工具跑完了，但没有输出结果行）"
        observation = f"工具 {tool_name} 执行成功，结果：{detail}"
    else:
        observation = f"工具 {tool_name} 执行失败：{failure}"
    return AgentStep(
        round_index,
        tool_name,
        dict(arguments),
        ok,
        observation,
        "",
        results if ok else [],
        "",
    )


def _run_agent_turn(
    planner,
    *,
    context: tools.ToolContext,
    execute: Callable[[str], tuple[bool, list[str], str]],
    on_progress: Callable[[str], None] | None = None,
    max_rounds: int = MAX_AGENT_ROUNDS,
    max_steps: int = MAX_AGENT_STEPS,
    allow_followup_mutations: bool = False,
    analysis_preparation: bool = False,
    refresh_context: Callable[[], str] | None = None,
    environment: tools.LocalEnvironment | None = None,
    cancel: threading.Event | None = None,
) -> TurnOutcome:
    """循环本体：规划 → 执行 → 回灌 → 再规划（planner 负责具体接口）。

    两道防线对付小模型的退化行为（实测会重复调用同一个工具）：
    ① 同一个动作（工具名 + 参数完全相同）只执行一次，重复的调用只回灌一条
       「这一步刚才已经做过」；
    ② 一次请求最多实际执行 :data:`MAX_AGENT_STEPS` 个动作。

    还有一道针对「顺手多做」的护栏：用户没明确说要接着做时（见
    :func:`wants_second_step`），第二轮之后不许再执行会改动对象的工具
    （``tools.MUTATING_TOOLS``）——只读查询不受限制。
    """

    outcome = TurnOutcome()
    executed: dict[str, AgentStep] = {}
    unresolved: dict[str, str] = {}
    steps_done = 0
    stopped_early = False
    cancelled = False
    for round_index in range(1, max_rounds + 1):
        if cancel is not None and cancel.is_set():
            # 用户在等上一轮结果时按了「停止」：不再规划、不再执行。
            cancelled = True
            break
        try:
            actions, reply = planner.next()
        except qwen.TruncatedResponseError as error:
            outcome.failure = str(error)
            outcome.reply = "模型回答未完成：" + str(error)
            outcome.reply_source = "static"
            return outcome
        if not actions:
            if not reply:
                # 模型既没调工具、又没写正文（实测思考型模型会把话全放在
                # reasoning_content 里，或者干脆回空）：再问它一次要一句正文，
                # 别让界面用一句兜底话糊过去。
                ask = getattr(planner, "ask_for_text", None)
                if ask is not None:
                    try:
                        reply = ask()
                    except qwen.TruncatedResponseError as error:
                        outcome.failure = str(error)
                        outcome.reply = "模型回答未完成：" + str(error)
                        outcome.reply_source = "static"
                        return outcome
            outcome.reply = reply or _summary_of(outcome)
            outcome.reply_source = "model" if reply else "summary"
            return outcome
        for action_index, action in enumerate(actions):
            if cancel is not None and cancel.is_set():
                cancelled = True
                for pending in actions[action_index:]:
                    planner.observe(pending, "已取消，未执行此工具调用。")
                break
            signature = _action_signature(action) + repr(context.objects)
            tool_name = str(action.get("tool", "") or "")
            if signature in executed:
                observation = (
                    "这一步刚才已经执行过，没有重复执行。"
                    f"上一次的结果：{executed[signature].observation}"
                )
                step = AgentStep(
                    round_index,
                    tool_name,
                    dict(action.get("arguments") or {}),
                    executed[signature].ok,
                    observation,
                    "",
                    [],
                    "重复调用被跳过。",
                )
                outcome.steps.append(step)
                outcome.notes.append(f"跳过一次重复调用：{tool_name}")
                planner.observe(action, observation)
                continue
            if (
                round_index > 1
                and not allow_followup_mutations
                and tools.is_mutating(tool_name)
                and not (analysis_preparation and tool_name in ANALYSIS_PREPARATION_TOOLS)
            ):
                # 用户这句话没有要求第二步，而这个动作会改动对象：不做。
                observation = (
                    f"没有执行这个动作：{tool_name} 会改动 Praat 里的对象，"
                    "而用户这次只要求做一件事。请直接用上面已经拿到的结果回答；"
                    "如果确实还需要这一步，就在回答里说明需要做什么。"
                )
                step = AgentStep(
                    round_index,
                    tool_name,
                    dict(action.get("arguments") or {}),
                    True,
                    observation,
                    "",
                    [],
                    f"拦下了一次多余的操作：{tool_name}（用户没有要求第二步）",
                )
                outcome.steps.append(step)
                outcome.notes.append(step.note)
                planner.observe(action, observation)
                continue
            # 步数上限与预算判定走 execution_core，和云端路径同一口径。
            if execution_core.loop_decision(execution_core.LoopSignals(
                    tool_attempts=steps_done, tool_limit=max_steps)) == execution_core.BUDGET:
                stopped_early = True
                for pending in actions[action_index:]:
                    planner.observe(pending, f"已达到本次操作 {max_steps} 步的上限，此调用未执行。请根据已有实测结果回答。")
                break
            step = _execute_action(
                action, context, execute, round_index, environment
            )
            steps_done += 1
            executed[signature] = step
            outcome.steps.append(step)
            outcome.used_tools = True
            if step.ok:
                outcome.results.extend(step.results)
                unresolved.pop(tool_name, None)
            else:
                unresolved[tool_name] = step.observation
                # 失败也可能带回结果行：脚本在 Praat 里报错时，Praat 会把错误原文
                # 写进结果文件（不再弹模态框），那一行要显示给用户看。
                outcome.results.extend(step.results)
            outcome.failure = "；".join(unresolved.values())
            if step.note:
                outcome.notes.append(step.note)
            planner.observe(action, step.observation)
            if on_progress is not None:
                on_progress(_progress_text(step))
            if refresh_context is not None:
                try:
                    fresh_text = refresh_context()
                    if not fresh_text.splitlines() or not fresh_text.splitlines()[0].startswith("id\tclass\tname\tselected"):
                        raise tools.ToolError("没有读到完整的对象列表。")
                    context = replace(context, objects=tools.parse_object_context(fresh_text))
                    update = getattr(planner, "update_context", None)
                    if update is not None:
                        update(fresh_text)
                except (OSError, tools.ToolError) as error:
                    outcome.failure = f"对象状态刷新失败，已停止后续操作：{error}"
                    for pending in actions[action_index + 1:]:
                        planner.observe(pending, outcome.failure + "；此调用未执行。")
                    stopped_early = True
                    break
        if stopped_early or cancelled:
            break
    if cancelled:
        outcome.notes.append("已取消：后面的步骤没有再执行。")
        outcome.reply = "已取消这次操作（已经执行完的那几步结果还在下面）。"
        outcome.reply_source = "static"
        return outcome
    # 收尾失败不能丢失已执行结果：重试一次，再失败使用本地摘要并记录原因。
    for attempt in range(2):
        try:
            wrapped = planner.wrap_up()
            outcome.reply = wrapped or _summary_of(outcome)
            outcome.reply_source = "model" if wrapped else "summary"
            break
        except qwen.TruncatedResponseError as error:
            outcome.failure = str(error)
            outcome.reply = "模型回答未完成：" + str(error)
            outcome.reply_source = "static"
            break
        except qwen.QwenError as error:
            if attempt == 0:
                outcome.notes.append(f"收尾回答失败，正在重试一次：{error}")
                continue
            outcome.notes.append(
                f"收尾回答两次都没成功（{error}），下面用本次实测结果拼的摘要。"
            )
            outcome.reply = _summary_of(outcome)
            outcome.reply_source = "summary"
    return outcome


class _NativePlanner:
    """原生工具调用：自己维护 messages，逐轮把 tool 结果塞回去。"""

    def __init__(
        self,
        client: qwen.QwenClient,
        *,
        user_text: str,
        context_text: str,
        history: list[dict[str, str]],
        result_path: str,
        state_path: str,
    ) -> None:
        self.client = client
        self.result_path = result_path
        self.state_path = state_path
        self.messages: list[dict[str, Any]] = [
            {
                "role": "system",
                "content": qwen.planner_instructions(self.client.config)
                + "\n\n"
                + tool_context_text(context_text, result_path, state_path),
            }
        ]
        self.messages.extend(qwen.history_messages(history))
        self.messages.append({"role": "user", "content": user_text})
        self.turn_start = len(self.messages) - 1

    def update_context(self, text: str) -> None:
        self.messages[0]["content"] = qwen.planner_instructions(self.client.config) + "\n\n" + tool_context_text(text, self.result_path, self.state_path)

    def fit_messages(self, *, with_tools: bool = True) -> None:
        """云端逐轮核算预算，保留所有 tool_call_id 配对；完整结果仍留在界面与文件。"""
        if self.client.config.token_mode is not None:
            schema_cost = qwen.estimate_tokens(json.dumps(tools.tool_schemas(), ensure_ascii=False)) if with_tools else 0
            reserve = self.client.config.plan_max_tokens if self.client.config.token_mode != 'provider' else 512
            if qwen.estimate_tokens(json.dumps(self.messages, ensure_ascii=False)) + schema_cost + reserve >= self.client.config.max_context_tokens:
                raise qwen.QwenError('本轮工具上下文超过真实窗口；保留证据并停止，未静默裁剪历史')
            return
        if self.client.config.provider != "api" or not self.client.config.limit_tokens:
            return
        schema_cost = qwen.estimate_tokens(json.dumps(tools.tool_schemas(), ensure_ascii=False)) if with_tools else 0
        available = self.client.config.max_context_tokens - self.client.config.plan_max_tokens - qwen.SAFETY_TOKENS - schema_cost
        def cost() -> int:
            return qwen.estimate_tokens(json.dumps(self.messages, ensure_ascii=False))
        if cost() <= available:
            return
        if self.turn_start > 1:
            del self.messages[1:self.turn_start]
            self.turn_start = 1
        for message in self.messages:
            if cost() <= available:
                return
            if message.get("role") != "tool":
                continue
            original = str(message.get("content", ""))
            if len(original) < 200:
                continue
            lo, hi = 80, len(original)
            marker = "\n（工具结果因上下文预算截短；缺失部分不得推测，请缩小查询范围。）\n"
            while lo < hi:
                middle = (lo + hi + 1) // 2
                message["content"] = original[:middle] + marker
                if cost() <= available:
                    lo = middle
                else:
                    hi = middle - 1
            message["content"] = original[:lo] + marker
        if cost() > available:
            raise qwen.QwenError("API 上下文预算不足以保留工具调用和结果，请在 API 设置中增大上下文窗口或缩小分析范围。")

    def next(self) -> tuple[list[dict[str, Any]], str]:
        self.fit_messages()
        message = self.client.chat_message(
            self.messages,
            tools=tools.tool_schemas(),
            max_tokens=self.client.config.plan_max_tokens,
            temperature=self.client.config.plan_temperature,
        )
        actions = qwen.extract_tool_actions(message)
        # 使用统一正文提取，content 为空时只取明确的最终答案。
        content = qwen.message_text(message).strip()
        if not actions:
            # 没开 --jinja 时小模型偶尔把调用写成正文里的 <tool_call> 文本；
            # 那种情况也要当工具调用执行，而不是把一坨 XML 当成回答显示给用户。
            actions, content = qwen.parse_text_tool_calls(content)
        if actions:
            # 回灌形状必须和这次响应一致：assistant(tool_calls) + 每条 tool 结果。
            # 正文只留 XML 之外的文字，别把工具调用文本也塞回历史里（会把模型带偏）。
            self.messages.append(qwen.assistant_tool_message(message, actions, content))
        return actions, content

    def observe(self, action: Mapping[str, Any], observation: str) -> None:
        call_id = str(action.get("id", "") or "call-1")
        self.messages.append(qwen.tool_result_message(call_id, observation))

    def ask_for_text(self) -> str:
        """模型没给正文时再问一次（不要工具，只要一句话中文回答）。"""

        self.messages.append(
            {
                "role": "user",
                "content": (
                    "请直接用中文把你的回答写出来：回答上面的问题；做不到就说清缺什么。"
                    "不要留空，也不要只写「已完成」。"
                ),
            }
        )
        self.fit_messages(with_tools=False)
        message = self.client.chat_message(
            self.messages,
            max_tokens=self.client.config.plan_max_tokens,
            temperature=self.client.config.plan_temperature,
        )
        return qwen.message_text(message).strip()

    def wrap_up(self) -> str:
        self.fit_messages(with_tools=False)
        text = self.client.chat(
            self.messages,
            max_tokens=self.client.config.plan_max_tokens,
            temperature=self.client.config.plan_temperature,
        ).strip()
        # 收尾这轮也可能又写工具调用文本：那些不执行了，也不能显示给用户。
        _actions, leftover = qwen.parse_text_tool_calls(text)
        return leftover


class _JsonPlanner:
    """老的提示词接口：把观察结果写成一条「上一轮执行结果」再问一次。"""

    def __init__(
        self,
        client: qwen.QwenClient,
        *,
        user_text: str,
        context_text: str,
        history: list[dict[str, str]],
        result_path: str,
        state_path: str,
    ) -> None:
        self.client = client
        self.user_text = user_text
        self.context_text = context_text
        self.history = list(history)
        self.result_path = result_path
        self.state_path = state_path
        self.observations: list[str] = []

    def update_context(self, text: str) -> None:
        self.context_text = text

    def observed_history(self) -> list[dict[str, str]]:
        history = list(self.history)
        if self.observations:
            history.append({"role": "user", "content": "上一步的执行结果：\n" + "\n".join(self.observations)
                            + "\n根据这些结果回答用户，需要继续分析时再选工具。"})
        return history

    def next(self) -> tuple[list[dict[str, Any]], str]:
        history = self.observed_history()
        plan = self.client.plan_praat_command(
            self.user_text,
            self.context_text,
            history,
            tool_catalog=tools.catalog_text(),
            result_path=self.result_path,
            state_path=self.state_path,
        )
        return tools.plan_actions(plan), str(plan.get("reply", "") or "").strip()

    def observe(self, action: Mapping[str, Any], observation: str) -> None:
        tool_name = str(action.get("tool", "") or "")
        self.observations.append(f"{tool_name}：{observation}")

    def ask_for_text(self) -> str:
        """模型没给 reply 时再问一次（同一个 JSON 接口，只要 reply）。"""

        plan = self.client.plan_praat_command(
            self.user_text + "\n（请直接在 reply 里用中文回答，不要选工具，也不要留空。）",
            self.context_text,
            self.observed_history(),
            tool_catalog=tools.catalog_text(),
            result_path=self.result_path,
            state_path=self.state_path,
        )
        return str(plan.get("reply", "") or "").strip()

    def wrap_up(self) -> str:
        return ""


def tool_context_text(
    object_context: str, result_path: str, state_path: str
) -> str:
    """现场信息：结果文件、完成标记、当前选中、对象列表。"""

    return "\n".join(
        [
            f"结果文件（脚本把数值结果写到这里）：{result_path}",
            f'完成标记文件（脚本最后一行必须是 appendFileLine: "{state_path}", "done"）：'
            f"{state_path}",
            "",
            qwen.selected_object_hint(object_context),
            "",
            "当前 Praat 对象列表（id、类、名称、是否选中）：",
            object_context,
        ]
    )


def run_turn(
    client: qwen.QwenClient,
    *,
    user_text: str,
    context_text: str,
    history: list[dict[str, str]],
    context: tools.ToolContext,
    execute: Callable[[str], tuple[bool, list[str], str]],
    on_progress: Callable[[str], None] | None = None,
    native: bool | None = None,
    max_rounds: int | None = None,
    max_steps: int | None = None,
    refresh_context: Callable[[], str] | None = None,
    environment: tools.LocalEnvironment | None = None,
    cancel: threading.Event | None = None,
) -> TurnOutcome:
    """跑一轮用户请求：先规划，执行，把结果回灌，再规划（见 ``MAX_AGENT_ROUNDS``）。

    ``execute(脚本)`` 由调用方提供，返回 ``(是否成功, 结果行, 失败说明)``——对话窗口
    用它投递给正在运行的 Praat，验证脚本用它跑批处理。

    ``cancel``（C7）是「停止」按钮的事件；``context_text`` / ``history`` 会按
    token 预算裁一遍（A5），被省掉的部分写进 ``TurnOutcome.notes``。
    """

    if cancel is not None and cancel.is_set():
        return TurnOutcome(reply="已取消这次操作。")
    context_text = _request_context(context_text, user_text)
    context = replace(context, objects=tools.parse_object_context(context_text))
    api_mode = client.config.provider == "api"
    round_limit = max_rounds if max_rounds is not None else (API_AGENT_ROUNDS if api_mode else MAX_AGENT_ROUNDS)
    step_limit = max_steps if max_steps is not None else (API_AGENT_STEPS if api_mode else MAX_AGENT_STEPS)
    vot_arguments = vot_request.direct_arguments(user_text)
    if vot_arguments is not None:
        try:
            if "object" in vot_arguments:
                requested = next((row for row in context.objects if row.id == vot_arguments["object"]), None)
                if requested is None:
                    raise tools.ToolError(f"对象列表里没有编号 {vot_arguments['object']}。")
                allowed = frozenset({"Sound", "TextGrid"}) if "burst" in vot_arguments else tools.SOUND_CLASSES
                context.require_class(requested, allowed, "指定对象不支持这次 VOT 测量")
            times = [value for key, value in vot_arguments.items() if key != "object"]
            if any(not 0 <= value <= 36000 for value in times):
                raise tools.ToolError("时间必须在 0–36000 秒范围内。")
            if "from" in vot_arguments and vot_arguments["to"] - vot_arguments["from"] < 0.002:
                raise tools.ToolError("搜索范围的终点必须晚于起点，范围至少为 2 毫秒。")
            if "burst" in vot_arguments or "voicing" in vot_arguments:
                if not {"burst", "voicing"} <= vot_arguments.keys():
                    raise tools.ToolError("需要同时给出 burst（爆破）和 voicing（浊音起始）两个时刻。")
                if vot_arguments["voicing"] <= vot_arguments["burst"]:
                    raise tools.ToolError("浊音起始必须晚于爆破时刻；未改用自动检测。")
            if not {"burst", "voicing", "from", "to"} & vot_arguments.keys():
                row = context.resolve_by_class(vot_arguments.get("object"), tools.SOUND_CLASSES, "自动检测 VOT 需要 Sound")
                if row.selection is not None and row.selection[1] - row.selection[0] < 0.002:
                    raise tools.ToolError("编辑器选区不足 2 毫秒，请扩大选区到爆破和浊音起始附近。")
            if "选区" in user_text:
                row = context.resolve_by_class(vot_arguments.get("object"), tools.SOUND_CLASSES, "按选区测量需要 Sound")
                if row.selection is None:
                    raise tools.ToolError("没有有效的编辑器选区，请先在波形上圈选爆破到浊音起始附近的范围。")
        except tools.ToolError as error:
            message = "VOT 测量未完成：" + str(error)
            return TurnOutcome(reply=message, failure=message)
        # The known command has one required result; no model can substitute
        # metadata, invent landmarks, or stop before the measurement happens.
        step = _execute_action(
            {"tool": "vot", "arguments": vot_arguments}, context, execute, 1, environment
        )
        if on_progress is not None:
            on_progress(_progress_text(step))
        failure_detail = step.observation.partition("：")[2] or step.observation
        failure_reply = failure_detail if failure_detail.startswith("VOT 测量未完成") else "VOT 测量未完成：" + failure_detail
        return TurnOutcome(
            reply="\n".join(step.results) if step.ok else failure_reply,
            results=step.results,
            steps=[step],
            used_tools=bool(step.script),
            failure="" if step.ok else step.observation,
        )

    use_native = (
        qwen.planner_mode() != qwen.JSON_MODE if native is None else native
    )
    schemas = tools.tool_schemas() if use_native else []
    # 先扣掉工具 schema、系统提示、用户这句话和回答预留，剩下的才是
    # 「现场信息 + 对话历史」能用的额度；对象列表最多拿三分之一（它得让模型看到
    # 当前选中，但不该把历史挤光）。
    # 老的 JSON 规划把工具清单写在提示词里，那份开销也要算进去。
    instructions = (
        qwen.planner_instructions(client.config) if use_native else tools.catalog_text()
    )
    if client.config.limit_tokens and client.config.token_mode is None:
        budget = qwen.history_budget(
            client.config.max_context_tokens,
            instructions=instructions,
            tool_schemas=schemas,
            user_text=user_text,
            response_tokens=client.config.plan_max_tokens,
        )
        trimmed_context, hidden_rows = qwen.trim_object_context(
            context_text, max_tokens=max(120, budget // 3)
        )
        trimmed_history, dropped_turns = qwen.trim_history(
            history, max_tokens=max(0, budget - qwen.estimate_tokens(trimmed_context))
        )
    else:
        trimmed_context, hidden_rows = context_text, 0
        trimmed_history, dropped_turns = qwen.history_messages(history), 0
    notes: list[str] = []
    if hidden_rows:
        notes.append(
            f"对象列表太长，这次只列了一部分给模型（还有 {hidden_rows} 个没列出）。"
            "先说清要操作哪个对象更稳。"
        )
    if dropped_turns:
        overhead = qwen.estimate_tokens(instructions) + qwen.estimate_tokens(
            json.dumps(schemas, ensure_ascii=False)
        )
        notes.append(
            f"对话太长：这一轮只带了最近 {len(trimmed_history)} 条历史"
            f"（省掉更早的 {dropped_turns} 条）。本机上下文窗口 "
            f"{client.config.max_context_tokens}，工具说明加系统提示约占 "
            f"{overhead} token——想多带历史可以在模型预设里把 context_tokens 调大。"
        )
    planner_class = _NativePlanner if use_native else _JsonPlanner
    planner = planner_class(
        client,
        user_text=user_text,
        context_text=trimmed_context,
        history=trimmed_history,
        result_path=str(context.result_path),
        state_path=str(context.state_path),
    )
    outcome = _run_agent_turn(
        planner,
        context=context,
        execute=execute,
        on_progress=on_progress,
        max_rounds=round_limit if use_native or api_mode else min(round_limit, 2),
        max_steps=step_limit,
        allow_followup_mutations=wants_second_step(user_text),
        analysis_preparation=api_mode,
        refresh_context=(lambda: _request_context(refresh_context(), user_text)) if refresh_context is not None else None,
        environment=environment,
        cancel=cancel,
    )
    _guard_local_delivery(outcome, context_text=context_text, user_text=user_text)
    outcome.notes[:0] = notes
    return outcome


def _send_script(
    executable: str,
    script: str,
    *,
    request_id: str = "",
    cancel: threading.Event | None = None,
    process_id: int | None = None,
    outcome: list[str] | None = None,
) -> tuple[bool, str]:
    with sendpraat.command_lock():
        return _send_script_unlocked(executable, script, request_id=request_id,
                                     cancel=cancel, process_id=process_id, outcome=outcome)


def _send_script_unlocked(
    executable: str,
    script: str,
    *,
    request_id: str = "",
    cancel: threading.Event | None = None,
    process_id: int | None = None,
    outcome: list[str] | None = None,
) -> tuple[bool, str]:
    """Hand the script to the running Praat and wait for its result files.

    默认走 :mod:`praat_ai.sendpraat`：自己写 ``Message.txt`` 再发 ``WM_APP``。
    这样 Praat 一个窗口都不会被激活——用户报的「弹出 Praat Info」和「声音窗口
    盖住对话窗口」都是老的 ``Praat.exe --send`` 干的（见 guide.md §8.5）。

    设 ``PRAAT_AI_SEND_MODE=argv`` 可以退回 ``--send`` 排障：那条路会激活一个
    Praat 子窗口，而且 ``--send`` 在 Praat 被模态窗口挡住时会一直阻塞（实测
    超过 60 秒，脚本其实已经排队），所以老路径仍然用「后台 Popen + 轮询」，
    不回到 ``subprocess.run(timeout=60)``。

    每条指令写一个自己的脚本文件，并在开头带上请求编号（:func:`script_preamble`）：
    这是「投递出去的那条」和「真的执行了的那条」对得上的凭据。

    ``process_id`` 指定送给哪一个 Praat（默认最新打开的那个，和 ``--send`` 一致）；
    验证脚本用它把自己开的那个实例和用户开着的实例区分开。

    ``outcome``（可选）是**结构化的投递事实**出口：本函数往里追加一格
    ``DELIVERED`` 或 ``NOT_DELIVERED``（见 :mod:`praat_ai.delivery`）。失败时只
    看返回值分不出「根本没送出去」和「送出去了没等到结果」，而这两件事的处理完全
    不同（前者可以改参数重试，后者必须停下来核实）。
    """

    request_id = request_id or new_request_id()
    target = script_path(request_id)
    _clear_result_files()
    target.write_text(script_preamble(request_id) + script, encoding="utf-8")
    prune_command_scripts()
    if sendpraat.send_mode() == sendpraat.ARGV_MODE:
        return _send_script_via_argv(executable, target, request_id, cancel=cancel, outcome=outcome)
    delivered, note = sendpraat.deliver(ai_directory(), target, process_id=process_id)
    if not delivered:
        if outcome is not None:
            outcome.append(delivery.NOT_DELIVERED)
        return False, note
    if outcome is not None:
        outcome.append(delivery.DELIVERED)
    return _wait_for_result(None, request_id=request_id, cancel=cancel)


def _send_script_via_argv(
    executable: str,
    target: Path,
    request_id: str = "",
    *,
    cancel: threading.Event | None = None,
    outcome: list[str] | None = None,
) -> tuple[bool, str]:
    """排障兜底：``Praat.exe --FULL-TRUST --send``（会激活 Praat 的一个子窗口）。"""

    creation_flags = 0
    if os.name == "nt":
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_path = send_log_path()
    try:
        log_path.unlink()
    except FileNotFoundError:
        pass
    try:
        with log_path.open("wb") as log_handle:
            process = subprocess.Popen(
                [executable, "--FULL-TRUST", "--send", str(target)],
                cwd=ai_directory(),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creation_flags,
            )
    except OSError as error:
        if outcome is not None:
            outcome.append(delivery.NOT_DELIVERED)
        return False, f"调用 Praat 失败：{error}"
    if outcome is not None:
        outcome.append(delivery.DELIVERED)
    return _wait_for_result(process, request_id=request_id, cancel=cancel)


def _read_send_log() -> str:
    try:
        data = send_log_path().read_bytes()
    except OSError:
        return ""
    return _decode_console(data)


def _close_send_process(process: subprocess.Popen[bytes]) -> None:
    """收掉发送进程：脚本已经交给 Praat 之后它就没事可做了。"""

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


def _read_results() -> list[str]:
    path = result_path()
    if not path.is_file():
        return []
    try:
        # 按 errors='replace' 读（和 _read_failure 一致）：结果文件里混进一个坏字节时，
        # 只按 UTF-8 替换解码，避免一个非法字节让已经确认的投递事实
        # 变成无法归因的失败。
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return [line.strip() for line in text.splitlines() if line.strip()]


def _read_failure() -> str:
    """Praat 写下的脚本错误（没有就返回空串）。"""

    path = failure_path()
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""


def praat_process_ids(executable: str = "Praat.exe") -> list[int] | None:
    """正在运行的 Praat 进程 id；``None`` 表示查不到（不要据此拦截用户）。

    对话窗口把脚本交给「已经开着的 Praat」；如果 Praat 其实没开，
    ``Praat.exe --send`` 会另起一个空实例，脚本必然找不到对象，用户还要白等
    一次超时。而 ``--send`` 只能把脚本交给最新的那个 Praat，所以同时开多个
    实例时也要提醒用户，免得脚本跑进了错误的窗口。
    """

    if os.name != "nt" or not executable:
        return None
    name = Path(executable).name
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {name}", "/FO", "CSV", "/NH"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=15,
            creationflags=creation_flags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or "").strip()
    if not output or output.upper().startswith("INFO"):
        return []
    ids: list[int] = []
    for line in output.splitlines():
        parts = [item.strip().strip('"') for item in line.split('","')]
        if len(parts) >= 2 and parts[1].isdigit():
            ids.append(int(parts[1]))
    return ids


def praat_process_running_from(process_ids: list[int] | None) -> bool:
    """根据调用方已经取得的进程列表判断 Praat 是否运行，避免重复查询。"""

    if process_ids is None:
        return True
    return bool(process_ids)


def praat_instance_warning_from(process_ids: list[int] | None) -> str:
    """按已经查好的进程列表给「多个 Praat」的提醒。"""

    if not process_ids or len(process_ids) < 2:
        return ""
    return (
        f"检测到 {len(process_ids)} 个 Praat 在运行：脚本只会送给最新打开的那个"
        "窗口。请关掉多余的 Praat，否则可能操作到别的对象列表。"
    )


def praat_process_running(executable: str) -> bool:
    return praat_process_running_from(praat_process_ids(executable))


def praat_instance_warning(executable: str) -> str:
    """同时开着多个 Praat 时返回一句提醒，否则返回空字符串。"""

    return praat_instance_warning_from(praat_process_ids(executable))


def api_choice_label(config) -> str:
    """API 模式下模型预设下拉框的显示文字，使用有效云端模型名。"""

    model = str(getattr(config.api, "model", "") or "").strip()
    name = str(getattr(config.api, "label", "") or "").strip()
    parts = [part for part in (name, model) if part]
    if not parts:
        return "云端 API：未配置"
    return "云端 API：" + " / ".join(parts)


def local_preset_allowed(config) -> bool:
    """云端锁定时禁选本地；云端在用且本机服务被停用时也禁选。"""

    return not (config.api.locked or (api_is_active(config) and config.api.stop_local_service))


def model_status_text(config) -> str:
    """状态栏文案：模型来自服务实际加载的模型，而不是配置里的文件名。"""

    if api_is_active(config):
        label = config.api.label or "云端 API"
        return f"模型：{config.api.model}（{label}，API 模式，不需要本机服务）"
    info = running_model_info(config.qwen.base_url)
    live = Path(str(info.get("id", ""))).name if info else ""
    configured = (
        Path(config.server.model_path).name if config.server.model_path else ""
    )
    capabilities = [str(item).casefold() for item in (info.get("capabilities") or [])]
    vision = any("multimodal" in item for item in capabilities)
    preset = active_preset(config)
    parts = [f"模型：{live or configured or '未配置'}"]
    if live and vision:
        parts.append("视觉已开")
    if live and configured and live.casefold() != configured.casefold():
        parts.append(f"配置里是 {configured}")
    if not live:
        parts.append("服务未响应")
    if preset is not None:
        parts.append(f"预设：{preset.display_name}")
    return "  |  ".join(parts)


def refresh_object_context(
    executable: str,
    process_ids: list[int] | None = None,
    *,
    force: bool = False,
    assume_fresh: bool = False,
) -> tuple[bool, str]:
    """让正在运行的 Praat 重新写一次对象列表，返回 ``(是否成功, 说明)``。

    ``chat_context.tsv`` 由 Praat 维护：Praat 重启、换会话或对象被改动之后，
    对话窗口手里的列表可能是旧的。旧 id 会让脚本报「没有编号为 1」这种看不懂
    的错误，所以每次执行前先送一条空脚本刷新，顺带确认 Praat 还在响应用户。

    **C5**：Praat 会在文件末尾写 ``# praat-pid=<进程号>``。标记就是当前这个
    Praat 时说明列表是最新的（Praat 自己会在每条 app 消息之后、以及用户改选中或
    增删对象之后重写它），这时**一条消息都不发**；只有标记对不上（Praat 重启过）、
    文件缺失、或者对方的 Praat 不写标记时才真的投一条刷新。``force=True`` 强制刷。

    ``assume_fresh=True`` 是给「老版本 Praat（不写标记）」用的同一条捷径：Praat
    每执行完一条 app 消息都会重写对象列表（``cb_userMessage()`` 里那句），所以只要
    这一轮之前已经成功投递过一条脚本、而且还是同一个 Praat 进程，列表就是新的。
    对话窗口自己记着这件事（``ChatWindow.context_ready_pids``）。
    """

    if not executable:
        return False, ""
    if process_ids is None:
        process_ids = praat_process_ids(executable)
    if not praat_process_running_from(process_ids):
        return False, ""
    if not force and context_path().is_file():
        if assume_fresh or context_belongs_to(process_ids):
            return True, ""
    ok, output = _send_script(executable, context_ping_script())
    if ok:
        return True, ""
    return False, output or "Praat 没有响应，无法刷新对象列表。"


def context_belongs_to(process_ids: list[int] | None) -> bool:
    """对象列表文件是不是这几个正在跑的 Praat 之一写的。"""

    if not process_ids:
        return False
    path = context_path()
    if not path.is_file():
        return False
    try:
        marker = context_pid(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return False
    return marker != 0 and marker in set(process_ids)


def resolve_preset_id(presets: list[dict], label: str) -> str:
    """把下拉框里的显示文本换回预设 id（标签带模型名和「视觉/纯文本」后缀）。

    下拉框的值是给人看的文本，切换之后列表会重建，所以不能只按完全相等查，
    还要能按 id、模型文件名和标签前缀兜底。
    """

    text = (label or "").strip()
    if not text:
        return ""
    for preset in presets:
        if text == preset_label_text(preset):
            return preset["id"]
    for preset in presets:
        if text in {preset["id"], preset["model"]}:
            return preset["id"]
    for preset in presets:
        if text.startswith(preset["label"] or preset["model"]):
            return preset["id"]
    return ""


def preset_label_text(preset: dict) -> str:
    """下拉框里那一行文字（不含「当前」，切换后不会变）。"""

    parts = [preset["label"] or preset["model"]]
    if preset["model"]:
        parts.append(preset["model"])
    parts.append("视觉" if preset["vision"] else "纯文本")
    if not preset["available"]:
        parts.append("文件缺失")
    return " · ".join(item for item in parts if item)


#: 「最小 Markdown」子集：只认标题、行首项目符号、**粗体**、`行内代码`。
_HEADING_PREFIXES = ("### ", "## ", "# ")
_BULLET_PREFIXES = ("- ", "* ", "• ", "· ")
_INLINE_PATTERN = re.compile(r"\*\*(.+?)\*\*|`([^`]+)`")


def _inline_segments(line: str, base: str) -> list[tuple[str, Any]]:
    """把一行的行内标记切成 ``(片段, tag)``；``base`` 是这一行所属块/行内 tag。"""

    segments: list[tuple[str, Any]] = []
    position = 0
    for match in _INLINE_PATTERN.finditer(line):
        if match.start() > position:
            segments.append((line[position : match.start()], base))
        if match.group(1) is not None:
            segments.append((match.group(1), (base, "bold") if base else "bold"))
        else:
            segments.append((match.group(2), (base, "code") if base else "code"))
        position = match.end()
    if position < len(line):
        segments.append((line[position:], base))
    return segments or [("", base)]


def render_message(text: str) -> list[tuple[str, Any]]:
    """把模型回答切成 ``(片段, tag)``，tag 可以直接喂给 ``Text.insert``。

    只认这几样：``#``/``##``/``###`` 标题、行首 ``-``/``*``/``•`` 项目符号、
    ``**粗体**``、`` `代码` ``。用户自己敲的字不渲染（原样显示），见 guide.md §8.16。
    """

    segments: list[tuple[str, Any]] = []
    lines = str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    for index, line in enumerate(lines):
        if index:
            segments.append(("\n", ""))
        stripped = line.strip()
        base = ""
        if any(stripped.startswith(prefix) for prefix in _HEADING_PREFIXES):
            body = stripped.lstrip("#").strip()
            base = "heading"
        elif any(stripped.startswith(prefix) for prefix in _BULLET_PREFIXES):
            body = "• " + stripped[2:].strip()
            base = "bullet"
        else:
            body = line.rstrip()
        segments.extend(_inline_segments(body, base))
    return segments


@dataclass
class _ProcessBlock:
    """一轮请求的灰色过程信息；正文与复制链接不属于这个折叠区。"""

    started_at: float
    header_tag: str
    body_tag: str
    end_mark: str
    finished_at: float | None = None
    collapsed: bool = False
    #: 上一次由右键切换折叠的时刻。同一次点击里如果 Tk 既投递了 ``<Button-3>``
    #: 又投递了 ``<ButtonRelease-3>``，会被这个时间戳吃掉第二次，避免「闪一下又回来」。
    last_toggle_at: float = 0.0


class ChatWindow:
    def __init__(self) -> None:
        self.root = ui_windows.create_root()
        self.root.title("Praat AI 对话")
        #: 主题：TW-Elements 的设计令牌 + 跟随系统深浅色（见 ai/praat_ai/ui_theme.py）。
        self.theme = ui_theme.Theme(self.root)
        self.root.geometry(f"{self.theme.pad(900)}x{self.theme.pad(680)}")
        self.root.minsize(self.theme.pad(680), self.theme.pad(480))
        self.root.configure(background=self.theme.color("canvas"))
        self.config = load_config()
        self.client = qwen.QwenClient(self.config.qwen)
        #: 配置文件（ai_config.json）的修改时间：别的窗口改了它我们就重新读。
        self.config_stamp = self._config_stamp()
        self.next_config_check = 0.0
        self.history: list[dict[str, str]] = []
        from .conversation_store import ConversationStore
        from .materials import cleanup_abandoned
        cleanup_abandoned(runtime_dir() / "tasks")
        self.store = ConversationStore(runtime_dir() / 'conversations.sqlite3',
                                       secrets=[self.config.api.api_key, self.config.qwen.api_key])
        self.session_id = self.store.new_session()
        self.analysis_state = None
        self.pending_analysis = None
        self.task_materials = []
        self.current_materials = None
        self._choice_tags = []
        self.messages: queue.Queue[tuple[str, str]] = queue.Queue()
        self.busy = False
        #: 点「停止」时置上；正在等待的投递、批处理和规划循环都会检查。
        self.cancel_event = threading.Event()
        #: 已经投递过脚本的 Praat 进程集合；同一进程不必重复做上下文 ping。
        self.context_ready_pids: frozenset[int] = frozenset()
        #: 加载/停止模型时的迷你进度小窗（懒创建，用完关掉）。
        self.progress_window = None
        #: 「⧉ 复制」那些文本 tag → 对应的消息原文（点一下复制整条）。
        self._copy_registry: dict[str, str] = {}
        self._process_blocks: dict[str, _ProcessBlock] = {}
        self._active_process: _ProcessBlock | None = None

        frame = tk.Frame(self.root, background=self.theme.color("canvas"))
        frame.pack(fill="both", expand=True, padx=14, pady=14)
        frame.rowconfigure(3, weight=1)
        frame.columnconfigure(0, weight=1)
        self.frame = frame

        self.presets: list[dict] = list_presets(self.config)
        self.preset_ids: dict[str, str] = {}
        # 「Praat AI  |  ……」这一条同时喂给状态栏和顶部那颗状态胶囊：标题已经写在
        # AppBar 上了，所以胶囊里只显示竖线右边那半截。
        self.status = tk.StringVar(value="Praat AI  |  " + model_status_text(self.config))
        self.status_text = tk.StringVar(value=model_status_text(self.config))
        self.app_bar = ui_widgets.Card(
            frame, self.theme, padding=(14, 10, 14, 10), radius=12
        )
        self.app_bar.grid(row=0, column=0, sticky="ew")
        tk.Label(
            self.app_bar.body,
            text="Praat AI",
            background=self.theme.color("surface"),
            foreground=self.theme.color("text"),
            font=self.theme.font("title"),
        ).pack(side="left")
        self.status_chip = ui_widgets.Chip(
            self.app_bar.body,
            self.theme,
            textvariable=self.status_text,
            kind="neutral",
            background="surface",
        )
        self.status_chip.pack(side="right")
        self.records_button = ui_widgets.RoundedButton(
            self.app_bar.body, self.theme, '查看记录', self.view_records, kind='text')
        self.records_button.pack(side='right', padx=8)

        preset_row = tk.Frame(frame, background=self.theme.color("canvas"))
        preset_row.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        preset_row.columnconfigure(1, weight=1)
        self.preset_row = preset_row
        tk.Label(
            preset_row,
            text="模型预设",
            background=self.theme.color("canvas"),
            foreground=self.theme.color("textMuted"),
            font=self.theme.font("small"),
        ).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.preset_choice = tk.StringVar(value="")
        self.preset_box = ttk.Menubutton(
            preset_row,
            textvariable=self.preset_choice,
            width=48,
            style="PraatPreset.TMenubutton",
        )
        self.preset_menu = tk.Menu(self.preset_box, tearoff=False)
        self.preset_box.configure(menu=self.preset_menu)
        self.preset_box.grid(row=0, column=1, sticky="ew")
        self.preset_button = ui_widgets.RoundedButton(
            preset_row,
            self.theme,
            "应用预设",
            self.apply_selected_preset,
            kind="filled",
        )
        self.preset_button.grid(row=0, column=2, sticky="e", padx=(8, 0))
        # API 配置：填 key 接云端大模型（更大的模型当后端），不用本地 llama-server。
        self.api_button = ui_widgets.RoundedButton(
            preset_row,
            self.theme,
            "API 配置…",
            self.open_api_settings,
            kind="outlined",
        )
        self.api_button.grid(row=0, column=3, sticky="e", padx=(6, 0))
        self.preset_hint = tk.StringVar(value="")
        self.preset_snack = ui_widgets.Snackbar(
            preset_row,
            self.theme,
            textvariable=self.preset_hint,
            kind="neutral",
            background="canvas",
            wraplength=760,
        )
        self.preset_snack.grid(row=1, column=0, columnspan=4, sticky="ew", pady=(8, 0))
        self.refresh_preset_widgets()

        self.context_label = tk.StringVar(value=selected_object_label())
        self.context_line = tk.Label(
            frame,
            textvariable=self.context_label,
            anchor="w",
            background=self.theme.color("canvas"),
            foreground=self.theme.color("textMuted"),
            font=self.theme.font("small"),
        )
        self.context_line.grid(row=2, column=0, sticky="ew", pady=(0, 10))

        transcript_frame = tk.Frame(frame, background=self.theme.color("canvas"))
        transcript_frame.grid(row=3, column=0, sticky="nsew")
        transcript_frame.rowconfigure(0, weight=1)
        transcript_frame.columnconfigure(0, weight=1)
        self.transcript_frame = transcript_frame
        self.transcript = tk.Text(
            transcript_frame,
            wrap="word",
            state="disabled",
            font=self.theme.font("body"),
            background=self.theme.color("canvas"),
            foreground=self.theme.color("text"),
            insertbackground=self.theme.color("text"),
            relief="flat",
            borderwidth=0,
            padx=2,
            pady=2,
            spacing1=0,
            spacing3=0,
        )
        self.transcript.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(
            transcript_frame,
            orient="vertical",
            command=self.transcript.yview,
        )
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.transcript.configure(yscrollcommand=scrollbar.set)
        self._configure_transcript_tags(self.theme)
        # Text 的 tag_bind 依赖上一次 Motion 的 current 标签；滚动/重绘后可能过期。
        # 在控件上接收右键，再按本次点击坐标判断标题，不依赖悬停状态。
        self.transcript.bind("<Button-3>", self._on_process_right_click)

        composer = ui_widgets.Card(frame, self.theme, padding=(12, 10, 12, 10), radius=12)
        composer.grid(row=4, column=0, sticky="ew", pady=(12, 0))
        composer_body = composer.body
        composer_body.columnconfigure(0, weight=1)
        self.composer = composer
        self.entry_field = ui_widgets.FieldCard(
            composer_body, self.theme, background="surface", radius=8
        )
        self.entry_field.grid(row=0, column=0, columnspan=5, sticky="ew")
        self.entry = tk.Text(
            self.entry_field,
            height=3,
            wrap="word",
            font=self.theme.font("body"),
            relief="flat",
            borderwidth=0,
            highlightthickness=0,
            padx=8,
            pady=6,
            background=self.theme.color("surface"),
            foreground=self.theme.color("text"),
            insertbackground=self.theme.color("text"),
        )
        self.entry_field.attach(self.entry)
        self.entry.bind("<Return>", self.on_return)
        self.entry.bind("<KP_Enter>", self.on_return)
        self.entry.bind("<Shift-Return>", self.on_shift_return)
        # 输入框下方的提示、思考强度和操作按钮共用白色区域。
        composer_actions = tk.Frame(
            composer_body, background=self.theme.color("surface")
        )
        composer_actions.grid(
            row=1,
            column=0,
            columnspan=5,
            sticky="ew",
            pady=(self.theme.pad(8), 0),
        )
        composer_actions.columnconfigure(0, weight=1)
        composer_actions.rowconfigure(0, weight=1)
        self.composer_actions = composer_actions
        self.composer_hint = tk.Label(
            composer_actions,
            text="Enter 发送，Shift+Enter 换行",
            anchor="w",
            background=self.theme.color("surface"),
            foreground=self.theme.color("textMuted"),
            font=self.theme.font("small"),
        )
        self.composer_hint.grid(row=0, column=0, sticky="w")
        self.thinking_choice_labels = {
            value: label for label, value in COMPOSER_THINKING_CHOICES
        }
        self.thinking_choice_values = {
            label: value for label, value in COMPOSER_THINKING_CHOICES
        }
        current_level = api_settings.normalize_thinking_level(
            self.config.qwen.thinking_level
        )
        self.thinking_choice = tk.StringVar(
            value=self.thinking_choice_labels[current_level]
        )
        self.thinking_label = tk.Label(
            composer_actions,
            text="思考强度：",
            anchor="e",
            background=self.theme.color("surface"),
            foreground=self.theme.color("textMuted"),
            font=self.theme.font("small"),
        )
        self.thinking_label.grid(
            row=0, column=1, sticky="e", padx=(0, self.theme.pad(4))
        )
        self.thinking_box = ttk.Combobox(
            composer_actions,
            textvariable=self.thinking_choice,
            values=[label for label, _ in COMPOSER_THINKING_CHOICES],
            state="readonly",
            width=6,
            font=self.theme.font("body"),
        )
        self.thinking_box.grid(
            row=0, column=2, sticky="e", padx=(0, self.theme.pad(24))
        )
        self.thinking_box.bind(
            "<<ComboboxSelected>>", self.on_thinking_level_selected
        )
        self.send_button = ui_widgets.RoundedButton(
            composer_actions,
            self.theme,
            "发送",
            self.submit,
            kind="filled",
        )
        self.send_button.grid(row=0, column=3, sticky="e", padx=(0, self.theme.pad(8)))
        # 等待可以取消：Praat 卡住或社区脚本耗时过长时不必干等完成标记。
        self.stop_button = ui_widgets.RoundedButton(
            composer_actions,
            self.theme,
            "停止",
            self.cancel_turn,
            kind="outlined",
        )
        self.stop_button.configure(state="disabled")
        self.stop_button.grid(row=0, column=4, sticky="e")

        self.append("assistant", "请直接用自然语言描述要执行的 Praat 操作。")
        self.append_hint(
            "例如：提取当前语音的第二共振峰带宽；把选中的声音改名为 测试；"
            "查询 0.5 秒处的基频；统计整段基频；截取 0.2–0.5 秒；"
            "把两个声音拼起来；另存为 D:/out/a.wav。"
            "接上云端大模型之后，也可以直接问语音学/声学问题（它会用自己的知识"
            "解释，测量数字仍然来自 Praat）。"
        )
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self.flush_messages)
        self.root.after(400, self.entry.focus_set)
        # 跟随启动窗口的 Praat 退出；没有原生父进程时不监视。
        self.closing = False
        self.praat_watcher = None
        if parent_watch.should_watch():
            self.praat_watcher = parent_watch.ParentWatcher(
                self.root,
                executable=praat_executable(),
                grace_sec=2.0,
                on_close=self._on_praat_gone,
            )
            self.praat_watcher.start()
        # 主题：窗口级的东西（ttk style、文本 tag、底色）也注册进去，系统深浅色一变
        # 就整体重刷。只跟随系统，不提供手动开关（见 guide.md §8.16）。
        self.theme.add_listener(self.apply_theme)
        self.theme_watcher = ui_theme.ThemeWatcher(self.root, self.on_system_theme_changed)
        self.theme_watcher.start()

    # ------------------------------------------------------------ 模型预设

    # ---------------------------------------------------------------- 主题

    def set_status(self, text: str) -> None:
        """状态栏文案。``self.status`` 保持老格式（回归脚本会读），胶囊里只显示后半截。"""

        self.status.set(text)
        head, _, tail = text.partition("|")
        self.status_text.set((tail.strip() if tail else head.strip()) or head.strip())
        try:
            self.status_chip.set_kind(self._status_kind())
        except Exception:   # noqa: BLE001 - 窗口还没建好时忽略
            pass

    def _status_kind(self) -> str:
        """状态胶囊的语义色：正常=success，忙碌/警告=warning，服务不通=danger。"""

        text = self.status_text.get()
        if "失败" in text or "未响应" in text:
            return "danger"
        if "正在" in text or "停止" in text or "stopped" in text:
            return "warning"
        if "API 模式" in text:
            return "primary"
        return "success"

    def _hint_kind(self) -> str:
        """预设提示条的语义色：API 模式/切换中各有各的颜色。"""

        text = self.preset_hint.get()
        if "失败" in text:
            return "danger"
        if "API 模式" in text:
            return "primary"
        if "正在" in text:
            return "warning"
        return "neutral"

    def on_system_theme_changed(self, dark: bool) -> None:
        """系统换了深浅色（每 5 秒看一次）。"""

        self.theme.set_dark(dark)

    def apply_theme(self, theme=None) -> None:
        """把当前主题铺到窗口级的东西上（ttk style、文本 tag、底色、胶囊颜色）。"""

        theme = theme or self.theme
        canvas_color = theme.color("canvas")
        surface = theme.color("surface")
        self.root.configure(background=canvas_color)
        style = ttk.Style(self.root)
        # 只在确实不是 clam 时切换：每次刷新都切一遍会广播 <<ThemeChanged>>，
        # 既费时间，测试里还会打出 Tcl 的吓人噪声。
        if "clam" in style.theme_names() and style.theme_use() != "clam":
            style.theme_use("clam")
        ui_widgets.configure_ttk(style, theme)
        for widget in (self.frame, self.preset_row, self.transcript_frame):
            widget.configure(background=canvas_color)
        self.context_line.configure(background=canvas_color, foreground=theme.color("textMuted"))
        self.composer_hint.configure(background=surface, foreground=theme.color("textMuted"))
        self.composer_actions.configure(background=surface)
        self.thinking_label.configure(
            background=surface, foreground=theme.color("textMuted")
        )
        self.entry.configure(
            background=surface,
            foreground=theme.color("text"),
            insertbackground=theme.color("text"),
            font=theme.font("body"),
        )
        style.configure(
            "PraatPreset.TMenubutton",
            background=surface,
            foreground=theme.color("text"),
            arrowcolor=theme.color("text"),
            bordercolor=theme.color("border"),
            lightcolor=theme.color("border"),
            darkcolor=theme.color("border"),
            font=theme.font("body"),
            padding=(8, 5),
            relief="flat",
        )
        style.map(
            "PraatPreset.TMenubutton",
            background=[("pressed", theme.color("surfaceAlt")), ("active", theme.color("surfaceAlt"))],
            foreground=[("disabled", theme.color("textMuted"))],
            arrowcolor=[("disabled", theme.color("textMuted"))],
        )
        self.preset_menu.configure(
            background=surface,
            foreground=theme.color("text"),
            activebackground=theme.color("primarySoft"),
            activeforeground=theme.color("text"),
            disabledforeground=theme.color("textMuted"),
            font=theme.font("body"),
        )
        self.thinking_box.configure(font=theme.font("body"))
        self._configure_transcript_tags(theme)
        try:
            self.status_chip.set_kind(self._status_kind())
            self.preset_snack.set_kind(self._hint_kind())
        except Exception:   # noqa: BLE001 - 刚建窗口时这些还没齐
            pass

    def _configure_transcript_tags(self, theme) -> None:
        """消息块的样式（TW-Elements 的 card / notification 形态）。

        顺序有讲究：先建"块"tag（底色 + 缩进），再建行内 tag（加粗/代码）——Tk 里
        后建的 tag 优先级更高，行内 tag 才能盖住块的字号与底色。
        """

        transcript = self.transcript
        pad = theme.pad(12)
        transcript.configure(
            background=theme.color("canvas"),
            foreground=theme.color("text"),
            insertbackground=theme.color("text"),
            font=theme.font("body"),
        )
        blocks = {
            "user": (theme.color("primarySoft"), theme.color("text"), theme.color("primary")),
            "assistant": (theme.color("surface"), theme.color("text"), theme.color("primary")),
            # 结果块用 shadowRing 而不是 codeBg：codeBg 太接近画布底色，块看不出来。
            "result": (
                theme.color("shadowRing"),
                theme.color("codeText"),
                theme.color("successText"),
            ),
            # 失败块的正文用普通文字色（danger 压 dangerSoft 只有 3.4:1），
            # danger 只留给加粗的「▍ 失败」那一行。
            "failure": (
                theme.color("dangerSoft"),
                theme.color("text"),
                theme.color("danger"),
            ),
        }
        for name, (background, foreground, accent) in blocks.items():
            transcript.tag_configure(
                f"{name}_block",
                background=background,
                foreground=foreground,
                font=theme.font("data" if name in {"result", "failure"} else "body"),
                lmargin1=pad,
                lmargin2=pad,
                rmargin=theme.pad(10),
                justify="right" if name == "user" else "left",
                spacing1=theme.pad(8),
                spacing3=theme.pad(2),
                borderwidth=0,
            )
            transcript.tag_configure(
                f"{name}_header",
                background=background,
                foreground=accent,
                font=theme.font("small_bold"),
                lmargin1=pad,
                lmargin2=pad,
                rmargin=theme.pad(10),
                justify="right" if name == "user" else "left",
                spacing1=theme.pad(8),
            )
        transcript.tag_configure(
            "hint",
            foreground=theme.color("textMuted"),
            lmargin1=pad,
            lmargin2=pad,
            spacing1=theme.pad(4),
            spacing3=theme.pad(6),
            font=theme.font("small"),
        )
        # 行内 tag：只改字号/底色，不能碰 lmargin（会把块的缩进顶掉）。
        transcript.tag_configure("heading", font=theme.font("heading"))
        transcript.tag_configure(
            "bullet", lmargin1=pad + theme.pad(8), lmargin2=pad + theme.pad(18)
        )
        transcript.tag_configure("bold", font=theme.font("body_bold"))
        transcript.tag_configure(
            "code",
            font=theme.font("data"),
            background=theme.color("chipBg"),
            foreground=theme.color("codeText"),
        )
        # 动态的「⧉ 复制」tag（每条消息一个）也要跟着换色。
        for tag in getattr(self, "_copy_registry", {}):
            transcript.tag_configure(
                tag,
                foreground=theme.color("primary"),
                font=theme.font("small_bold"),
                spacing1=theme.pad(2),
                spacing3=theme.pad(6),
                lmargin1=theme.pad(12),
            )

    def preset_label_for(self, preset: dict) -> str:
        # 标签里不要写「当前」：切换后标签会变，下拉框里的旧值就对不上了。
        # 当前预设显示在下拉框下面的提示行里。
        return preset_label_text(preset)

    def preset_hint_text(self) -> str:
        if api_is_active(self.config):
            label = self.config.api.label or "云端 API"
            from . import api_settings

            bits: list[str] = []
            level = self.config.qwen.thinking_level
            if level and level != "auto":
                bits.append(f"思考档位：{api_settings.thinking_choice_label(level)}")
            bits.append(
                "可以发挥自己的语言学知识"
                if qwen.knowledge_is_open(self.config.qwen)
                else "只按工具结果回答"
            )
            bits.append("不需要本机 llama-server")
            if local_preset_allowed(self.config):
                bits.append("可从顶部选本地预设并应用")
            else:
                bits.append("本地预设已禁用，可在 API 配置中解除限制")
            return (
                f"当前是 API 模式：{label} / {self.config.api.model}"
                f"（{'，'.join(bits)}）。"
            )
        if not self.presets:
            return (
                "还没有配置模型预设：在 ai/ai_config.json 的 server.presets "
                "里添加模型条目后重启前端。"
            )
        current = next((preset for preset in self.presets if preset["active"]), None)
        if current is None:
            return "当前模型不在预设列表里；选中一个预设并点「应用预设」即可切换。"
        bits = [f"{current['label']}：{current['model']}", "当前预设"]
        if current["mmproj"]:
            bits.append(f"投影文件 {current['mmproj']}")
        if current["context_tokens"]:
            bits.append(f"上下文 {current['context_tokens']} token")
        return "；".join(item for item in bits if item)

    def refresh_preset_widgets(self) -> None:
        self.preset_ids = {
            self.preset_label_for(preset): preset["id"] for preset in self.presets
        }
        self.api_choice = api_choice_label(self.config)
        labels = [self.api_choice, *self.preset_ids]
        self.preset_labels = labels
        self.preset_menu.delete(0, "end")
        for label in labels:
            allowed = label == self.api_choice or local_preset_allowed(self.config)
            self.preset_menu.add_command(
                label=label,
                state="normal" if allowed else "disabled",
                command=lambda choice=label: self.select_model_choice(choice),
            )
        self.preset_box.configure(state="normal")
        self.preset_button.configure(state="normal")
        current = next((preset for preset in self.presets if preset["active"]), None)
        if api_is_active(self.config) or not local_preset_allowed(self.config):
            self.preset_choice.set(self.api_choice)
        elif current is not None:
            self.preset_choice.set(self.preset_label_for(current))
        elif labels:
            self.preset_choice.set(labels[0])
        else:
            self.preset_choice.set("")
        self.preset_hint.set(self.preset_hint_text())
        self.preset_snack.set_kind(self._hint_kind())

    def select_model_choice(self, label: str) -> None:
        """顶部菜单选择即切换；按钮仍可用于失败后的重试。"""

        if self.busy:
            self.append_hint("正在处理上一个请求，稍后再切换模型。")
            return
        self.preset_choice.set(label)
        self.apply_selected_preset()

    def apply_selected_preset(self, _event: object = None) -> None:
        if self.busy:
            return
        if self.preset_choice.get() == self.api_choice:
            if not api_is_active(self.config):
                self.busy = True
                self.send_button.configure(state="disabled")
                self.preset_button.configure(state="disabled")
                self.set_status("Praat AI  |  正在切换到云端 API…")
                threading.Thread(target=self.activate_api_worker, daemon=True).start()
                return
            self.append_hint(
                f"当前已经在用云端 API 模型：{self.config.api.model}"
                "（不需要本机 llama-server）。"
            )
            return
        if not local_preset_allowed(self.config):
            self.append_hint("本地模型已禁用；请先在 API 配置中解除云端锁定并取消停用本地模型服务。")
            self.refresh_preset_widgets()
            return
        preset_id = resolve_preset_id(self.presets, self.preset_choice.get())
        if not preset_id:
            self.append_hint("预设列表已经变化，请重新选择一个预设再点「应用预设」。")
            return
        self.busy = True
        self._set_thinking_selector_busy(True)
        self.send_button.configure(state="disabled")
        self.preset_button.configure(state="disabled")
        self.set_status("Praat AI  |  正在切换模型预设…")
        threading.Thread(
            target=self.apply_preset_worker,
            args=(preset_id,),
            daemon=True,
        ).start()

    def apply_preset_worker(self, preset_id: str) -> None:
        try:
            from . import control

            result = control.apply_preset(preset_id, progress=self._progress_sink())
        except (PresetError, qwen.QwenError, OSError, ValueError) as error:
            self.messages.put(("assistant", f"切换模型预设失败：{error}"))
        else:
            label = str(result.get("frontend_preset_label", "")) or preset_id
            model = str(result.get("frontend_model", ""))
            vision = "视觉已开" if result.get("frontend_vision") else "纯文本"
            self.messages.put(
                ("assistant", f"已切换模型预设：{label}（{model}，{vision}）")
            )
        finally:
            self.messages.put(("progress-done", ""))
            self.messages.put(("reload", ""))
            self.messages.put(("done", ""))

    def activate_api_worker(self) -> None:
        try:
            from . import control

            control.activate_api(progress=self._progress_sink())
        except (PresetError, qwen.QwenError, OSError, ValueError) as error:
            self.messages.put(("assistant", f"切换云端 API 失败：{error}"))
        else:
            self.messages.put(("assistant", "已切换到云端 API。"))
        finally:
            self.messages.put(("progress-done", ""))
            self.messages.put(("reload", ""))
            self.messages.put(("done", ""))

    # ------------------------------------------------------------ API 配置

    def _progress_sink(self):
        """把「加载/停止模型」的进度送到界面线程（迷你进度条那张小窗）。"""

        def sink(fraction: float, message: str) -> None:
            self.messages.put(("progress", f"{fraction:.4f}|{message}"))

        return sink

    def open_api_settings(self) -> None:
        """打开「API 配置」小窗口（填 key 接云端大模型）。"""

        # 已经开着就把它提到前面，别开一堆同样的窗口。
        existing = getattr(self, "api_dialog", None)
        if existing is not None:
            try:
                if existing.window.winfo_exists():
                    existing.window.lift()
                    existing.window.focus_set()
                    return
            except tk.TclError:
                pass
        dialog = api_settings.ApiSettingsDialog(
            self.root,
            config=self.config,
            on_saved=self.on_api_settings_saved,
        )
        self.api_dialog = dialog
        self.append_hint(
            "API 配置：填写服务商、地址、模型名和 API key；锁定云端后顶部不能选本地模型。"
        )
        try:
            dialog.window.transient(self.root)
        except tk.TclError:
            pass

    def on_api_settings_saved(self, values: dict) -> None:
        """保存 API 设置后同步有效模型和本地服务。api.stop_local_service 控制 API 模式下是否停止本地服务；切回本地时确保配置模型可用。"""

        self.reload_config()
        if api_is_active(self.config):
            self.append_hint(
                f"已切到 API 模式：{self.config.api.label or '云端 API'} / "
                f"{self.config.api.model}（对话走云端，不再需要本机模型服务）。"
            )
            if not self.config.api.stop_local_service:
                self.append_hint(
                    "按设置保留本机模型服务（切回本地时不用重新加载；想腾显存就在"
                    " Praat 菜单点「前端 → 停止前端」）。"
                )
        else:
            self.append_hint(
                "已关闭 API 模式：前端回到本地模型预设，正在把本机模型服务起起来…"
            )
        self.messages.put(("reconcile-service", ""))

    def reconcile_service_worker(self) -> None:
        """Use the same saved-configuration transition as the standalone dialog."""

        from . import control

        try:
            result = control.reconcile_api_transition(progress=self._progress_sink())
        except (QwenServerError, qwen.QwenError, OSError, ValueError) as error:
            self.messages.put(("failure", f"切换 API／本机服务失败：{error}"))
        else:
            if result.get("api_enabled"):
                self.messages.put(("hint", "API 设置已应用，本机服务已按选项处理。"))
            else:
                model = str(result.get("frontend_model", ""))
                self.messages.put(
                    ("hint", f"本机模型服务已就绪：{model or '（模型名读取中）'}")
                )
        finally:
            self.messages.put(("progress-done", ""))

    def append(self, role: str, text: str) -> None:
        self._insert_message(role, text)
        if getattr(self, 'store', None) is not None:
            self.store.append(self.session_id, 'message', {'role':role, 'text':text})

    def append_lines(self, tag: str, text: str) -> None:
        self._insert_message(tag, text)

    def append_hint(self, text: str) -> None:
        block = self._active_process
        if block is not None:
            transcript = self.transcript
            transcript.configure(state="normal")
            # 提示可能在回答之后入队，仍插回本轮过程区；左重力防止正文进入折叠区。
            transcript.mark_gravity(block.end_mark, "right")
            transcript.insert(block.end_mark, f"ⓘ {text}\n", ("hint", block.body_tag))
            transcript.mark_gravity(block.end_mark, "left")
            transcript.configure(state="disabled")
            transcript.see("end")
            return
        self._insert_message("hint", text)

    def _begin_process(self) -> None:
        number = len(self._process_blocks) + 1
        block = _ProcessBlock(
            started_at=time.monotonic(),
            header_tag=f"process_header_{number}",
            body_tag=f"process_body_{number}",
            end_mark=f"process_end_{number}",
        )
        self._process_blocks[block.header_tag] = block
        self._active_process = block
        transcript = self.transcript
        transcript.configure(state="normal")
        transcript.tag_configure(block.body_tag, elide=False)
        transcript.tag_bind(block.header_tag, "<Enter>", lambda _event: self._set_cursor("hand2"))
        transcript.tag_bind(block.header_tag, "<Leave>", lambda _event: self._set_cursor("xterm"))
        transcript.insert("end", "正在思考和计算…  ▴\n", ("hint", block.header_tag))
        transcript.mark_set(block.end_mark, "end-1c")
        transcript.mark_gravity(block.end_mark, "left")
        transcript.configure(state="disabled")
        transcript.see("end")

    def _finish_process(self, finished_at: float) -> None:
        block = self._active_process
        if block is None:
            return
        block.finished_at = finished_at
        block.collapsed = True
        self._active_process = None
        self._render_process_header(block)
        self.transcript.mark_unset(block.end_mark)
        self.transcript.see("end")

    def _render_process_header(self, block: _ProcessBlock) -> None:
        elapsed = max(0, int(block.finished_at - block.started_at))
        minutes, seconds = divmod(elapsed, 60)
        arrow = "▾" if block.collapsed else "▴"
        label = f"已完成，用时{minutes}分{seconds}秒  {arrow}"
        transcript = self.transcript
        start, end = transcript.tag_ranges(block.header_tag)
        transcript.configure(state="normal")
        # 保留换行，避免更换标题时把正文边界挪进标题里。
        transcript.delete(start, f"{end}-1c")
        transcript.insert(start, label, ("hint", block.header_tag))
        transcript.tag_configure(block.body_tag, elide=block.collapsed)
        transcript.configure(state="disabled")

    def _process_block_at(self, index: str, point: tuple[int, int] | None = None) -> _ProcessBlock | None:
        """这一处文字属于哪个**已结束**的过程折叠区（用于右键命中）。

        只认标题那一行会漏掉两种情况，用户会看到「右键没反应」：

        - 标题行右侧、行尾之后的空白：那里没有文字，``@x,y`` 落在行首/行尾的换行上，
          拿不到 header tag；
        - 标题行上下的行距（``hint`` 的 ``spacing1/spacing3`` 是**行间**空隙，不映射到
          任何字符），差一两行同样点不到 header tag。

        所以先按字符找：题目所在的字符，加上该行的行首/行尾与下一行的行首/行尾。折叠时
        正文被 elide 掉，正文那一行的字符索引正好等于标题行行尾的下一个位置，所以
        「点折叠区也能展开」也顺带成立，而展开状态下正文各行不会命中。

        ``point``（``@x,y`` 的像素坐标）是行距的兜底：同一条行距在不同字体/DPI 下会被
        判给上一行或下一行（实测：标题行高 17px 时，标题下方 6px 处会解析成**下一行**，
        于是命中判定扑空）。给了 ``point`` 就再按标题行的像素带找最近的已结束折叠区，
        容差是半个行高——足够覆盖行距，又不会把手伸到正文里。
        """

        block = self._process_block_by_characters(index)
        if block is not None or point is None:
            return block
        return self._process_block_near_point(*point)

    def _process_block_by_characters(self, index: str) -> _ProcessBlock | None:
        """字符法：题目字符、所在行的首尾、下一行的首尾都算命中。"""

        transcript = self.transcript
        number = index.split(".")[0]
        candidates: list[str] = [index]
        for offset in (0, 1):
            line = int(number) + offset
            if line < 1:
                continue
            candidates.append(f"{line}.0")
            candidates.append(f"{line}.end")
        for candidate in candidates:
            try:
                names = transcript.tag_names(candidate)
            except tk.TclError:
                continue
            for tag in names:
                block = self._process_blocks.get(tag)
                if block is not None and block.finished_at is not None:
                    return block
        return None

    def _process_block_near_point(self, x: int, y: int) -> _ProcessBlock | None:
        """像素兜底：点落在某个折叠区标题行的上下半个行高内就算命中，取最近的那个。"""

        transcript = self.transcript
        best: tuple[int, _ProcessBlock] | None = None
        for block in self._process_blocks.values():
            if block.finished_at is None:
                continue
            try:
                first = transcript.index(f"{block.header_tag}.first")
                box = transcript.bbox(first)
            except tk.TclError:
                continue
            if not box:
                continue
            top, height = box[1], box[3]
            slack = max(2, height // 2)
            if not top - slack <= y <= top + height + slack:
                continue
            distance = 0 if top <= y <= top + height else min(abs(y - top), abs(y - (top + height)))
            if best is None or distance < best[0]:
                best = (distance, block)
        return best[1] if best else None

    def _process_block_at_characters(self, index: str) -> _ProcessBlock | None:
        """旧名字保留给测试/调用方（等价于 :meth:`_process_block_by_characters`）。"""

        return self._process_block_by_characters(index)

    #: 同一次右键里重复投递的事件（``<Button-3>`` + ``<ButtonRelease-3>``）必须只算一次，
    #: 否则两次切换互相抵消，用户看到的就是「点了没反应 / 闪一下又收起」。
    _TOGGLE_DEBOUNCE_SEC = 0.28

    def _set_process_collapsed(self, block: _ProcessBlock, collapsed: bool, *, debounce: bool) -> bool:
        """把某个折叠区设成指定状态；``debounce=True`` 时吃掉同一秒内的重复事件。"""

        now = time.monotonic()
        if debounce and now - block.last_toggle_at < self._TOGGLE_DEBOUNCE_SEC:
            return False
        if collapsed == block.collapsed:
            return False
        block.last_toggle_at = now
        block.collapsed = collapsed
        self._render_process_header(block)
        return True

    def toggle_process(self, block: _ProcessBlock) -> bool:
        """无条件切换（右键命中的正常路径，带去抖）。"""

        return self._set_process_collapsed(block, not block.collapsed, debounce=True)

    def _on_process_right_click(self, event) -> str | None:
        try:
            index = self.transcript.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return None
        block = self._process_block_at(index, (event.x, event.y))
        if block is None:
            return None
        self.toggle_process(block)
        # 命中标题就吃掉这次事件，别让它变成文本选择的起点。
        return "break"

    # ---------------------------------------------------------- 消息块渲染

    #: 角色 → (tag 前缀, 头部文字)。头部的 ▍ 是块左侧那条强调线。
    _MESSAGE_ROLES: dict[str, tuple[str, str]] = {
        "user": ("user", "你"),
        "assistant": ("assistant", "Praat AI"),
        "result": ("result", "结果"),
        "failure": ("failure", "失败"),
    }

    #: 哪些消息值得一键复制（用户自己敲的不用）。
    _COPYABLE_ROLES = {"assistant", "result"}

    def _insert_message(self, role: str, text: str) -> None:
        """插一条消息块（底色 + ▍ 强调线 + 间距），回答/结果顺带渲染最小 Markdown。"""

        transcript = self.transcript
        body = str(text or "")
        transcript.configure(state="normal")
        if role == "hint":
            transcript.insert("end", f"ⓘ {body}\n", "hint")
            transcript.configure(state="disabled")
            transcript.see("end")
            return
        prefix, label = self._MESSAGE_ROLES.get(role, self._MESSAGE_ROLES["assistant"])
        transcript.insert("end", f"▍ {label}\n", (f"{prefix}_block", f"{prefix}_header"))
        markdown = role in {"assistant", "result"}
        for chunk, tag in (render_message(body) if markdown else [(body, "")]):
            if isinstance(tag, str):
                tags: tuple[str, ...] = ((f"{prefix}_block", tag) if tag else (f"{prefix}_block",))
            else:
                tags = (f"{prefix}_block", *tag)
            transcript.insert("end", chunk, tags)
        if not body.endswith("\n"):
            transcript.insert("end", "\n", f"{prefix}_block")
        if prefix in self._COPYABLE_ROLES and body.strip():
            self._insert_copy_link(body)
        transcript.configure(state="disabled")
        transcript.see("end")

    def _insert_copy_link(self, raw: str) -> None:
        """在消息块末尾放一个「⧉ 复制」文本按钮。

        早先用 ``window_create`` 内嵌一个自绘按钮，实测会压住下一块——Tk 不会为内嵌
        窗口抬高行高（截图里两个「复制」叠在一起）。改成文本 tag + 点击命中，滚动和
        重绘都不会出问题。
        """

        tag = f"copy_{len(self._copy_registry) + 1}"
        self._copy_registry[tag] = raw
        self.transcript.tag_configure(
            tag,
            foreground=self.theme.color("primary"),
            font=self.theme.font("small_bold"),
            spacing1=self.theme.pad(2),
            spacing3=self.theme.pad(6),
            lmargin1=self.theme.pad(12),
        )
        self.transcript.tag_bind(tag, "<Button-1>", self._on_copy_click)
        self.transcript.tag_bind(tag, "<Enter>", lambda _event: self._set_cursor("hand2"))
        self.transcript.tag_bind(tag, "<Leave>", lambda _event: self._set_cursor("xterm"))
        self.transcript.insert("end", "⧉ 复制\n", tag)

    def _set_cursor(self, cursor: str) -> None:
        try:
            self.transcript.configure(cursor=cursor)
        except tk.TclError:
            pass

    def _on_copy_click(self, event) -> None:
        try:
            index = self.transcript.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        for name in self.transcript.tag_names(index):
            if name in self._copy_registry:
                self.copy_message(self._copy_registry[name])
                return

    def copy_message(self, text: str) -> None:
        """把整条消息写进剪贴板（消息区本身仍然可以选中复制）。"""

        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(text)
        except tk.TclError:
            return
        if not hasattr(self, "status_text"):
            return
        try:
            self.status_text.set("已复制到剪贴板")
            self.status_chip.set_kind("success")
            self.root.after(1500, self._restore_status_text)
        except tk.TclError:
            pass

    def _restore_status_text(self) -> None:
        try:
            self.set_status(self.status.get())
        except tk.TclError:
            pass

    def on_return(self, _event: tk.Event) -> str:
        if ui_windows.is_composing(self.entry):
            # Native IME confirmation is handled before Tk key bindings; don't
            # also send the prompt or let Text insert a stray newline.
            return "break"
        self.submit()
        return "break"

    def _set_thinking_selector_busy(self, busy: bool) -> None:
        self.thinking_box.configure(state="disabled" if busy else "readonly")

    def on_thinking_level_selected(self, _event: object = None) -> str:
        previous = api_settings.normalize_thinking_level(
            self.config.qwen.thinking_level
        )
        if self.busy:
            self.thinking_choice.set(self.thinking_choice_labels[previous])
            return "break"

        level = self.thinking_choice_values.get(self.thinking_choice.get(), "auto")
        if level == previous:
            return "break"
        values = {
            "api": {"thinking_level": level},
            "qwen": {"thinking_level": level},
        }
        if level != "auto":
            values["qwen"]["enable_thinking"] = level != "off"
        try:
            from . import control

            control.update_config(values, config_path())
        except Exception as error:  # noqa: BLE001 - keep config errors in the chat UI
            self.thinking_choice.set(self.thinking_choice_labels[previous])
            self.append_hint(f"思考强度保存失败，已恢复原选择：{error}")
            return "break"

        self.reload_config()
        self.append_hint(
            f"思考强度已设为「{self.thinking_choice_labels[level]}」，从下一次请求生效。"
        )
        return "break"

    def on_shift_return(self, _event: tk.Event) -> str:
        if ui_windows.is_composing(self.entry):
            return "break"
        self.entry.insert("insert", "\n")
        return "break"

    def submit(self, _event: object = None) -> str:
        if self.busy:
            return "break"
        text = self.entry.get("1.0", "end").strip()
        if not text:
            return "break"
        self.entry.delete("1.0", "end")
        # A typed new goal must not inherit a previously prepared continuation.
        if self.pending_analysis is not None and text != self.pending_analysis.visible_prompt:
            self.pending_analysis = None
        self.busy = True
        self.cancel_event.clear()
        self.send_button.configure(state="disabled")
        self._set_thinking_selector_busy(True)
        self.stop_button.configure(state="normal")
        self.preset_button.configure(state="disabled")
        self.set_status("Praat AI  |  正在处理请求…")
        self.append("user", text)
        self._begin_process()
        self._turn_worker = threading.Thread(
            target=self.process_message,
            args=(text,),
            daemon=True,
        )
        self._turn_worker.start()
        return "break"

    def cancel_turn(self) -> None:
        """不再等待当前步骤；已经在 Praat 里运行的脚本不受影响。"""

        if not self.busy:
            return
        self.cancel_event.set()
        self.stop_button.configure(state="disabled")
        self.append_hint("已请求停止：不再等这一步；Praat 里已经在跑的脚本不受影响。")

    def run_user_turn(self, text: str, executable: str) -> TurnOutcome:
        """规划并执行一轮用户请求（可能多步），返回最终回答和每一步的结果。

        执行由 :func:`_send_script` 完成，所以每一步都是一条独立的 app 消息（各自
        有请求编号）；每步跑完把结果（或错误）回灌给模型，让它接着做或者解释结果。
        """

        context_text = object_context()
        rows = tools.parse_object_context(context_text)
        context = tools.ToolContext(
            objects=rows,
            result_path=result_path(),
            state_path=state_path(),
        )

        def execute(script: str) -> tuple[bool, list[str], str]:
            ok, output = _send_script(executable, script, cancel=self.cancel_event)
            if not ok:
                return False, [], output or _blocked_reason()
            failure = _read_failure()
            if failure:
                # 脚本在 Praat 里报错了：Praat 把原文写进了 chat_failure.txt
                # （不再弹模态框），这里按「失败」回灌给模型。
                return False, _read_results(), failure
            return True, _read_results(), ""

        # 本地工具（跑现成 .praat 脚本那种）需要：往开着的 Praat 投递脚本、
        # 批处理用的 Praat 路径、放临时文件的地方，以及「用户点停止了吗」。
        environment = tools.LocalEnvironment(
            execute=execute,
            praat_executable=executable,
            runtime_directory=runtime_dir(),
            cancelled=self.cancel_event.is_set,
        )

        return run_turn(
            self.client,
            user_text=text,
            context_text=context_text,
            history=self.history,
            context=context,
            execute=execute,
            on_progress=lambda line: self.messages.put(("hint", line)),
            environment=environment,
            cancel=self.cancel_event,
            refresh_context=object_context,
        )

    def process_message(self, text: str) -> None:
        try:
            if api_is_active(self.config):
                self.process_cloud_message(text)
                return
            executable = praat_executable()
            # 共用一次进程查询判断 Praat 是否运行及实例数。
            process_ids = praat_process_ids(executable) if executable else None
            praat_ready = bool(executable) and praat_process_running_from(process_ids)
            if praat_ready:
                warning = praat_instance_warning_from(process_ids)
                if warning:
                    self.messages.put(("hint", warning))
                # 先刷新对象列表，免得拿着过期 id 去规划。C5：已经确认过、而且还是
                # 同一个 Praat 进程时，这一步一次往返都不花。
                already_ready = self.context_ready_pids == frozenset(process_ids or [])
                refreshed, note = refresh_object_context(
                    executable, process_ids, assume_fresh=already_ready
                )
                if not refreshed and note:
                    self.messages.put(("hint", note))
                elif refreshed:
                    self.context_ready_pids = frozenset(process_ids or [])

            if not executable:
                raise OSError(
                    praat_app.describe_search()
                    + " 也可以从 Praat 菜单「前端 → 启动前端」重新打开对话窗口。"
                )
            if not praat_ready:
                raise OSError(
                    "没有检测到正在运行的 Praat。请先打开 Praat，"
                    "再从菜单「前端 → 启动前端」启动对话窗口。"
                )

            outcome = self.run_user_turn(text, executable)
            if getattr(self, 'store', None) is not None:
                from dataclasses import asdict
                self.store.append(self.session_id, 'local_turn', asdict(outcome))
            for note in outcome.notes:
                self.messages.put(("hint", note))

            if not outcome.used_tools:
                # 模型判断不需要动手（打招呼、或者这件事做不到），直接回话。
                self.messages.put(("assistant", outcome.reply))
                self.history.append({"role": "user", "content": text})
                self.history.append({"role": "assistant", "content": outcome.reply})
                return

            if outcome.results:
                body = outcome.reply
                if body != "\n".join(outcome.results):
                    body += f"\n结果：{'；'.join(outcome.results)}"
                self.messages.put(("assistant", body))
                self.messages.put(("result", "结果：\n" + "\n".join(outcome.results)))
                if outcome.failure:
                    # 前面几步成功、后面某一步没做成：结果照给，另起一句说明。
                    self.messages.put(("hint", outcome.failure))
                self.history.append({"role": "user", "content": text})
                self.history.append({"role": "assistant", "content": body})
                return

            message = (
                f"{outcome.reply}\n执行未完成：Praat 没有返回结果。"
                "常见原因是 Praat 里还开着别的模态窗口（错误提示、正在播放的窗口、"
                "没关掉的对话框）挡住了消息；关掉它们再发一次即可。"
                "脚本自己报错时 Praat 会把原文写回这里（见下面那行）。"
            )
            if outcome.failure:
                message += f"\n{outcome.failure}"
            else:
                message += (
                    "\n（Praat 没有输出任何信息：常见原因是脚本里的命令和当前"
                    "选中的对象不匹配，或者 Praat 正被对话框挡住。）"
                )
            if outcome.last_script:
                message += f"\n\n本次脚本：\n{tools.describe_script(outcome.last_script)}"
            self.messages.put(("assistant", message))
            self.history.append({"role": "user", "content": text})
            self.history.append(
                {"role": "assistant", "content": "（上次脚本执行未完成）"}
            )
        except (qwen.QwenError, tools.ToolError, PresetError, OSError) as error:
            self.messages.put(("assistant", f"处理失败：{error}"))
        finally:
            # 记录工作线程真正结束的时刻，避免 UI 队列延迟计入耗时。
            self.messages.put(("done", str(time.monotonic())))

    def _begin_stream_reply(self):
        if getattr(self, '_streaming_reply', False): return
        transcript = self.transcript
        transcript.configure(state='normal')
        transcript.mark_set('stream_reply_start', 'end-1c')
        transcript.mark_gravity('stream_reply_start', 'left')
        transcript.insert('end-1c', '\n助手：\n', ('assistant',))
        # Process hints are inserted immediately before this reply. Follow
        # those insertions so finalization removes only the streamed answer.
        transcript.mark_gravity('stream_reply_start', 'right')
        transcript.mark_set('stream_reply_end', 'end-1c')
        transcript.mark_gravity('stream_reply_end', 'left')
        transcript.configure(state='disabled')
        self._streaming_reply = True

    def _append_stream_reply(self, text):
        if not getattr(self, '_streaming_reply', False): return
        transcript = self.transcript
        transcript.configure(state='normal')
        # Let Tk advance a temporary mark across Unicode text; Tcl string
        # lengths and Text character offsets differ for non-BMP characters.
        transcript.mark_set('stream_reply_cursor', 'stream_reply_end')
        transcript.mark_gravity('stream_reply_cursor', 'right')
        transcript.insert('stream_reply_end', text, ('assistant',))
        transcript.mark_set('stream_reply_end', 'stream_reply_cursor')
        transcript.mark_unset('stream_reply_cursor')
        transcript.configure(state='disabled')
        transcript.see('stream_reply_end')

    def _finish_stream_reply(self, text):
        if getattr(self, '_streaming_reply', False):
            transcript = self.transcript
            transcript.configure(state='normal')
            transcript.delete('stream_reply_start', 'stream_reply_end')
            transcript.mark_unset('stream_reply_start', 'stream_reply_end')
            transcript.configure(state='disabled')
            self._streaming_reply = False
        self.append('assistant', text)

    def flush_messages(self) -> None:
        try:
            while True:
                role, text = self.messages.get_nowait()
                if role == 'assistant-start':
                    self._begin_stream_reply()
                elif role == 'assistant-delta':
                    self._append_stream_reply(text)
                elif role == 'assistant-final':
                    self._finish_stream_reply(text)
                elif role == "done":
                    self._finish_process(float(text) if text else time.monotonic())
                    self.busy = False
                    self.cancel_event.clear()
                    self.send_button.configure(state="normal")
                    self._set_thinking_selector_busy(False)
                    self.stop_button.configure(state="disabled")
                    self.preset_button.configure(state="normal")
                    self.set_status("Praat AI  |  " + model_status_text(self.config))
                    self.context_label.set(selected_object_label())
                    self.entry.focus_set()
                elif role == "reload":
                    self.reload_config()
                elif role == 'choices':
                    self.show_analysis_choices()
                elif role == "progress":
                    self.show_progress(text)
                elif role == "progress-done":
                    self.hide_progress()
                elif role == "reconcile-service":
                    threading.Thread(
                        target=self.reconcile_service_worker, daemon=True
                    ).start()
                elif role in {"result", "failure"}:
                    self.append_lines(role, text)
                elif role == "hint":
                    self.append_hint(text)
                else:
                    self.append(role, text)
        except queue.Empty:
            pass
        # 配置文件被别的窗口改了（Praat 菜单里的「API 配置…」跑在独立进程里，
        # 手工编辑也算）就跟着刷新，别让界面显示的还是旧模型。
        now = time.monotonic()
        if now >= self.next_config_check:
            self.next_config_check = now + 1.0
            self.check_config_changed()
        self.root.after(100, self.flush_messages)

    def check_config_changed(self) -> None:
        """ai_config.json 的修改时间变了就重新读一遍（不打扰用户）。

        Praat 菜单里的「API 配置…」现在跑在独立进程里（不然 Praat 会被那个窗口
        堵死，见 sys/PraatAiControl.cpp），它保存之后这个窗口得跟着换模型。
        """

        try:
            stamp = config_path().stat().st_mtime_ns
        except OSError:
            return
        if stamp == self.config_stamp:
            return
        if self.busy:
            # 正在跑一轮请求时先别换模型；**别**更新时间戳，下一次 tick 再看一遍。
            return
        self.config_stamp = stamp
        self.reload_config()
        self.append_hint("配置已经更新（可能是另一个窗口改的），界面已刷新。")

    def show_progress(self, payload: str) -> None:
        """更新（必要时创建）迷你进度窗。``payload`` 是 ``"0.42|说明"``。"""

        fraction_text, _, message = payload.partition("|")
        try:
            fraction = float(fraction_text)
        except ValueError:
            fraction = 0.0
        if self.progress_window is None:
            self.progress_window = progress_popup.MiniProgress(
                self.root, message=message or "正在加载模型…"
            )
        self.progress_window.update(fraction, message or None)

    def hide_progress(self) -> None:
        window = self.progress_window
        if window is None:
            return
        # 太快结束的操作（例如模型已经在跑时点「应用预设」）也让小窗露个脸再关，
        # 不然用户只看到「闪一下」。
        remaining = window.remaining_minimum_seconds()
        if remaining > 0.0:
            try:
                self.root.after(int(remaining * 1000) + 20, self.hide_progress)
            except Exception:   # noqa: BLE001 - 窗口已经在关的时候别再排队
                pass
            return
        window.close()
        self.progress_window = None

    def close(self) -> None:
        if getattr(self, "_closed", False):
            return   # WM_DELETE_WINDOW 和「Praat 关了」可能同时来，别 destroy 两次
        self._closed = True
        if getattr(self, 'cancel_event', None) is not None:
            self.cancel_event.set()
        if getattr(self, 'api_dialog', None) is not None:
            self.api_dialog.close()
        if not getattr(self, 'busy', False):
            self.cleanup_materials()
        if getattr(self, "praat_watcher", None) is not None:
            self.praat_watcher.stopped = True
        if getattr(self, "theme_watcher", None) is not None:
            self.theme_watcher.stop()   # 关掉之后别再排主题轮询回调
        pid_path = runtime_dir() / "chat.pid"
        try:
            pid_path.unlink()
        except FileNotFoundError:
            pass
        worker = getattr(self, '_turn_worker', None)
        if worker is not None and worker.is_alive():
            self.root.withdraw()
            self.root.after(100, self._destroy_when_idle)
        else:
            self.cleanup_materials()
            self._close_cloud_runtime()
            self.root.destroy()

    def _destroy_when_idle(self):
        worker = getattr(self, '_turn_worker', None)
        if worker is not None and worker.is_alive():
            self.root.after(100, self._destroy_when_idle)
            return
        self.cleanup_materials()
        self._close_cloud_runtime()
        self.root.destroy()

    def _close_cloud_runtime(self):
        runtime = getattr(self, 'cloud_runtime', None)
        if runtime is not None: runtime.close()

    def process_cloud_message(self, text):
        from .cloud_workflow import process_cloud
        process_cloud(self, text)

    def _continuation_fits(self, state, *, mode='', direction=''):
        if not self.config.api.limit_tokens:
            return True
        # Use the same retained observations, provenance and excerpt policy as
        # the actual report, including its instructions and output schema.
        from .cloud_agent import minimum_continuation_tokens
        minimum = minimum_continuation_tokens(self.config, state, mode=mode, direction=direction)
        return minimum + self.config.api.plan_max_tokens + 256 <= self.config.api.max_context_tokens

    def continue_analysis(self, direction, state=None, materials=None, mode=''):
        state = state or self.analysis_state
        if self.busy or state is None or not state.can_continue:
            return
        if not api_is_active(self.config):
            self.append_hint('继续分析需要当前云端 API；请先选择 API 模型。')
            return
        if not self._continuation_fits(state, mode=mode, direction=direction):
            self.append_hint('当前上下文无法容纳最小报告和回复预留，请先增加上下文或降低回复上限。原证据已保留。')
            return
        self.pending_analysis = state.continued(direction, mode=mode)
        state.can_continue = False
        if materials is not None:
            self.current_materials = materials
        self.entry.delete('1.0', 'end')
        self.entry.insert('1.0', self.pending_analysis.visible_prompt)
        self.submit()

    def show_analysis_choices(self):
        if self.analysis_state is None or not self.analysis_state.can_continue:
            return
        options = [('继续解释已有证据', '补充解释已有证据及原目标未完成部分', 'L2'),
                   ('继续一般讨论', '补充一般原理、参照和练习方法，明确具体录音证据的缺口', 'L4')]
        material = self.current_materials
        audio_available = bool(material and not material.closed and (material.audio_path is not None
                               or (not material.snapshot_attempted and any(row.selected and row.class_name in tools.SOUND_CLASSES
                                   for row in tools.parse_object_context(self.analysis_state.context_text)))))
        if self.config.api.audio_input_enabled and audio_available:
            options.append(('分析原目标音频', '直接分析原始音频片段并结合已有测量', 'L3'))
        fitting = [(label, direction, mode) for label, direction, mode in options
                   if self._continuation_fits(self.analysis_state, mode=mode, direction=direction)]
        if len(fitting) < len(options):
            self.append_hint('部分继续方式需要增加上下文或降低回复上限，避免新轮次立即收尾。')
        options = fitting
        self.transcript.configure(state='normal')
        self.transcript.insert('end', '\n可继续： ')
        original_state, original_materials = self.analysis_state, self.current_materials
        for label, direction, mode in options:
            tag = 'continue-' + uuid.uuid4().hex
            self._choice_tags.append(tag)
            self.transcript.insert('end', label + '  ', (tag,))
            self.transcript.tag_configure(tag, foreground=self.theme.color('primary'), underline=True)
            self.transcript.tag_bind(tag, '<Button-1>', lambda _event, d=direction, s=original_state, m=original_materials, level=mode:self.continue_analysis(d, s, m, level))
        tag = 'audio-settings-' + uuid.uuid4().hex
        self.transcript.insert('end', '设置或验证音频能力  ', (tag,))
        self.transcript.tag_configure(tag, foreground=self.theme.color('primary'), underline=True)
        self.transcript.tag_bind(tag, '<Button-1>', lambda _event:self.open_api_settings())
        self.transcript.insert('end', '\n')
        self.transcript.configure(state='disabled')
        self.transcript.see('end')

    def view_records(self):
        dialog = tk.Toplevel(self.root)
        dialog.title('对话与分析记录（仅查看）')
        dialog.geometry('880x600')
        sessions = self.store.sessions()
        chooser = ttk.Combobox(dialog, state='readonly', values=[date[:19] + '  ' + identity[:8] for identity, date in sessions])
        chooser.pack(fill='x', padx=10, pady=8)
        body = tk.Text(dialog, wrap='word', font=self.theme.font('body'))
        body.pack(fill='both', expand=True, padx=10, pady=10)
        def show(_event=None):
            if chooser.current() < 0:
                return
            rows = self.store.records(sessions[chooser.current()][0])
            body.configure(state='normal')
            body.delete('1.0', 'end')
            for row in rows:
                body.insert('end', row['created'] + '  ' + row['kind'] + '\n'
                            + json.dumps(row['payload'], ensure_ascii=False, indent=2) + '\n\n')
            body.configure(state='disabled')
        chooser.bind('<<ComboboxSelected>>', show)
        if sessions:
            chooser.current(0)
            show()

    def cleanup_materials(self):
        for materials in getattr(self, 'task_materials', []):
            if not materials.closed:
                try:
                    materials.close()
                except OSError as error:
                    if getattr(self, 'store', None) is not None:
                        self.store.append(self.session_id, 'cleanup', {'reason':str(error)})

    def _on_praat_gone(self) -> None:
        """Praat 退出后保留云端证据分析；本地模式继续沿用跟随退出行为。"""

        if api_is_active(self.config):
            self.append_hint('Praat 已经关闭，相关操作暂停；云端仍可解释已有证据、分析已取得的片段或继续一般讨论。')
            return

        try:
            self.append_hint("Praat 已经关闭，前端窗口跟着退出。")
        except Exception:   # noqa: BLE001 - 界面已经不正常时只要把窗口收掉
            pass
        try:
            self.root.after(700, self.close)
        except Exception:   # noqa: BLE001
            self.close()

    def reload_config(self) -> None:
        """重新读取 ai_config.json（切换预设之后模型和 mmproj 都会变）。"""

        self.config = load_config()
        self.client = qwen.QwenClient(self.config.qwen)
        self.config_stamp = self._config_stamp()
        self.presets = list_presets(self.config)
        self.refresh_preset_widgets()
        self.thinking_choice.set(
            self.thinking_choice_labels[
                api_settings.normalize_thinking_level(self.config.qwen.thinking_level)
            ]
        )
        self.set_status("Praat AI  |  " + model_status_text(self.config))

    @staticmethod
    def _config_stamp() -> int:
        try:
            return config_path().stat().st_mtime_ns
        except OSError:
            return 0

    def run(self) -> int:
        self.root.mainloop()
        return 0


def main() -> int:
    from .service_lock import service_transition_lock
    with service_transition_lock(runtime_dir() / 'frontend.lock', timeout=1):
        return ChatWindow().run()
