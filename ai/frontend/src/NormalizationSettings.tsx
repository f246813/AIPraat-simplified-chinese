import {useEffect, useRef, useState} from 'react';
import type {TutorRpc} from './PronunciationForm';

export type NormalizationSelection = {version: 'normalization-v1'; formants: boolean; confirmed: boolean; reference_calibration_id?: string; learner_calibration_id?: string;
  pitch?: boolean; loudness?: boolean; pitch_confirmed?: boolean; pitch_baseline_mode?: 'utterance' | 'independent'; pitch_settings?: {floor_hz: string | number; ceiling_hz: string | number}; reference_pitch_baseline_id?: string; learner_pitch_baseline_id?: string};
type Calibration = {id: string; speaker_label: string; status: string; formants: Record<string, {status: string; reason: string; coverage: Record<string, number>}>; material: {wav_name: string; textgrid_name: string}};
type Capability = {label: string; ready: boolean; reason: string; hint: string};
type Capabilities = {ready: boolean; features: Record<string, Capability>};

/** Context-keyed by caller: changing either speaker or the reading discards selection/confirmation. */
export function NormalizationSettings({rpc, selection, onChange, inventory, reference, learner, language, profileId, readingId, disabled, onBusy}: {
  rpc: TutorRpc; selection: NormalizationSelection; onChange: (value: NormalizationSelection) => void;
  inventory: string[]; reference: string; learner: string; language: string; profileId: string; readingId: string;
  disabled: boolean; onBusy: (busy: boolean) => void;
}) {
  const [calibrations, setCalibrations] = useState<Partial<Record<'reference' | 'learner', Calibration>>>({});
  const [capability, setCapability] = useState<Capability>();
  const [busy, setBusy] = useState(false); const [error, setError] = useState('');
  const alive = useRef(true); const pending = useRef(false);
  const latestSelection = useRef(selection); latestSelection.current = selection;
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  // 只在**确定**缺依赖时禁用并撤销该项：拿不到状态（旧后端 / RPC 失败）不拦截，避免凭猜测挡人。
  useEffect(() => {void rpc('tutor.capabilities', {}).then(value => {
    const features = (value as Capabilities | undefined)?.features;
    if (!alive.current || features?.formants?.ready !== false) return;
    setCapability(features.formants);
    onChange({...latestSelection.current, formants: false, confirmed: false});
  }).catch(() => {});}, [rpc]);
  const importCalibration = async (role: 'reference' | 'learner') => {
    if (pending.current || disabled) return;
    pending.current = true; setBusy(true); onBusy(true); setError('');
    try {
      const result = await rpc('tutor.import_calibration', {role, sound_id: role === 'reference' ? reference : learner, language, profile_id: profileId, reading_id: readingId}) as Calibration | null;
      if (!alive.current || !result) return;
      setCalibrations(current => ({...current, [role]: result}));
      onChange({...latestSelection.current, confirmed: false, [role + '_calibration_id']: result.id});
    } catch (e) {if (alive.current) setError(e instanceof Error ? e.message : String(e));}
    finally {pending.current = false; if (alive.current) setBusy(false); onBusy(false);}
  };
  const pairReady = (feature: string) => calibrations.reference?.formants[feature]?.status === 'applied' && calibrations.learner?.formants[feature]?.status === 'applied';
  return <section className="pron-normalization" aria-labelledby="pron-normalization-title">
    <h3 id="pron-normalization-title">减小说话人与录音差异 <span className="badge">实验性</span></h3>
    <label className="pron-toggle"><input type="checkbox" checked={selection.formants} disabled={disabled || busy || !inventory.length || !!capability} onChange={e => onChange({...selection, formants: e.target.checked})}/><span>独立元音校准 · Lobanov 共振峰归一化</span></label>
    <small>原始 Hz 始终保留，不据 z 值判定发音对错。音高与响度在下方独立启用，不依赖元音校准。</small>
    {capability && <p role="alert">{capability.label}当前不可用：{capability.reason}。{capability.hint}</p>}
    {!inventory.length ? <p role="status">不适用：此读音尚无固定校准清单。</p> : selection.formants && <>
      <p>本预设清单：/{inventory.join(' / /')}/，每种 3 个可靠单元音 token。先选独立 WAV，再选唯一 phones/phonemes IPA 区间层的 TextGrid。不使用本次待测音段拟合。</p>
      <small>中部 40%–60% 取样和上述清单是待真实材料验证的采样协议；美式清单不自动认证其他口音。</small>
      <div className="pron-normalization-slots">{(['reference', 'learner'] as const).map(role => <div key={role}>
        <strong>{role === 'reference' ? '标准说话人校准' : '学习者校准'}</strong>
        <button type="button" disabled={disabled || busy} onClick={() => void importCalibration(role)}>{busy ? '正在导入…' : calibrations[role] ? '重新导入 WAV + TextGrid' : '导入 WAV + TextGrid'}</button>
        <small>{calibrations[role] ? `${calibrations[role]!.speaker_label} · ${calibrations[role]!.material.wav_name}` : '未导入：仅原始 Hz 观察'}</small>
        {calibrations[role] && Object.entries(calibrations[role]!.formants).map(([feature, state]) => <small key={feature}>{feature.toUpperCase()}：{state.status === 'applied' ? '覆盖就绪' : state.reason}</small>)}
      </div>)}</div>
      <label className="pron-toggle"><input type="checkbox" checked={selection.confirmed} disabled={disabled || busy || !calibrations.reference || !calibrations.learner} onChange={e => onChange({...selection, confirmed: e.target.checked})}/><span>确认双方校准分别属于当前录音的说话人，且语言/读音、测量设置一致</span></label>
    </>}
    <p role="status">{!selection.formants ? '已关闭：仅原始 Hz 观察。' : (['f1', 'f2', 'f3'] as const).map(feature => `${feature.toUpperCase()}：${pairReady(feature) && selection.confirmed ? '预计应用 z 比较' : '证据不足，仅原始 Hz 观察'}`).join('；')}</p>
    {selection.formants && <small>提交后以实际有效帧与独立材料检查为准。缺少一方校准不阻止其他分析；更换说话人或读音后须重新导入确认，校准文件不会自动跟随下次任务。</small>}
    {error && <p role="alert">{error}</p>}
  </section>;
}
