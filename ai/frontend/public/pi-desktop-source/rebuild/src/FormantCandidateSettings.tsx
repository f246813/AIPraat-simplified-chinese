import {useEffect, useRef, useState} from 'react';
import type {TutorRpc} from './PronunciationForm';
import type {Calibration, NormalizationSelection, FormantSettings} from './NormalizationSettings';

type Role = 'reference' | 'learner';
type Candidate = {id: string; parameters: FormantSettings; quality: {status: string; score: number | null; reason: string}; can_apply: boolean};
type Analysis = {id: string; material: {wav_name: string}; decision: {status: string; reason: string; recommended_id: string | null}; candidates: Candidate[]; applied_calibration?: Calibration};
const label = (role: Role) => role === 'reference' ? '标准音' : '学习者';

export function FormantCandidateSettings({rpc, selection, onChange, onApplied, reference, learner, language, profileId, readingId, disabled, onBusy}: {
  rpc: TutorRpc; selection: NormalizationSelection; onChange: (value: NormalizationSelection) => void;
  onApplied: (role: Role, value: Calibration) => void; reference: string; learner: string; language: string; profileId: string; readingId: string;
  disabled: boolean; onBusy: (value: boolean) => void;
}) {
  const [analyses, setAnalyses] = useState<Partial<Record<Role, Analysis>>>({});
  const [bandwidth, setBandwidth] = useState<Record<Role, string>>({reference: '', learner: ''});
  const [busy, setBusy] = useState<Role>(); const [error, setError] = useState('');
  const alive = useRef(true); const pending = useRef(false); const latest = useRef(selection); latest.current = selection;
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const context = (role: Role) => ({role, sound_id: role === 'reference' ? reference : learner, language,
    profile_id: profileId, reading_id: readingId, reference_sound_id: reference, learner_sound_id: learner,
    measurement_settings: latest.current.measurement_settings});
  const adopt = (role: Role, record: Calibration) => {
    if (!record.measurement_settings) throw new Error('候选回执缺少实际测量参数，未采用');
    onChange({...latest.current, confirmed: false, [role + '_calibration_id']: record.id,
      measurement_settings: {...latest.current.measurement_settings, [role]: record.measurement_settings}});
    onApplied(role, record);
  };
  const run = async (role: Role, candidate?: Candidate) => {
    if (pending.current || disabled) return;
    pending.current = true; setBusy(role); onBusy(true); setError('');
    try {
      if (candidate) {
        const record = await rpc('tutor.select_formant_candidate', {...context(role), analysis_id: analyses[role]?.id, candidate_id: candidate.id}) as Calibration;
        if (alive.current) adopt(role, record);
      } else {
        const result = await rpc('tutor.analyze_formant_candidates', {...context(role), ...(bandwidth[role] ? {bandwidth_hz: bandwidth[role]} : {})}) as Analysis | null;
        if (!alive.current || !result) return;
        setAnalyses(current => ({...current, [role]: result}));
        if (result.applied_calibration) adopt(role, result.applied_calibration);
      }
    } catch (value) {if (alive.current) setError(value instanceof Error ? value.message : String(value));}
    finally {pending.current = false; if (alive.current) setBusy(undefined); onBusy(false);}
  };
  return <section aria-label="独立元音自动选参">
    <h3>独立元音自动选参</h3>
    <p>分别选择该说话人的独立元音 WAV 与可靠 IPA TextGrid。只比较候选自身的 F1/F2 覆盖和中部稳定性，不使用待测音位，也不按双方接近程度选参。</p>
    <div className="pron-normalization-slots">{(['reference', 'learner'] as const).map(role => <div key={role}>
      <strong>{label(role)}候选分析</strong>
      <label className="pron-field"><span>已知源录音带宽（Hz，可留空）</span><input aria-label={`${label(role)}源录音带宽`} type="number" min="1" step="any" disabled={disabled || !!busy} value={bandwidth[role]}
        onChange={event => {setBandwidth(current => ({...current, [role]: event.target.value})); setAnalyses(current => ({...current, [role]: undefined}));}}/></label>
      <button type="button" disabled={disabled || !!busy} onClick={() => void run(role)}>{busy === role ? '正在分析…' : `${label(role)}：自动分析并选参`}</button>
      {analyses[role] && <>
        <p role="status">{analyses[role]!.material.wav_name} · {analyses[role]!.decision.reason}</p>
        <small>{analyses[role]!.decision.recommended_id ? '明确建议已应用该侧参数并导入校准，尚未确认 Lobanov 配对。' : '当前参数未自动改变；可人工采用可用候选。'}</small>
        <ul>{analyses[role]!.candidates.map(candidate => <li key={candidate.id}>
          <span>{candidate.parameters.maximum_formant_hz} Hz · {candidate.quality.score === null ? '无有效分数' : `稳定性分数 ${candidate.quality.score.toFixed(3)}`} {candidate.quality.reason}</span>{' '}
          <button type="button" disabled={disabled || !!busy || !candidate.can_apply} onClick={() => void run(role, candidate)} aria-label={`${label(role)}采用 ${candidate.parameters.maximum_formant_hz} Hz 候选`}>采用此候选</button>
        </li>)}</ul>
      </>}
    </div>)}</div>
    <small>候选无法区分时保留不确定；工程分数不是共振峰准确率。带宽未知时仅按源采样率限制候选，不声称重采样恢复了录音带宽。</small>
    {error && <p role="alert">{error}</p>}
  </section>;
}
