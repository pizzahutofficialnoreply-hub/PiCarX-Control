// 직접 만드는 관계식: 정해진 틀 없이 식을 글자로 쓰고, 데이터에 맞춰 매개변수를 구하고, 다른 식과 비교합니다.
import { useState } from 'react';
import { FlaskConical, Save, Trash2 } from 'lucide-react';
import { headingOut, outputFromSpeed, type ModelResult } from '../services/experiment';
import { MASS_COLORS, XYChart } from '../components/ExperimentCharts';
import { usePersistentState } from '../services/persist';
import type { ExperimentContext } from './context';
import { fitFormula, KNOWN_VARS, type Env, type FitResult, type Point } from './formula';
import { storeFormulas, t95, targetOfGroup, targetOfRun, TARGETS, type SavedFormula, type Target } from './theory';

const PRESETS: { expr: string; target: Target; name: string; why: string }[] = [
  { expr: 'k*v^n', target: 'delta', name: '보고서 식 (속도만)', why: 'Δa = k₀·vⁿ. 질량이 한 단계일 때 보고서에 쓰는 식' },
  { expr: 'k*v^n*m^p', target: 'delta', name: '보고서 식 (속도·질량)', why: 'Δa = k₀·vⁿ·mᵖ. 질량을 여러 단계로 측정한 뒤 쓰는 식(n: 속도 영향, p: 질량 영향)' },
];

const TIPS: [string, string][] = [
  ['먼저 그림을 본다', '측정·분석 탭의 그래프 1(속도–Δa)에서 점들이 곧은 선인지, 위로 휘는지(가속), 아래로 휘는지(포화) 봅니다. 위로 휘면 v², vⁿ(n>1) 꼴, 거의 직선이면 v 꼴이 어울립니다.'],
  ['로그를 씌우면 지수가 보인다', '엑셀에서 =LN(v), =LN(Δa)를 만들고 둘의 산점도에 추세선을 그리면, 기울기가 바로 n입니다(Δa = k·vⁿ ⇒ ln Δa = ln k + n·ln v). 질량까지 넣으려면 =LINEST(LN(Δa), LN(v):LN(m))를 쓰면 n과 p가 한꺼번에 나옵니다.'],
  ['물리에서 출발한다', '이론 ②(충격량)는 Δa ∝ v, 이론 ③(원운동)은 Δa ∝ v²/R 을 줍니다. 실제는 둘이 섞이므로 a·v + b·v² 처럼 더해 보거나, 지수 n이 1과 2 사이 어디인지로 어느 효과가 큰지 해석할 수 있습니다.'],
  ['원점 조건을 지킨다', '속도가 0이면 충격도 0이어야 합니다. a + b·v 처럼 상수항이 있으면 느린 속도에서 엉뚱한 값이 나옵니다. 상수항을 넣었다면 그 값이 0에 가까운지 확인하세요.'],
  ['단위를 맞춘다', 'v²/(g·R)는 단위가 없는 수(Π)입니다. 이런 꼴로 쓰면 계수 c가 "차체에 전달되는 비율"처럼 뜻이 생기고, 다른 방지턱·다른 차와 비교할 수 있습니다.'],
  ['매개변수는 적게', '조건 평균 점 수가 매개변수 수의 3배 이상이 되게 하세요. 매개변수가 많으면 R²는 무조건 커지지만 새 조건 예측은 나빠집니다. 그래서 식끼리는 R²가 아니라 교차검증 오차(한 점씩 빼고 맞춘 식이 그 점을 얼마나 틀리는가)로 비교합니다.'],
  ['남은 오차의 모양을 본다', '예측–실측 그래프에서 점들이 대각선 주변에 고르게 흩어져야 합니다. 빠른 조건만 위로 올라가는 식의 패턴이 있으면 식 모양(지수 등)이 데이터와 다른 것입니다.'],
  ['범위 밖은 조심', '식은 측정한 속도·질량 범위 안에서만 믿을 수 있습니다. 범위 밖(외삽)은 검증 실험으로 확인한 뒤에 쓰세요(식 검증 탭).'],
];

