"""Cloud staircase boundary to the existing Tk queue and Praat dispatcher."""
from __future__ import annotations

import copy
import re
from dataclasses import asdict, replace
from pathlib import Path

from . import delivery, model_capabilities as caps, tools
from .escape_policy import AnalysisState, Budget, BranchExit
from .escape_policy import Evidence
from .dialogue_policy import direct_measurement, force_high
import time
from .materials import TaskMaterials


def bound_context(original: tools.ToolContext, fresh_text: str) -> tools.ToolContext:
    fresh = tools.parse_object_context(fresh_text)
    by_id = {row.id:row for row in fresh}
    for row in original.objects:
        if row.selected:
            current = by_id.get(row.id)
            if current is None or (current.name, current.class_name) != (row.name, row.class_name):
                raise BranchExit('原目标对象已消失或身份改变；未替换为当前选中的对象')
    # Existing rows retain selection/range. Newly created objects remain available
    # by explicit id but cannot silently become the default original target.
    original_ids = {row.id for row in original.objects}
    return replace(original, objects=tuple(original.objects) + tuple(
        replace(row, selected=False) for row in fresh if row.id not in original_ids))


def renamed_context(original: tools.ToolContext, fresh_text: str, identifier: int) -> tools.ToolContext:
    """Adopt one app-confirmed rename; retain original selection and range."""
    fresh = {row.id:row for row in tools.parse_object_context(fresh_text)}
    previous = original.resolve_object(identifier)
    current = fresh.get(identifier)
    if current is None or current.class_name != previous.class_name:
        raise BranchExit('重命名后原目标 ID/class 无法确认，禁止后续投递')
    updated = replace(original, objects=tuple(
        replace(row, name=current.name) if row.id == identifier else row for row in original.objects))
    return bound_context(updated, fresh_text)


def object_context_text(objects, previous_text: str) -> str:
    metadata = [line for line in previous_text.splitlines()[1:] if not line.split('\t',1)[0].isdigit()]
    lines = ['id\tclass\tname\tselected\tsel_start\tsel_end']
    lines.extend('\t'.join([str(row.id),row.class_name,row.name,'1' if row.selected else '0',
                            str(row.selection_start) if row.selection_start is not None else '',
                            str(row.selection_end) if row.selection_end is not None else '']) for row in objects)
    return '\n'.join(lines + metadata) + '\n'


def explicit_wav(goal: str) -> Path | None:
    match = re.search(r'([A-Za-z]:[\\/][^\n"<>|?]*?\.wav)(?=[\s"。；,，]|$)', goal, re.I)
    return Path(match.group(1).strip()) if match else None


def explicit_range(goal: str) -> tuple[float, float] | None:
    match = re.search(r'(\d+(?:\.\d+)?)\s*(?:秒)?\s*(?:[-–—~～]|到|至)\s*(\d+(?:\.\d+)?)\s*秒', goal)
    selection = tuple(map(float, match.groups())) if match else None
    if selection and selection[1] <= selection[0]:
        raise BranchExit('原目标片段终点必须晚于起点')
    return selection


