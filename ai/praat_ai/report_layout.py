"""Keep scientific conclusions, supporting measurements and arithmetic separate."""
from __future__ import annotations
import re


def present(state, text, *, force=False):
    body = str(text or '').strip()
    if not body or not getattr(state, 'evidence', None):
        return body
    if not force and not re.search(r'\d\s*(?:Hz|ms|dB|秒|毫秒|%)|VOT|基频|共振峰|测量|计算', body, re.I):
        return body
    calculations = []
    for item in (getattr(state, 'metrics', {}) or {}).get('calculation_receipts', []):
        equation, value = item['equation'], item['value']
        bare = re.sub(r'\s*\[E\d+\]', '', equation).strip()
        bare_value = re.sub(r'\s*\[E\d+\]', '', value).strip()
        # Existing calculation sections are already separated and stay intact.
        sections = re.split(r'(?m)(^#{1,6}\s+[^\n]+\n?)', body)
        in_calculations = False
        for index, section in enumerate(sections):
            if re.match(r'^#{1,6}\s+', section):
                in_calculations = section.strip('# \n') == '计算过程'
            elif not in_calculations and (equation in section or bare in section):
                sections[index] = section.replace(equation, value).replace(bare, bare_value)
                calculations.append(bare)
        body = ''.join(sections)
    if not re.search(r'(?m)^#{1,6}\s+\S', body):
        body = '## 分析结论\n\n' + body
    # Keep Markdown block boundaries stable after clause deletion or recovery.
    body = re.sub(r'(?m)(\S)\n(?=##?\s)', r'\1\n\n', body)
    body = re.sub(r'(?m)^(#{1,6}\s+[^\n]+)\n(?=\S)', r'\1\n\n', body)
    if calculations:
        added = '\n'.join('- ' + x for x in dict.fromkeys(calculations) if x not in body)
        if added:
            existing = re.search(r'(?m)^## 计算过程\s*$', body)
            if existing:
                body = body[:existing.end()] + '\n\n' + added + body[existing.end():]
            else:
                body += '\n\n## 计算过程\n\n' + added
    return body


def sanitize_vot_explanation(state, text):
    """Correct only explicit reversals of VOT timing, with no phoneme classifier."""
    if not any(getattr(item, 'tool', '') == 'vot' for item in getattr(state, 'evidence', [])):
        return text
    clauses = re.split(r'(?<=[。！？\n])', str(text))
    for i, clause in enumerate(clauses):
        if (re.search(r'负(?:值|的|\s*VOT)|negative\s+VOT', clause, re.I)
                and re.search(r'声门开启后.{0,12}无声|声带振动.{0,8}晚于|起浊.{0,8}晚于|爆破.{0,8}早于.{0,6}起(?:声|浊)', clause)):
            clauses[i] = '负 VOT 表示相关声带振动早于除阻；预浊候选仍须核对其闭塞归属。'
            state.metrics['vot_definition_corrected'] = state.metrics.get('vot_definition_corrected', 0) + 1
    return ''.join(clauses)
