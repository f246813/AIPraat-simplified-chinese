import { useEffect, useRef, useState, type FormEvent } from 'react';
import { AudioLines, Info, List, ArrowRight } from 'lucide-react';
import { HostBridge } from './bridge';
import { MfaPresetEditor, MfaStatusView, mfaStatusLabel, type MfaData } from './MfaPresetEditor';
import './pronunciation.css';
import {NormalizationSettings, type NormalizationSelection} from './NormalizationSettings';
import {ProsodySettings} from './ProsodySettings';

type Bootstrap = {sounds: {id: string; name: string; duration?: number | null}[]; profiles: {id: string; name: string; language: string}[]; model: {source: string; name: string}; theme: string; mfa?: MfaData; normalization?: {version: string; formants: boolean; pitch?: boolean; loudness?: boolean; inventories: Record<string, string[]>}};
export type Generation = {id: string; preset_id: string; transcript: string; words: {word: string; candidates: {raw: string[]; ipa: string[]; source: string}[]}[]; sequence: string[]; source: string};
export type TutorRpc = (method: string, params: Record<string, unknown>) => Promise<unknown>;
const hostRpc: TutorRpc = async (method, params) => {
  await new HostBridge().wait();
  return window.pywebview!.api!.rpc!(method, params);
};

export function PronunciationForm({rpc=hostRpc}: {rpc?: TutorRpc}) {
  const [data, setData] = useState<Bootstrap>();
  const [reference, setReference] = useState(''); const [learner, setLearner] = useState('');
  const [language, setLanguage] = useState('cmn'); const [phonemes, setPhonemes] = useState('');
  const [transcript, setTranscript] = useState(''); const [profileId, setProfileId] = useState('');
  const [rules, setRules] = useState(false); const [threshold, setThreshold] = useState('1.25');
  const [explain, setExplain] = useState(true); const [vision, setVision] = useState(false);
  const [advanced, setAdvanced] = useState(false); const [busy, setBusy] = useState(false);
  const [normalization, setNormalization] = useState<NormalizationSelection>({version: 'normalization-v1', formants: false, confirmed: false});
  const [calibrationBusy, setCalibrationBusy] = useState(false);
  const [error, setError] = useState(''); const [fieldError, setFieldError] = useState('');
  const phoneRef = useRef<HTMLInputElement>(null); const thresholdRef = useRef<HTMLInputElement>(null);
  const submitting = useRef(false);
  const [mfa, setMfa] = useState<MfaData>(); const [presetId, setPresetId] = useState(''); const [editorId, setEditorId] = useState('');
  const [generation, setGeneration] = useState<Generation>(); const [choices, setChoices] = useState<Record<string, number>>({});
  const [generationValid, setGenerationValid] = useState(false); const [applied, setApplied] = useState(false);
  const [generating, setGenerating] = useState(false); const [notice, setNotice] = useState('');
  const epoch = useRef(0); const alive = useRef(true); const generationPending = useRef(false);
  const invalidate = () => {epoch.current++; setGenerationValid(false); setApplied(false);};
  useEffect(() => {
    alive.current = true;
    let mounted = true; const media = matchMedia('(prefers-color-scheme: dark)'); let preference = 'system';
    const theme = () => {document.documentElement.dataset.theme = preference === 'system' ? (media.matches ? 'dark' : 'light') : preference;};
    theme(); media.addEventListener('change', theme);
    void rpc('tutor.bootstrap', {}).then(result => {
      if (!mounted) return;
      const value = result as Bootstrap; setData(value); setReference(value.sounds[0].id); setLearner(value.sounds[1].id);
      setProfileId(value.profiles[0]?.id || ''); preference = ['light','dark'].includes(value.theme) ? value.theme : 'system'; theme();
      setNormalization({version: 'normalization-v1', formants: value.normalization?.formants === true, confirmed: false,
        ...(value.normalization?.pitch ? {pitch: true} : {}), ...(value.normalization?.loudness ? {loudness: true} : {})});
      setMfa(value.mfa); const initial = value.mfa?.presets.find(p => p.id === value.mfa?.default_preset); if (initial) {setPresetId(initial.id); setLanguage(initial.language);}
    }).catch(e => {if (mounted) setError(e instanceof Error ? e.message : String(e));});
    return () => {mounted = false; alive.current = false; epoch.current++; if (generationPending.current) void rpc('tutor.cancel_generation', {}).catch(() => {}); media.removeEventListener('change', theme);};
  }, [rpc]);
  const preset = mfa?.presets.find(p => p.id === presetId);
  const effectiveProfileId = preset ? preset.rule_id || '' : profileId;
  const profile = data?.profiles.find(value => value.id === effectiveProfileId);
  const calibrationContext = JSON.stringify([reference, learner, language, effectiveProfileId, presetId, rules]);
  useEffect(() => {setNormalization(value => ({version: 'normalization-v1', formants: value.formants, confirmed: false,
    ...(value.pitch !== undefined ? {pitch: value.pitch, pitch_confirmed: false, pitch_baseline_mode: 'utterance', pitch_settings: value.pitch_settings} : {}),
    ...(value.loudness !== undefined ? {loudness: value.loudness} : {})}));}, [calibrationContext]);
  const chooseRules = (value: boolean) => {setRules(value); if (value && profile && !preset) setLanguage(profile.language);};
  const switchManual = (edited?: string) => {invalidate(); if (preset) {setLanguage(preset.language); if (preset.rule_id) setProfileId(preset.rule_id);} setPresetId(''); if (edited !== undefined) setPhonemes(edited); setNotice('已转为手动 IPA：不再提交 G2P 生成记录或原始标签。');};
  const selectPreset = (id: string) => {if (!id) {switchManual(); return;} invalidate(); setPresetId(id); const selected = mfa?.presets.find(p => p.id === id); if (selected) {setLanguage(selected.language); if (!selected.rule_id) setRules(false);} setNotice(phonemes ? '目标已改变，旧音位仅供查看；请重新生成并应用，或明确转为手动 IPA。' : '');};
  const updateMfa = (value: MfaData) => {if (JSON.stringify([mfa?.presets.find(p => p.id === presetId), mfa?.launcher]) !== JSON.stringify([value.presets.find(p => p.id === presetId), value.launcher])) {invalidate(); setNotice('资源或映射已改变，请重新生成；旧音位不会自动覆盖。');} setMfa(value);};
  const applyGeneration = (result: Generation, selected: Record<string, number>) => {
    const byWord = new Map(result.words.map(w => [w.word, w]));
    const phones = result.sequence.flatMap(word => byWord.get(word)?.candidates[selected[word]]?.ipa || []);
    setPhonemes(phones.join(' ')); setApplied(true); setFieldError(''); setNotice('已应用完整读音；编辑 IPA 将明确转为手动路径。');
  };
  const completeChoices = !!generation && generation.words.every(w => Number.isInteger(choices[w.word]) && !!w.candidates[choices[w.word]]?.ipa.length);
  const generate = async () => {
    if (!preset || !transcript.trim() || !preset.status.can_generate || generationPending.current || submitting.current) return;
    const current = ++epoch.current; const mayReplace = !phonemes.trim() || generationValid && applied;
    generationPending.current = true; setGenerating(true); setGenerationValid(false); setApplied(false); setError(''); setNotice('正在本地运行 MFA G2P；不会调用解释模型。');
    try {
      const result = await rpc('tutor.generate_phonemes', {preset_id: preset.id, transcript}) as Generation;
      if (!alive.current || epoch.current !== current) return;
      if (result.preset_id !== preset.id || result.transcript.trim() !== transcript.trim() || !result.words.length || !result.sequence.length || result.words.some(w => !w.candidates.length || w.candidates.some(c => !c.ipa.length)) || result.sequence.some(word => !result.words.some(w => w.word === word))) throw new Error('生成结果不完整或与当前输入不一致，请重新生成。');
      const selected = Object.fromEntries(result.words.filter(w => w.candidates.length === 1).map(w => [w.word, 0]));
      setGeneration(result); setChoices(selected); setGenerationValid(true);
      if (mayReplace && result.words.every(w => w.candidates.length === 1)) applyGeneration(result, selected);
      else setNotice('请选择每个词的读音并应用。已有 IPA 未被覆盖；应用会替换旧内容。');
    } catch(e) {if (alive.current && epoch.current === current) {setError(e instanceof Error ? e.message : String(e)); setNotice('生成未完成，请根据错误提示检查资源或输入后重试；原有 IPA 已保留。');}}
    finally {generationPending.current = false; if (alive.current) setGenerating(false);}
  };
  const cancelGeneration = async () => {invalidate(); setNotice('已请求取消，本次结果不会应用；未结束前不能再次生成。'); try {await rpc('tutor.cancel_generation', {});} catch(e) {if (alive.current) setError(e instanceof Error ? e.message : String(e));}};
  const acousticReady = !!preset && !!mfa?.resources.acoustic.some(r => r.id === preset.acoustic && r.exists) && !!mfa?.resources.dictionary.some(r => r.id === preset.dictionary && r.exists);
  const submit = async (event: FormEvent) => {
    event.preventDefault(); if (submitting.current || generationPending.current || calibrationBusy || !data) return;
    setError(''); setFieldError('');
    if (preset && (!generationValid || !applied || !completeChoices || !generation || !acousticReady)) {setError('请先重新生成、选择并应用完整读音，并配置可用的词典和声学模型。'); return;}
    if (!phonemes.split(/[\s,，;；]+/).filter(Boolean).length) {setFieldError('phones'); phoneRef.current?.focus(); return;}
    if (!rules && (threshold.trim() === '' || !Number.isFinite(Number(threshold)))) {setFieldError('threshold'); setAdvanced(true); setTimeout(() => thresholdRef.current?.focus(), 0); return;}
    if (rules && (!profile || !preset && language.trim() !== profile.language)) {setError('目标语言与所选音位判据不一致。'); return;}
    submitting.current = true; setBusy(true);
    try {await rpc('tutor.submit', {reference, learner, language: preset?.language || language, phonemes, transcript, threshold: Number(threshold), profile_id: rules ? effectiveProfileId : null, explain, vision, ...(rules ? {normalization} : {}), ...(preset && generation ? {preset_id: preset.id, generation_id: generation.id, choices} : {})});}
    catch(e) {setError(e instanceof Error ? e.message : String(e)); submitting.current = false; setBusy(false);}
  };
  const cancel = async () => {
    if (submitting.current) return; submitting.current = true; setBusy(true);
    invalidate();
    try {await rpc('tutor.cancel', {});} catch(e) {setError(e instanceof Error ? e.message : String(e)); submitting.current = false; setBusy(false);}
  };
  const name = (id: string) => data?.sounds.find(sound => sound.id === id)?.name || '';
  const source = data ? `${data.model.source} · ${data.model.name}` : '';
  const sectionTitle = (number: string, title: string) => <div className="pron-section-head"><span>{number}</span><h2>{title}</h2></div>;
  return <div className="pron-window">
    <div className="pron-topbar"><div className="pron-brand"><AudioLines aria-hidden="true" size={20}/>AIPraat <span className="badge">AI 纠音</span></div></div>
    <main className="pron-main"><header className="pron-intro"><div><div className="pron-eyebrow">PRONUNCIATION</div><h1>分析两段音频，比较差异</h1><p>选择标准音与学习者录音，设置本次分析的发音目标。</p></div><div className="pron-mark"><AudioLines aria-hidden="true" size={27}/></div></header>
      {!data ? <div className="pron-loading">{error ? <p role="alert">{error}</p> : <p role="status">正在读取录音对象与模型设置…</p>}<button onClick={() => void cancel()} disabled={busy}>关闭</button></div> :
      <form onSubmit={submit} noValidate className="pron-form">
        {error && <div className="pron-error" role="alert">{error}</div>}
        <div className="pron-layout"><div className="pron-card"><fieldset disabled={busy || calibrationBusy}>
          <section className="pron-section">{sectionTitle('01','录音对象')}<div className="pron-fields">
            <label className="pron-field"><span>标准音范本</span><select value={reference} onChange={e => setReference(e.target.value)}>{data.sounds.map(sound => <option key={sound.id} value={sound.id}>{sound.id}: {sound.name}</option>)}</select></label>
            <label className="pron-field"><span>学习者录音</span><select value={learner} onChange={e => setLearner(e.target.value)}>{data.sounds.map(sound => <option key={sound.id} value={sound.id}>{sound.id}: {sound.name}</option>)}</select></label>
          </div><p className="pron-help">来自 Praat 当前选中的 Sound 对象。</p></section>
          <section className="pron-section">{sectionTitle('02','发音目标')}<div className="pron-fields">
            <div className="pron-target pron-full"><label className="pron-field"><span>目标语言与读音</span><select value={presetId} onChange={e => selectPreset(e.target.value)}><option value="">高级手动 IPA（普通话 / 日语 / 自定义）</option>{mfa?.presets.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label><button type="button" disabled={generating || !mfa?.presets.length} onClick={() => setEditorId(presetId || mfa?.presets[0]?.id || '')}>配置配套资源</button></div>
            {preset ? <div className="pron-full"><MfaStatusView status={preset.status}/><p className="pron-help">缺声学模型不妨碍仅生成读音，但不能完成 MFA 音频对齐。</p></div> : <label className="pron-field pron-full"><span>目标语言代码</span><input value={language} onChange={e => setLanguage(e.target.value)} aria-describedby="pron-language-help"/><small id="pron-language-help">手动语言代码，例如 cmn、en、ja；不会自动运行通用分词。</small></label>}
            <div className="pron-transcript pron-full"><label className="pron-field"><span>参考文本 <em>· {preset ? '生成必填' : '选填'}</em></span><input value={transcript} placeholder="例如 hello world" onChange={e => {setTranscript(e.target.value); invalidate(); if (preset && (generation || generating)) setNotice('参考文本已改变，生成音位已失效；本次迟到结果不会应用。请重新生成或转为手动 IPA。');}} aria-describedby="pron-transcript-help"/><small id="pron-transcript-help">{preset ? !transcript.trim() ? '请输入参考文本后再生成。' : !preset.status.can_generate ? '无法生成：请配置 G2P、音位体系 / 映射和 MFA 启动方式，并检查兼容性。' : '仅点击按钮才运行本地 MFA G2P。' : '使用旧 MFA 词典对齐时填写对应文本。'}</small></label>{preset && <button type="button" disabled={generating || !transcript.trim() || !preset.status.can_generate} onClick={() => void generate()}>{generating ? '正在生成音位…' : '自动生成音位'}</button>}</div>
            {generating && <div className="pron-full pron-generation-progress" role="status">本地 G2P 正在运行，分析暂不可用。<button type="button" onClick={() => void cancelGeneration()}>取消生成</button></div>}
            {preset && generation && generationValid && <div className="pron-candidates pron-full"><h3>逐词读音候选</h3>{generation.words.map(w => <label className="pron-field" key={w.word}><span>{w.word}</span><select aria-label={`${w.word} 的读音`} value={choices[w.word] ?? ''} onChange={e => {setChoices(p => ({...p, [w.word]: Number(e.target.value)})); setApplied(false);}}><option value="" disabled>请选择读音</option>{w.candidates.map((c, i) => <option key={i} value={i}>{c.ipa.join(' ')} · {c.source}（原始：{c.raw.join(' ')}）</option>)}</select></label>)}<button type="button" disabled={!completeChoices || generating} onClick={() => applyGeneration(generation, choices)}>{applied ? '已应用读音' : '确认并应用读音'}</button><small>重复词使用同一选择，并按参考文本原词序还原。</small></div>}
            <label className="pron-field pron-full"><span>目标音位 <em>· IPA</em></span><input className="pron-ipa" ref={phoneRef} value={phonemes} onChange={e => {if (preset) switchManual(e.target.value); else setPhonemes(e.target.value); if (fieldError === 'phones') setFieldError('');}} aria-invalid={fieldError === 'phones'} aria-describedby="pron-phone-help pron-phone-error"/><small id="pron-phone-help">用空格、逗号或分号分隔。{preset && '编辑即转为手动 IPA，不保留失配的原始标签。'}</small>{fieldError === 'phones' && <small className="pron-field-error" id="pron-phone-error" role="alert">请输入至少一个 IPA 音位。</small>}</label>
            {(notice || preset) && <div className="pron-full pron-generation-notice"><p role="status">{notice || '请生成并应用读音后再开始分析。'}</p>{preset && <button type="button" onClick={() => switchManual()}>转为手动 IPA</button>}</div>}
            {preset && generation && <p className="pron-full pron-help">音位来源：{generation.source}{!generationValid ? '（已失效）' : !applied ? '（尚未应用）' : ''}</p>}
          </div></section>
          <section className="pron-section">{sectionTitle('03','分析方式')}<div className="pron-modes" role="radiogroup" aria-label="分析方式">
            <label className="pron-choice"><input type="radio" name="pron-mode" checked={!rules} onChange={() => chooseRules(false)}/><span><strong>声学对比</strong><small>沿用现有声学差异阈值</small></span></label>
            <label className="pron-choice"><input type="radio" name="pron-mode" checked={rules} disabled={!!preset && !profile} onChange={() => chooseRules(true)}/><span><strong>音位判据 <span className="badge">实验性</span></strong><small>英语 / 日语的分音类观察</small></span></label>
          </div>{preset && !profile && <p className="pron-notice">{preset.name}暂无配套音位判据；可用声学对比，不套用美式规则。</p>}{rules && <>{!preset && <label className="pron-field pron-profile"><span>判据预设</span><select value={profileId} onChange={e => {setProfileId(e.target.value); const selected = data.profiles.find(p => p.id === e.target.value); if (selected) setLanguage(selected.language);}}>{data.profiles.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>}<p className="pron-notice">{preset && `本次判据：${profile?.name}。`}需可靠对齐；未执行独立音素识别，判错阈值尚未校准。{profile?.language === 'ja' && ' 日语拍结构尚未评估。'}</p></>}
          {rules && <NormalizationSettings key={calibrationContext} rpc={rpc} selection={normalization} onChange={setNormalization} inventory={data.normalization?.inventories[effectiveProfileId] || []} reference={reference} learner={learner} language={preset?.language || language} profileId={effectiveProfileId} readingId={presetId || effectiveProfileId} disabled={busy || generating} onBusy={setCalibrationBusy}/>}
          {rules && <ProsodySettings key={'prosody' + calibrationContext} durations={[data.sounds.find(s => s.id === reference)?.duration, data.sounds.find(s => s.id === learner)?.duration]} rpc={rpc} selection={normalization} onChange={setNormalization} reference={reference} learner={learner} language={preset?.language || language} profileId={effectiveProfileId} readingId={presetId || effectiveProfileId} disabled={busy || generating} onBusy={setCalibrationBusy}/>}
          <label className="pron-switchrow"><span><strong>使用 AI 生成解释</strong><small>当前模型：{source}（仅用于解释，不用于 G2P 或对齐）</small></span><input className="pron-switch" type="checkbox" checked={explain} onChange={e => setExplain(e.target.checked)}/></label>
          <details open={advanced} onToggle={e => setAdvanced(e.currentTarget.open)}><summary>高级设置</summary><div className="pron-advanced">
            <label className="pron-field"><span>声学对比阈值</span><input ref={thresholdRef} type="number" step="any" disabled={rules || busy} value={threshold} onChange={e => {setThreshold(e.target.value); if (fieldError === 'threshold') setFieldError('');}} aria-invalid={fieldError === 'threshold'} aria-describedby={rules ? 'pron-threshold-help' : fieldError === 'threshold' ? 'pron-threshold-error' : undefined}/>{rules && <small id="pron-threshold-help">音位判据不使用此阈值。</small>}{fieldError === 'threshold' && <small className="pron-field-error" id="pron-threshold-error" role="alert">请输入有效数字。</small>}</label>
            <label className="pron-switchrow"><span><strong>AI 视觉解释</strong><small>将对比图交给当前模型解释</small></span><input className="pron-switch" type="checkbox" checked={vision} onChange={e => setVision(e.target.checked)}/></label>
          </div></details></section>
        </fieldset></div><aside className="pron-sidebar"><div className="pron-aside"><h2><List aria-hidden="true" size={20}/>本次分析</h2><dl><dt>标准音 → 学习者</dt><dd>{name(reference)} → {name(learner)}</dd><dt>目标语言与读音</dt><dd>{preset?.name || `手动 IPA · ${language || '未填写'}`}</dd><dt>目标音位</dt><dd className="pron-ipa">{phonemes.split(/[\s,，;；]+/).filter(Boolean).join(' · ') || '未填写'}</dd><dt>音位来源</dt><dd>{preset ? generationValid && applied ? generation?.source : '等待生成 / 应用（旧结果无效）' : '手动 IPA'}</dd>{preset && <><dt>本次资源</dt><dd>G2P：{mfaStatusLabel(preset.status.g2p)}<br/>词典：{mfaStatusLabel(preset.status.dictionary)}<br/>声学模型：{mfaStatusLabel(preset.status.acoustic)}</dd></>}<dt>分析方式</dt><dd>{rules ? `${profile?.name}音位判据` : '声学对比'}</dd><dt>结果解释</dt><dd>{[explain ? 'AI 文字解释' : '', vision ? 'AI 视觉解释' : ''].filter(Boolean).join(' + ') || '不使用 AI 解释'}</dd>{(explain || vision) && <><dt>解释模型</dt><dd>{source}</dd></>}</dl><p>分析会比较两段录音的声学特征，输出发音差异与参考解释。</p></div><div className="pron-info"><Info aria-hidden="true" size={16}/><p>音位判据用于观察声学特征，实验性结果不作为已校准的发音评分。</p></div></aside></div>
        <footer className="pron-footer"><p>{preset && !acousticReady ? '请配置声学模型以完成 MFA 对齐。' : '检查录音顺序与目标音位后，开始本次分析。'}</p><div><button type="button" disabled={busy} onClick={() => void cancel()}>取消</button><button className="primary" type="submit" disabled={busy || generating || calibrationBusy || !!preset && (!generationValid || !applied || !completeChoices || !acousticReady)}>{busy ? '正在提交…' : '开始分析'}<ArrowRight aria-hidden="true" size={16}/></button></div></footer>
      </form>}
    </main>
    {mfa && editorId && <MfaPresetEditor key={editorId} data={mfa} presetId={editorId} rpc={rpc} onData={updateMfa} onClose={() => setEditorId('')}/>}
  </div>;
}
