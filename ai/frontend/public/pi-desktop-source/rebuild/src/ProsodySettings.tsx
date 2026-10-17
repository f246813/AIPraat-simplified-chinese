import {useEffect, useRef, useState} from 'react';
import type {TutorRpc} from './PronunciationForm';
import type {NormalizationSelection} from './NormalizationSettings';
type Baseline = {id: string; baseline: {status: string; reason: string; baseline_hz?: number; material: {wav_name: string}}};
type Capability = {label: string; ready: boolean; reason: string; hint: string};
type Capabilities = {ready: boolean; features: Record<string, Capability>};
/** 只在**确定**缺依赖时禁用开关：旧后端或 RPC 失败（拿不到状态）不拦截提交，避免凭猜测挡人。 */
const gap = (caps: Capabilities | undefined, id: string) => caps?.features?.[id]?.ready === false ? caps!.features[id] : undefined;
const gapText = (value: Capability) => `${value.label}当前不可用：${value.reason}。${value.hint}`;

export function ProsodySettings({rpc, selection, onChange, reference, learner, language, profileId, readingId, disabled, onBusy, durations}: {
  rpc: TutorRpc; selection: NormalizationSelection; onChange: (value: NormalizationSelection) => void;
  reference: string; learner: string; language: string; profileId: string; readingId: string; disabled: boolean; onBusy: (busy: boolean) => void; durations?: [number | null | undefined, number | null | undefined];
}) {
  const [baselines, setBaselines] = useState<Partial<Record<'reference' | 'learner', Baseline>>>({});
  const [caps, setCaps] = useState<Capabilities>();
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState('');
  const alive = useRef(true); const pending = useRef(false); const latest = useRef(selection); latest.current = selection;
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  // 提交之前就问一次「这一步现在能不能跑」：缺依赖时撤销该项并给出可执行提示，而不是等报告。
  useEffect(() => {void rpc('tutor.capabilities', {}).then(value => {
    if (!alive.current || !value) return;
    const features = (value as Capabilities).features;
    setCaps(value as Capabilities);
    const cleared = (['pitch', 'loudness'] as const).filter(id => features?.[id]?.ready === false);
    if (cleared.length) onChange({...latest.current, ...Object.fromEntries(cleared.map(id => [id, false])), ...(cleared.includes('pitch') ? {pitch_confirmed: false} : {})});
  }).catch(() => {});}, [rpc]);
  const pitchGap = gap(caps, 'pitch'); const loudnessGap = gap(caps, 'loudness');
  const settings = selection.pitch_settings || {floor_hz: 75, ceiling_hz: 600};
  const validRange = Number(settings.floor_hz) > 0 && Number(settings.ceiling_hz) > Number(settings.floor_hz) && Number.isFinite(Number(settings.ceiling_hz));
  const changeRange = (key: 'floor_hz' | 'ceiling_hz', value: string) => {
    setBaselines({});
    onChange({...selection, pitch_settings: {...settings, [key]: value}, pitch_confirmed: false, reference_pitch_baseline_id: undefined, learner_pitch_baseline_id: undefined});
  };
  const importBaseline = async (role: 'reference' | 'learner') => {
    if (pending.current || disabled || !validRange) return;
    pending.current = true; setBusy(true); onBusy(true); setMessage('');
    try {
      const result = await rpc('tutor.import_pitch_baseline', {role, sound_id: role === 'reference' ? reference : learner, language, profile_id: profileId, reading_id: readingId, pitch_settings: settings}) as Baseline | null;
      if (alive.current && result) {
        setBaselines(value => ({...value, [role]: result}));
        onChange({...latest.current, [role + '_pitch_baseline_id']: result.id, pitch_confirmed: false});
      }
    } catch (error) {if (alive.current) setMessage(error instanceof Error ? error.message : String(error));}
    finally {pending.current = false; if (alive.current) setBusy(false); onBusy(false);}
  };
  const configureRuntime = async () => {
    try {const result = await rpc('tutor.configure_runtime', {}) as {message: string}; if (alive.current) setMessage(result.message);}
    catch (error) {if (alive.current) setMessage(error instanceof Error ? error.message : String(error));}
  };
  const independent = selection.pitch_baseline_mode === 'independent';
  const knownDurations = durations?.every(value => typeof value === 'number' && Number.isFinite(value));
  const loudnessPreview = knownDurations ? durations!.some(value => value! < .4)
    ? '本次候选：双方统一采用活动区间 RMS（不是 LUFS）；任一侧短于 400 ms，不补静音或缩块。仍需可靠非静音区间。'
    : '本次候选：双方均至少 400 ms，整段 integrated LUFS → −23 LUFS 恒定增益；依赖检测与门控有限值检查通过后才应用。'
    : '预计：双方均至少 400 ms 时采用整段 LUFS → −23 LUFS 恒定增益；任一侧较短时双方统一使用可靠活动区间 RMS（不是 LUFS）。静音或无可靠区间时跳过。';
  const ready = validRange && selection.pitch_confirmed && (!independent || baselines.reference?.baseline.status === 'applied' && baselines.learner?.baseline.status === 'applied');
  return <section className="pron-normalization" aria-labelledby="pron-prosody-title">
    <h3 id="pron-prosody-title">相对音高与整段响度 <span className="badge">独立实验观察</span></h3>
    <label className="pron-toggle"><input type="checkbox" checked={selection.pitch === true} disabled={disabled || busy || !!pitchGap} onChange={e => onChange({...selection, pitch: e.target.checked})}/><span>相对音高（半音 st）</span></label>
    {selection.pitch && <>
      <div className="pron-normalization-slots">
        <label className="pron-field"><span>F0 下限（Hz）</span><input type="number" min="1" value={settings.floor_hz} disabled={disabled || busy} onChange={e => changeRange('floor_hz', e.target.value)}/></label>
        <label className="pron-field"><span>F0 上限（Hz）</span><input type="number" min="1" value={settings.ceiling_hz} disabled={disabled || busy} onChange={e => changeRange('ceiling_hz', e.target.value)}/></label>
      </div>
      {!validRange && <p role="alert">请填写正数且上限大于下限的 F0 范围。</p>}
      <label className="pron-field"><span>音高基线方式</span><select value={selection.pitch_baseline_mode || 'utterance'} disabled={disabled || busy} onChange={e => onChange({...selection, pitch_baseline_mode: e.target.value as 'utterance' | 'independent', pitch_confirmed: false})}><option value="utterance">本句相对音高（双方各自整句）</option><option value="independent">独立中性语句（双方同协议）</option></select></label>
      {independent && <div className="pron-normalization-slots">{(['reference', 'learner'] as const).map(role => <div key={role}><strong>{role === 'reference' ? '标准说话人中性语句' : '学习者中性语句'}</strong><button type="button" disabled={disabled || busy || !validRange || !!pitchGap} onClick={() => void importBaseline(role)}>导入中性语句 WAV</button><small>{baselines[role] ? `${baselines[role]!.baseline.material.wav_name}：${baselines[role]!.baseline.status === 'applied' ? `${baselines[role]!.baseline.baseline_hz?.toFixed(1)} Hz 基线` : baselines[role]!.baseline.reason}` : '未导入；不与本句基线混配'}</small></div>)}</div>}
      <label className="pron-toggle"><input type="checkbox" checked={selection.pitch_confirmed === true} disabled={disabled || busy || !validRange || !!pitchGap} onChange={e => onChange({...selection, pitch_confirmed: e.target.checked})}/><span>已检查 F0 范围和跟踪质量{independent ? '；独立材料为当前说话人的同协议中性语句' : '；本句基线只用于相对音高观察'}</span></label>
      <small>只减去 log2 域中位基线，不拉齐音域或除以标准差；无声与疑似八度跳变留缺口。不诊断重音、日语高低音或声调。</small>
    </>}
    <p role="status">{pitchGap ? '当前解释器缺少相对音高所需的运行库，先别打开或确认这一项。' : !selection.pitch ? '相对音高已关闭。' : ready ? independent ? '预计使用双方独立中性语句的相对半音。' : '预计使用本句相对半音，不称作完整说话人校准。' : '音高证据不足或未确认；以提交后的实际帧检查为准。'}</p>
    {pitchGap && <p role="alert">{gapText(pitchGap)}</p>}
    <label className="pron-toggle"><input type="checkbox" checked={selection.loudness === true} disabled={disabled || busy || !!loudnessGap} onChange={e => onChange({...selection, loudness: e.target.checked})}/><span>整段恒定响度校正（不修改音频）</span></label>
    <p role="status">{loudnessGap ? '当前解释器缺少响度所需的运行库，先别打开这一项。' : selection.loudness ? loudnessPreview : '响度校正已关闭。'}</p>
    {loudnessGap && <p role="alert">{gapText(loudnessGap)}</p>}
    <small>原始电平与实际基线始终写入报告。不能消除既有削波、AGC、噪声或麦克风频响，也不据单音素校正均值判断轻重音。正常长度测量需要 pyloudnorm / SciPy；依赖缺失时保留原始观察，不自动安装。</small>
    <button type="button" disabled={disabled || busy} onClick={() => void configureRuntime()}>路径配置 → 配置运行库…</button>
    {message && <p role="status">{message}</p>}
  </section>;
}
