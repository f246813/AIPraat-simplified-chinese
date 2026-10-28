"""Durable pronunciation reports delivered to the existing conversation window."""
from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
import re

from .desktop_launch import runtime_directory, write_record


FEATURES = {'pitch': '基频', 'f1': '第一共振峰', 'f2': '第二共振峰', 'f3': '第三共振峰',
            'intensity': '强度', 'duration': '时长', 'spectral_centroid': '频谱重心',
            'rms': '均方根振幅', 'zcr': '过零率', 'high_frequency_ratio': '高频能量比',
            'vot': 'VOT', 'burst_to_periodicity_ms': '爆破后周期性间隔',
            'burst_to_clear_vowel_ms': '爆破至清晰元音间隔'}
STATUSES = {'ok': '已完成', 'observed': '已比较', 'partial': '部分完成',
            'insufficient_evidence': '证据不足', 'not_evaluated': '未评估',
            'not_applicable': '不适用', 'dependency_missing': '缺少依赖',
            'extraction_failed': '测量失败', 'unavailable': '不可用', 'applied': '已应用',
            'provisional': '候选，待复核', 'validated': '地标时间关系已验证'}
CHECKS = {'phone_acoustics': '音位声学对比', 'pitch_formant_measurement': '基频与共振峰测量',
          'independent_phone_recognition': '独立音素识别'}


def cell(value):
    return re.sub(r'\s+', ' ', str(value or '')).replace('\\', '\\\\').replace('|', '\\|').replace('<', '&lt;').replace('>', '&gt;')


