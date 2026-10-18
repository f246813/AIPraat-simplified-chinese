import {useEffect, useRef, useState} from 'react';
import type {TutorRpc} from './PronunciationForm';

/** 与后端 `praat_ai/environment.py` 的 report() 一一对应（同一份判据，不在前端重复猜路径）。 */
export type EnvironmentItem = {
  id: string; label: string; required: boolean; ready: boolean; detail: string; hint: string;
};
export type EnvironmentReport = {
  ready: boolean; degraded: boolean; items: EnvironmentItem[]; missing: string[]; message: string;
};

/**
 * 运行环境三态：
 * - 必需项缺失（红）→ 提交被禁用，给出「去哪儿给」；
 * - 可选项缺失（黄）→ 允许提交，但说清会降级什么；
 * - 都在（绿）→ 只显示路径，不占版面。
 *
 * 「重新检测」走后端 `tutor.refresh_environment`（先清 Praat 探测缓存再体检），
 * 避免用户改完路径还得重启窗口。
 */
export function EnvironmentStatus({rpc, report, onChange}: {
  rpc: TutorRpc; report?: EnvironmentReport; onChange: (value: EnvironmentReport) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);

  const refresh = async () => {
    setBusy(true); setError('');
    try {
      const value = await rpc('tutor.refresh_environment', {}) as EnvironmentReport;
      if (alive.current && value) onChange(value);
    } catch (e) {
      if (alive.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (alive.current) setBusy(false);
    }
  };

  if (!report || !Array.isArray(report.items)) return null;
  const state = !report.ready ? 'blocked' : report.degraded ? 'degraded' : 'ready';
  return (
    <div className={`pron-env pron-env-${state}`} role="status" aria-live="polite">
      <div className="pron-env-head">
        <strong>运行环境</strong>
        <button type="button" onClick={() => void refresh()} disabled={busy}>{busy ? '检测中…' : '重新检测'}</button>
      </div>
      <ul>
        {report.items.map(item => (
          <li key={item.id} className={item.ready ? 'ok' : item.required ? 'bad' : 'warn'}>
            <span className="pron-env-dot" aria-hidden="true"/>
            <span className="pron-env-label">{item.label}</span>
            <span className="pron-env-detail">{item.detail}</span>
            {!item.ready && <span className="pron-env-hint">{item.hint}</span>}
          </li>
        ))}
      </ul>
      {report.message && <p className="pron-env-message">{report.message}</p>}
      {error && <p className="pron-env-message" role="alert">{error}</p>}
    </div>
  );
}
