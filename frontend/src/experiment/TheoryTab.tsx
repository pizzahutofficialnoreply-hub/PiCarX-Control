// 이론·예측 탭: 목표 1(질량·속도 → 충격 예측식과 역계산), 이론식 ①②③으로 데이터 분석,
// 직접 만드는 관계식, 목표 2(방지턱 앞 감속 제어), 초음파 거리·속력 분석.
import { useMemo, useState } from 'react';
import { Calculator, Footprints, FlaskConical, PencilRuler, Gauge, Radar } from 'lucide-react';
import { fmt, G, outputFromSpeed, outputLabel, paramText, speedFromOutput, type ModelResult, type OutputMode } from '../services/experiment';
import { XYChart, MASS_COLORS } from '../components/ExperimentCharts';
import NumInput from '../components/NumInput';
import { usePersistentState } from '../services/persist';
import type { ExperimentContext } from './context';
import { baseEnv, builtinPredictor, bumpConst, customPredictor, linfit, loadFormulas, slopeThroughOrigin, type Predictor, type SavedFormula } from './theory';
import { solveFor } from './formula';
import FormulaLab from './FormulaLab';
import ControlLab from './ControlLab';
import EventsLab from './EventsLab';
import HelpBox from '../components/HelpBox';

type Section = 'predict' | 'theory' | 'events' | 'formula' | 'control' | 'ultra';
const SECTIONS: { id: Section; label: string; icon: typeof Calculator }[] = [
  { id: 'predict', label: '① 예측식·계산기', icon: Calculator },
  { id: 'theory', label: '② 이론 분석', icon: FlaskConical },
  { id: 'events', label: '③ 단계별 충격', icon: Footprints },
  { id: 'formula', label: '④ 내 관계식', icon: PencilRuler },
  { id: 'control', label: '⑤ 감속 제어', icon: Gauge },
  { id: 'ultra', label: '⑥ 초음파·카메라 거리', icon: Radar },
];

const num = (x: number, d = 3) => (Math.abs(x) >= 100 ? x.toFixed(1) : Math.abs(x) >= 1 ? x.toFixed(d) : x.toPrecision(d));
export function builtinText(m: ModelResult): string {
  const c = m.coef;
  if (m.kind === 'power') return `${num(c.k0)}·v^${num(c.n)}${c.p ? `·m^${num(c.p)}` : ''}`;
  if (m.kind === 'linear') return `${num(c.a)} + ${num(c.b)}·v${c.c ? ` + ${num(c.c)}·m` : ''}`;
  return `${num(c.a)} + ${num(c.b)}·v²${c.c ? ` + ${num(c.c)}·m` : ''}`;
}
export function customText(f: SavedFormula): string {
  let s = f.expr;
  Object.entries(f.values).forEach(([k, v]) => { s = s.replace(new RegExp(`\\b${k}\\b`, 'g'), `(${num(v)})`); });
  return s;
}

