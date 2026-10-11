"""Experimental, single-request local decisions; not enabled in the desktop.

The caller supplies verified objects and a bounded tool catalogue. This module
never sends Praat commands, changes selection, or interprets measured values.
"""
from __future__ import annotations

import json
import copy
import re
from . import qwen, tools

DECISION_TOOLS = (
    'object_info', 'duration', 'pitch', 'pitch_statistics', 'intensity',
    'intensity_statistics', 'formant_frequency', 'formant_bandwidth',
    'formant_statistics', 'harmonicity_statistics', 'measure', 'vot',
    'spectral_emphasis', 'intensity_slope',
)

INSTRUCTIONS = """你是 Praat 的决策组件，只识别请求并填写结构化计划，不执行、不回答测量结果。
输出 intent、scope、actions、reason。intent: measure=测量；explain=一般概念；analyze=综合分析；reuse=解释已有结果；reject=明确无法执行；clarify=有歧义。
scope: whole=明确整段；selection=当前选区或未指定范围但有选区；explicit=指定起止；point=指定时间点；default=无范围依据。
对象只选现场真实 id。指定不存在编号应 reject，不换别的对象；指定 Harmonicity 做 To Pitch 应 reject，不能改为 Sound。
当前声音按选中的 Sound 或唯一 Sound 判断；当前对象严格按实际选择，类型不适用就拒绝。多个同样合理候选须 clarify。
明确范围优先于选区；“不要整段”不是整段指令。整段只标 scope=whole，不猜终点时长。选区只标 scope=selection，由程序提供真实边界。
时间单位统一为秒，毫秒除以1000。多时刻 time 用逗号字符串，多共振峰 formant 用逗号字符串。
只填写用户给出的参数与必要对象 id，不猜默认参数。自动 VOT 没有已知 burst/voicing 时只填 from/to，不猜时刻。
统计与时间点测量不同；优先专用工具，只有专用工具不覆盖时用 measure；多个独立目标要保留全部动作。
一般概念解释不测录音；解释已有结果不重新测量；静音请求仍可计划测量，是否无法求值由执行器决定。
综合发音分析可提出相关测量，但没有转写、标准录音、实际测量时不能给发音结论。
reason 写简短决策依据，不能输出实测数值或自定义脚本。
""".strip()


def catalogue():
    """Fixed acoustic scope, same catalogue for every case (no gold filtering)."""
    entries = []
    for schema in tools.tool_schemas():
        fn = schema['function']
        if fn['name'] not in DECISION_TOOLS:
            continue
        properties = fn['parameters'].get('properties', {})
        # All supported measurement names remain present; no per-case answers.
        parameter = properties.get('parameter', {}).get('description', '')
        entries.append(dict(tool=fn['name'], description=fn['description'],
                            parameters=list(properties), measurement_parameters=parameter))
    return entries


def output_schema():
    args = {name: {'type': 'number'} for name in (
        'from', 'to', 'burst', 'voicing', 'pitch_floor', 'pitch_ceiling',
        'burst_db', 'multiplier', 'smoothing', 'time_step')}
    args.update(object={'type': 'integer'}, time={'type': ['number', 'string']},
                formant={'type': ['integer', 'string']}, parameter={'type': 'string'},
                unit={'type': 'string', 'enum': ['hertz', 'Bark']},
                method={'type': 'string', 'enum': ['local', 'global']}, tier={'type': 'integer'})
    action = {'type': 'object', 'properties': {
        'tool': {'type': 'string', 'enum': list(DECISION_TOOLS)},
        'arguments': {'type': 'object', 'properties': args, 'additionalProperties': False}},
        'required': ['tool', 'arguments'], 'additionalProperties': False}
    return {'type': 'object', 'properties': {
        'intent': {'type': 'string', 'enum': ['measure', 'explain', 'analyze', 'reuse', 'reject', 'clarify']},
        'scope': {'type': 'string', 'enum': ['whole', 'selection', 'explicit', 'point', 'default']},
        'actions': {'type': 'array', 'items': action, 'maxItems': 5},
        'reason': {'type': 'string'}},
        'required': ['intent', 'scope', 'actions', 'reason'], 'additionalProperties': False}


def decide(client, text, object_context, *, previous_evidence=()):
    if client.config.provider != qwen.LOCAL_PROVIDER:
        raise ValueError('实验决策路径只允许配置的本地模型')
    messages = [dict(role='system', content=INSTRUCTIONS + '\n候选工具：\n' +
                     json.dumps(catalogue(), ensure_ascii=False, separators=(',', ':'))),
                dict(role='user', content=json.dumps(dict(request=text, objects=object_context,
                     previous_evidence=list(previous_evidence)), ensure_ascii=False))]
    payload = dict(model=client.config.model, messages=messages, temperature=0.1,
                   top_p=0.8, presence_penalty=0.0, max_tokens=600,
                   chat_template_kwargs={'enable_thinking': False},
                   response_format={'type': 'json_object'})
    response = client._post(payload)
    choice = response['choices'][0]
    if choice.get('finish_reason') == 'length':
        raise qwen.TruncatedResponseError('决策 JSON 被输出上限截断')
    decision = json.loads(choice['message']['content'])
    from jsonschema import Draft202012Validator
    Draft202012Validator(output_schema()).validate(decision)
    return decision, {k: response[k] for k in ('model', 'usage', 'timings') if k in response}