def target_context(goal: str, text: str) -> str:
    """Resolve explicit original target/range before snapshots or continuation."""
    rows = tools.parse_object_context(text)
    references = re.findall(r'(?:对象\s*(?:编号|id|#)?\s*|(?:编号|id|#)\s*|(?:Sound|LongSound)\s+)(\d+)(?!\d)|(\d+)\s*号(?:对象|声音)?', goal, re.I)
    identifiers = {int(left or right) for left, right in references}
    named = {row.id for row in rows if any(label and label in goal for label in (
        '"' + row.name + '"', '“' + row.name + '”', row.class_name + ' ' + row.name))}
    targets = identifiers | named
    known = {row.id for row in rows}
    missing_ids = sorted(targets - known)
    # 用户点名但不在对象列表里的名字也要记下来：否则模型只能自己猜，
    # 可能答成「未收到音频」而不是说明该对象不存在。
    quoted = {name.strip() for name in re.findall(r'[“"「\']([^”"」\']{1,60})[”"」\']', goal) if name.strip()}
    quoted |= {name.strip() for name in re.findall(
        r'(?:名为|名字(?:是|叫|为)|叫作|叫做|named?)\s*([^\s，,。；;的]{1,60})', goal, re.I) if name.strip()}
    missing_names = sorted(name for name in quoted
                           if not any(name == row.name or name in row.name_variants() for row in rows))
    if missing_ids and not (targets - set(missing_ids)):
        # 一个都不存在：仍然硬拒，绝不替换成当前选中对象（对象选错=结论全错）。
        raise BranchExit('明确指定的原目标不在对象快照中，未替换为当前选择')
    targets -= set(missing_ids)
    note = ''
    if missing_ids or missing_names:
        parts = [str(item) for item in missing_ids] + [f'「{name}」' for name in missing_names]
        note = ('# 用户提到但这些对象不在当前 Praat 对象列表里：' + '、'.join(parts)
                + '；只能就现有对象测量，并在回答里明确说明它们不存在，不要改用别的对象。')
    selection = explicit_range(goal)
    if not targets and not selection:
        return text + ('\n' + note if note else '')
    selected_ids = targets or {row.id for row in rows if row.selected}
    bound = [replace(row, selected=row.id in selected_ids,
                     selection_start=selection[0] if selection and row.id in selected_ids else row.selection_start,
                     selection_end=selection[1] if selection and row.id in selected_ids else row.selection_end) for row in rows]
    return object_context_text(bound, text + ('\n' + note if note else ''))