export default function TheoryTab({ ctx }: { ctx: ExperimentContext }) {
  const { data, settings, condition } = ctx;
  const bumpIds = useMemo(() => Array.from(new Set((data?.groups ?? []).map(g => g.bump_id))).sort(), [data]);
  const [bumpPref, setBump] = usePersistentState('theory-bump', condition.bump_id);
  const bump = bumpIds.includes(bumpPref) ? bumpPref : bumpIds[0] ?? condition.bump_id;
  const [formulas, setFormulas] = useState<SavedFormula[]>(loadFormulas);
  const [predId, setPredId] = usePersistentState('theory-pred', 'builtin');
  const c = bumpConst(settings, bump);
  const env = baseEnv(c);
  const groups = (data?.groups ?? []).filter(g => g.bump_id === bump);
  const runs = (data?.runs ?? []).filter(r => r.condition.bump_id === bump && !r.excluded && (r.condition.purpose ?? 'main') === 'main' && r.analysis.bump_detected !== false
    && (r.condition.direction ?? 'forward') === (settings.analysis_direction ?? 'forward'));
  const cmp = data?.models?.[bump];
  const chosen = cmp?.models.find(m => m.id === data?.chosen_models?.[bump]) ?? null;
  const builtin = builtinPredictor(chosen);
  const customs = formulas.filter(f => f.bump === bump).map(f => ({ f, p: customPredictor(f, env) })).filter((x): x is { f: SavedFormula; p: Predictor } => !!x.p);
  const pred: Predictor | null = predId === 'builtin' ? builtin : customs.find(x => x.p.id === predId)?.p ?? builtin;
  const predF = pred?.source === 'custom' ? customs.find(x => x.p.id === pred.id)?.f ?? null : null;
  const aLimit = data?.criteria?.[bump]?.a_limit_g ?? null;
  const [secPref, setSec] = usePersistentState<Section>('theory-section', 'predict');
  const sec: Section = SECTIONS.some(x => x.id === secPref) ? secPref : 'predict';

  // ---------------- 계산기
  const [massG, setMassG] = usePersistentState('calc-massG', condition.base_mass_g + condition.added_mass_g || 600);
  const [inMode, setInMode] = usePersistentState<'pwm' | 'mps'>('calc-inmode', 'pwm');
  const [inVal, setInVal] = usePersistentState('calc-inval', 70);
  const [outMode, setOutMode] = usePersistentState<OutputMode>('calc-outmode', condition.output_mode);
  const [invType, setInvType] = usePersistentState<'peak' | 'force' | 'delta'>('calc-invtype', 'peak');
  const [invVal, setInvVal] = usePersistentState('calc-invval', aLimit ?? 1.7);
  const models = data?.speed_models;
  const sm = models?.find(x => x.bump_id === bump && x.output_mode === outMode && x.b);
  const mKg = massG / 1000;
  const v = inMode === 'mps' ? inVal : speedFromOutput(models, bump, outMode, inVal, massG);
  const P = inMode === 'pwm' ? inVal : outputFromSpeed(models, bump, outMode, inVal, massG);
  const dA = pred && v != null && v > 0 ? pred.delta(v, mKg, P) : null;
  const pi = v != null && c.R ? v * v / (G * c.R) : null;
  const impPts = runs.filter(r => r.analysis.impulse_ns && r.analysis.momentum_nsx).map(r => ({ x: r.analysis.momentum_nsx as number, y: r.analysis.impulse_ns as number }));
  const impFit = slopeThroughOrigin(impPts);
  const speeds = groups.map(g => g.mean_speed_mps).filter((x): x is number => x != null);
  const masses = Array.from(new Set(groups.map(g => g.total_mass_g)));
  const warn: string[] = [];
  if (v != null && speeds.length && (v < Math.min(...speeds) * 0.9 || v > Math.max(...speeds) * 1.1)) warn.push(`속도 ${v.toFixed(3)} m/s는 측정한 범위(${Math.min(...speeds).toFixed(2)}~${Math.max(...speeds).toFixed(2)} m/s) 밖이라 식을 늘려 쓴 값(외삽)입니다.`);
  if (masses.length && (massG < Math.min(...masses) * 0.9 || massG > Math.max(...masses) * 1.1)) warn.push(`질량 ${massG} g은 측정한 범위(${Math.min(...masses)}~${Math.max(...masses)} g) 밖입니다.`);
  if (inMode === 'pwm' && sm && (inVal < sm.output_range[0] || inVal > sm.output_range[1])) warn.push(`출력 ${inVal}은 측정한 출력 범위(${sm.output_range[0]}~${sm.output_range[1]}) 밖이라 속도 환산이 부정확할 수 있습니다.`);
  // 역계산: 목표 충격 → 속도 → 출력
  const dTarget = invType === 'peak' ? invVal - 1 : invType === 'force' ? invVal / (mKg * G) - 1 : invVal;
  const vStar = pred && dTarget > 0 ? solveFor(x => pred.delta(x, mKg, outputFromSpeed(models, bump, outMode, x, massG)) ?? NaN, dTarget) : null;
  const pStar = vStar != null ? outputFromSpeed(models, bump, outMode, vStar, massG) : null;

  const formulaLine = pred ? (pred.source === 'builtin' && chosen ? builtinText(chosen) : predF ? customText(predF) : pred.formula) : null;
  const speedLine = sm && sm.a != null && sm.b != null ? `v = ${num(sm.a)} + ${num(sm.b)}·P${sm.uses_mass ? ` ${sm.c >= 0 ? '+' : '−'} ${num(Math.abs(sm.c))}·m` : ''}` : null;

  return <div className="exp-theory">
    <div className="exp-theory-top">
      <HelpBox id="theory-intro" title="이 탭은 이런 순서로 보세요">
        <ul>
          <li><b>① 예측식·계산기</b>: 측정·분석 탭에서 고른 관계식으로 '질량·속도(PWM) → 충격(g, N)'과 거꾸로 '허용 충격 → 최대 속도·PWM'을 계산합니다.</li>
          <li><b>② 이론 분석</b>: 계획서 이론 ①F=ma ②충격량 ③원운동을 데이터에 대 보고, 맞는 점과 벗어나는 이유를 봅니다(보고서 토의용).</li>
          <li><b>③ 단계별 충격</b>: 앞바퀴 오름·내림, 뒷바퀴 오름·내림 네 순간을 따로 모아 단계마다 식과 한계 속도를 구합니다.</li>
          <li><b>④ 내 관계식</b>: 직접 식을 만들어 맞추고, 예측식으로 쓸 수 있습니다.</li>
          <li><b>⑤ 감속 제어</b>: ①·③의 식으로 방지턱 앞에서 얼마나 줄일지 계산해 실제로 달려 확인합니다(시간·거리·초음파·카메라로 방지턱 감지).</li>
          <li><b>⑥ 초음파·카메라 거리</b>: 벽까지 거리로 속력을 재고, 방지턱 감지용 센서를 시험합니다.</li>
        </ul>
        <p>위의 방지턱 선택은 모든 칸에 함께 적용됩니다. 데이터가 바뀌면(측정·제외·재분석) 모든 칸이 자동으로 새로 계산됩니다.</p>
      </HelpBox>
      <div className="exp-theory-bar">
        {bumpIds.length > 1 && <div className="segmented">{bumpIds.map(id => <button key={id} type="button" className={id === bump ? 'selected' : ''} onClick={() => setBump(id)}>방지턱 {id}</button>)}</div>}
        {bumpIds.length <= 1 && <span className="exp-sub">방지턱 {bump}</span>}
      </div>
      <nav className="exp-subtabs" aria-label="이론·예측·제어 하위 메뉴">{SECTIONS.map(x => { const I = x.icon; return <button key={x.id} type="button" className={x.id === sec ? 'selected' : ''} onClick={() => setSec(x.id)}><I size={14} />{x.label}</button>; })}</nav>
    </div>
    {sec === 'predict' && <section className="card experiment-card">
      <div className="card-head"><div><h2><Calculator size={17} /> 목표 1 · 질량과 속도로 충격을 예측하는 식</h2>
        <p>계획서 이론 ① F = m·a 에서 a(위아래 가속도)를 실험으로 구한 식으로 바꿔 넣은 꼴입니다. 질량 m과 속도 v(또는 PWM)만 넣으면 최대 수직 가속도(g)와 충격력(N)이 나오고, 거꾸로 '이 충격을 넘지 않는 속도·PWM'도 구합니다.</p></div>
</div>
      {!pred ? <div className="exp-why"><b>아직 식이 없습니다.</b> 측정·분석 탭에서 출력(속도) 2단계 이상, 가능하면 질량 2단계 이상을 측정하면 자동으로 생깁니다.</div> : <>
        <label className="exp-inline exp-predsel">사용할 식<select value={pred.id === builtin?.id ? 'builtin' : pred.id} onChange={e => setPredId(e.target.value)}>
          {builtin && <option value="builtin">측정·분석 탭에서 고른 식: {builtin.name}</option>}
          {customs.map(x => <option key={x.p.id} value={x.p.id}>직접 만든 식: {x.f.name}</option>)}</select></label>
        <div className="exp-formula-box">
          <div><small>① 충격(가속도)</small><b>Δa = {formulaLine}</b><span>{pred.source === 'builtin' && chosen ? chosen.params.map(p => paramText(p)).join(' · ') : predF ? `직접 만든 식 · 목표값 ${predF.target}` : ''}</span></div>
          <div><small>② 최대 수직 가속도</small><b>a_peak = 1 + Δa  (g)</b><span>가만히 있을 때 1 g에 충격만큼 더해짐</span></div>
          <div><small>③ 충격력 (F = m·a)</small><b>F = m × {G.toFixed(2)} × (1 + {formulaLine})  (N)</b><span>m은 kg, v는 m/s</span></div>
          <div><small>④ PWM → 속도 ({outMode === 'duty' ? '듀티' : 'PWM'})</small><b>{speedLine ?? '— (출력 2단계 이상 측정 필요)'}</b><span>{sm ? `측정 ${sm.n}개 조건으로 맞춤 · R² ${sm.r2?.toFixed(3)}${sm.uses_mass ? ' · 무거우면 느려지는 정도까지 반영' : ''}` : ''}</span></div>
        </div>
        <div className="exp-calc">
          <div className="experiment-fields">
            <label>총질량 m (g)<NumInput min={1} value={massG} onChange={e => setMassG(Math.max(1, Number(e.target.value) || 0))} /></label>
            <label>입력<div className="exp-inline"><div className="segmented"><button type="button" className={inMode === 'pwm' ? 'selected' : ''} onClick={() => setInMode('pwm')}>PWM·듀티</button><button type="button" className={inMode === 'mps' ? 'selected' : ''} onClick={() => setInMode('mps')}>속도 m/s</button></div>
              <NumInput step={inMode === 'mps' ? 0.01 : 1} value={inVal} onChange={e => setInVal(Number(e.target.value) || 0)} /></div></label>
            <label>출력 방식<select value={outMode} onChange={e => setOutMode(e.target.value as OutputMode)}><option value="pwm">라이브러리 PWM</option><option value="duty">직접 듀티비</option></select></label>
          </div>
          <div className="exp-chain">
            <span>m = {mKg.toFixed(3)} kg</span><i>→</i>
            <span>{inMode === 'pwm' ? <>P = {inVal} → v = <b>{fmt(v, 3, 'm/s')}</b></> : <>v = <b>{inVal} m/s</b>{P != null ? ` (≈ ${outputLabel(outMode, Math.round(P))})` : ''}</>}</span><i>→</i>
            <span>Δa = <b>{fmt(dA, 3, 'g')}</b></span><i>→</i>
            <span>a_peak = <b>{dA == null ? '—' : (1 + dA).toFixed(3) + ' g'}</b></span><i>→</i>
            <span>F = m·g·a_peak = <b>{dA == null ? '—' : (mKg * G * (1 + dA)).toFixed(2) + ' N'}</b></span>
          </div>
          <div className="exp-stats">
            <div className={aLimit != null && dA != null && 1 + dA > aLimit ? 'over' : ''}><small>허용 기준과 비교</small><b>{aLimit == null || dA == null ? '—' : 1 + dA > aLimit ? '기준 초과' : '기준 이내'}</b><span>기준 {aLimit?.toFixed(2) ?? '—'} g</span></div>
            <div><small>무게보다 더 받은 힘 F_dyn</small><b>{dA == null ? '—' : (mKg * G * dA).toFixed(2) + ' N'}</b><span>= m·g·Δa</span></div>
            <div><small>충격량 I (이론 ②)</small><b>{v != null && c.s != null ? (mKg * v * c.s).toFixed(4) + ' N·s' : '—'}</b><span>I = m·v·sin α (sin α = {fmt(c.s, 3)}){impFit ? ` · 실측 맞춤 I = ${impFit.k.toFixed(3)}·m·v → ${v != null ? (impFit.k * mKg * v).toFixed(4) : '—'} N·s` : ''}</span></div>
            <div><small>원운동 지수 Π (이론 ③)</small><b>{fmt(pi, 3)}</b><span>Π = v²/(g·R_eff){pi != null ? ` · 꼭대기에서 a ≈ ${(1 - pi).toFixed(2)} g${pi > 1 ? ' · 1을 넘어 점프 예상' : ''}` : ''}</span></div>
          </div>
          {warn.length > 0 && <ul className="exp-flags">{warn.map(w => <li key={w}>{w}</li>)}</ul>}
          <div className="exp-inverse">
            <b>거꾸로: 이 충격을 넘지 않는 가장 빠른 속도와 출력</b>
            <div className="experiment-fields">
              <label>목표<select value={invType} onChange={e => setInvType(e.target.value as 'peak' | 'force' | 'delta')}><option value="peak">최대 수직 가속도 a_peak (g)</option><option value="force">충격력 F (N)</option><option value="delta">Δa (g)</option></select></label>
              <label>값<div className="exp-inline"><NumInput step={0.05} value={invVal} onChange={e => setInvVal(Number(e.target.value) || 0)} />{aLimit && invType === 'peak' && <button type="button" className="secondary" onClick={() => setInvVal(Number(aLimit.toFixed(3)))}>허용 기준 {aLimit.toFixed(2)}</button>}</div></label>
            </div>
            <p className="exp-chain"><span>Δa 목표 = {dTarget.toFixed(3)} g</span><i>→</i><span>v_max = <b>{vStar == null ? '— (식의 범위에서 풀 수 없음)' : `${vStar.toFixed(3)} m/s`}</b></span><i>→</i><span>{outMode === 'duty' ? '듀티' : 'PWM'} = <b>{pStar == null ? '—' : pStar.toFixed(1)}</b>{pStar != null && (pStar < 0 || pStar > 100) ? ' (0~100 밖: 이 질량으로는 출력으로 맞출 수 없음)' : ''}</span>
              {vStar != null && v != null && v > 0 && <><i>·</i><span>지금 입력 속도의 <b>{(vStar / v * 100).toFixed(0)}%</b>로 줄이면 됨</span></>}</p>
          </div>
        </div>
      </>}
    </section>}

    {sec === 'theory' && <TheoryCharts ctx={ctx} bump={bump} chosen={chosen} />}
    {sec === 'events' && <EventsLab ctx={ctx} bump={bump} />}
    {sec === 'formula' && <FormulaLab ctx={ctx} bump={bump} env={env} formulas={formulas} setFormulas={list => setFormulas(list)} onUse={id => { setPredId(id); setSec('predict'); }} chosen={chosen} />}
    {sec === 'control' && <ControlLab ctx={ctx} bump={bump} part="control" />}
    {sec === 'ultra' && <ControlLab ctx={ctx} bump={bump} part="ultra" />}
  </div>;
}