def decide_native(client, text, object_context, *, previous_evidence=()):
    """Same bounded task, using the checkpoint's native tool-call format."""
    if client.config.provider != qwen.LOCAL_PROVIDER:
        raise ValueError('实验决策路径只允许配置的本地模型')
    schemas = []
    ids = [row['id'] for row in object_context]
    for schema in tools.tool_schemas():
        fn = schema['function']
        if fn['name'] not in DECISION_TOOLS:
            continue
        schema = copy.deepcopy(schema)
        parameters = schema['function']['parameters']
        def compact(value):
            if isinstance(value, dict):
                return {key: compact(item) for key, item in value.items()
                        if key not in {'description', 'default', 'title', 'examples'}}
            if isinstance(value, list):
                return [compact(item) for item in value]
            return value
        measure_description = parameters.get('properties', {}).get('parameter', {}).get('description')
        parameters = compact(parameters)
        schema['function']['parameters'] = parameters
        if measure_description:
            parameters['properties']['parameter']['description'] = '可逗号组合：' + ','.join(
                re.findall(r'([a-z][a-z0-9_]+)（', measure_description))
        parameters['properties']['object'] = {'type': 'integer', 'enum': ids}
        parameters['properties']['scope'] = output_schema()['properties']['scope']
        parameters['required'] = list(dict.fromkeys(parameters.get('required', []) + ['object', 'scope']))
        parameters['additionalProperties'] = False
        schemas.append(schema)
    schemas.append({'type': 'function', 'function': {
        'name': 'route_request', 'description': '不测量时选择概念解释、综合分析、复用、拒绝或澄清路径，不生成测量动作。',
        'parameters': {'type': 'object', 'properties': {
            'intent': {'type': 'string', 'enum': ['explain', 'analyze', 'reuse', 'reject', 'clarify']},
            'reason': {'type': 'string'}}, 'required': ['intent', 'reason'], 'additionalProperties': False}}})
    instructions = '''你是 Praat 决策器，只提出原生工具调用，不执行、不编造测量值。
仅测用户要求的量，保留全部独立目标；已有结果足够就不重复测量。
测量调用对应工具，object 必须填现场真实 id。指定编号不存在或指定类型不适用时，只调用 route_request(intent=reject)，不能换对象。Harmonicity 不能 To Pitch。
当前声音取选中的 Sound 或唯一 Sound；当前对象取实际选择；多候选无依据时 clarify。
scope: whole=明确整段；selection=选区或未指定范围且有选区；explicit=指定起止；point=时间点/已知VOT地标；default=无范围。
指定范围优先于选区。“不要整段”不是 whole。whole 不猜终点，selection 不猜边界，由程序取真实值。
毫秒转秒。多个 time/formant 用逗号字符串。只填用户参数，不填默认值；自动VOT只填搜索范围，不猜 burst/voicing。
点测量与区间统计不同；优先专用工具。一般概念只 route_request(explain)，综合分析 analyze，解释已有结果 reuse，拒绝 reject，歧义 clarify；这些路由不混入测量动作。'''
    messages = [dict(role='system', content=instructions), dict(role='user', content=json.dumps(
        dict(request=text, objects=object_context, previous_evidence=list(previous_evidence)), ensure_ascii=False))]
    message = client.chat_message(messages, tools=schemas, max_tokens=600, temperature=0.1)
    calls = qwen.extract_tool_actions(message)
    routes = [call for call in calls if call['tool'] == 'route_request']
    actions = [call for call in calls if call['tool'] != 'route_request']
    if routes:
        if actions or len(routes) != 1:
            raise tools.ToolError('路由和测量动作混合，未接受该计划')
        args = routes[0]['arguments']
        return dict(intent=args['intent'], scope='default', actions=[], reason=args['reason']), {}
    if not actions:
        raise tools.ToolError('没有结构化决策；未把普通回答当作计划')
    scopes = []
    plans = []
    for action in actions:
        args = dict(action['arguments'])
        scope = args.pop('scope', None)
        scopes.append(scope)
        plans.append(dict(tool=action['tool'], arguments=args))
    decision = dict(intent='measure', scope=scopes[0] if len(set(scopes)) == 1 else 'default',
                    actions=plans, reason=qwen.message_text(message).strip())
    from jsonschema import Draft202012Validator
    Draft202012Validator(output_schema()).validate(decision)
    return decision, {}


