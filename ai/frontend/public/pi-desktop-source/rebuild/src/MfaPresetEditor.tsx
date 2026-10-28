import { useEffect, useRef, useState } from 'react';
import { hintText } from './ui';
import './mfa-preset-editor.css';

export type MfaStatus = {g2p: string; dictionary: string; acoustic: string; compatibility: string; issues: string[]; can_generate: boolean};
export type Preset = {id: string; name: string; language: string; variant: string; rule_id: string | null; g2p: string; dictionary: string; acoustic: string; phone_set: 'ipa' | 'arpabet' | 'custom'; phone_map: Record<string, string>; status: MfaStatus};
export type Resource = {id: string; name: string; path: string; exists: boolean; used_by: string[]};
export type Launcher = {executable: string; conda_executable: string; conda_environment: string};
export type MfaData = {presets: Preset[]; default_preset: string; resources: Record<'g2p' | 'dictionary' | 'acoustic', Resource[]>; launcher: Launcher; theme: string};
export type MfaRpc = (method: string, params: Record<string, unknown>) => Promise<unknown>;
export type MfaEnvironmentCheck = {id: string; label: string; status: string; detail: string; repair: string};
export type MfaEnvironmentProbe = {schema: string; ready: boolean; summary: string; launcher: string; environment: string; version: string; python: string; checks: MfaEnvironmentCheck[]; blocking: string[]};
export type MfaEnvironmentResource = {id: string; name: string; path: string; exists: boolean} | null;
export type MfaEnvironment = {schema: string; preset: {id: string; name: string; language: string; variant: string; dictionary: string; acoustic: string}; launcher: Launcher; ready: boolean; summary: string; environment: MfaEnvironmentProbe; resources: {g2p: MfaEnvironmentResource; dictionary: MfaEnvironmentResource; acoustic: MfaEnvironmentResource}; status: MfaStatus; blocking: string[]; notes: string[]};
const resourceKinds = [{kind: 'dictionary', label: 'MFA 发音词典'}, {kind: 'acoustic', label: 'MFA 声学模型'}] as const;
const statusNames: Record<string, string> = {missing: '未配置 / 文件缺失', unconfigured: '未配置', not_configured: '未配置', configured: '已配置', exists: '文件存在', passed: '静态检查通过', static_ok: '静态检查通过', compatible: '静态兼容', unknown: '未验证', unverified: '未验证', incompatible: '冲突', conflict: '冲突', verified: '已完成实际验证'};
export const mfaStatusLabel = (value: string) => statusNames[value] || value || '未配置';
const checkNames: Record<string, string> = {ok: '可用', error: '阻塞', missing: '未配置'};
export function MfaStatusView({status}: {status: MfaStatus}) {
  return <div className="mfa-status" role="status"><p>G2P：{mfaStatusLabel(status.g2p)} · 词典：{mfaStatusLabel(status.dictionary)} · 声学模型：{mfaStatusLabel(status.acoustic)}</p><p>兼容性：{mfaStatusLabel(status.compatibility)}</p>{status.issues.length > 0 && <ul>{status.issues.map((issue, i) => <li key={i}>{hintText(issue)}</li>)}</ul>}</div>;
}
const environmentResource = (resource: MfaEnvironmentResource, label: string) => `${label}：${resource ? `${resource.name}${resource.exists ? '' : '（文件缺失）'}` : '未配置'}`;
/** Environment facts only: per-item status, the first blocker and its repair hint. */
export function MfaEnvironmentView({report, onRetry, busy}: {report: MfaEnvironment; onRetry: () => void; busy: boolean}) {
  return <section className="mfa-environment" role="status" aria-label="对齐环境检测结果">
    <h3>对齐环境：{report.ready ? '可用' : '不完整'}</h3>
    <p>{hintText(report.summary)}</p>
    {report.blocking.length > 0 && <p className="mfa-error" role="alert">首个阻塞：{hintText(report.blocking[0])}</p>}
    <ul className="mfa-checks">{report.environment.checks.map(check => <li key={check.id} className={check.status === 'ok' ? 'ok' : 'bad'}>
      <span className="mfa-check-name">{check.label}：{checkNames[check.status] || mfaStatusLabel(check.status)}</span>
      <span className="mfa-check-detail">{hintText(check.detail)}</span>
      {check.repair && <span className="mfa-check-repair">修复：{hintText(check.repair)}</span>}
    </li>)}</ul>
    <p className="muted">{environmentResource(report.resources.dictionary, '词典')} · {environmentResource(report.resources.acoustic, '声学模型')} · 启动方式：{report.environment.launcher}{report.environment.version ? ` · ${report.environment.version}` : ''}</p>
    {report.notes.map((note, i) => <p className="muted" key={i}>{hintText(note)}</p>)}
    <div className="actions"><button type="button" disabled={busy} onClick={onRetry}>重新检测</button></div>
  </section>;
}