def process_cloud(window, text: str):
    from . import chat
    from .cloud_agent import run_cloud_turn, entry_mode

    config = copy.deepcopy(window.config)  # In-flight settings cannot mutate new choices.
    expected_identity = caps.config_identity(config.api)
    progress = lambda line:window.messages.put(('hint', line))
    response_budget = config.api.plan_max_tokens if config.api.limit_tokens else 8192
    direct = direct_measurement(text) if window.pending_analysis is None and not force_high(config.api) else None
    from .cloud_agent import CloudRuntime, dialogue_kind, run_dialogue_turn
    kind = dialogue_kind(text) if window.pending_analysis is None else None
    if kind is not None:
        if getattr(window, 'cloud_runtime', None) is None: window.cloud_runtime = CloudRuntime()
        state = AnalysisState(text, '', budget=Budget(config.api.max_context_tokens, response_budget))
        state.dialogue_context = [{"role":item["role"], "content":str(item.get("content", ""))[:800]}
                                  for item in window.history[-4:] if item.get('role') in {'user','assistant'}]
        window.analysis_state = state
        window.store.secrets = tuple(value for value in set(window.store.secrets) | {config.api.api_key, config.qwen.api_key}
                                     if value and value != 'EMPTY')
        window.store.append(window.session_id, 'user', {'prompt':text, 'continuation':False})
        window.messages.put(('assistant-start', ''))
        try:
            run_dialogue_turn(config, state, kind=kind, cancel=window.cancel_event, progress=progress,
                on_text=lambda delta:window.messages.put(('assistant-delta', delta)), runtime=window.cloud_runtime)
        except Exception as error:
            state.status = 'partial'; state.report = '本次回复未完成：'+caps.safe_error(error,config.api.api_key)
        window.messages.put(('assistant-final', state.report))
        window.history.extend([{'role':'user','content':text},{'role':'assistant','content':state.report}])
        window.store.append(window.session_id, 'turn', {'goal':text,'report':state.report,'coverage':[],
            'status':state.status,'requests':state.requests,'metrics':state.metrics,'events':state.events,
            'model':config.api.model,'api_path':caps.path_identity(config.api)})
        return
    state = window.pending_analysis
    window.pending_analysis = None
    if state is None:
        context_text = chat._request_context(chat.object_context(), text)
        binding_error = ''
        try:
            context_text = target_context(text, context_text)
        except BranchExit as error:
            binding_error, context_text = str(error), ''
        state = AnalysisState(text, context_text,
                              budget=Budget(config.api.max_context_tokens, response_budget))
        state.dialogue_context = [{"role":item["role"], "content":str(item.get("content", ""))[:800]}
                                  for item in window.history[-4:] if item.get("role") in {"user", "assistant"}]
        state.reason = binding_error
        materials = None if direct else TaskMaterials(chat.runtime_dir() / 'tasks')
        if materials is not None: window.task_materials.append(materials)
        window.current_materials = materials
        source = explicit_wav(text)
        if source:
            try:
                materials.snapshot_wav(source, explicit_range(text))
                state.events.append({'mode':'material', 'reason':'用户源文件的窗口内快照', 'source':str(source)})
            except (OSError, ValueError) as error:
                state.reason = '用户音频文件未取得：' + str(error)
                materials.snapshot_error = state.reason
    else:
        state.budget = Budget(config.api.max_context_tokens, response_budget)
        materials = window.current_materials
    window.analysis_state = state
    window.store.secrets = tuple(value for value in set(window.store.secrets) | {config.api.api_key, config.qwen.api_key}
                                 if value and value != 'EMPTY')
    window.store.append(window.session_id, 'user', {'prompt':text, 'original_goal':state.goal, 'continuation':state.continuation})
    original = tools.ToolContext(tools.parse_object_context(state.context_text), chat.result_path(), chat.state_path())
    original_pid = chat.context_pid(state.context_text)
    ready = False
    execution_unknown = False
    #: 最近一次投递的事实（delivery.DELIVERED / NOT_DELIVERED / …），挂到 AgentStep 上。
    last_execution = ''

    def ensure_praat():
        nonlocal ready
        if window.cancel_event.is_set():
            raise BranchExit('用户已取消，未发出 Praat 操作')
        executable = chat.praat_executable()
        ids = chat.praat_process_ids(executable) if executable else None
        if not executable or not chat.praat_process_running_from(ids):
            raise BranchExit('Praat 操作所需能力不可用：没有运行中的 Praat；已有证据和材料仍可分析')
        if original_pid and original_pid not in (ids or []):
            raise BranchExit('原 Praat 进程已退出，不能用新实例的同编号对象替换原材料')
        if not ready:
            ok, note = chat.refresh_object_context(executable, ids, assume_fresh=False)
            if not ok:
                raise BranchExit('Praat 状态无法确认：' + note)
            ready = True
        bound_context(original, chat.object_context())
        return executable

    def execute(script):
        nonlocal execution_unknown, last_execution
        if execution_unknown:
            # 上一条指令结果不明：这条按设计**没有投递**，是「未执行」，不是又一条
            # 「状态不明」。统一成 unknown 会让调用方把闸门关得更死（见 delivery.py）。
            last_execution = delivery.EXECUTION_BLOCKED
            return False, [], '本轮已因上一条指令的结果无法确认而停止投递 Praat 操作；请用已有证据报告'
        try:
            executable = ensure_praat()
        except BranchExit as error:
            last_execution = delivery.NOT_DELIVERED
            return False, [], str(error)
        outcome: list[str] = []
        ok, output = chat._send_script(executable, script, cancel=window.cancel_event, outcome=outcome)
        if not ok:
            if outcome and outcome[-1] == delivery.NOT_DELIVERED:
                # 消息文件写不进去、找不到窗口…：Praat 什么都没做。这一条是确定的
                # 失败，按一次有区别的修正处理，**不能**把整轮投递闸门关上。
                last_execution = delivery.NOT_DELIVERED
                return False, [], '脚本没有投递给 Praat（Praat 没有收到这条指令）：' + (
                    output or '投递失败，原因不明')
            # 交出去了但没等到完成标记（超时/取消/完成标记对不上）：这才是状态不明，
            # 不再盲目投递，按设计暂停这条分支并用已有证据收尾。
            execution_unknown = True
            last_execution = delivery.EXECUTION_UNKNOWN
            return False, [], output or '执行状态不明，未再投递'
        last_execution = delivery.DELIVERED
        failure = chat._read_failure()
        return (False, chat._read_results(), failure) if failure else (True, chat._read_results(), '')

    def execute_action(name, arguments, index):
        nonlocal original, ready, execution_unknown, last_execution
        last_execution = ''
        try:
            ensure_praat()
            context = bound_context(original, chat.object_context())
        except BranchExit as error:
            return chat.AgentStep(index, name, arguments, False, str(error))
        environment = tools.LocalEnvironment(execute=execute, praat_executable=chat.praat_executable(),
                                             runtime_directory=chat.runtime_dir(), cancelled=window.cancel_event.is_set)
        step = chat._execute_action({'tool':name, 'arguments':arguments}, context, execute, index, environment)
        # 把投递事实带出去：调用方不能只靠说明文字猜「执行状态不明」。
        step.execution = last_execution
        if step.ok and name == 'rename_object':
            row = context.resolve_object(arguments.get('object'))
            try:
                # Refresh directly: the previous binding still carries the old name.
                executable = chat.praat_executable()
                ids = chat.praat_process_ids(executable)
                if original_pid and original_pid not in (ids or []):
                    raise BranchExit('重命名后原 Praat 进程已退出')
                ok, note = chat.refresh_object_context(executable, ids, assume_fresh=False)
                if not ok:
                    raise BranchExit('重命名后状态无法确认：' + note)
                original = renamed_context(context, chat.object_context(), row.id)
                state.context_text = object_context_text(original.objects, state.context_text)
                ready = True
                state.events.append({'mode':'binding', 'reason':'app_confirmed_rename', 'id':row.id,
                                     'previous_name':row.name, 'name':original.resolve_object(row.id).name})
            except (BranchExit, tools.ToolError, OSError) as error:
                # The rename succeeded, but its identity cannot safely be used again.
                execution_unknown = True
                state.reason = str(error)
                state.events.append({'mode':'binding', 'reason':str(error)})
        return step

    def refresh_context():
        nonlocal original, ready
        ready = False
        ensure_praat()
        original = bound_context(original, chat.object_context())
        state.context_text = object_context_text(original.objects, state.context_text)
        return state.context_text

    def prepare_audio():
        if materials is None: raise BranchExit('直接测量不准备音频材料')
        if materials.audio_path is not None:
            return
        if materials.snapshot_attempted:
            raise BranchExit('原目标快照此前未取得，未用后来变化的材料重新导出：' + materials.snapshot_error)
        materials.snapshot_attempted = True
        sounds = [row for row in original.objects if row.selected and row.class_name in tools.SOUND_CLASSES]
        if len(sounds) != 1:
            raise BranchExit("直接音频输入需要明确一个主目标 Sound；多个对象的测量仍可继续")
        ensure_praat()
        if state.tool_attempts >= state.budget.tool_limit:
            raise BranchExit('跨阶段工具预算不足，未导出新音频')
        state.tool_attempts += 1
        row = original.resolve_by_class(None, tools.SOUND_CLASSES, '原目标需要 Sound 或 LongSound')
        output = materials.directory / 'segment.wav'
        lines = [f'selectObject: {row.id}', 'duration = Get total duration']
        start, end = row.selection or (0.0, None)
        if end is None:
            lines.extend(['if duration > 120', '    exitScript: "原目标音频超过 120 秒，请缩小目标片段"', 'endif'])
            lines.append(f'Save as WAV file: {tools.quote(output)}')
        else:
            if end - start > 120:
                raise BranchExit('原目标片段超过 120 秒，请缩小范围')
            lines.extend([f'Extract part: {start:.9f}, {end:.9f}, "rectangular", 1, "no"',
                          f'Save as WAV file: {tools.quote(output)}', 'Remove', f'selectObject: {row.id}'])
        selected = [r.id for r in tools.parse_object_context(chat.object_context()) if r.selected]
        if selected:
            lines.append(f'selectObject: {selected[0]}')
            lines.extend(f'plusObject: {identifier}' for identifier in selected[1:])
        script = tools._assemble(lines, original)
        ok, rows, failure = execute(script)
        state.attempts.append({'tool':'export_original_audio', 'status':'success' if ok else 'failed',
                               'execution':last_execution, 'script':script, 'reason':failure, 'rows':rows})
        if not ok or not output.is_file():
            raise BranchExit('原目标音频导出失败：' + (failure or '未生成目标文件'))
        materials.audio_path, materials.source, materials.range = output, row.label, row.selection
        materials.source_kind = 'praat'
        materials.bytes()  # Validate before any upload.

    def correct_audio(reason):
        if caps.correct_audio_setting(chat.config_path(), expected_identity, reason):
            progress('已持久关闭当前 API 路径的直接音频分析设置；本轮继续使用已有证据')
            window.messages.put(('reload', ''))
        else:
            progress('配置已改变，旧请求的音频能力纠正仅用于本轮，未覆盖新配置')

    try:
        # 在对象或选区变化前保存任务材料；仅音频输入启用且任务需要时导出。
        if (materials is not None and materials.audio_path is None and not state.continuation
                and config.api.audio_input_enabled
                and entry_mode(state.goal, state.context_text) == 'L0'
                and re.search(r'(分析|发音|录音|选区|声音|选中|基频|共振峰)', state.goal)
                and any(row.selected and row.class_name in tools.SOUND_CLASSES for row in original.objects)):
            try:
                prepare_audio()
                progress('已建立原目标片段的窗口内临时快照；关闭后清理，不长期保存音频')
            except (BranchExit, tools.ToolError, OSError, ValueError) as error:
                materials.snapshot_error = str(error)
                state.events.append({'mode':'material', 'reason':str(error)})
                progress('原目标片段快照未取得：' + str(error))
        if direct:
            started = time.monotonic()
            state.event('L0', '明确时长请求直接调用既有测量工具')
            state.tool_attempts = 1
            step = execute_action(direct['tool'], direct['arguments'], 1)
            state.attempts.append({'tool':step.tool, 'arguments':direct['arguments'],
                'status':'success' if step.ok else 'failed','reason':step.observation,'script':step.script,'rows':step.results})
            if step.ok and step.results:
                state.add_evidence(Evidence(step.tool, direct['arguments'], list(step.results), state.context_text))
                state.report, state.status = '\n'.join(step.results), 'complete'
            else:
                state.report, state.status = '时长测量未完成：'+step.observation, 'partial'
            state.can_continue = False
            state.metrics = {'total_seconds':time.monotonic()-started, 'requests':0, 'first_text_seconds':None,
                             'effective_thinking_level':None, 'route':'direct_measurement'}
        else:
            if getattr(window, 'cloud_runtime', None) is None: window.cloud_runtime = CloudRuntime()
            run_cloud_turn(config, state, execute_action=execute_action, cancel=window.cancel_event,
                           progress=progress, materials=materials, prepare_audio=prepare_audio, correct_audio=correct_audio,
                           runtime=window.cloud_runtime, refresh_context=refresh_context)
        labels = {'complete':'完成', 'partial':'部分完成', 'missing':'缺证据'}
        coverage = '\n'.join('- ' + item['item'] + '：' + labels[item['status']] + '。' + item['explanation'] for item in state.coverage)
        body = state.report + ('\n\n交付项状态：\n' + coverage if coverage else '')
        window.messages.put(('assistant', body))
        window.history.extend([{'role':'user', 'content':text}, {'role':'assistant', 'content':body}])
        window.store.append(window.session_id, 'turn', {
            'goal':state.goal, 'report':state.report, 'coverage':state.coverage, 'status':state.status,
            'evidence':[asdict(e) for e in state.evidence], 'attempts':state.attempts, 'events':state.events,
            'target':state.context_text, 'materials':materials.provenance() if materials is not None else None, 'requests':state.requests,
            'audio_input':state.audio_sent, 'audio_response_received':state.audio_received,
            'audio_report_completed':state.audio_report_completed, 'audio_observations':state.audio_observations,
            'previous_report':state.previous_report, 'previous_coverage':state.previous_coverage,
            'model':config.api.model, 'api_path':caps.path_identity(config.api),
            'metrics':state.metrics,
        })
        if state.can_continue and state.status != 'cancelled':
            window.messages.put(('choices', ''))
    finally:
        if getattr(window, '_closed', False):
            window.cleanup_materials()
