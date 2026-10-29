"""Screened F0 observations, interval validation and derived measurement provenance."""
from __future__ import annotations
import math
import statistics
import re

from .acoustic_events import AcousticAnalysis, AcousticEvent, AcousticInterval, DerivedMeasure, _number

HEADER = '@aipraat-pitch-v1\t'
FRAME = '@aipraat-frame-v1\t'
QUERY = '@aipraat-query-v1\t'
PARAMETERS = dict(mean_pitch='mean', minimum_pitch='minimum', maximum_pitch='maximum',
                  median_pitch='median', sd_pitch='sd', pitch_start='start', pitch_end='end',
                  pitch_slope='slope_absolute', pitch_slope_octave_free='slope_octave_free')
LABELS = {'pitch.mean':'平均基频','pitch.minimum':'最低基频','pitch.maximum':'最高基频',
          'pitch.median':'基频中位数','pitch.sd':'基频标准差','pitch.start':'起始基频',
          'pitch.end':'结束基频','pitch.slope_absolute':'基频平均绝对斜率',
          'pitch.slope_octave_free':'不含倍频跳变的基频斜率','pitch.peak_latency':'基频峰值延迟'}


def _octave_free_slope(frames):
    """Praat's folded absolute semitone change per first-to-last voiced span."""
    if len(frames) < 2:
        return None
    total = 0.0
    for a, b in zip(frames, frames[1:]):
        change = abs(12 * math.log2(b['value'] / a['value'])) % 12
        total += min(change, 12 - change)
    return total / (frames[-1]['time'] - frames[0]['time'])


def covered_row(row, analysis):
    """Only replace metrics derived by this pipeline; retain other tool outputs."""
    from .measures import load_table
    entries=analysis.measures if isinstance(analysis,AcousticAnalysis) else analysis.get('measures',[])
    names={entry.name if isinstance(entry,DerivedMeasure) else entry['name'] for entry in entries}
    return any('pitch.'+role in names and (row.startswith(load_table().find(parameter).label)
               or row.startswith(LABELS['pitch.'+role])) for parameter,role in PARAMETERS.items())


