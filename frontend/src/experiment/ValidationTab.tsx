// 식 검증: 본 실험으로 얻은 관계식으로 새 조건의 충격을 예측하고, 그 조건에서 다시 측정해 맞는지 확인합니다.
import { useMemo, useState } from 'react';
import { CheckCircle2, CircleStop, Download, Play } from 'lucide-react';
import { fmt, modelPredict, modelSolveSpeed, outputForSpeed, outputLabel, type OutputMode } from '../services/experiment';
import { XYChart } from '../components/ExperimentCharts';
import type { ExperimentContext } from './context';
import NumInput from '../components/NumInput';
import { usePersistentState } from '../services/persist';

export default function ValidationTab({ ctx }: { ctx: ExperimentContext }) {
  const { data, settings, condition } = ctx;
  const bumpIds = useMemo(() => Object.keys(data?.models ?? {}).sort(), [data]);
  const [bump, setBump] = useState(condition.bump_id);
  const activeBump = bumpIds.includes(bump) ? bump : bumpIds[0] ?? condition.bump_id;
  const [added, setAdded] = usePersistentState('val-added', 75);
  const [target, setTarget] = usePersistentState<'vmax' | 'custom'>('val-target', 'vmax');
  const [customV, setCustomV] = usePersistentState('val-customV', 0.5);
  const [mode, setMode] = useState<OutputMode>(condition.output_mode);
  const [repeats, setRepeats] = usePersistentState('val-repeats', 3);
  const [manualOutput, setManualOutput] = useState<number | ''>('');

  const comparison = data?.models?.[activeBump];
  const model = comparison?.models.find(m => m.id === data?.chosen_models?.[activeBump]) ?? null;
  const criterion = data?.criteria?.[activeBump];
  const aLimit = criterion?.a_limit_g ?? null;
  const massG = condition.base_mass_g + added;
  const vmax = aLimit && model ? modelSolveSpeed(model, aLimit - 1, massG / 1000) : null;
  const vTarget = target === 'vmax' ? vmax : customV;
  const predicted = vTarget && model ? modelPredict(model, vTarget, massG / 1000) : null;
  const rec = vTarget ? outputForSpeed(data?.speed_maps, activeBump, massG, mode, vTarget) : null;
  const output = manualOutput === '' ? (rec ? Math.round(Math.max(1, Math.min(100, rec.output))) : null) : manualOutput;
  const testedMasses = Array.from(new Set((data?.groups ?? []).filter(g => g.bump_id === activeBump).map(g => g.total_mass_g)));
  const val = data?.validation;
  const rows = (val?.rows ?? []).filter(r => r.bump_id === activeBump);
  const errs = rows.filter(r => r.error_pct != null).map(r => Math.abs(r.error_pct as number));
  const mape = errs.length ? errs.reduce((a, b) => a + b, 0) / errs.length : null;
  const verdict = mape == null ? null : mape <= 10 ? { cls: 'good', text: `평균 오차 ${mape.toFixed(1)}%: 관계식이 실험하지 않은 조건도 잘 예측합니다(10% 이내).` }
    : mape <= 20 ? { cls: 'mid', text: `평균 오차 ${mape.toFixed(1)}%: 대체로 맞지만 오차가 큽니다. 반복 횟수를 늘리거나 다른 후보식과 비교해 보세요.` }
    : { cls: 'bad', text: `평균 오차 ${mape.toFixed(1)}%: 식을 보정해야 합니다. 후보식을 바꾸거나, 측정 범위를 넓히거나, 검증 시행을 본 실험으로 옮겨 식을 다시 맞추세요.` };

  async function moveToMain() {
    if (!rows.length || !window.confirm(`검증 시행 ${rows.length}개를 본 실험으로 옮겨 관계식을 다시 맞출까요? (검증용 데이터가 없어지므로, 보정 뒤에는 새 조건으로 다시 검증하세요)`)) return;
    try {
      for (const r of rows) await ctx.request(`/api/experiment/runs/${r.run_id}`, { method: 'PATCH', body: JSON.stringify({ purpose: 'main' }) });
      ctx.setMessage(`검증 시행 ${rows.length}개를 본 실험으로 옮겼습니다. 관계식이 다시 계산됩니다.`);
      await ctx.loadRuns();
    } catch (e) { ctx.fail(e); }
  }

  return <>
    <section className="card experiment-card">
      <div className="card-head"><div><h2>식 검증</h2><p>본 실험 데이터로 만든 관계식이 <b>실험하지 않은 조건</b>(예: 새로운 질량 +75 g, +225 g)에서도 맞는지 확인합니다. 아래에서 조건을 고르면 식이 예측한 충격과 필요한 출력을 보여 주고, '검증 측정'은 그 조건으로 자동 측정해 <b>식 검증</b> 시행으로 저장합니다. 검증 시행은 관계식 계산에 쓰지 않습니다.</p></div></div>
      {!model ? <div className="empty-chart">아직 관계식이 없습니다. 측정·분석 탭에서 본 실험(속도 2단계 이상)을 먼저 하세요.</div> : <>
        <div className="experiment-fields">
          <label>방지턱<select value={activeBump} onChange={e => setBump(e.target.value)}>{bumpIds.map(id => <option key={id} value={id}>방지턱 {id}</option>)}</select></label>
          <label>추가 질량 (g){testedMasses.length ? ` · 실험한 총질량: ${testedMasses.join(', ')} g` : ''}<NumInput min={0} value={added} onChange={e => setAdded(Math.max(0, Number(e.target.value) || 0))} /></label>
          <label>목표 속도<select value={target} onChange={e => setTarget(e.target.value as 'vmax' | 'custom')}><option value="vmax">허용 기준의 최대 통과 속도 v_max</option><option value="custom">직접 입력</option></select></label>
          {target === 'custom' && <label>목표 속도 (m/s)<NumInput min={0.05} step={0.01} value={customV} onChange={e => setCustomV(Math.max(0.01, Number(e.target.value) || 0.5))} /></label>}
          <label>출력 방식<select value={mode} onChange={e => setMode(e.target.value as OutputMode)}><option value="pwm">라이브러리 PWM</option><option value="duty">직접 듀티비</option></select></label>
          <label>출력값 (빈칸 = 추천값){rec ? ` · 추천 ${rec.output.toFixed(0)}` : ''}<NumInput min={1} max={100} value={manualOutput} placeholder={output ? String(output) : '출력→속도 자료 부족'} onChange={e => setManualOutput(e.target.value === '' ? '' : Math.max(1, Math.min(100, Number(e.target.value))))} /></label>
          <label>반복 횟수<NumInput min={1} max={10} value={repeats} onChange={e => setRepeats(Math.max(1, Math.min(10, Math.round(Number(e.target.value) || 3))))} /></label>
        </div>
        <div className="exp-fit">
          <div><small>사용하는 관계식</small><b>{model.name}: {model.formula}</b><span>측정·분석 탭에서 바꿀 수 있습니다.</span></div>
          <div><small>예측</small><b>총질량 {massG.toLocaleString()} g · v = {fmt(vTarget, 3, 'm/s')}</b><span>예측 Δa = {fmt(predicted, 3, 'g')} → 최대 수직 가속도 약 {predicted == null ? '—' : (predicted + 1).toFixed(2)} g{target === 'vmax' && aLimit ? ` (허용 기준 ${aLimit.toFixed(2)} g)` : ''}</span></div>
          <div><small>필요한 출력</small><b>{output == null ? '—' : outputLabel(mode, output)}</b><span>{rec ? `총질량 ${rec.basedOnMass} g의 '출력→속도' 자료로 추정${rec.extrapolated ? ' · 측정 범위 밖(외삽)이라 실제 속도가 다를 수 있음' : ''}` : '같은 방지턱·출력 방식에서 출력 2단계 이상의 본 실험이 있어야 추천할 수 있습니다.'} 실제 속도는 측정 후 앞·뒤 바퀴 피크로 다시 계산되므로, 목표와 조금 달라도 검증에는 문제없습니다.</span></div>
        </div>
        <div className="button-row">
          <button className="primary" disabled={!ctx.canAutoRun || !output} onClick={() => void ctx.startAutoRun({ purpose: 'validation', bump_id: activeBump, added_mass_g: added, output_mode: mode, output_value: output ?? 1, note: `검증: 목표 ${vTarget?.toFixed(3) ?? '-'} m/s` }, { repeats })}><Play size={15} /> 검증 측정 ({repeats}회)</button>
          <button className="emergency exp-stop" disabled={!ctx.serverAvailable} onClick={() => void ctx.stopNow()}><CircleStop size={15} /> 정지</button>
          {ctx.auto && ctx.busy && <span className="exp-hint">{ctx.auto.message}</span>}
        </div>
      </>}
    </section>

    <section className="card experiment-card">
      <div className="card-head"><div><h2>검증 결과 (방지턱 {activeBump}) · {rows.length}회</h2><p>실측 속도를 관계식에 넣은 예측 Δa와 실제 Δa를 비교합니다. 오차율 = (실측 − 예측) / 예측.</p></div>
        <div className="button-row"><button className="secondary" disabled={!val?.rows.length} onClick={() => ctx.download('/api/experiment/export/validation.csv', 'picarx_validation.csv')}><Download size={14} /> 검증 CSV</button><button className="secondary" disabled={!rows.length} onClick={() => void moveToMain()}>본 실험으로 옮겨 식 다시 맞추기</button></div></div>
      {verdict && <div className={'exp-verdict ' + verdict.cls}><CheckCircle2 size={16} />{verdict.text}</div>}
      {rows.length === 0 ? <div className="empty-chart">아직 검증 시행이 없습니다.</div> : <>
        <XYChart name={`validation_${activeBump}`} title={`그래프 4. 관계식 검증 – 예측과 실측 (방지턱 ${activeBump})`} xLabel="관계식 예측 Δa (g)" yLabel="실측 Δa (g)" identity band={0.1} xZero yZero
          series={[{ name: '검증 시행', color: '#a04fa0', points: rows.filter(r => r.predicted_delta_g != null && r.measured_delta_g != null).map(r => ({ x: r.predicted_delta_g as number, y: r.measured_delta_g as number, label: `${r.total_mass_g}g` })) }]}
          caption="점이 초록 띠(±10%) 안에 있으면 식이 그 조건을 잘 예측한 것입니다. 띠 위쪽이면 식이 충격을 작게, 아래쪽이면 크게 예측한 것입니다." />
        <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>시각</th><th>총질량</th><th>출력</th><th>실측 속도</th><th>예측 Δa</th><th>실측 Δa</th><th>오차</th><th>오차율</th></tr></thead>
          <tbody>{rows.map(r => <tr key={r.run_id}><td>{new Date(r.created_at).toLocaleTimeString()}</td><td>{r.total_mass_g} g</td><td>{outputLabel(r.output_mode, r.output_value)}</td><td>{fmt(r.speed_mps, 3)}</td><td>{fmt(r.predicted_delta_g, 3)}</td><td>{fmt(r.measured_delta_g, 3)}</td><td>{fmt(r.abs_error_g, 3)}</td><td className={r.error_pct != null && Math.abs(r.error_pct) > 10 ? 'diagnostic-error' : ''}>{r.error_pct == null ? '—' : `${r.error_pct > 0 ? '+' : ''}${r.error_pct.toFixed(1)}%`}</td></tr>)}</tbody></table></div>
      </>}
    </section>
  </>;
}