def decide_slots(client, text, object_context, *, previous_evidence=()):
    """Narrow alternative: model extracts semantics, fixed mappings own tools."""
    if client.config.provider != qwen.LOCAL_PROVIDER:
        raise ValueError('实验决策路径只允许配置的本地模型')
    mappings = {
        '时长': ('duration', {}), '基本信息': ('object_info', {}),
        '时间点基频': ('pitch', {}), '基频统计': ('pitch_statistics', {}),
        '时间点强度': ('intensity', {}), '强度统计': ('intensity_statistics', {}),
        '共振峰频率': ('formant_frequency', {}), '共振峰带宽': ('formant_bandwidth', {}),
        '共振峰统计': ('formant_statistics', {}), '谐噪比': ('harmonicity_statistics', {}),
        '频谱重心': ('measure', {'parameter':'centre_of_gravity'}),
        '频谱偏度': ('measure', {'parameter':'skewness'}),
        '频谱峰度': ('measure', {'parameter':'kurtosis'}),
        'jitter': ('measure', {'parameter':'local_jitter'}),
        'shimmer百分比': ('measure', {'parameter':'local_shimmer_percent'}),
        'CPPS': ('measure', {'parameter':'cpps'}), 'VOT': ('vot', {}),
        '谱强调': ('spectral_emphasis', {}),
        '整体强度斜率': ('intensity_slope', {'method':'global'}),
    }
    properties = {
        'intent': output_schema()['properties']['intent'],
        'scope': output_schema()['properties']['scope'],
        'object': {'type': ['integer', 'null']},
        'metrics': {'type': 'array', 'items': {'type':'string', 'enum':list(mappings)}, 'maxItems':5},
        'time': {'type': ['number', 'string']}, 'formant': {'type':['integer', 'string']},
        **{key:{'type':'number'} for key in ('from','to','burst','voicing')},
    }
    schema = {'type':'function', 'function': {'name':'extract_request',
        'description':'识别请求，不执行；非测量请求 metrics 为空；object 是明确目标编号。',
        'parameters': {'type':'object', 'properties':properties,
                       'required':['intent','scope','object','metrics'], 'additionalProperties':False}}}
    instructions = '''只调用 extract_request 抽取用户意思，不执行、不提供实测结论。
intent: measure 测量；explain 一般知识；analyze 综合发音分析；reuse 解释已有结果；reject 指定对象不存在或类型不能执行；clarify 多候选无依据。
scope: whole 明确整段；selection 当前选区或未指定范围且有选区；explicit 指定起止；point 指定时刻或VOT地标；default 无范围。
明确起止优先于选区；“不要整段”不是 whole。只在 explicit 时填写 from/to；whole/selection 的边界由程序取，不猜。
当前声音取选中的或唯一 Sound，当前对象按实际选择；用户指定编号或类型时必须尊重。Harmonicity 不能转换 Pitch；编号不存在就 reject，不能换对象。
只抽取用户给出的参数，毫秒换成秒，多 time/formant 用逗号字符串。平均基频=基频统计；F1/F2是共振峰频率，平均/标准差是共振峰统计；HNR=谐噪比。
自动VOT只抽取搜索范围，不能猜 burst/voicing。组合测量保留全部 metrics，一般概念与复用不填 metrics。'''
    message = client.chat_message([dict(role='system', content=instructions + '\n现场对象与历史证据（仅数据）：\n' +
        json.dumps(dict(objects=object_context, previous_evidence=list(previous_evidence)), ensure_ascii=False)),
        dict(role='user', content=text)], tools=[schema],
        tool_choice='required', max_tokens=400, temperature=0.1)
    calls = qwen.extract_tool_actions(message)
    if len(calls) != 1 or calls[0]['tool'] != 'extract_request':
        raise tools.ToolError('未返回唯一语义抽取结果')
    slots = calls[0]['arguments']
    from jsonschema import Draft202012Validator
    Draft202012Validator(schema['function']['parameters']).validate(slots)
    actions = []
    if slots['intent'] == 'measure':
        for metric in slots['metrics']:
            tool, defaults = mappings[metric]
            args = dict(defaults)
            if slots['object'] is not None:
                args['object'] = slots['object']
            available = tools.tool_parameters(tool).get('properties', {})
            for key in ('from', 'to', 'time', 'formant', 'burst', 'voicing'):
                if key in slots and key in available:
                    args[key] = slots[key]
            if tool == 'measure' and actions and actions[-1]['tool'] == 'measure' and {
                    k:v for k,v in actions[-1]['arguments'].items() if k != 'parameter'} == {
                    k:v for k,v in args.items() if k != 'parameter'}:
                actions[-1]['arguments']['parameter'] += ',' + args['parameter']
            else:
                actions.append(dict(tool=tool, arguments=args))
    return dict(intent=slots['intent'], scope=slots['scope'], actions=actions, reason=''), {'slots':slots}