/** Imports persist independently; only Save changes the preset binding and launcher. */
export function MfaPresetEditor({data: initial, presetId, selectedG2pId, rpc, onData, onClose}: {data: MfaData; presetId: string; selectedG2pId?: string; rpc: MfaRpc; onData: (data: MfaData) => void; onClose: () => void}) {
  const original = initial.presets.find(p => p.id === presetId)!;
  const [data, setData] = useState(initial);
  const [draft, setDraft] = useState<Preset>(() => ({...original, phone_map: {...original.phone_map},
    ...(selectedG2pId !== undefined && selectedG2pId !== original.g2p ? {g2p: selectedG2pId, status: {...original.status, g2p: selectedG2pId ? 'unknown' : 'missing', compatibility: 'unknown', can_generate: false, issues: ['G2P 模型选择已修改，请检查兼容性并保存配套']}} : {})}));
  const [launcher, setLauncher] = useState({...initial.launcher});
  const [mapping, setMapping] = useState(JSON.stringify(original.phone_map, null, 2));
  const [makeDefault, setMakeDefault] = useState(initial.default_preset === presetId);
  const [busy, setBusy] = useState(false); const pending = useRef(false);
  const [error, setError] = useState(''); const [message, setMessage] = useState('');
  const [environment, setEnvironment] = useState<MfaEnvironment | null>(null);
  const dialog = useRef<HTMLDialogElement>(null); const alive = useRef(true);
  useEffect(() => {alive.current = true; dialog.current?.showModal(); return () => {alive.current = false;};}, []);
  const close = () => {if (!pending.current) onClose();};
  const change = (patch: Partial<Preset>) => {setDraft(p => {const next = {...p, ...patch}; return {...next, status: {g2p: next.g2p ? 'unknown' : 'missing', dictionary: next.dictionary ? 'unknown' : 'missing', acoustic: next.acoustic ? 'unknown' : 'missing', compatibility: 'unknown', can_generate: false, issues: ['草稿尚未检查；请检查资源类型和音位覆盖']}};}); setEnvironment(null); setMessage('草稿已修改；请重新检查兼容性');};
  const parsedPreset = () => {
    const parsed: unknown = JSON.parse(mapping);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed) || Object.entries(parsed).some(([key, value]) => !key.trim() || typeof value !== 'string' || !value.trim() || /\s/.test(value))) throw new Error('映射必须为 JSON 对象，每个原始标签对应一个非空 IPA 单位（不能包含空格）');
    return {...draft, phone_map: parsed as Record<string, string>};
  };
  const run = async (action: () => Promise<void>) => {
    if (pending.current) return; pending.current = true; setBusy(true); setError('');
    try {await action();} catch (e) {if (alive.current) setError(e instanceof Error ? e.message : String(e));}
    finally {pending.current = false; if (alive.current) setBusy(false);}
  };
  const publish = (value: MfaData) => {if (!alive.current) return; setData(value); onData(value);};
  const detectEnvironment = () => void run(async () => {
    const preset = parsedPreset();
    const result = await rpc('mfa.environment', {preset, launcher}) as MfaEnvironment;
    if (!alive.current) return;
    setDraft(p => ({...p, phone_map: preset.phone_map, status: result.status}));
    setEnvironment(result);
    setMessage('对齐环境检测完成');
  });
  return <dialog ref={dialog} className="modal mfa-editor" aria-label={`配置配套资源 · ${original.name}`} onCancel={e => {e.preventDefault(); close();}} onClick={e => {if (e.target === e.currentTarget) close();}}>
    <header><h2>配置配套资源 · {original.name}</h2><button type="button" aria-label="关闭资源配置" disabled={busy} onClick={close}>×</button></header>
    <p className="muted">{original.id.startsWith('custom-') ? '填写此配套的语言与目标读音' : `${original.language} · ${original.variant}`}</p>
    <p className="mfa-note">配置本地词典、声学模型和 MFA 启动方式；导入的资源保留在资源库，点击“保存配套”应用绑定</p>
    {error && <p className="mfa-error" role="alert">{error}</p>}
    <fieldset disabled={busy}>
      {original.id.startsWith('custom-') && <div className="mfa-custom-identity"><label>配套名称<input value={draft.name} onChange={e => change({name: e.target.value})}/></label><label>语言代码<input value={draft.language} placeholder="例如 de、fr、cmn" onChange={e => change({language: e.target.value})}/></label><label>地区 / 读音变体（选填）<input value={draft.variant} onChange={e => change({variant: e.target.value})}/></label><p className="muted">按所选模型填写语言，使用声学对比分析发音</p></div>}
      {resourceKinds.map(({kind, label}) => <div className="mfa-resource" key={kind}><label><span>{label}</span><select value={draft[kind]} onChange={e => change({[kind]: e.target.value})}><option value="">未配置</option>{draft[kind] && !data.resources[kind].some(r => r.id === draft[kind]) && <option value={draft[kind]}>已移除的资源</option>}{data.resources[kind].map(r => <option value={r.id} key={r.id}>{r.name}{!r.exists ? '（文件缺失）' : ''}</option>)}</select></label><button type="button" onClick={() => void run(async () => {publish(await rpc('mfa.choose', {kind}) as MfaData); if (alive.current) setMessage('资源已登记；请从下拉框选择，绑定仍未保存');})}>{kind === 'dictionary' ? '添加本地词典' : '添加本地声学模型'}</button></div>)}
      <details><summary>音位体系与高级 IPA 映射</summary><label>音位体系<select value={draft.phone_set} onChange={e => change({phone_set: e.target.value as Preset['phone_set']})}><option value="ipa">IPA</option><option value="arpabet">英语 ARPAbet（内置显式映射）</option><option value="custom">自定义标签</option></select></label><label>原始标签 → IPA 映射（JSON）<textarea value={mapping} onChange={e => {setMapping(e.target.value); change({});}} spellCheck={false}/></label><p className="muted">每个标签对应一个 IPA 单位；未知标签与多对多映射不能应用；ARPAbet 可使用内置映射，额外映射在此补充</p></details>
      <section className="mfa-launcher"><h3>MFA 启动方式（独立于旧全局对齐配置）</h3><p className="muted">直接执行路径，或清空直接路径后填写 Conda 路径及环境名</p>{([{key: 'executable', label: 'MFA 可执行文件路径'}, {key: 'conda_executable', label: 'Conda 可执行文件路径'}, {key: 'conda_environment', label: 'Conda 环境名'}] as const).map(({key, label}) => <div className="mfa-resource" key={key}><label>{label}<input value={launcher[key]} onChange={e => {setLauncher(p => ({...p, [key]: e.target.value})); change({}); setMessage('启动配置尚未保存；请检查 MFA');}}/></label>{key !== 'conda_environment' && <button type="button" onClick={() => void run(async () => {const result = await rpc('mfa.choose_launcher', {}) as {path: string}; if (alive.current && result.path) {setLauncher(p => ({...p, [key]: result.path})); change({});}})}>选择{key === 'executable' ? 'MFA' : 'Conda'}路径</button>}</div>)}<div className="mfa-launcher-actions"><button type="button" onClick={() => void run(async () => {const result = await rpc('mfa.check_launcher', {launcher}) as {version: string; help_order: string; status: string}; if (alive.current) setMessage(`MFA：${result.status} · 版本：${result.version} · 参数顺序：${result.help_order}`);})}>检查 MFA</button><button type="button" onClick={detectEnvironment}>检测对齐环境</button></div></section>
      {environment && <MfaEnvironmentView report={environment} onRetry={detectEnvironment} busy={busy}/>}
      <label className="mfa-check"><input type="checkbox" checked={makeDefault} onChange={e => setMakeDefault(e.target.checked)}/>将此读音设为默认预设</label>
    </fieldset>
    <MfaStatusView status={draft.status}/>{message && <p role="status" className="mfa-note">{hintText(message)}</p>}
    <div className="actions"><button type="button" disabled={busy} onClick={close}>取消</button><button type="button" disabled={busy} onClick={() => void run(async () => {const preset = parsedPreset(); const status = await rpc('mfa.check', {preset, launcher}) as MfaStatus; if (alive.current) {setDraft(p => ({...p, phone_map: preset.phone_map, status})); setMessage('资源兼容性检查完成');}})}>检查兼容性</button><button type="button" className="primary" disabled={busy} onClick={() => void run(async () => {const value = await rpc('mfa.save', {preset: parsedPreset(), launcher, make_default: makeDefault}) as MfaData; if (alive.current) {publish(value); onClose();}})}>{busy ? '处理中…' : '保存配套'}</button></div>
  </dialog>;
}
