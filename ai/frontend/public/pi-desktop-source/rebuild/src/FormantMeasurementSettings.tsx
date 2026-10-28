import type {FormantSettings, NormalizationSelection} from './NormalizationSettings';

const defaults: Required<FormantSettings> = {maximum_formant_hz: 5500, max_number_of_formants: 5, time_step_sec: .01, window_length_sec: .025, pre_emphasis_from_hz: 50};
const fields = [
  ['maximum_formant_hz', '共振峰上限（Hz）', 1000, 20000, 50],
  ['max_number_of_formants', '共振峰数', 3, 10, .5],
  ['time_step_sec', '步长（秒）', .001, .1, .001],
  ['window_length_sec', '有效窗长（秒）', .005, .1, .005],
  ['pre_emphasis_from_hz', '预加重起点（Hz）', 0, 2000, 10],
] as const;

export function FormantMeasurementSettings({selection, onChange, disabled}: {
  selection: NormalizationSelection; onChange: (value: NormalizationSelection) => void; disabled: boolean;
}) {
  const update = (role: 'reference' | 'learner', key: keyof FormantSettings, value: string) => {
    onChange({...selection, confirmed: false, [role + '_calibration_id']: undefined,
      measurement_settings: {...selection.measurement_settings,
        [role]: {...defaults, ...selection.measurement_settings?.[role], [key]: value}}});
  };
  return <section aria-label="共振峰分析参数">
    <h3>共振峰分析参数 · Burg</h3>
    <p>标准音与学习者分别使用自己的参数。5500 Hz 是分析预设，不按性别或基频自动推断；请用独立、可靠标注的稳定元音选择参数。</p>
    <div className="pron-normalization-slots">{(['reference', 'learner'] as const).map(role => <div key={role}>
      <strong>{role === 'reference' ? '标准音' : '学习者'}分析设置</strong>
      {fields.map(([key, label, min, max, step]) => <label className="pron-field" key={key}>
        <span>{label}</span><input aria-label={`${role === 'reference' ? '标准音' : '学习者'}${label}`} type="number" min={min} max={max} step={step}
          disabled={disabled} value={selection.measurement_settings?.[role]?.[key] ?? defaults[key]}
          onChange={event => update(role, key, event.target.value)}/>
      </label>)}
      <small>修改参数会撤销该侧校准。校准录音与待测录音须使用该侧同一套设置；上限不能超过源录音 Nyquist。</small>
    </div>)}</div>
    <small>Praat Burg 的实际高斯窗覆盖约为有效窗长的两倍。支持的读音可在下方以独立元音自动分析上限候选；结果不确定时保留当前参数并供人工覆盖。</small>
  </section>;
}
