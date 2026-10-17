"""UI-free turn adapter. The host owns cloud permission and local model leases.

Bridge files still belong to chat's native runtime, not to runtime_directory
(which owns task materials). Never change chat globals or process environment.
All modern executors share one dispatcher gate; unknown delivery is restart-only.
Legacy Tk dispatchers in another process do not participate in this gate.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import asdict, replace
from pathlib import Path
from types import FunctionType
from typing import Callable

from . import chat, delivery, model_capabilities as caps, qwen, tools
from .cloud_agent import run_cloud_turn, run_dialogue_turn
from .cloud_runtime import CloudRuntime
from .cloud_workflow import (explicit_range, explicit_wav, object_context_text,
                             renamed_context, target_context)
from .config import AppConfig, api_is_active
from .dialogue_policy import dialogue_kind, direct_measurement
from .workflow_policy import WorkflowPolicy, explicit_action, finish_locally, publish_results, valid_measurement_rows
from . import router
from .escape_policy import AnalysisState, BranchExit, Budget, Evidence, note_guard
from .materials import TaskMaterials
from .process import identities_match, process_identity

PRAAT_LOCK = threading.RLock()
_POISON = ''  # 投递状态不明的原因；由一次成功的显式核对 ping 解除，不再要求重启桌面。
_IDENTITY_PREFIX = '# modern-process='
_MAX_TEXT_BYTES = 1024 * 1024


def _gate(cancel: threading.Event, *, reconcile: bool = False) -> None:
    """投递前的闸门。``reconcile=True`` 只用于「显式寻址的核对 ping」那条路。

    投递状态不明（`_POISON`）时不能再发新脚本——但也不能只靠重启解决：2026-10-07 实测
    一次 25 秒超时（Praat 临时被模态窗口挡住）就把整个进程锁死，之后的测量全部失败。
    核对 ping 是串行队列里排在那个不明脚本**之后**的消息：ping 能成功返回，就说明
    Praat 已经把它处理完了，且结果文件按请求编号区分，不会串行——这时可以放行。
    """

    if _POISON and not reconcile:
        raise BranchExit('Praat dispatcher blocked until reconciliation: ' + _POISON)
    if cancel.is_set():
        raise BranchExit('已取消，未投递 Praat 操作')


def _send(executable: str, pid: int, script: str, cancel: threading.Event):
    """Caller owns PRAAT_LOCK, including subsequent result reads."""
    global _POISON
    facts: list[str] = []
    try:
        ok, note = chat._send_script(executable, script, process_id=pid,
                                     cancel=cancel, outcome=facts)
    except Exception as error:
        note = str(error)
        ok = False
        # An exception after handoff (or without a declared fact) is ambiguous.
    fact = delivery.DELIVERED if ok else (
        delivery.NOT_DELIVERED if facts and facts[-1] == delivery.NOT_DELIVERED
        else delivery.EXECUTION_UNKNOWN)
    if fact == delivery.EXECUTION_UNKNOWN:
        _POISON = note or 'completion not confirmed'
    if not ok:
        return False, [], note, fact
    try:
        failure = chat._read_failure()
        return not bool(failure), chat._read_results(), failure, fact
    except OSError as error:
        return False, [], '完成已确认，但结果读取失败：' + str(error), fact


def _refresh(executable: str, pid: int, cancel: threading.Event, facts=None) -> str:
    global _POISON
    reconciling = bool(_POISON)
    _gate(cancel, reconcile=True)
    # refresh_object_context's old-PID shortcut and bool-only failure lose
    # delivery facts. Use its real ping, explicitly addressed, through our gate.
    ok, _, note, fact = _send(executable, pid, chat.context_ping_script(), cancel)
    if facts is not None:
        facts.append(fact)
    if not ok:
        error = BranchExit('Praat 状态无法确认；未使用旧快照：' + note)
        # 本来就在等核对：这次失败说明「仍然封锁」，不是又出现一次状态不明。
        error.execution = (delivery.EXECUTION_BLOCKED if reconciling else
                           (delivery.EXECUTION_UNKNOWN if _POISON else delivery.NOT_DELIVERED))
        raise error
    text = chat.object_context()
    if not text.startswith('id\tclass\tname\tselected'):
        raise BranchExit('没有读到完整对象快照；未使用旧快照')
    marker = chat.context_pid(text)
    if marker and marker != pid:
        raise BranchExit('对象快照来自不同 Praat 进程')
    # A successful explicitly addressed ping validates legacy markerless output.
    if not marker:
        text = text.rstrip() + f'\n# praat-pid={pid}\n'
    # 这次 ping 是串行队列里排在那条「状态不明」脚本之后的消息：它回来了就说明 Praat
    # 已经把那条脚本处理完，可以解除封锁继续工作（不必重启桌面）。
    if _POISON:
        _POISON = ''
    return text


def _snapshot_wav(materials: TaskMaterials, path: Path, selection):
    # Bound source bytes before TaskMaterials decodes/allocates PCM frames.
    if not path.is_file() or path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('WAV 源文件必须为真实文件且不超过 20 MiB')
    materials.snapshot_wav(path, selection)


class _Dispatcher:
    def __init__(self, target, cancel, activity, runtime_directory):
        self.target, self.cancel, self.activity = target, cancel, activity
        self.runtime_directory = runtime_directory
        self.context = tools.ToolContext(tools.parse_object_context(target),
                                         chat.result_path(), chat.state_path())
        self.pid = chat.context_pid(target)
        self.identity = None
        for line in target.splitlines():
            if line.startswith(_IDENTITY_PREFIX):
                self.identity = json.loads(line[len(_IDENTITY_PREFIX):])
        self.last_execution = ''
        self.attempts: list[dict] = []
        self.evidence: list[Evidence] = []
        # 任务开始时的对象快照：只有这之后新建、且被后续测量用到的对象才会被清理。
        self.original_ids = frozenset(row.id for row in self.context.objects)
        self.segment_facts: list[str] = []
        self.refresh_facts: list[str] = []

    def ensure(self, *, allow_missing=()):
        # 这里允许在「状态不明」时先跑一次核对 ping（_refresh 成功后会解除封锁）：
        # 否则一次 25 秒超时就要靠重启桌面才能继续。
        _gate(self.cancel, reconcile=True)
        executable = chat.praat_executable()
        ids = chat.praat_process_ids(executable) if executable else []
        if not self.pid or not self.identity:
            raise BranchExit('没有经过验证的原 Praat 目标，未替换为当前选择')
        if self.pid not in (ids or []) or not identities_match(self.identity, process_identity(self.pid)):
            raise BranchExit('原 Praat 进程/会话身份已改变，未使用新实例')
        if chat.sendpraat.send_mode() == chat.sendpraat.ARGV_MODE:
            raise BranchExit('argv 投递不能保证原进程绑定，未执行')
        fresh = _refresh(executable, self.pid, self.cancel, self.refresh_facts)
        current = {row.id: row for row in tools.parse_object_context(fresh)}
        # 只有清理核对允许临时对象消失；原始对象仍必须存在且身份一致。
        missing_allowed = set(allow_missing) - self.original_ids
        # Conservative: validate every retained row, not just selected rows.
        # Explicit references to unselected originals are equally identity-bound.
        for row in self.context.objects:
            now = current.get(row.id)
            if now is None and row.id in missing_allowed:
                continue
            if now is None or (now.class_name, now.name) != (row.class_name, row.name):
                raise BranchExit(f'原对象 {row.id} 已消失或身份改变，未替换为当前选择')
        retained = {row.id for row in self.context.objects}
        self.context = replace(self.context, objects=tuple(
            row for row in self.context.objects if row.id in current) + tuple(
            replace(row, selected=False) for row in current.values() if row.id not in retained))
        return executable

    def execute(self, script):
        with PRAAT_LOCK:
            try:
                _gate(self.cancel)
                # Within one LocalTool segment do not ping between sends: pings
                # clear the shared results, and intermediate objects may be temporary.
                executable = chat.praat_executable()
                if not identities_match(self.identity or {}, process_identity(self.pid)):
                    raise BranchExit('原 Praat 进程身份已改变')
            except BranchExit as error:
                fact = delivery.EXECUTION_BLOCKED if _POISON else delivery.NOT_DELIVERED
                self.segment_facts.append(fact)
                self.last_execution = delivery.EXECUTION_UNKNOWN if delivery.EXECUTION_UNKNOWN in self.segment_facts else fact
                return False, [], str(error)
            ok, rows, note, fact = _send(executable, self.pid, script, self.cancel)
            self.segment_facts.append(fact)
            self.last_execution = delivery.EXECUTION_UNKNOWN if delivery.EXECUTION_UNKNOWN in self.segment_facts else fact
            return ok, rows, note

    def action(self, action, index):
        global _POISON
        with PRAAT_LOCK:
            self.last_execution = ''
            self.segment_facts = []
            self.refresh_facts = []
            name, args = str(action.get('tool', '')), action.get('arguments') or {}
            try:
                executable = self.ensure()
                environment = tools.LocalEnvironment(self.execute, executable,
                    self.runtime_directory, self.cancel.is_set)
                # Lock spans render/select/every LocalTool send/read, not just sends.
                step = chat._execute_action(action, self.context, self.execute, index, environment)
                step.execution = self.last_execution
                if self.last_execution == delivery.EXECUTION_UNKNOWN:
                    step.ok, step.results = False, []
                    step.observation += '；执行状态不明：' + _POISON
                if step.ok and name == 'rename_object':
                    row = self.context.resolve_object(args.get('object'))
                    try:
                        fresh = _refresh(executable, self.pid, self.cancel, self.refresh_facts)
                        self.context = renamed_context(self.context, fresh, row.id)
                        self.target = object_context_text(self.context.objects, self.target)
                    except (BranchExit, tools.ToolError, OSError) as error:
                        _POISON = 'rename identity cannot be reconciled: ' + str(error)
                        # The rename's delivered fact remains delivered.
                        step.note = str(error)
            except BranchExit as error:
                step = chat.AgentStep(index, name, dict(args) if isinstance(args, dict) else {},
                                      False, str(error), execution=getattr(error, 'execution',
                                      delivery.EXECUTION_BLOCKED if _POISON else delivery.NOT_DELIVERED))
            status = 'success' if step.ok else (
                'unknown' if step.execution == delivery.EXECUTION_UNKNOWN else
                'not_executed' if step.execution in {delivery.NOT_DELIVERED, delivery.EXECUTION_BLOCKED}
                or not step.script else 'failed')
            record = {'tool':step.tool, 'arguments':step.arguments, 'status':status,
                      'execution':step.execution, 'reason':step.observation,
                      'script':step.script, 'rows':list(step.results), 'deliveries':list(self.segment_facts)}
            record['refresh_deliveries'] = list(self.refresh_facts)
            if step.note:
                record['note'] = step.note
            self.attempts.append(record)
            if step.ok and step.results:
                self.evidence.append(Evidence(step.tool, step.arguments, list(step.results), self.target,
                    'operation' if tools.is_mutating(step.tool) else 'measurement'))
            self.activity('tool', name=step.tool, args=step.arguments, result=list(step.results),
                          text=step.observation, status=status, execution=step.execution)
            return step

    def cloud_action(self, name, arguments, index):
        return self.action({'tool':name, 'arguments':arguments}, index)

    def refresh_context(self):
        with PRAAT_LOCK:
            try:
                self.ensure()
            except BranchExit as error:
                raise tools.ToolError(str(error)) from error
            self.target = object_context_text(self.context.objects, self.target)
            return self.target

    #: 会新建对象的工具，以及它们结果行里的新对象编号。
    CREATING_TOOLS = frozenset({'create_sound', 'extract_part', 'duplicate_object',
                                'resample_sound', 'spectrogram', 'read_file',
                                'concatenate_sounds'})
    CREATED_ID = re.compile(r'（id\s*(\d+)）')
    #: 用户自己要求创建/保留东西时不动他的对象。
    KEEP_GOAL = re.compile(r'创建|新建|生成|截取|复制|另存|保存|导出|频谱图|拼接|重采样|读入|打开这个文件')

    def created_ids(self):
        """本任务新建、且被后续测量当成目标的对象 id（按创建顺序）。"""

        created: list[int] = []
        used: set[int] = set()
        for record in self.attempts:
            if record.get('status') != 'success':
                continue
            tool = record.get('tool')
            if tool in self.CREATING_TOOLS:
                for row in record.get('rows') or []:
                    match = self.CREATED_ID.search(row)
                    if match:
                        created.append(int(match.group(1)))
                continue
            target = (record.get('arguments') or {}).get('object')
            try:
                identifier = int(target)
            except (TypeError, ValueError):
                continue
            if identifier in created:
                used.add(identifier)
        return [identifier for identifier in created
                if identifier in used and identifier not in self.original_ids]

    def cleanup_created_objects(self, goal: str) -> list[int]:
        """删掉「只为这次测量而建」的临时对象；用户要求创建的保留。

        以「对象还在不在」为最终依据：这个环境里 Praat 偶尔会慢到拿不到完成标记
        （脚本其实已经执行），只按投递结果记账会漏报，也会留下没清掉的对象。
        """

        if self.KEEP_GOAL.search(goal or ''):
            return []
        removable = self.created_ids()
        if not removable:
            return []
        with PRAAT_LOCK:
            for identifier in removable:
                if identifier not in {row.id for row in self.context.objects}:
                    continue
                self.execute(f'selectObject: {identifier}\nRemove')
            # 完成标记丢失仍可核对；核对失败则上报，不能用旧快照冒充核对结果。
            self.ensure(allow_missing=removable)
            present = {row.id for row in self.context.objects}
            removed = [identifier for identifier in removable if identifier not in present]
            self.target = object_context_text(self.context.objects, self.target)
            return removed


def _rebind(function, namespace):
    """Task-private chat hooks without monkeypatching its process-global module.

    run_turn currently has no action callback. Rebind only its two pure function
    globals so the existing constraints/planners run unchanged, with atomic
    actions. Remove this shim if run_turn gains an execute_action parameter.
    """
    bound = FunctionType(function.__code__, namespace, function.__name__,
                         function.__defaults__, function.__closure__)
    bound.__kwdefaults__ = function.__kwdefaults__
    return bound


class ModernExecutor:
    def __init__(self, runtime_directory: Path, config_path: Path):
        self.runtime_directory, self.config_path = Path(runtime_directory), Path(config_path)
        self._guard = threading.Lock()
        self._closed = False
        self._active: set[threading.Event] = set()
        self._runtimes = {}

    def session_runtime(self, sid):
        with self._guard:
            if self._closed:
                raise RuntimeError('Modern executor is closed')
            if sid not in self._runtimes:
                self._runtimes[sid] = CloudRuntime()
            return self._runtimes[sid]

    def release_session(self, sid):
        with self._guard:
            runtime = self._runtimes.pop(sid, None)
        if runtime is not None:
            runtime.close()

    def capture_workflow_target(self, text, policy):
        return self.capture_target(text, policy=policy)

    def capture_target(self, text: str, *, policy=None) -> str:
        with self._guard:
            if self._closed:
                raise RuntimeError('Modern executor is closed')
        disabled = policy is not None and not policy.l0_enabled
        if policy is None:
            from .modern_settings import SettingsService
            cfg = SettingsService(self.config_path).candidate()
            disabled = api_is_active(cfg) and not cfg.workflow.l0_enabled
        if disabled or dialogue_kind(text) is not None:
            return ''  # No process lookup, Tk window, model call or queue.
        with PRAAT_LOCK:
            # 这里也允许核对（_refresh 成功即解除封锁）：否则一次 25 秒超时之后，
            # 连「重新捕获目标」这一步都进不来，后面每个用例都会瞬间失败。
            _gate(threading.Event(), reconcile=True)
            executable = chat.praat_executable()
            ids = chat.praat_process_ids(executable) if executable else []
            if not ids:
                return ''  # Files/concepts can still be analysed; no stale fallback.
            if len(ids) != 1:
                raise BranchExit('多个 Praat 实例，原目标进程不明确')
            pid = ids[0]
            identity = process_identity(pid)
            if not identity:
                raise BranchExit('原 Praat 进程身份无法验证')
            if chat.sendpraat.send_mode() == chat.sendpraat.ARGV_MODE:
                raise BranchExit('argv 投递不能保证目标进程绑定')
            fresh = _refresh(executable, pid, threading.Event())
            if not identities_match(identity, process_identity(pid)):
                raise BranchExit('捕获目标时 Praat 进程身份改变')
            target = target_context(text, chat._request_context(fresh, text))
            return target.rstrip() + '\n' + _IDENTITY_PREFIX + json.dumps(identity) + '\n'

    def close(self):
        with self._guard:
            self._closed = True
            for cancel in self._active:
                cancel.set()
            runtimes, self._runtimes = list(self._runtimes.values()), {}
        for runtime in runtimes:
            runtime.close()
        # Task finally blocks close their own clients/materials; never reset poison.

    def run(self, *, config: AppConfig, text: str, history: list[dict], target: str,
            cancel: threading.Event, emit: Callable[[str, dict], None],
            attachments: list[dict] = [], session_id='', task_id='', phase_context=None,
            context_epoch=0, historical_evidence=(), task_metrics=None, continuation=None) -> dict:
        with self._guard:
            if self._closed:
                raise RuntimeError('Modern executor is closed')
            self._active.add(cancel)
        config = copy.deepcopy(config)  # Never mutate the externally prepared snapshot.
        expected_identity = caps.config_identity(config.api)
        started = time.monotonic()
        activities: list[dict] = []
        deltas: list[str] = []
        materials = runtime = dispatcher = None
        state = AnalysisState(text, target, budget=Budget(config.api.max_context_tokens,
                                                        config.api.plan_max_tokens))
        if api_is_active(config):
            state.workflow = config.workflow or WorkflowPolicy()
            state.workflow_override = explicit_action(text)
        if continuation:
            state.goal = continuation['goal']
            state.deliveries = continuation.get('deliveries') or [state.goal]
            state.evidence = [Evidence(**e) for e in continuation.get('evidence', [])]
            state.attempts = copy.deepcopy(continuation.get('attempts', []))
            state.material_metadata = copy.deepcopy(continuation.get('material_metadata', {}))
            state.workflow_override = continuation['mode']
            state.continuation, state.continuation_mode = True, continuation['mode']
            state.direction = text
        state.dialogue_context = copy.deepcopy(history)
        state.phase_context = phase_context
        state.context_epoch, state.session_id, state.task_id = context_epoch, session_id, task_id
        state.historical_evidence = copy.deepcopy(list(historical_evidence))
        state.metrics.update(task_metrics or {})
        state.metrics['capability_identity'] = expected_identity
        if state.workflow is not None:
            state.metrics['workflow_policy'] = asdict(state.workflow)
            state.metrics['workflow_override'] = dict(mode=state.workflow_override, source='visible_user_action' if state.workflow_override else '')
        if phase_context is not None:
            phase_context.sync_formal(history)
        state.requests = len(state.metrics.get('request_timings', []))

        def activity(kind='progress', **payload):
            item = {'id':uuid.uuid4().hex, 'type':kind, **payload}
            activities.append(item)
            emit('activity', copy.deepcopy(item))

        def progress(line):
            # These are actual backend notices, never fabricated reasoning.
            kind = 'compression' if any(word in line for word in ('压缩', '省掉', '摘录')) else 'progress'
            activity(kind, text=str(line))

        def delta(value):
            deltas.append(value)
            emit('delta', {'text':value})

        def deliver_results(current):
            if current.results:
                current.metrics.setdefault('results_first_visible_seconds', time.monotonic() - started)
            emit('results', dict(content=current.measurement_content, results=copy.deepcopy(current.results),
                                evidence=[asdict(e) for e in current.evidence], attempts=copy.deepcopy(current.attempts),
                                target=current.context_text, metrics=copy.deepcopy(current.metrics),
                                material_metadata=copy.deepcopy(current.material_metadata), goal=current.goal,
                                verified_explanation=current.report if current.metrics.get('report_verified') else '',
                                deliveries=list(current.deliveries)))
        state.on_results = deliver_results if state.workflow is not None else None

        try:
            if cancel.is_set():
                state.status, state.report = 'cancelled', '已取消本次操作。'
            else:
                # 一轮只判一次：正则给不出答案时才问模型；结果同时供对话分派与
                # entry_mode 使用（见 praat_ai/router.py）。这里不预先建 runtime：
                # 纯测量回合不该为路由开一个模型连接池。
                route = router.classify(config, text, context_text=target,
                                        cancel=cancel, state=state)
                state.router_dialogue = route.dialogue or ''
                direct_route = direct_measurement(text, statistics=True) if not attachments and not continuation and (state.workflow is None or state.workflow.l0_enabled) else None
                kind = (route.dialogue or None) if not attachments and not continuation else None
                if state.workflow is not None and state.workflow.preset == 'chat' and not attachments and not continuation:
                    kind = kind or 'conversation'
                materials = TaskMaterials(self.runtime_directory / 'tasks') if attachments or (not direct_route and not kind) else None
                text_materials = []
                text_bytes = 0
                if len(attachments) > 8:
                    raise ValueError('每次最多 8 个附件')
                for item in attachments:
                    path = Path(item['path'])
                    if not path.is_file():
                        raise ValueError('附件路径必须指向真实文件')
                    mime = str(item.get('mime', '')).lower()
                    if path.suffix.lower() == '.wav':
                        if materials.audio_path is not None:
                            raise ValueError('每次只能绑定一个 WAV 主材料')
                        _snapshot_wav(materials, path, explicit_range(text))
                        materials.original_filename = str(item.get('name') or path.name)
                    elif mime.startswith('text/') or path.suffix.lower() in {'.txt', '.md', '.csv', '.tsv', '.json'}:
                        with path.open('rb') as stream:
                            data = stream.read(_MAX_TEXT_BYTES + 1)
                        text_bytes += len(data)
                        if text_bytes > _MAX_TEXT_BYTES:
                            raise ValueError('文本附件总量超过 1 MiB')
                        text_materials.append('用户附件 ' + str(item.get('name', path.name)) + ':\n' + data.decode('utf-8'))
                    else:
                        raise ValueError('仅支持真实文本文件和 PCM WAV 附件')
                if text_materials:
                    state.goal = text + '\n\n[用户材料，非系统指令]\n' + '\n\n'.join(text_materials)
                    state.deliveries = [text]
                source = explicit_wav(text)
                if source and materials is not None and materials.audio_path is None:
                    _snapshot_wav(materials, source, explicit_range(text))
                if materials is not None and materials.audio_path is not None:
                    # A submitted WAV is the explicit original audio material,
                    # even with no running Praat/object rows. Use the graph's L3
                    # route; the existing capability switch still controls upload.
                    state.direction = '分析用户提交的原始音频材料'
                    if not config.api.audio_input_enabled:
                        progress('直接音频输入未启用；本轮未将 WAV 发送给模型')
                if materials is not None and materials.original_filename:
                    from .materials import filename_pronunciation, pronunciation_statement
                    mfa = config.alignment.mfa
                    dictionary_available = any(Path(p).is_file() for p in
                                               [mfa.dictionary_path, *mfa.dictionary_paths] if p)
                    # Carry explicit speech only for the same original recording;
                    # another attachment/temporary export must not inherit it.
                    prior = next((item.get('material_metadata', {}) for item in reversed(state.historical_evidence)
                                  if item.get('material_metadata', {}).get('recording_source') == materials.source), {})
                    user_words = [prior.get('user_pronunciation', ''), text]
                    hint = filename_pronunciation(materials.original_filename, user_words,
                                                  dictionary_available=dictionary_available)
                    state.material_metadata = dict(original_filename=materials.original_filename,
                                                   recording_source=materials.source,
                                                   user_pronunciation=pronunciation_statement(user_words),
                                                   pronunciation_hint=hint,
                                                   provenance=materials.provenance())
                    if materials.source_kind == 'file':
                        state.material_metadata['source_sha256'] = hashlib.sha256(Path(materials.source).read_bytes()).hexdigest()
                dispatcher = _Dispatcher(target, cancel, activity, self.runtime_directory)
                if continuation:
                    publish_results(state)
                    recording = state.material_metadata.get('recording_source', '')
                    if state.continuation_mode == 'L3' and recording and Path(recording).is_file():
                        if hashlib.sha256(Path(recording).read_bytes()).hexdigest() != state.material_metadata.get('source_sha256'):
                            raise BranchExit('原始材料版本已改变；保留原证据，未发送新的录音')
                        selection = state.material_metadata.get('provenance', {}).get('range')
                        _snapshot_wav(materials, Path(recording), tuple(selection) if selection else explicit_range(state.goal))
                        if materials.fingerprint != state.material_metadata.get('provenance', {}).get('sha256'):
                            raise BranchExit('原始片段版本无法核实；未发送录音')
                        materials.original_filename = state.material_metadata.get('original_filename', '')

                def prepare_audio():
                    if state.workflow is not None and not state.workflow.l0_enabled and (materials is None or materials.audio_path is None):
                        raise BranchExit('L0 已关闭，不能导出 Praat 音频')
                    if materials is None:
                        raise BranchExit('本轮没有音频材料')
                    if materials.audio_path is not None:
                        materials.bytes()
                        return
                    if materials.snapshot_attempted:
                        raise BranchExit('原目标快照未取得，未重新导出后来变化的材料：' + materials.snapshot_error)
                    with PRAAT_LOCK:
                        try:
                            dispatcher.ensure()
                            dispatcher.last_execution = ''
                            dispatcher.segment_facts = []
                            materials.snapshot_attempted = True
                            if state.tool_attempts >= state.budget.tool_limit:
                                raise BranchExit('跨阶段工具预算不足')
                            state.tool_attempts += 1
                            row = dispatcher.context.resolve_by_class(None, tools.SOUND_CLASSES, '音频需要唯一原目标 Sound')
                            output = materials.directory / 'segment.wav'
                            lines = [f'selectObject: {row.id}', 'duration = Get total duration']
                            start, end = row.selection or (0.0, None)
                            if end is None:
                                lines += ['if duration > 120', '    exitScript: "音频超过 120 秒"', 'endif',
                                          f'Save as WAV file: {tools.quote(output)}']
                            else:
                                if end - start > 120:
                                    raise BranchExit('原目标片段超过 120 秒')
                                lines += [f'Extract part: {start:.9f}, {end:.9f}, "rectangular", 1, "no"',
                                          f'Save as WAV file: {tools.quote(output)}', 'Remove', f'selectObject: {row.id}']
                            selected = [item.id for item in tools.parse_object_context(chat.object_context()) if item.selected]
                            if selected:
                                lines += [f'selectObject: {selected[0]}'] + [f'plusObject: {identifier}' for identifier in selected[1:]]
                            script = tools._assemble(lines, dispatcher.context)
                            ok, rows, reason = dispatcher.execute(script)
                            status = 'success' if ok else ('unknown' if dispatcher.last_execution == delivery.EXECUTION_UNKNOWN
                                else 'not_executed' if dispatcher.last_execution in {delivery.NOT_DELIVERED, delivery.EXECUTION_BLOCKED} else 'failed')
                            record = {'tool':'export_original_audio', 'arguments':{}, 'status':status,
                                      'execution':dispatcher.last_execution, 'reason':reason, 'script':script, 'rows':rows,
                                      'deliveries':list(dispatcher.segment_facts)}
                            dispatcher.attempts.append(record)
                            activity('tool', name=record['tool'], result=rows, status=record['status'],
                                     execution=record['execution'], text=reason)
                            if not ok or not output.is_file():
                                raise BranchExit('原目标音频导出失败：' + reason)
                            materials.audio_path, materials.source, materials.range = output, row.label, row.selection
                            materials.source_kind = 'praat'
                            materials.bytes()
                        except (BranchExit, tools.ToolError, OSError, ValueError) as error:
                            materials.snapshot_attempted = True
                            materials.snapshot_error = str(error)
                            raise

                def correct_audio(reason):
                    saved = caps.correct_audio_setting(self.config_path, expected_identity, reason)
                    progress('当前 API 路径音频能力纠正已保存' if saved else '配置身份已改变，音频纠正仅用于本轮')

                def cloud_action(name, arguments, index):
                    if state.workflow is not None and not state.workflow.l0_enabled:
                        raise BranchExit('工作流程已关闭专用工具执行')
                    step = dispatcher.cloud_action(name, arguments, index)
                    state.context_text = dispatcher.target
                    return step

                if api_is_active(config):
                    direct = direct_route
                    if direct:
                        from .tool_guards import ToolGuards
                        from types import SimpleNamespace
                        guard_session = SimpleNamespace(state=state, schemas=tools.tool_schemas(),
                            original_object_ids={r.id for r in dispatcher.context.objects})
                        # 直接测量同样受工具预算限制，不能绕过 tool_limit 不断执行。
                        try:
                            if state.tool_attempts >= state.budget.tool_limit:
                                raise BranchExit('跨阶段工具预算不足')
                            arguments = ToolGuards(guard_session).normalize(direct['tool'], direct['arguments'])
                            state.tool_attempts += 1
                            step = dispatcher.cloud_action(direct['tool'], arguments, 1)
                        except tools.ToolError as error:
                            from .tool_guards import SchemaViolation
                            note_guard(state, 'block', ('tool.schema.' if isinstance(error, SchemaViolation) else 'tool.policy.') + direct['tool'])
                            step = chat.AgentStep(1, direct['tool'], direct['arguments'], False, str(error), execution=delivery.NOT_DELIVERED)
                            dispatcher.attempts.append(dict(tool=direct['tool'], arguments=direct['arguments'],
                                status='not_executed', execution=delivery.NOT_DELIVERED, rows=[], reason=str(error)))
                        except BranchExit as error:
                            step = chat.AgentStep(1, direct['tool'], direct['arguments'], False, str(error),
                                                  execution=delivery.EXECUTION_BLOCKED)
                            dispatcher.attempts.append(dict(tool=direct['tool'], arguments=direct['arguments'],
                                status='not_executed', execution=delivery.EXECUTION_BLOCKED, rows=[],
                                reason=str(error)))
                        state.evidence = [e for e in dispatcher.evidence if valid_measurement_rows(e.tool, e.rows)]
                        state.attempts = copy.deepcopy(dispatcher.attempts)
                        state.reason = '' if step.ok else step.observation
                        if step.ok and not valid_measurement_rows(step.tool, step.results):
                            state.reason = '工具已执行，但没有目标所需的有效测量值'
                            state.attempts[-1]['result_valid'] = False
                        state.metrics.update(requests=0, route='direct_measurement')
                        publish_results(state)
                        finish_locally(state)
                        if step.ok and state.workflow.l2_mode == 'always':
                            if runtime is None:
                                runtime = self.session_runtime(session_id) if session_id else CloudRuntime()
                            state.continuation, state.continuation_mode = True, 'L2'
                            run_cloud_turn(config, state, execute_action=cloud_action, cancel=cancel,
                                progress=progress, runtime=runtime, materials=materials,
                                prepare_audio=prepare_audio, correct_audio=correct_audio)
                    else:
                        if runtime is None:
                            runtime = self.session_runtime(session_id) if session_id else CloudRuntime()
                        if kind and state.workflow is not None and not state.workflow.l4_enabled:
                            state.reason = '工作流程已关闭通用讨论'
                            finish_locally(state)
                        elif kind:
                            run_dialogue_turn(config, state, kind=kind, cancel=cancel,
                                              progress=progress, on_text=delta, runtime=runtime)
                        else:
                            # Capture original audio before model/network work or mutations.
                            if (materials is not None and materials.audio_path is None
                                    and config.api.audio_input_enabled
                                    and (state.workflow is None or (state.workflow.l0_enabled and
                                         (state.workflow.l3_mode == 'auto' or state.workflow_override == 'L3')))
                                    and not continuation
                                    and re.search(r'(分析|发音|录音|选区|声音|选中|基频|共振峰)', text)
                                    and any(row.selected and row.class_name in tools.SOUND_CLASSES
                                            for row in dispatcher.context.objects)):
                                try:
                                    prepare_audio()
                                    progress('已建立原目标的任务临时音频快照')
                                except (BranchExit, tools.ToolError, OSError, ValueError) as error:
                                    progress('原目标音频快照未取得：' + str(error))
                            run_cloud_turn(config, state, execute_action=cloud_action,
                                cancel=cancel, progress=progress, materials=materials,
                                prepare_audio=prepare_audio, correct_audio=correct_audio, runtime=runtime,
                                refresh_context=dispatcher.refresh_context)
                else:
                    if materials is not None and materials.audio_path is not None:
                        raise BranchExit('本地 turn 适配器不支持 WAV 模型输入；未发送或伪造音频证据')
                    namespace = dict(chat.run_turn.__globals__)
                    namespace['_execute_action'] = lambda action, context, execute, index, environment=None: dispatcher.action(action, index)
                    namespace['_run_agent_turn'] = _rebind(chat._run_agent_turn, namespace)
                    run_turn = _rebind(chat.run_turn, namespace)
                    outcome = run_turn(qwen.QwenClient(config.qwen), user_text=state.goal,
                        context_text=target, history=copy.deepcopy(history), context=dispatcher.context,
                        execute=dispatcher.execute, on_progress=progress,
                        refresh_context=dispatcher.refresh_context, cancel=cancel)
                    state.report, state.status = outcome.reply, 'partial' if outcome.failure else 'complete'
                    state.evidence = dispatcher.evidence
                    for note in outcome.notes:
                        progress(note)
                    state.metrics = {'route':'local'}
        except Exception as error:
            state.reason = caps.safe_error(error, config.api.api_key)
            if config.qwen.api_key and config.qwen.api_key != 'EMPTY':
                state.reason = state.reason.replace(config.qwen.api_key, '[redacted]')
            state.report = ''.join(deltas) or ('本次任务未完成：' + state.reason)
            state.status = 'partial' if deltas or (dispatcher and dispatcher.evidence) else 'failed'
            if dispatcher and not state.evidence:
                state.evidence = dispatcher.evidence
        finally:
            for resource in (runtime, materials):
                if resource is not None:
                    if resource is runtime and session_id:
                        continue
                    try:
                        resource.close(wait=True) if resource is runtime else resource.close()
                    except Exception as error:
                        state.metrics.setdefault('cleanup_errors', []).append(caps.safe_error(error, config.api.api_key))
            with self._guard:
                self._active.discard(cancel)
        if cancel.is_set():
            state.status = 'cancelled'  # Does not change delivered/unknown facts.
            if deltas:
                state.report = ''.join(deltas)
        analysis_content = state.report
        formal_content = state.report
        if state.measurement_content and not formal_content.startswith(state.measurement_content):
            formal_content = state.measurement_content + '\n\n' + formal_content
            state.report = formal_content
        # 遮过数值仍交付的正文也算正式回合：它已经显示给用户，
        # 下一轮追问需要能引用它。
        formal = not cancel.is_set() and (state.status == 'complete'
                                          or state.metrics.get('report_verified', False)
                                          or state.metrics.get('report_repaired', False))
        if formal and state.phase_contexts.get('dialogue'):
            state.phase_contexts['dialogue'].mark_formal([*history, dict(role='user', content=text),
                                                        dict(role='assistant', content=formal_content)])
        # 交付项状态不再拼进正文（读起来像系统日志）：改为结构化字段随结果返回，
        # 由前端渲染成看板；正文只留模型自己写的结论。
        if state.coverage and state.metrics.get('route_reason') != 'measurement_complete':
            state.metrics['coverage'] = [dict(item) for item in state.coverage]
        # 为满足「只看这一段」而创建的对象在测量后清理，避免留在对象列表里
        # 被下一轮当作测量对象；用户明确要求创建的对象不清理。
        if dispatcher and not cancel.is_set():
            try:
                # 空列表表示没有候选；字段缺失表示没有进入清理流程。
                state.metrics['cleanup_candidates'] = dispatcher.created_ids()
                removed = dispatcher.cleanup_created_objects(state.goal)
            except (BranchExit, tools.ToolError, OSError) as error:
                removed = []
                note = caps.safe_error(error, config.api.api_key)
                state.metrics.setdefault('cleanup_errors', []).append(note)
                activity('progress', text='临时对象清理结果未能确认：' + note)
            if removed:
                state.metrics['cleaned_objects'] = removed
                activity('progress', text='已清理本次分析临时创建的对象：' + '、'.join(str(i) for i in removed))
        if not deltas and state.report:
            delta(state.report)
        state.metrics.setdefault('total_seconds', time.monotonic() - started)
        state.metrics.update(audio_input=state.audio_sent, audio_received=state.audio_received,
                             audio_report_completed=state.audio_report_completed)
        attempts = list(dispatcher.attempts) if dispatcher else []
        for attempt in attempts:
            annotated = next((a for a in reversed(state.attempts)
                              if a.get('tool') == attempt.get('tool')
                              and a.get('arguments') == attempt.get('arguments')
                              and a.get('result_valid') is False), None)
            if annotated is not None:
                attempt['result_valid'] = False
        # Graph-only schema/cache/policy refusals never reached the dispatcher.
        for item in state.attempts:
            if item.get('status') == 'not_executed' and not any(
                    item.get('tool') == a['tool'] and item.get('arguments') == a['arguments']
                    and item.get('reason') == a['reason'] for a in attempts):
                attempts.append(item)
                activity('tool', name=item.get('tool'), args=item.get('arguments', {}),
                         text=item.get('reason', ''), status='not_executed', execution=item.get('execution', ''))
        return {'content':state.report, 'status':state.status, 'activities':activities,
                'formal_content':formal_content, 'formal':bool(formal), 'model_contexts':state.phase_contexts,
                'material_metadata':state.material_metadata, 'results':state.results,
                'result_content':state.measurement_content, 'analysis_content':analysis_content,
                'goal':state.goal, 'deliveries':state.deliveries,
                'coverage':[dict(item) for item in state.coverage],
                'evidence':[asdict(item) for item in state.evidence], 'attempts':attempts,
                'target':dispatcher.target if dispatcher else target, 'metrics':state.metrics,
                'audio_observations':copy.deepcopy(state.audio_observations)}