def consume(rows, arguments, source, tool):
    headers = [row[len(HEADER):].split('\t') for row in rows if row.startswith(HEADER)]
    if not headers:
        return None
    if len(headers) != 1 or len(headers[0]) != 7:
        raise ValueError('基频观察回执不完整或重复')
    lo, hi, step = map(_number, headers[0][:3])
    if lo is None or hi is None or step is None or not lo < hi or step <= 0:
        raise ValueError('基频观察缺少有效时间范围')
    method, floor, ceiling, quality = headers[0][3:]
    extracted = method == 'filtered_autocorrelation'
    analysis = AcousticAnalysis(source, (lo, hi), kind='pitch', method=dict(
        name=method, purpose='speech_f0' if extracted else None,
        floor_hz=_number(floor) if extracted else None, ceiling_hz=_number(ceiling) if extracted else None,
        silence_threshold=.09 if extracted else None))
    frames = []
    for row in rows:
        if not row.startswith(FRAME):
            continue
        parts = row[len(FRAME):].split('\t')
        if len(parts) != 5:
            raise ValueError('基频候选帧回执不完整')
        time, value = map(_number, parts[:2])
        if time is None or not lo <= time <= hi or (frames and time <= frames[-1]['time']):
            raise ValueError('基频候选帧时间域无效')
        used, energy, run = (p == '1' for p in parts[2:])
        reason = ('没有周期性候选' if not value else '能量不足' if not energy
                  else '连续性不足' if not run else '候选带外' if not used else '')
        if used and (not value or not energy or not run):
            raise ValueError('基频候选帧未通过验证却被用于统计')
        frames.append(dict(time=time, value=value or None, status='screened' if used else 'rejected', reason=reason))
    accepted = [f for f in frames if f['status'] == 'screened']
    status = 'screened' if quality == '已筛查' else 'uncertain'
    analysis.checks.append(dict(rule='pitch.screening', status=status, reason=quality,
                               candidates=len(frames), accepted=len(accepted), frame_step=step))
    run = []
    def add_run():
        if run:
            analysis.intervals.append(AcousticInterval('voiced_run_'+str(len(analysis.intervals)+1),
                'screened_periodicity', max(lo,run[0]['time']-step/2), min(hi,run[-1]['time']+step/2), status, method))
    for f in frames:
        if f['status'] == 'screened':
            run.append(f)
        else:
            add_run(); run=[]
    add_run()
    def measure(name, value, unit, formula, inputs=None):
        analysis.measures.append(DerivedMeasure(name,value,unit,inputs or ['screened_frames'],formula,
            status if value is not None else 'unavailable', '' if value is not None else '没有足够的有效候选帧'))
    if tool == 'pitch':
        for index,row in enumerate(r for r in rows if r.startswith(QUERY)):
            time,value = map(_number,row[len(QUERY):].split('\t'))
            # Query time is a requested observation, not a phoneme boundary.
            event_id='query_'+str(index+1)
            in_domain = time is not None and lo <= time <= hi
            if value is not None and not in_domain:
                raise ValueError('范围外的基频查询不能返回测量值')
            analysis.events.append(AcousticEvent(event_id,'observation_time',time,
                'validated' if value is not None else 'unavailable' if in_domain else 'rejected', 'query'))
            measure('pitch.value',value,'Hz','screened tracker interpolation at requested time',[event_id])
            if value is None:
                reason='请求时刻超出声音绝对时间域' if not in_domain else '该时刻没有通过筛查的连续周期性帧'
                analysis.events[-1].reason=reason
                analysis.measures[-1].reason=reason
    else:
        values=[f['value'] for f in accepted]
        peak=max(accepted,key=lambda f:f['value']) if accepted else None
        slope_pairs=[(a,b) for a,b in zip(frames,frames[1:]) if a['status']==b['status']=='screened']
        numbers = dict(mean=statistics.mean(values) if values else None,
            minimum=min(values) if values else None, maximum=max(values) if values else None,
            median=statistics.median(values) if values else None,
            sd=statistics.stdev(values) if len(values)>1 else None,
            start=values[0] if values else None,end=values[-1] if values else None,
            slope_octave_free=_octave_free_slope(accepted),
            slope_absolute=sum(abs(a['value']-b['value']) for a,b in slope_pairs)/(len(slope_pairs)*step) if slope_pairs else None)
        requested={'mean','minimum','maximum'} if tool=='pitch_statistics' else set()
        if tool == 'measure':
            from .tools import _measure_entries
            names={entry.parameter for entry in _measure_entries(arguments)}
            requested={value for key,value in PARAMETERS.items() if key in names}
        for name in sorted(requested):
            unit = '半音/s' if name == 'slope_octave_free' else 'Hz/s' if name == 'slope_absolute' else 'Hz'
            formula = ('sum(fold_to_0_6(abs(12*log2(next_F0/F0))))/(last_frame_time-first_frame_time)'
                       if name == 'slope_octave_free' else 'mean(abs(adjacent_F0_difference))/frame_step'
                       if name == 'slope_absolute' else name+'(screened_frames)')
            inputs = None
            if name in {'start', 'end'}:
                frame = (accepted[0] if name == 'start' else accepted[-1]) if accepted else None
                event_id = 'f0_' + name
                analysis.events.append(AcousticEvent(event_id, 'f0_' + name + '_time',
                    frame['time'] if frame else None, 'candidate' if frame else 'unavailable', method,
                    '' if frame else '没有有效基频帧'))
                inputs = [event_id]
            measure('pitch.'+name,numbers[name],unit,formula,inputs)
        if tool in {'pitch_statistics','pitch_peak_latency'} and peak:
            analysis.events.append(AcousticEvent('f0_peak','f0_peak_time',peak['time'],'candidate',method))
        if tool=='pitch_peak_latency':
            measure('pitch.peak_latency',(peak['time']-lo)/(hi-lo)*100 if peak else None,'%',
                    '(f0_peak_time - scope_start)/(scope_end - scope_start)*100',['f0_peak','scope_start','scope_end'])
    return analysis,frames


def result_rows(analysis, rows, tool):
    """Use the same derived values in visible rows and quantity references."""
    kept=[row for row in rows if not row.startswith('@aipraat-')
          and (row.startswith('基频质量：') or (not covered_row(row,analysis) if tool=='measure'
               else not re.search(r'基频|音高|F0|pitch',row,re.I)))]
    for measure in analysis.measures:
        if measure.name=='pitch.value':
            event=next(e for e in analysis.events if e.id==measure.inputs[0])
            label=f'基频（{event.time:.6f} 秒处）'
        else:
            label=LABELS[measure.name]
            if measure.name in {'pitch.start', 'pitch.end'}:
                event=next(e for e in analysis.events if e.id==measure.inputs[0])
                if event.time is not None:
                    position='首个' if measure.name=='pitch.start' else '末个'
                    label+=f'（{position}有效帧，{event.time:.6f} 秒）'
        suffix='（候选，待复核）' if measure.status=='uncertain' else ''
        kept.append(f'{label} = {measure.value:.3f} {measure.unit}{suffix}' if measure.value is not None else
                    f'{label}无法计算：{measure.reason}')
    if tool!='pitch':
        kept.insert(0,f'分析范围：{analysis.scope[0]:.6f}–{analysis.scope[1]:.6f} 秒')
        for event in analysis.events:
            if event.kind=='f0_peak_time':
                kept.append(f'基频最高点出现在 {event.time:.6f} 秒')
    return kept
