"""Local task skills; no model classification, tool registration or script execution.

Pi skills.ts @28dcce2 (MIT) is the catalog/on-demand-body reference. Existing
AIPraat predicates select bodies; a task keeps its loaded version snapshot.
"""
from pathlib import Path
from .session_context import PhaseContext

ROOT = Path(__file__).resolve().parents[1] / 'skills'
INDEX = (('prosody', '音调'), ('placement', '选段'), ('failure', '失败'),
         ('notation', '数值写法'))


def catalog(phase):
    return '\n按需技能：' + '；'.join(f'{key}:{description}' for key, description in INDEX
                                    if phase != 'planner' or key == 'prosody')


def load(context: PhaseContext, phase, state):
    from .cloud_agent import _PROSODY_TOPICS, _PLACEMENT_TOPICS, blocked_attempts
    selected = []
    if _PROSODY_TOPICS.search(state.goal or ''):
        selected.append('prosody')
    if phase == 'report':
        if _PLACEMENT_TOPICS.search(state.goal or ''):
            selected.append('placement')
        if state.failures or blocked_attempts(state):
            selected.append('failure')
        # 有测量才有数值写法问题；没有证据时把「不要填录音数值」交给固定提示词。
        if state.evidence:
            selected.append('notation')
    for key in selected:
        identity = f'{key}:1:{phase}'
        if identity not in context.loaded:
            content = (ROOT / key / 'SKILL.md').read_text(encoding='utf-8').split('---', 2)[-1].strip()
            context.append_guidance(identity, f'[应用任务指导 skill={key} version=1 phase={phase}；仅适用于本任务，权限由代码核对]\n{content}')