export default function FormulaLab({ ctx, bump, env, formulas, setFormulas, onUse, chosen }: {
  ctx: ExperimentContext; bump: string; env: Env; formulas: SavedFormula[]; setFormulas: (l: SavedFormula[]) => void; onUse: (id: string) => void; chosen: ModelResult | null;
}) {
  const { data, settings } = ctx;
  const [expr, setExpr] = usePersistentState('lab-expr', 'k*v^n*m^p');
  const [target, setTarget] = usePersistentState<Target>('lab-target', 'delta');
  const [level, setLevel] = usePersistentState<'groups' | 'runs'>('lab-level', 'groups');
  const [name, setName] = usePersistentState('lab-name', '');
  const [res, setRes] = useState<{ fit: FitResult; pts: Point[]; expr: string; target: Target; level: string } | null>(null);
  const known = Object.keys(KNOWN_VARS);

  const groups = (data?.groups ?? []).filter(g => g.bump_id === bump);
  const metric = settings.impact_metric ?? 'up';
  const runs = (data?.runs ?? []).filter(r => r.condition.bump_id === bump && !r.excluded && (r.condition.purpose ?? 'main') === 'main' && r.analysis.bump_detected !== false
    && (r.condition.direction ?? 'forward') === (settings.analysis_direction ?? 'forward') && !headingOut(r, settings.heading_limit_deg ?? 10));
  const points = (): Point[] => level === 'groups'
    ? groups.filter(g => g.mean_speed_mps && targetOfGroup(g, target) != null).map(g => ({ env: { ...env, v: g.mean_speed_mps as number, m: g.total_mass_g / 1000, P: g.output_value }, y: targetOfGroup(g, target) as number, label: `${g.total_mass_g}g·${g.output_value}` }))
    : runs.filter(r => r.analysis.speed_mps && targetOfRun(r, target, metric) != null).map(r => ({ env: { ...env, v: r.analysis.speed_mps as number, m: r.condition.total_mass_g / 1000, P: r.condition.output_value }, y: targetOfRun(r, target, metric) as number, label: `${r.condition.total_mass_g}g·${r.condition.output_value}·${r.condition.trial}` }));
  const run = (e = expr, t = target) => { const pts = points(); setRes({ fit: fitFormula(e, pts, known), pts, expr: e, target: t, level }); };

  const fit = res?.fit;
  const unit = TARGETS[res?.target ?? target].unit;
  const df = fit ? fit.n - fit.params.length : 0;
  const massesG = Array.from(new Set((res?.pts ?? []).map(p => Math.round((p.env.m as number) * 1000)))).sort((a, b) => a - b);
  const vs = (res?.pts ?? []).map(p => p.env.v as number);
  const vLo = vs.length ? Math.min(...vs) * 0.8 : 0, vHi = vs.length ? Math.max(...vs) * 1.15 : 1;
  const usable = res && fit?.ok && ['delta', 'peak', 'force', 'dynforce'].includes(res.target);

  function save() {
    if (!res || !fit?.ok) return;
    const nm = (name || `${TARGETS[res.target].short} = ${res.expr}`).slice(0, 40);
    const item: SavedFormula = { name: nm, expr: res.expr, target: res.target, values: fit.values, bump, r2: fit.r2, loocv: fit.loocv };
    const next = [item, ...formulas.filter(f => !(f.name === nm && f.bump === bump))];
    setFormulas(next); storeFormulas(next);
    ctx.setMessage(`'${nm}' 식을 저장했습니다. 위 계산기의 '사용할 식'에서 고를 수 있습니다.`);
  }

  return <section className="card experiment-card">
    <div className="card-head"><div><h2><FlaskConical size={17} /> 직접 만드는 관계식</h2>
      <p>정해진 틀 없이 식을 글자로 쓰면, 데이터에 가장 잘 맞는 매개변수를 찾아 줍니다(비선형 최소제곱). 내장 후보식과 같은 기준(교차검증 오차)으로 비교할 수 있고, 저장하면 위 계산기에서 바로 씁니다.</p></div></div>
    <div className="exp-lab">
      <div className="experiment-fields">
        <label className="exp-wide">식 (예: k*v^n*m^p, c*v^2/(g*R), a*v + b*v^2)<input value={expr} spellCheck={false} onChange={e => setExpr(e.target.value)} onKeyDown={e => { if (e.key === 'Enter') run(); }} /></label>
        <label>맞출 값<select value={target} onChange={e => setTarget(e.target.value as Target)}>{(Object.keys(TARGETS) as Target[]).map(t => <option key={t} value={t}>{TARGETS[t].label} ({TARGETS[t].unit})</option>)}</select></label>
        <label>데이터<select value={level} onChange={e => setLevel(e.target.value as 'groups' | 'runs')}><option value="groups">조건 평균 ({groups.length}점, 이상치 처리 반영)</option><option value="runs">모든 회차 ({runs.length}점)</option></select></label>
      </div>
      <p className="exp-hint">쓸 수 있는 변수: {Object.entries(KNOWN_VARS).map(([k, v]) => <code key={k} title={v}>{k}</code>)} (마우스를 올리면 뜻) · 함수: <code>exp ln sqrt abs pow min max</code> · 그 밖의 이름(k, n, p, a, b, c…)은 모두 데이터로 정할 <b>매개변수</b>입니다. 곱하기는 <code>*</code>, 거듭제곱은 <code>^</code>. 지금 방지턱 값: R = {env.R != null ? (env.R * 1000).toFixed(1) + ' mm' : '—'}, h = {env.h != null ? (env.h * 1000).toFixed(1) + ' mm' : '—'}, sin α = {env.s != null ? env.s.toFixed(3) : '—'}.</p>
      <div className="exp-presets">{PRESETS.map(p => <button key={p.expr + p.target} type="button" className="secondary" title={p.why} onClick={() => { setExpr(p.expr); setTarget(p.target); run(p.expr, p.target); }}>{p.name}</button>)}</div>
      <div className="button-row"><button className="primary" onClick={() => run()}>맞추기</button></div>
      {fit && !fit.ok && <p className="diagnostic-error">{fit.error}</p>}
      {fit?.ok && res && <>
        <div className="exp-formula-box">
          <div><small>맞춘 식 ({res.level === 'groups' ? '조건 평균' : '모든 회차'} {fit.n}점)</small><b>{TARGETS[res.target].short} = {res.expr}</b>
            <span>{fit.params.length === 0 ? '매개변수 없는 이론식: 그대로 계산해 비교했습니다.' : fit.params.map(p => { const se = fit.se[p]; const t = t95(df); return `${p} = ${fit.values[p].toPrecision(4)}${se != null && Number.isFinite(t) ? ` (95% 범위 ${(fit.values[p] - t * se).toPrecision(3)} ~ ${(fit.values[p] + t * se).toPrecision(3)})` : ''}`; }).join(' · ')}</span></div>
          <div><small>얼마나 맞나</small><b>R² {fit.r2.toFixed(3)} · RMSE {fit.rmse.toPrecision(3)} {unit}</b><span>교차검증 RMSE {fit.loocv == null ? '— (점이 더 필요)' : `${fit.loocv.toPrecision(3)} ${unit}`}{fit.aic != null ? ` · AIC ${fit.aic.toFixed(1)}` : ''}</span></div>
          {chosen && res.target === 'delta' && res.level === 'groups' && <div><small>내장 식과 비교 ({chosen.name})</small><b>교차검증 {chosen.loocv_rmse == null ? '—' : chosen.loocv_rmse.toPrecision(3)} g vs 이 식 {fit.loocv == null ? '—' : fit.loocv.toPrecision(3)} g</b>
            <span>{fit.loocv != null && chosen.loocv_rmse != null ? (fit.loocv < chosen.loocv_rmse * 0.95 ? '이 식이 새 조건을 더 잘 맞힙니다.' : fit.loocv > chosen.loocv_rmse * 1.05 ? '내장 식이 더 잘 맞힙니다.' : '비슷합니다. 더 단순하거나 물리적으로 설명되는 식을 고르세요.') : ''}</span></div>}
          {fit.params.length > 0 && df < 2 * fit.params.length && <div className="warn"><small>주의</small><b>점 {fit.n}개에 매개변수 {fit.params.length}개</b><span>점이 적어 우연히 잘 맞을 수 있습니다. 조건을 늘리거나 매개변수를 줄이세요.</span></div>}
        </div>
        <div className="exp-theory-grid">
          <XYChart name={`lab_pred_${bump}`} title="이 식의 예측과 실측" xLabel={`예측 ${TARGETS[res.target].short} (${unit})`} yLabel={`실측 (${unit})`} identity band={0.1} xZero yZero
            series={massesG.map((mg, i) => ({ name: `${mg} g`, color: MASS_COLORS[i % MASS_COLORS.length], points: res.pts.filter(p => Math.round((p.env.m as number) * 1000) === mg).map(p => ({ x: fit.predict(p.env), y: p.y, label: res.level === 'groups' ? `출력 ${p.env.P}` : undefined })) }))}
            caption="대각선에 가까울수록 잘 맞습니다(초록 띠 ±10%)." />
          <XYChart name={`lab_curve_${bump}`} title="속도에 따른 예측 곡선" xLabel="통과 속도 v (m/s)" yLabel={`${TARGETS[res.target].short} (${unit})`} yZero
            series={massesG.flatMap((mg, i) => {
              const color = MASS_COLORS[i % MASS_COLORS.length];
              const curve = Array.from({ length: 40 }, (_, k) => vLo + (vHi - vLo) * k / 39).map(v => {
                const P = outputFromSpeed(data?.speed_models, bump, (data?.groups.find(g => g.bump_id === bump)?.output_mode) ?? 'pwm', v, mg);
                try { return { x: v, y: fit.predict({ ...env, v, m: mg / 1000, P: P ?? NaN }) }; } catch { return { x: v, y: NaN }; }
              }).filter(p => Number.isFinite(p.y));
              return [{ name: `${mg} g 예측`, color, line: true, dots: false, points: curve }, { name: `${mg} g 실측`, color, points: res.pts.filter(p => Math.round((p.env.m as number) * 1000) === mg).map(p => ({ x: p.env.v as number, y: p.y })) }];
            })} legendMax={12} />
        </div>
        <div className="button-row"><label className="exp-inline">이름<input value={name} placeholder={`${TARGETS[res.target].short} = ${res.expr}`} onChange={e => setName(e.target.value)} /></label>
          <button className="secondary" onClick={save}><Save size={14} /> 저장</button>
          {usable && <button className="secondary" onClick={() => { save(); onUse('custom:' + ((name || `${TARGETS[res.target].short} = ${res.expr}`).slice(0, 40))); }}>저장하고 계산기에 사용</button>}
          {!usable && fit.ok && <span className="exp-hint">충격량·Δt 식은 계산기(가속도·힘 예측)에는 쓰지 않고 분석용으로만 저장합니다.</span>}</div>
      </>}
      {formulas.filter(f => f.bump === bump).length > 0 && <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>저장한 식</th><th>값</th><th>R²</th><th>교차검증</th><th></th></tr></thead>
        <tbody>{formulas.filter(f => f.bump === bump).map(f => <tr key={f.name}><td><b>{f.name}</b><br /><small>{TARGETS[f.target].short} = {f.expr}</small></td><td><small>{Object.entries(f.values).map(([k, v]) => `${k}=${v.toPrecision(3)}`).join(', ')}</small></td><td>{f.r2?.toFixed(3) ?? '—'}</td><td>{f.loocv == null ? '—' : f.loocv.toPrecision(3)}</td>
          <td><button className="text-button" onClick={() => { setExpr(f.expr); setTarget(f.target); run(f.expr, f.target); }}>다시 맞추기</button> <button className="text-button" onClick={() => { const next = formulas.filter(x => x !== f); setFormulas(next); storeFormulas(next); }}><Trash2 size={13} /></button></td></tr>)}</tbody></table></div>}
      <details className="exp-tips"><summary>내 관계식을 만드는 요령 (팁)</summary>
        <ol>{TIPS.map(([h, t]) => <li key={h}><b>{h}</b> {t}</li>)}</ol>
        <p className="exp-hint">보고서에는 "후보식 여러 개를 같은 데이터에 맞춰 교차검증 오차로 비교했고, 그중 물리적으로 해석되는(이론 ①②③과 연결되는) 식을 골랐다"고 쓰면 됩니다. 고른 식의 매개변수(예: n)를 이론값(1 또는 2)과 비교해 무엇이 충격을 키우는지 토의하세요.</p>
      </details>
    </div>
  </section>;
}
