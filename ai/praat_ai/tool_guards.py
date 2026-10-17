"""Application checks attached to Pydantic AI's native tool Hooks.

Only the dispatcher executes Praat. These checks resolve real objects and
validate/render proposals before that dispatcher can receive them.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from dataclasses import replace
from pathlib import Path

from jsonschema import Draft202012Validator
from pydantic_ai import ModelRetry
from pydantic_ai.capabilities import Hooks
from pydantic_ai.exceptions import SkipToolExecution

from . import tools
from .escape_policy import BranchExit, Evidence, note_guard


class SchemaViolation(tools.ToolError):
    """参数写法不符合工具 schema（不是测量失败、也不是策略越界）。

    执行前挡下的提案错误一律不记进 ``state.failures``（见
    :meth:`ToolGuards.before_execute`）；这个子类只用来把「参数写法/参数表」这一类
    从别的策略拒绝里分出来，方便测试和日志辨认。
    """


class StrictContext(tools.ToolContext):
    def resolve_object(self, requested=None):
        if requested in (None, ''):
            selected = [row for row in self.objects if row.selected]
            if len(selected) == 1:
                return selected[0]
            if not selected and len(self.objects) == 1:
                return self.objects[0]
            raise tools.ToolError('没有唯一目标对象，请使用快照中的明确编号。')
        normalized = tools._normalize_text(requested)
        identifier = re.fullmatch(r'(?:id\s*)?#?(\d+)\s*(?:号|对象)?', normalized)
        matches = [row for row in self.objects if (
            row.id == int(identifier.group(1)) if identifier else normalized in row.name_variants())]
        if len(matches) != 1:
            raise tools.ToolError(f'对象“{requested}”没有唯一精确匹配；不得猜测编号或部分名称。')
        return matches[0]


_TYPE = r'Sound|LongSound|Pitch|Formant|Intensity|TextGrid|Spectrum|Ltas|PointProcess|Harmonicity|Spectrogram'
_TARGET_ID = re.compile(r'(?:对象\s*(?:编号|id|#)?\s*|(?:编号|id|#)\s*|(?:' + _TYPE + r')\s+)(\d+)(?!\d)|(\d+)\s*号(?:对象|声音)?', re.I)
_TYPE_ID = re.compile(r'\b(' + _TYPE + r')\s+(\d+)\b', re.I)
_NAMES = re.compile(r'(?:命名为|命名成|改名为|改成名字|重命名为|名称(?:为|是|设为)|名字(?:为|是|叫)|叫作|叫做|取名(?:为)?|named?\s+|rename\s+(?:to\s+)?)(?:\s*[:：=]?\s*)(?:["“「](.*?)["”」]|([^\s，,。；;\n]+))', re.I)
_SCRIPT_SELECT = re.compile(r'^\s*(?:selectObject|plusObject|minusObject)\s*:\s*(.*?)\s*$', re.I | re.M)
_SCRIPT_RENAME = re.compile(r'^\s*Rename\s*:\s*(.*?)\s*$', re.I | re.M)
_SCRIPT_NEW_NAME = re.compile(r'^\s*(?:\w+\s*=\s*)?(?:Create [A-Za-z ()]+|Copy|Extract part)\s*:\s*"((?:[^"]|"")*)"', re.I | re.M)
_NON_PRAAT = re.compile(r'^\s*(?:import\b|from\s+\w+\s+import\b|def\s+\w+\s*\(|class\s+\w+\s*[:{]|for\s+\w+\s+in\b|(?:if|while|elif|else)\b[^\n]*:\s*$|(?:const|let|var|function)\s+\w+|(?:console\.log|print|require)\s*\(|(?:pip|npm|python|powershell)\s+|(?:\$\w+\s*=)|#!)', re.I | re.M)
_CREATES = {'create_sound', 'extract_part', 'concatenate_sounds', 'duplicate_object',
            'resample_sound', 'read_file', 'spectrogram', 'custom_script', 'run_praat_script'}


def _number(value):
    try:
        result = float(value)
        return result if math.isfinite(result) else None
    except (TypeError, ValueError):
        return None


def _names(state):
    # Original goal and this turn's user continuation are trusted naming inputs.
    return {next(value for value in match.groups() if value).strip()
            for match in _NAMES.finditer(state.goal + '\n' + state.direction)}


def _duration(state, row):
    for evidence in reversed(state.evidence):
        if evidence.kind != 'measurement' or evidence.tool not in {'duration', 'object_info', 'measure'}:
            continue
        context = StrictContext(tools.parse_object_context(evidence.source), Path('result'), Path('state'))
        try:
            target = context.resolve_object(evidence.arguments.get('object'))
        except tools.ToolError:
            continue
        if (target.id, target.class_name, target.name) != (row.id, row.class_name, row.name):
            continue
        if evidence.tool == 'measure' and not any('duration' in str(evidence.arguments.get(key, '')) for key in ('parameter', 'parameters')):
            continue
        for line in evidence.rows:
            match = re.search(r'(?:总时长|时长|duration)\s*(?:=|：|:)\s*(\d+(?:\.\d+)?)\s*(?:秒|s\b)', line, re.I)
            if match:
                return float(match.group(1))
    return None


class ToolGuards:
    def __init__(self, session):
        self.session = session
        self.validators = {schema['function']['name']: Draft202012Validator(schema['function']['parameters'])
                           for schema in session.schemas}
        self.hooks = Hooks(before_tool_execute=self.before_execute,
                           after_tool_execute=self.after_execute,
                           id='praat_tool_guards')

    def context(self, source=None):
        state = self.session.state
        from .chat import _request_context
        from .cloud_workflow import target_context
        text = source if source is not None else state.context_text
        text = _request_context(target_context(state.goal, text), state.goal)
        return StrictContext(tools.parse_object_context(text), Path('guard-result.tsv'), Path('guard-state.tsv'))

    def parameter(self, raw):
        """接受单个 measure 参数名、分隔字符串或列表，按参数表逐个校验并规范化为逗号串。"""

        if isinstance(raw, (list, tuple, set)):
            raw = ','.join(str(item) for item in raw)
        try:
            entries = tools._measure_entries({'parameter': raw})
        except tools.ToolError as error:
            raise SchemaViolation(str(error)) from error
        return ','.join(entry.parameter for entry in entries)

    def normalize(self, name, arguments, *, source=None):
        args = dict(arguments)
        # These are application-owned scope flags, never model assertions.
        args.pop('_whole_object', None)
        properties = self.validators[name].schema.get('properties', {})
        # 范围端点的同义写法要**双向**折叠：原来只把 from→start 折过去，声明 from/to 的
        # 工具收到 finish 时既不折叠也不报错，渲染会直接忽略它并按整段测量。
        # 方向按工具声明的那一对为准：声明 start 的用 start/end，只声明 from 的用
        # from/to；两个都声明的以 start/end 为准，只往一个方向搬运。
        # 同时声明两个键的工具（extract_part / textgrid_set_interval）以 start/end 为准。
        pairs = (('start', ('from',)), ('end', ('to', 'finish'))) if 'start' in properties else (
            ('from', ('start',)), ('to', ('end', 'finish')))
        for primary, aliases in pairs:
            for alias in aliases:
                if alias not in args:
                    continue
                if primary not in args:
                    args[primary] = args[alias]
                elif _number(args[primary]) != _number(args[alias]):
                    raise tools.ToolError(f'{primary} 与 {alias} 的范围互相矛盾。')
                args.pop(alias, None)
        if 'parameter' in properties:
            # 归一到逗号串后 schema 才只看格式，逐个名字的合法性由参数表负责报中文清单。
            args['parameter'] = self.parameter(args.get('parameter'))
        errors = sorted(self.validators[name].iter_errors(args), key=lambda e:str(e.path))
        if errors:
            raise SchemaViolation('JSON Schema 校验失败：' + '；'.join(e.message for e in errors))
        context = self.context(source)
        state = self.session.state
        by_id = {row.id:row for row in context.objects}
        for match in _TYPE_ID.finditer(state.goal):
            row = by_id.get(int(match.group(2)))
            if row is None or row.class_name.casefold() != match.group(1).casefold():
                raise tools.ToolError('用户指定的编号和类型与实际快照不符，不得替换目标。')
        for key in ('object', 'object2', 'textgrid'):
            if key in args and args[key] not in (None, ''):
                args[key] = context.resolve_object(args[key]).id
        allowed_names = _names(state)
        required = set(self.validators[name].schema.get('required') or ())
        for key in ('name', 'new_name'):
            if not args.get(key):
                continue
            if str(args[key]).strip() in allowed_names:
                continue
            if key in required:
                # 重命名这类工具：写错名字是用户可见的破坏性操作，必须用用户给的名字。
                raise tools.ToolError(f'{key} 必须是用户明确提供的新名字；没有新名字时省略名称参数。')
            # 名字可选的工具（截取片段、复制、生成频谱图等）只是给新对象命名：
            # 模型给出无害名称不应让整个步骤失败。
            args.pop(key, None)
        if name == tools.CUSTOM_SCRIPT_TOOL:
            args['script'] = self.script(str(args.get('script') or ''), context, allowed_names)
        elif name in tools.TOOL_MAP:
            # Fixed templates already own the full command/type validation.
            script = tools.render(name, args, context)
            if 'object' in properties and args.get('object') in (None, ''):
                match = re.search(r'^selectObject:\s*(\d+)\s*$', script, re.M)
                if match:
                    args['object'] = int(match.group(1))
        elif name == tools.RUN_SCRIPT_TOOL:
            args['object'] = context.resolve_by_class(args.get('object'), tools.SOUND_CLASSES, '外部脚本需要 Sound 或 LongSound').id
            if args.get('textgrid'):
                context.require_class(context.resolve_object(args['textgrid']), frozenset({'TextGrid'}), 'textgrid 必须是 TextGrid')
        row = context.resolve_object(args['object']) if args.get('object') is not None else None
        specified = {int(a or b) for a, b in _TARGET_ID.findall(state.goal)}
        # A derived object becomes eligible only after the app refresh verifies it.
        original_ids = self.session.original_object_ids
        if row and specified and row.id not in specified and row.id in original_ids:
            raise tools.ToolError('本次调用没有尊重用户指定的对象编号；不能改用另一个已有对象。')
        required_type = re.search(r'(?:这个|当前|选中的?|指定的?)\s*(' + _TYPE + r')\b', state.goal, re.I)
        if row and required_type and row.id in original_ids and row.class_name.casefold() != required_type.group(1).casefold():
            raise tools.ToolError('本次调用没有尊重用户指定的对象类型。')
        self.scope(name, args, row, properties)
        return args

    def scope(self, name, args, row, properties):
        if row is None:
            return
        from .chat import _whole_recording_request
        whole = _whole_recording_request(self.session.state.goal)
        ranged = any(key in properties for key in ('from', 'to', 'start', 'end'))
        if not ranged:
            if whole:
                args['_whole_object'] = True
            return
        start_key, end_key = ('start', 'end') if 'start' in properties else ('from', 'to')
        start, end = args.get(start_key), args.get(end_key)
        duration = _duration(self.session.state, row)
        if whole:
            args['_whole_object'] = True
            # Explicit subdivisions are needed for a pitch trajectory; they must
            # have real bounds and must not be silently borrowed from an editor.
            pitch_measure = name == 'pitch_statistics' or (name == 'measure' and
                all(parameter.startswith('pitch_') for parameter in tools._measure_names(args)))
            segmented_pitch = (pitch_measure and
                bool(re.search(r'音调|语调|重音|音高走向|基频走向|标准音|对比|比较|发音|练习|accent|intonation', self.session.state.goal, re.I)))
            if start is not None or end is not None:
                lo, hi = _number(start if start is not None else 0), _number(end) if end is not None else duration
                full = lo == 0 and (end is None or (duration is not None and hi is not None and abs(hi - duration) < 1e-6))
                segment = (segmented_pitch and duration is not None and lo is not None and hi is not None
                           and 0 <= lo < hi <= duration)
                from .cloud_workflow import explicit_range
                requested_range = explicit_range(self.session.state.goal)
                explicit_segment = (requested_range is not None and lo is not None and hi is not None
                    and (lo, hi) == requested_range
                    and bool(re.search(r'片段|选段|局部|另外|然后|再分析|再测量|再检查', self.session.state.goal))
                    and not re.search(r'(?:不要|不用|不使用|而不是|旧选区).{0,25}\d+(?:\.\d+)?\s*(?:秒)?\s*(?:[-–—~～]|到|至)', self.session.state.goal))
                if not full and not segment and not explicit_segment:
                    raise tools.ToolError('整段请求不能使用旧选区或未经核实的局部范围；先读取总时长，整段从 0 到真实终点。')
            elif duration is not None:
                args[start_key], args[end_key] = 0.0, duration
        elif start is None and end is None and row.selection:
            args[start_key], args[end_key] = row.selection
        elif start is None and end is None and duration is not None:
            args[start_key], args[end_key] = 0.0, duration

    def script(self, script, context, names):
        normalized = tools.validate_script(script)
        if _NON_PRAAT.search(normalized):
            raise tools.ToolError('custom_script 必须是 Praat 脚本，不能使用 Python、JavaScript 或 shell 代码。')
        # Do not silently accept an arbitrary string just because it lacks Python.
        code = [line.strip() for line in normalized.splitlines() if line.strip() and not line.lstrip().startswith(('#', ';'))]
        if not any(re.match(r'(?:[A-Z][A-Za-z ]+\s*:|(?:selectObject|plusObject|appendInfoLine|writeInfoLine|appendFileLine)\s*:|[a-z][\w$#]*\s*=)', line) for line in code):
            raise tools.ToolError('未识别到 Praat 命令或赋值；请改用内置工具或有效 Praat 英文脚本。')
        if any(re.match(r'Rename\b', line, re.I) and not re.match(r'Rename\s*:', line, re.I) for line in code):
            raise tools.ToolError('自定义脚本重命名须用 Rename: "用户提供的名称"，便于执行前核实。')
        for match in _SCRIPT_SELECT.finditer(normalized):
            literal = match.group(1).strip()
            row = None
            if re.fullmatch(r'\d+', literal):
                row = context.resolve_object(literal)
            elif re.fullmatch(r'"(?:[^"]|"")*"', literal):
                row = context.resolve_object(literal[1:-1].replace('""', '"'))
            else:
                # Variables are allowed only when Praat actually produced the id.
                prefix = normalized[:match.start()]
                produced = re.search(r'^\s*' + re.escape(literal) + r'\s*=\s*(?:To |Create |Extract |Copy:)', prefix, re.M)
                retrieved = re.search(r'^\s*' + re.escape(literal) + r'\s*=\s*selected\s*\(\s*"[^"]+"\s*\)', prefix, re.M)
                created = retrieved and re.search(r'^\s*(?:To |Create |Extract |Copy:)', prefix[:retrieved.start()], re.M)
                if not re.fullmatch(r'[a-zA-Z]\w*', literal) or not (produced or created):
                    raise tools.ToolError('自定义脚本对象引用必须使用快照中的编号/完整名称，或此前 Praat 创建后取得的对象 ID。')
            specified = {int(a or b) for a, b in _TARGET_ID.findall(self.session.state.goal)}
            if row and specified and row.id in self.session.original_object_ids and row.id not in specified:
                raise tools.ToolError('自定义脚本也必须尊重用户指定的对象编号。')
        # Legacy selection syntax has the same identity rule as selectObject.
        for match in re.finditer(r'^\s*(?:select|plus|minus)\s+([^\n]+)$', normalized, re.I | re.M):
            row = context.resolve_object(match.group(1).strip().strip('"'))
            specified = {int(a or b) for a, b in _TARGET_ID.findall(self.session.state.goal)}
            if specified and row.id in self.session.original_object_ids and row.id not in specified:
                raise tools.ToolError('自定义脚本旧式选择命令也必须尊重用户指定编号。')
        for match in _SCRIPT_RENAME.finditer(normalized):
            literal = match.group(1).strip()
            if not re.fullmatch(r'"(?:[^"]|"")*"', literal) or literal[1:-1].replace('""', '"') not in names:
                raise tools.ToolError('自定义脚本 Rename 的名字也必须由用户明确提供。')
        for match in _SCRIPT_NEW_NAME.finditer(normalized):
            if match.group(1).replace('""', '"') not in names:
                raise tools.ToolError('自定义脚本新对象的名称必须由用户提供；未指定名称请使用内置工具的默认命名。')
        from .chat import _whole_recording_request
        if _whole_recording_request(self.session.state.goal):
            raise tools.ToolError('整段请求请使用内置工具明确范围；自由脚本无法核实是否偷偷使用旧选区。')
        if context.objects and not re.match(r'(?:selectObject\s*:|select\s+|Create\s+)', code[0], re.I):
            normalized = f'selectObject: {context.resolve_object().id}\n' + normalized
        tools.validate_script_source_types(normalized, context)
        return normalized

    def key(self, name, args, source=None):
        context = self.context(source)
        targets = []
        for key in ('object', 'object2', 'textgrid'):
            if args.get(key) is not None:
                row = context.resolve_object(args[key])
                targets.append((key, row.id, row.class_name, row.name, row.selection))
        effective = args
        if name in tools.TOOL_MAP:
            # The fixed script includes actual defaults and numeric formatting;
            # supplying a default explicitly is the same call, not a repair.
            effective = hashlib.sha256(tools.render(name, args, context).encode('utf-8')).hexdigest()
        return json.dumps([name, effective, targets], ensure_ascii=False, sort_keys=True)

    def skip(self, name, arguments, reason, result):
        self.session.state.attempts.append({'tool':name, 'arguments':arguments, 'status':'not_executed',
                                           'guard':True, 'reason':reason})
        raise SkipToolExecution(result)

    async def before_execute(self, ctx, *, call, tool_def, args):
        session, state = self.session, self.session.state
        session.check()
        state.tool_attempts += 1
        if state.tool_attempts > state.budget.tool_limit:
            raise BranchExit('本轮工具尝试总预算已用完')
        name = call.tool_name
        if state.workflow is not None and not state.workflow.l0_enabled:
            raise BranchExit('工作流程已关闭专用工具执行')
        repair_limit = state.workflow.l1_repair_limit if state.workflow is not None else 1
        if state.failures.get(name, 0) > repair_limit:
            raise BranchExit('此前失败分支仍暂停：' + name + '；需要新依据才能重试')
        raw_signature = Evidence(name, args, [], state.context_text).signature
        if any(a.get('proposal_signature') == raw_signature and a.get('status') != 'success' for a in state.attempts):
            self.skip(name, args, '相同失败调用没有区别', {'status':'not_executed', 'reason':'相同失败调用没有区别；应退出该分支'})
        try:
            normalized = self.normalize(name, args)
            key = self.key(name, normalized)
        except (tools.ToolError, BranchExit) as error:
            reason = str(error)
            state.attempts.append({'tool':name, 'arguments':dict(args), 'proposal_signature':raw_signature,
                                   'status':'not_executed', 'guard':True, 'reason':reason})
            if isinstance(error, BranchExit):
                raise
            note_guard(state, 'block',
                       ('tool.schema.' if isinstance(error, SchemaViolation) else 'tool.policy.') + name)
            # 执行前拒绝记录为提案错误，不计实际测量失败；有限重试、相同提案跳过
            # 与工具预算限制重复尝试。真正执行失败由 cloud_agent 更新失败分支。
            raise ModelRetry(reason + '；请按提示改正后重试。') from error
        for evidence in state.evidence:
            if evidence.tool != name:
                continue
            try:
                previous = self.normalize(name, evidence.arguments, source=evidence.source)
                if self.key(name, previous, evidence.source) == key:
                    self.skip(name, normalized, '复用成功测量' if evidence.kind == 'measurement' else '操作已经成功，未重复执行',
                              {'status':'cached', 'rows':evidence.rows, 'source':'已有成功证据；未重复执行'})
            except (tools.ToolError, BranchExit):
                continue
        if any(a.get('guard_key') == key and a.get('status') != 'success' for a in state.attempts):
            self.skip(name, normalized, '相同失败调用没有区别', {'status':'not_executed', 'reason':'相同失败调用没有区别；应退出该分支'})
        from .chat import ANALYSIS_PREPARATION_TOOLS, wants_second_step
        if (state.planning_rounds > 1 and tools.is_mutating(name) and name not in ANALYSIS_PREPARATION_TOOLS
                and not wants_second_step(state.goal)):
            self.skip(name, normalized, '未授权追加对象操作', {'status':'not_executed', 'reason':'用户未要求追加对象操作，请用已有证据报告'})
        session.active_guard_key = key
        session.active_proposal_signature = raw_signature
        return normalized

    async def after_execute(self, ctx, *, call, tool_def, args, result):
        session = self.session
        if (call.tool_name in _CREATES and isinstance(result, dict) and result.get('status') == 'success'
                and session.refresh_context is not None):
            # Never repeat a completed action because refreshing its list failed.
            try:
                fresh = await asyncio.to_thread(session.refresh_context)
                session.state.context_text = fresh
                result = {**result, 'objects': fresh}
            except (BranchExit, tools.ToolError, OSError) as error:
                raise BranchExit('操作已完成，但后续对象状态无法核实：' + str(error)) from error
        return result