def number(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        return '未测得'
    return f'{value:.6g}'


def sound_name(value):
    return re.sub(r'^\d+_Sound_', '', Path(str(value).replace('\\', '/')).stem)


def render_report(payload):
    reference = sound_name(payload.get('reference_name') or payload.get('reference_path') or '标准音')
    learner = sound_name(payload.get('learner_name') or payload.get('learner_path') or '学习者录音')
    rules = bool(payload.get('profile_snapshot'))
    items = (payload.get('observations') or []) if rules else [*(payload.get('errors') or []), *(payload.get('observations') or [])]
    lines = ['# AI 纠音报告', '', f'**标准音：** {cell(reference)}  ', f'**学习者录音：** {cell(learner)}', '',
             f'目标语言：{cell(payload.get("language"))} · 分析方式：{"音位判据" if rules else "声学对比"}',
             f'对齐方式：{cell(payload.get("alignment_source") or "未记录")} · 对齐置信度：{number(payload.get("alignment_confidence"))}', '',
             f'本次记录 **{len(items)} 项{"音位观察" if rules else "声学差异"}**。', '',
             '这些结果描述两段录音的声学差异，不是发音准确率或独立音素识别结论。', '']
    if items:
        lines += ['## 具体结果', '', '| 音位 | 学习者区间（秒） | 指标 | 标准音 | 学习者 | 差值（学习者−标准音） | 结果 |',
                  '| --- | --- | --- | --- | --- | --- | --- |']
        for item in items:
            unit = cell(item.get('unit'))
            reference_value, learner_value = item.get('reference_value'), item.get('learner_value')
            delta = learner_value-reference_value if all(isinstance(v, (int, float)) and math.isfinite(v)
                for v in (reference_value, learner_value)) else None
            lines.append(f'| /{cell(item.get("ipa"))}/ | {number(item.get("start"))}–{number(item.get("end"))} | '
                         f'{cell(FEATURES.get(item.get("feature"), item.get("feature")))} | '
                         f'{number(item.get("reference_value"))} {unit} | {number(item.get("learner_value"))} {unit} | '
                         f'{number(delta)} {unit} | '
                         f'{cell(item.get("message") or item.get("direction"))} |')
    else:
        lines += ['本次没有记录超出判据的差异；还需结合下面的测量覆盖情况阅读。']
    scalar_rows = []
    for role, key in (("标准音", "reference_phones"), ("学习者", "learner_phones")):
        for phone in payload.get(key) or []:
            for name, measure in (phone.get("measurements") or {}).items():
                reason = measure.get('reason') or ''
                scalar_rows.append(f"| {role} /{cell(phone.get('ipa'))}/ | {cell(FEATURES.get(name, name))} | "
                    f"{number(measure.get('value'))} {cell(measure.get('unit'))} | "
                    f"{cell(STATUSES.get(measure.get('status'), measure.get('status')))} {cell(reason)} |")
    if scalar_rows:
        lines += ['', '## 地标派生单值', '', '| 录音与音位 | 测量 | 数值 | 状态与原因 |',
                  '| --- | --- | --- | --- |', *scalar_rows]
    if payload.get('measurement_protocol'):
        lines += ['', '<details><summary>测量参数与来源</summary>', '',
                  f"测量协议：`{cell(payload['measurement_protocol'])}`", '',
                  '完整观察、逐帧状态、地标和派生配对保存在 JSON 附件。']
        for item in items:
            metadata = item.get('evidence') or {}
            ids = [metadata.get(key) for key in ('measurement_id', 'reference_measurement_id', 'learner_measurement_id') if metadata.get(key)]
            if ids:
                lines.append(f"- /{cell(item.get('ipa'))}/ {cell(item.get('feature'))}：" + '，'.join(f'`{cell(value)}`' for value in ids))
        lines += ['', '</details>']
    lines += ['', '## 测量覆盖', '']
    for name, status in (payload.get('check_statuses') or {}).items():
        lines.append(f'- {cell(CHECKS.get(name, name))}：{cell(STATUSES.get(status, status))}')
    gaps = []
    for phone in payload.get('phone_assessments') or []:
        for check in phone.get('checks') or []:
            if check.get('status') not in {'observed', 'ok', 'applied', 'not_applicable'}:
                reason = check.get('reason') or STATUSES.get(check.get('status'), '未评估')
                gap = f'/{cell(phone.get("ipa"))}/（第 {cell(phone.get("phone_index"))} 个音位） · {cell(FEATURES.get(check.get("feature"), check.get("feature")))}：{cell(reason)}'
                if gap not in gaps:
                    gaps.append(gap)
    if gaps:
        lines += ['', '### 缺测与未评估项', '', *['- ' + item for item in gaps]]
    warnings = payload.get('warnings') or []
    if warnings:
        lines += ['', '## 分析提示', '', *['- ' + cell(item) for item in warnings]]
    lines += ['', '## AI 文字解释', '']
    explanation = str(payload.get('explanation') or '').strip()
    if explanation:
        lines.append(explanation)
    else:
        lines.append('本次未生成 AI 文字解释；上面的测量结果和覆盖状态已保存。')
    return '\n'.join(lines).strip()


def queue_report(outputs, *, runtime=None):
    """Queue only completed outputs; the desktop owns its live session database."""
    from .pronunciation import explanation_evidence
    from .tutor import _explanation_context
    context, reference, learner = _explanation_context(outputs.request_payload)
    state = explanation_evidence(outputs.result, context_text=context,
                                 reference_object=reference, learner_object=learner)
    source = str(outputs.json_path.resolve())
    key = hashlib.sha256(source.encode('utf8')).hexdigest()[:32]
    path = Path(runtime or runtime_directory()) / 'tutor-reports' / (key + '.json')
    write_record(path, dict(schema='tutor-chat-report-v1', source=source,
                           files=[str(p.resolve()) for p in (outputs.json_path, outputs.textgrid_path, outputs.overlay_path) if p],
                           evidence=[asdict(item) for item in state.evidence], target=context))
    return key


def import_report(application, record):
    if record.get('schema') != 'tutor-chat-report-v1':
        raise ValueError('纠音报告交付格式无效')
    source = Path(record['source']).resolve()
    key = hashlib.sha256(str(source).encode('utf8')).hexdigest()[:32]
    with application.store.connect() as db:
        if db.execute('SELECT 1 FROM sessions WHERE id=?', (key,)).fetchone():
            return key
    payload = json.loads(source.read_text(encoding='utf-8-sig'))
    if not isinstance(payload, dict) or not all(isinstance(payload.get(k), list) for k in ('errors', 'reference_phones', 'learner_phones')):
        raise ValueError('纠音报告缺少分析结果')
    attachments = []
    for value in record.get('files') or []:
        path = Path(value).resolve()
        if path.parent != source.parent or path.suffix.lower() not in {'.json', '.textgrid', '.png'}:
            raise ValueError('报告附件路径无效')
        if path.stat().st_size > application.attachments.LIMIT:
            raise ValueError('报告附件超过 20 MiB')
        attachments.append(application.attachments.import_bytes(path.name, path.read_bytes()))
    title = f'AI 纠音 · {sound_name(payload.get("reference_name"))} → {sound_name(payload.get("learner_name"))}'
    message = dict(id=key, role='assistant', content=render_report(payload), status='complete',
                   formal=True, attachments=attachments, report={'type': 'pronunciation', 'source': str(source)})
    evidence = {key: record.get(key, default) for key, default in (('evidence', []), ('target', ''))}
    application.store.import_report(key, title, message, evidence)
    return key


def receive_reports(application, *, runtime=None):
    """Consume durable requests once, leaving failed requests available for diagnosis."""
    pending = Path(runtime or runtime_directory()) / 'tutor-reports'
    navigation = None
    from .modern_store import identity
    for path in sorted(pending.glob('*.json')):
        try:
            record = json.loads(path.read_text(encoding='utf8'))
            sid = import_report(application, record)
            navigation = dict(id=identity(), category='报告', sessionId=sid)
            path.unlink()
        except (OSError, ValueError, KeyError, TypeError) as error:
            path.rename(path.with_suffix('.failed'))
            navigation = dict(id=path.stem, category='报告', error='报告显示失败：' + str(error))
    return navigation