function TheoryCharts({ ctx, bump, chosen }: { ctx: ExperimentContext; bump: string; chosen: ModelResult | null }) {
  const { data, settings } = ctx;
  const c = bumpConst(settings, bump);
  const groups = (data?.groups ?? []).filter(g => g.bump_id === bump);
  const runs = (data?.runs ?? []).filter(r => r.condition.bump_id === bump && !r.excluded && (r.condition.purpose ?? 'main') === 'main' && r.analysis.bump_detected !== false
    && (r.condition.direction ?? 'forward') === (settings.analysis_direction ?? 'forward'));
  const outs = Array.from(new Set(groups.map(g => g.output_value))).sort((a, b) => a - b);

  // ① F = m·a: 출력별로 질량–충격력
  const s1 = outs.flatMap((o, i) => {
    const gs = groups.filter(g => g.output_value === o && g.mean_force_n != null).sort((a, b) => a.total_mass_g - b.total_mass_g);
    if (!gs.length) return [];
    const aMean = gs.reduce((s, g) => s + (g.mean_peak_g ?? 0), 0) / gs.length;
    const mMax = Math.max(...gs.map(g => g.total_mass_g)) / 1000;
    const color = MASS_COLORS[i % MASS_COLORS.length];
    return [{ name: `출력 ${o}`, color, points: gs.map(g => ({ x: g.total_mass_g / 1000, y: g.mean_force_n as number, label: `${g.mean_peak_g?.toFixed(2)} g` })) },
      { name: `출력 ${o}: F = m·g·${aMean.toFixed(2)}`, color, line: true, dots: false, dash: '5 4', points: [{ x: 0, y: 0 }, { x: mMax * 1.1, y: mMax * 1.1 * G * aMean }] }];
  });
  const massEffect = outs.map(o => {
    const gs = groups.filter(g => g.output_value === o && g.mean_peak_g != null).sort((a, b) => a.total_mass_g - b.total_mass_g);
    if (gs.length < 2) return null;
    const lo = gs[0], hi = gs[gs.length - 1];
    return ((hi.mean_peak_delta_g as number) / (lo.mean_peak_delta_g as number) - 1) * 100 / ((hi.total_mass_g - lo.total_mass_g) / 100);
  }).filter((x): x is number => x != null && Number.isFinite(x));
  const meanMassEffect = massEffect.length ? massEffect.reduce((a, b) => a + b, 0) / massEffect.length : null;

  // ② 충격량–운동량
  const ip = runs.filter(r => r.analysis.impulse_ns && r.analysis.momentum_nsx).map(r => ({ x: r.analysis.momentum_nsx as number, y: r.analysis.impulse_ns as number, m: r.condition.total_mass_g }));
  const ipFit = slopeThroughOrigin(ip);
  const mlist = Array.from(new Set(ip.map(p => p.m))).sort((a, b) => a - b);
  const xMax = Math.max(0.01, ...ip.map(p => p.x)) * 1.1;
  const pulse = runs.filter(r => r.analysis.pulse_s && r.analysis.speed_mps).map(r => ({ x: r.analysis.speed_mps as number, y: (r.analysis.pulse_s as number) * 1000 }));
  const ll = linfit(pulse.filter(p => p.x > 0 && p.y > 0).map(p => ({ x: Math.log(p.x), y: Math.log(p.y) })));
  const q = ll ? -ll.b : null;
  const nModel = chosen?.kind === 'power' ? chosen.coef.n : null;

  // ③ 원운동: 꼭대기 최솟값과 1 − Π
  const cr = runs.filter(r => r.analysis.pi != null && r.analysis.crest_min_g != null).map(r => ({ x: r.analysis.pi as number, y: r.analysis.crest_min_g as number, jump: !!r.analysis.jump }));
  const piMax = Math.max(1.2, ...cr.map(p => p.x)) * 1.05;
  const crDiff = cr.length ? cr.reduce((s, p) => s + (p.y - (1 - p.x)), 0) / cr.length : null;

  return <section className="card experiment-card">
    <div className="card-head"><div><h2>계획서 이론식으로 데이터 분석</h2><p>이론적 배경 ①②③의 식을 그대로 데이터에 대 보고, 어디까지 맞고 어디서 벗어나는지 봅니다. 모두 본 실험(방지턱 통과가 확인된 회차)만 씁니다.</p></div></div>
    <div className="exp-theory-grid">
      <div>
        <h3>① 뉴턴 제2법칙 F = m·a</h3>
        {s1.length ? <XYChart name={`theory1_force_mass_${bump}`} title="질량과 최대 충격력 (출력별)" xLabel="총질량 m (kg)" yLabel="충격력 F = m·g·a_peak (N)" xZero yZero series={s1} legendMax={12}
          caption="점 = 조건 평균(옆 숫자는 a_peak), 점선 = 그 출력의 평균 a_peak로 그린 F = m·g·a. 점이 점선 위에 있으면 '가속도는 질량과 무관하고 힘만 질량에 비례'한다는 뜻입니다." /> : <div className="empty-chart">조건 평균이 필요합니다.</div>}
        <p className="exp-hint">{meanMassEffect == null ? '질량을 2단계 이상 측정하면 질량이 가속도에 주는 영향을 계산합니다.' : <>같은 출력에서 질량이 100 g 늘 때 Δa가 평균 <b>{meanMassEffect > 0 ? '+' : ''}{meanMassEffect.toFixed(1)}%</b> 바뀌었습니다. {Math.abs(meanMassEffect) < 3 ? '거의 변하지 않으므로 F는 질량에 거의 정비례합니다(F = m·a에서 a가 일정).' : meanMassEffect < 0 ? '무거울수록 가속도가 줄어듭니다(같은 충격을 큰 질량이 나눠 받음 + 무거우면 조금 느려짐). 그래서 힘은 질량보다 덜 늘어납니다.' : '무거울수록 가속도도 커졌습니다(서스펜션이 눌려 더 딱딱해지는 효과 등). 힘은 질량보다 더 빨리 늘어납니다.'}</>}</p>
      </div>
      <div>
        <h3>② 충격량 I = F·Δt = m·Δv</h3>
        {ip.length >= 2 ? <XYChart name={`theory2_impulse_${bump}`} title="운동량 m·v 와 충격량 I" xLabel="운동량 m·v (kg·m/s)" yLabel="충격량 I = m·g·∫(a−1)dt (N·s)" xZero yZero
          series={[...mlist.map((m, i) => ({ name: `${m} g`, color: MASS_COLORS[i % MASS_COLORS.length], points: ip.filter(p => p.m === m).map(p => ({ x: p.x, y: p.y })) })),
            ...(ipFit ? [{ name: `실측 맞춤 I = ${ipFit.k.toFixed(3)}·m·v`, color: '#1f2328', line: true, dots: false, points: [{ x: 0, y: 0 }, { x: xMax, y: ipFit.k * xMax }] }] : []),
            ...(c.s != null ? [{ name: `이론 I = sin α·m·v (sin α = ${c.s.toFixed(3)})`, color: '#c62828', line: true, dots: false, dash: '6 4', points: [{ x: 0, y: 0 }, { x: xMax, y: c.s * xMax }] }] : [])]}
          caption="바퀴가 원호 방지턱에 오르는 순간 바퀴 중심의 진행 방향이 각 α만큼 꺾여 위쪽 속도 v·sin α가 생깁니다. 그래서 차가 받는 위쪽 충격량은 이론상 I = m·v·sin α(운동량에 비례). 실측은 앞·뒤 바퀴 충격 펄스의 넓이를 더해 구했습니다." /> : <div className="empty-chart">충격량을 계산한 회차가 2개 이상 필요합니다(축간거리·질량·방지턱 높이 입력).</div>}
        <p className="exp-hint">{ipFit ? <>실측 기울기 <b>{ipFit.k.toFixed(3)}</b> (원점 직선 R² {ipFit.r2.toFixed(2)}){c.s != null ? <>, 이론 sin α = <b>{c.s.toFixed(3)}</b> → 실측/이론 = <b>{(ipFit.k / c.s).toFixed(2)}</b>. {ipFit.k < c.s ? '실측이 작은 것은 타이어·차체가 충격 일부를 흡수하고, 바퀴가 턱 모서리를 조금 깎아 넘어(실제 α가 더 작음) 생기는 차이입니다.' : '실측이 크면 펄스에 진동이나 다른 충격이 섞였는지 확인하세요.'}</> : ''} 점들이 원점을 지나는 직선에 모이면 '충격량은 운동량에 비례한다'는 ②가 성립합니다.</> : ''}</p>
        {pulse.length >= 3 && <XYChart name={`theory2_pulse_${bump}`} title="속도와 충격 시간 Δt" xLabel="통과 속도 v (m/s)" yLabel="앞바퀴 충격 펄스 길이 Δt (ms)" yZero
          series={[{ name: '회차', color: '#a04fa0', points: pulse }]}
          caption="같은 충격량이라도 Δt가 짧을수록 힘(F = I/Δt)이 큽니다. 빠를수록 턱을 지나는 시간이 짧아 Δt가 줄어듭니다." />}
        {q != null && <p className="exp-hint">로그 기울기로 보면 Δt ∝ v^−{q.toFixed(2)} 입니다. ②에서 I ∝ v 이므로 평균 힘 F = I/Δt ∝ v^{(1 + q).toFixed(2)} 로 예상되고, {nModel != null ? <>실험 관계식의 속도 지수 n = <b>{nModel.toFixed(2)}</b>와 비교하면 {Math.abs(1 + q - nModel) < 0.3 ? '잘 맞습니다: 속도가 충격을 키우는 이유를 ②(운동량↑, 충격 시간↓)로 설명할 수 있습니다.' : '차이가 있습니다: 최대값(피크)은 펄스 모양·진동의 영향도 받기 때문입니다. 차이 자체를 보고서에 토의하세요.'}</> : '거듭제곱 관계식의 n과 비교해 보세요.'}</p>}
      </div>
      <div>
        <h3>③ 원운동: N = mg − m·v²/R</h3>
        {cr.length ? <XYChart name={`theory3_crest_${bump}`} title="꼭대기에서의 가속도와 Π = v²/(g·R)" xLabel="Π = v² / (g · R_eff)" yLabel="꼭대기 구간 최소 가속도 (g)" xZero
          series={[{ name: '회차(빈 점 = 점프)', color: '#2f6fb0', points: cr.map(p => ({ x: p.x, y: p.y, label: p.jump ? '점프' : undefined })) },
            { name: '이론 a = 1 − Π', color: '#c62828', line: true, dots: false, dash: '6 4', points: [{ x: 0, y: 1 }, { x: piMax, y: 1 - piMax }] }]}
          vLines={[{ x: 1, label: 'Π = 1 (N = 0, 점프 시작)', color: '#c33' }]}
          caption={`꼭대기를 지날 때 바퀴 중심은 반지름 R_eff = 방지턱 R + 바퀴 r (= ${c.R ? (c.R * 1000).toFixed(1) + ' mm' : '—'})의 원을 돌므로 구심가속도 v²/R만큼 몸무게가 가벼워집니다: a = 1 − Π. Π가 1을 넘으면 수직항력이 0이 되어 바퀴가 뜹니다(이론 점프 속도 v_c = √(gR) = ${c.vc ? c.vc.toFixed(3) + ' m/s' : '—'}).`} /> : <div className="empty-chart">Π를 계산하려면 바퀴 반지름과 방지턱 높이·폭이 필요합니다.</div>}
        {crDiff != null && <p className="exp-hint">실측 최솟값이 이론보다 평균 <b>{crDiff > 0 ? '+' : ''}{crDiff.toFixed(3)} g</b> {crDiff > 0 ? '높습니다(덜 가벼워짐)' : '낮습니다(더 가벼워짐)'}. 앞바퀴만 꼭대기에 있고 뒷바퀴는 평지에 있어 차체 전체로는 원운동 효과가 절반쯤만 나타나고, 진동이 섞이는 것이 주된 이유입니다. 지금 속도 범위(Π ≈ {Math.min(...cr.map(p => p.x)).toFixed(2)}~{Math.max(...cr.map(p => p.x)).toFixed(2)})에서는 {Math.max(...cr.map(p => p.x)) < 1 ? '이론상 점프하지 않습니다. ③은 점프 한계를 알려 주는 상한으로 쓰고, 충격 크기 자체는 ①②로 설명하는 것이 맞습니다.' : '일부 회차가 점프 경계를 넘었습니다.'}</p>}
      </div>
    </div>
  </section>;
}
