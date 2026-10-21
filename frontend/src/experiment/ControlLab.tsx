// 목표 2(관계식으로 정한 속도로 방지턱 앞에서 감속)와 초음파 거리·속력 분석.
import { useEffect, useState } from 'react';
import { Gauge, Play, Radar, Square } from 'lucide-react';
import { EVENT_INFO, eventPredict, eventSolve, fmt, outputFromSpeed, outputLabel, speedFromOutput, type EventKind, type RunDetail, type VisionSettings } from '../services/experiment';
import HelpBox from '../components/HelpBox';
import { RunTraceChart, XYChart } from '../components/ExperimentCharts';
import NumInput from '../components/NumInput';
import { usePersistentState } from '../services/persist';
import type { ExperimentContext } from './context';
import { solveFor } from './formula';
import { builtinPredictor, type Predictor } from './theory';

type UsTest = { samples: number; valid: number; mean_cm: number | null; sd_cm: number | null; rate_hz: number; values: (number | null)[]; available: boolean; note: string | null };

type VisionTest = { score: [number, number] | null; threshold: number; detected: boolean; calibrated: boolean };

export default function ControlLab({ ctx, bump, part }: { ctx: ExperimentContext; bump: string; pred?: Predictor | null; part: 'control' | 'ultra' }) {
  const { data, settings, condition } = ctx;
  type Trig = 'time' | 'tape' | 'distance' | 'distance_auto' | 'camera' | 'camera_distance';
  const [trigger, setTrigger] = usePersistentState<Trig>('slow-trigger', 'time');
  const [margin, setMargin] = usePersistentState<'ratio' | 'sigma'>('slow-margin', 'ratio');
  const [staged, setStaged] = usePersistentState('slow-staged', false);
  const [resume, setResume] = usePersistentState('slow-resume', true);
  const [leadCm, setLeadCm] = usePersistentState('slow-lead-cm', 30);
  const [vt2, setVt2] = useState<VisionTest | null>(null);
  const [atS, setAtS] = usePersistentState('slow-at-s', 0.6);
  const [atCm, setAtCm] = usePersistentState('slow-at-cm', 60);
  const [safety, setSafety] = usePersistentState('slow-safety', 1);
  const [minOut, setMinOut] = usePersistentState('slow-min-out', 10);
  const [targetG, setTargetG] = usePersistentState<number | ''>('slow-target-g', '');
  const [repeats, setRepeats] = usePersistentState('slow-repeats', 3);
  const [runS, setRunS] = usePersistentState('slow-run-s', 4);
  const [usTest, setUsTest] = useState<UsTest | null>(null);
  const [testing, setTesting] = useState(false);

  // 서버와 똑같이 '측정·분석 탭에서 고른 식'으로 계획을 미리 계산해 보여 줍니다.
  const cmp = data?.models?.[bump];
  const chosen = cmp?.models.find(m => m.id === data?.chosen_models?.[bump]) ?? null;
  const pred = builtinPredictor(chosen);
  const aLimit = targetG === '' ? data?.criteria?.[bump]?.a_limit_g ?? null : targetG;
  const massG = condition.base_mass_g + condition.added_mass_g;
  const mKg = massG / 1000;
  const v0 = speedFromOutput(data?.speed_models, bump, condition.output_mode, condition.output_value, massG);
  const d0 = pred && v0 ? pred.delta(v0, mKg) : null;
  const err = chosen?.loocv_rmse ?? chosen?.rmse ?? 0;
  const dTarget = aLimit && aLimit > 1 ? (aLimit - 1) - (margin === 'sigma' ? 2 * err : 0) : null;
  const vmaxAll = pred && dTarget && dTarget > 0 ? solveFor(v => pred.delta(v, mKg) ?? NaN, dTarget) : null;
  const em = data?.event_models?.[bump] ?? {};
  const kinds: EventKind[] = ['FU', 'FD', 'RU', 'RD'];
  const haveEvents = kinds.every(k => em[k]);
  const vEv = haveEvents && dTarget && dTarget > 0 ? Object.fromEntries(kinds.map(k => [k, eventSolve(em[k]!, dTarget, mKg)])) as Record<EventKind, number | null> : null;
  const fac = margin === 'ratio' ? safety : 1;
  const vFront = vEv && vEv.FU != null && vEv.FD != null ? Math.min(vEv.FU, vEv.FD) * fac : null;
  const vRear = vEv && vEv.RU != null && vEv.RD != null ? Math.min(vEv.RU, vEv.RD) * fac : null;
  const vmax = vmaxAll;
  const vt = staged ? vFront : vmax != null ? vmax * fac : null;
  const pRear = staged && vRear != null ? outputFromSpeed(data?.speed_models, bump, condition.output_mode, vRear, massG) : null;
  const usAuto = (data?.runs ?? []).filter(r => r.condition.bump_id === bump && r.distance_summary?.distance_at_front_cm).map(r => r.distance_summary?.distance_at_front_cm as number).sort((a, b) => a - b);
  const autoCm = usAuto.length ? usAuto[Math.floor(usAuto.length / 2)] + leadCm : null;
  const visionOk = !!settings.vision?.floor_mean;
  const need = v0 != null && vt != null && v0 > vt;
  const pRaw = vt != null ? outputFromSpeed(data?.speed_models, bump, condition.output_mode, vt, massG) : null;
  const pSlow = need && pRaw != null ? Math.min(condition.output_value, Math.max(minOut, pRaw)) : null;
  const dT = pred && vt ? pred.delta(vt, mKg) : null;
  const tapeOk = !!(settings.line?.floor && settings.line?.line);
  const problem = condition.bump_id !== bump ? `실험 조건의 방지턱(${condition.bump_id})이 이 탭에서 보는 방지턱(${bump})과 다릅니다. 측정·분석 탭에서 조건을 맞추세요.`
    : !pred ? '관계식이 아직 없습니다.' : v0 == null ? '출력 → 속도 식이 없습니다(출력 2단계 이상 측정).' : !aLimit ? '허용 기준이 없습니다.' : vmax == null ? '이 기준에 맞는 속도를 식으로 풀 수 없습니다.'
    : trigger === 'tape' && !tapeOk ? '감속 테이프를 쓰려면 라인 추종 탭에서 바닥·테이프 보정을 먼저 하세요.'
    : trigger === 'distance_auto' && autoCm == null ? "'거리 자동'은 초음파를 켜고 측정한 회차가 있어야 합니다(앞바퀴 충격 때 벽까지 거리를 배움)."
    : (trigger === 'camera' || trigger === 'camera_distance') && !visionOk ? '카메라 감지는 아래에서 바닥 보정을 먼저 하세요.'
    : staged && !haveEvents ? '단계별 식(앞·뒤 바퀴 오름·내림)이 아직 없습니다. 기울기로 4순간이 찾아진 회차가 출력 2단계 이상 필요합니다.'
    : dTarget != null && dTarget <= 0 ? `예측 오차(±${err.toFixed(2)} g)×2가 허용 여유보다 커서 목표를 정할 수 없습니다. 반복 측정을 늘리세요.` : '';

  const slowRuns = (data?.runs ?? []).filter(r => r.slowdown && r.condition.bump_id === bump).slice(0, 40);
  const passN = slowRuns.filter(r => r.slowdown?.pass).length;
  const judged = slowRuns.filter(r => r.slowdown?.pass != null).length;
  const curve = pred ? Array.from({ length: 40 }, (_, i) => 0.05 + i * ((Math.max(v0 ?? 1, ...slowRuns.map(r => r.slowdown?.measured_speed_mps ?? 0)) * 1.2) - 0.05) / 39).map(v => ({ x: v, y: 1 + (pred.delta(v, mKg) ?? NaN) })).filter(p => Number.isFinite(p.y)) : [];

  // 결과 그래프: 가장 최근 감속 회차(또는 표에서 누른 회차)의 위아래 가속도를 바로 보여 줌
  const [pickId, setPickId] = useState<string | null>(null);
  const showId = pickId && slowRuns.some(r => r.id === pickId) ? pickId : slowRuns[0]?.id ?? null;
  const showRun = slowRuns.find(r => r.id === showId) ?? null;
  const [detail, setDetail] = useState<RunDetail | null>(null);
  useEffect(() => {
    if (!showId || !ctx.serverAvailable) { setDetail(null); return; }
    let off = false;
    ctx.request(`/api/experiment/runs/${showId}`, { timeoutMs: 10000 }).then(d => { if (!off) setDetail(d as RunDetail); }).catch(() => { if (!off) setDetail(null); });
    return () => { off = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showId, ctx.serverAvailable]);
  const powerModel = cmp?.models.find(m => m.kind === 'power') ?? null;
  async function reportPreset() {
    setMargin('ratio'); setSafety(1); setTargetG('');
    if (powerModel && data?.chosen_models?.[bump] !== powerModel.id) await ctx.patchSettings({ model_id: powerModel.id });
    ctx.setMessage(`보고서와 같은 방식으로 맞췄습니다: ${powerModel ? powerModel.formula : '거듭제곱 식'} · 기준 그대로(여유율 1.0).`);
  }
  function stopReason(r: typeof slowRuns[number]): string {
    const t = r.elapsed_s != null ? ` (${r.elapsed_s.toFixed(1)}s${r.slowdown?.drive_s ? ` / ${r.slowdown.drive_s}s` : ''})` : '';
    if (r.aborted) return '정지 버튼' + t;
    const why = r.end_reason ?? (r.stopped_by_wall ? 'wall' : r.bump_passed_s != null ? 'bump_passed' : 'time');
    return ({ wall: '벽 앞 자동 정지(초음파)', bump_passed: '다 넘고 정지', time: '주행 시간 끝', line_lost: '유도선 놓침', mark: '정지선', cross: '정지선' } as Record<string, string>)[why] + t;
  }

  async function runSlow() {
    await ctx.startAutoRun(undefined, {
      repeats, run_seconds: runS, ultrasonic: trigger === 'distance' || trigger === 'distance_auto' ? true : undefined,
      slowdown: { trigger, at_s: atS, at_cm: atCm, safety, min_output: minOut, target_g: targetG === '' ? null : targetG, margin, staged, resume, lead_cm: leadCm },
    });
    ctx.setMessage('감속 제어 측정을 시작했습니다. 진행 상황은 측정·분석 탭에서도 볼 수 있습니다.');
  }
  async function visionCal() {
    try { const r = await ctx.request('/api/experiment/vision/calibrate', { method: 'POST', body: JSON.stringify({}), timeoutMs: 10000 }); ctx.setMessage(`카메라 바닥 보정 완료 · 지금 점수 ${r.score ? r.score.map((x: number) => (x * 100).toFixed(0) + '%').join(' / ') : '—'}`); await ctx.loadRuns(); } catch (e) { ctx.fail(e); }
  }
  async function visionTest() { try { setVt2(await ctx.request('/api/experiment/vision/test', { timeoutMs: 10000 }) as VisionTest); } catch (e) { ctx.fail(e); } }
  async function visionPatch(patch: Partial<VisionSettings>) { await ctx.patchSettings({ vision: { ...(settings.vision as VisionSettings), ...patch } }); }
  async function testUs() {
    setTesting(true);
    try { setUsTest(await ctx.request('/api/experiment/ultrasonic/test?seconds=1.5', { timeoutMs: 8000 }) as UsTest); } catch (e) { ctx.fail(e); } finally { setTesting(false); }
  }

  const usRuns = (data?.runs ?? []).filter(r => r.distance_summary && r.condition.bump_id === bump && !r.excluded);
  const found = usRuns.filter(r => r.distance_summary?.us_vs_imu_s != null && Math.abs(r.distance_summary.us_vs_imu_s) <= 0.3).length;
  // 초음파 속력이 물리적으로 말이 안 되는 회차(음수·0 근처·2 m/s 초과 = 벽이 없거나 반사가 흔들림)는 그래프에서 뺌
  const usOk = (v: number | null | undefined) => v != null && v > 0.03 && v < 2;
  const cmpPts = usRuns.filter(r => r.analysis.speed_mps && usOk(r.distance_summary?.bump_speed_mps)).map(r => ({ x: r.analysis.speed_mps as number, y: r.distance_summary?.bump_speed_mps as number, label: `${r.condition.output_value}` }));
  const dropPts = usRuns.filter(r => usOk(r.distance_summary?.approach_speed_mps) && r.distance_summary?.drop_pct != null && Math.abs(r.distance_summary?.drop_pct as number) <= 100 && !r.slowdown).map(r => ({ x: r.distance_summary?.approach_speed_mps as number, y: r.distance_summary?.drop_pct as number }));
  const errs = cmpPts.map(p => Math.abs(p.y / p.x - 1) * 100);

  if (part === 'control') return <section className="card experiment-card">
      <div className="card-head"><div><h2><Gauge size={17} /> 목표 2 · 관계식으로 정한 속도로 방지턱 앞에서 감속</h2>
        <p>관계식을 거꾸로 풀어 '허용 기준을 넘지 않는 가장 빠른 속도 v_max'를 구하고, 방지턱 앞에서 그 속도가 나오는 출력으로 줄여 실제로 기준 안에 드는지 확인합니다. 결과는 '식 검증' 시행으로 저장돼 관계식 계산에는 섞이지 않습니다.</p></div></div>
      <div className="exp-formula-box">
        <div><small>원리</small><b>v* = v_max(m) × 여유율, 출력* = 속도식⁻¹(v*, m)</b><span>{chosen?.kind === 'power' ? `지금 식(거듭제곱)이면 v*/v₀ = (Δa_기준 / Δa(v₀))^(1/n), n = ${chosen.coef.n.toFixed(2)}: 충격을 절반으로 하려면 속도는 ${(0.5 ** (1 / chosen.coef.n) * 100).toFixed(0)}%로` : '관계식 Δa(v, m) = a_기준 − 1 을 v에 대해 풂'}</span></div>
        <div><small>이번 조건 ({condition.bump_id} · {massG} g · {outputLabel(condition.output_mode, condition.output_value)})</small><b>v₀ ≈ {fmt(v0, 3, 'm/s')} → 예측 {d0 == null ? '—' : (1 + d0).toFixed(2) + ' g'}</b><span>허용 기준 {aLimit?.toFixed(2) ?? '—'} g · v_max {fmt(vmax, 3, 'm/s')} · 목표 {fmt(vt, 3, 'm/s')}</span></div>
        <div className={need ? 'warn' : ''}><small>계획</small><b>{problem ? '—' : need ? `출력 ${condition.output_value} → ${pSlow?.toFixed(1)} 로 감속` : '감속 불필요 (이미 기준 이내)'}</b><span>{need && dT != null ? `감속 후 예측 ${(1 + dT).toFixed(2)} g${pRaw != null && pRaw < minOut ? ` · 계산값 ${pRaw.toFixed(1)}이 최소 출력보다 낮아 ${minOut}로 제한(멈춤 방지) → 기준을 넘을 수 있음` : ''}` : ''}</span></div>
      </div>
      {(dTarget != null || staged) && <div className="exp-formula-box">
        <div><small>목표 충격 Δa*</small><b>{dTarget == null ? '—' : `${dTarget.toFixed(3)} g`}{aLimit ? ` (기준 +${(aLimit - 1).toFixed(2)} g${margin === 'sigma' ? ` − 2×${err.toFixed(3)}` : ''})` : ''}</b><span>{margin === 'sigma' ? `관계식의 예측 오차(교차검증 RMSE ±${err.toFixed(3)} g)의 2배만큼 기준 아래를 목표로 → 측정이 흔들려도 약 95% 확률로 기준 이내` : `v_max × ${safety} : 속도를 ${((1 - safety) * 100).toFixed(0)}% 낮춰 여유를 둠`}</span></div>
        {staged && <div><small>단계별 최대 속도 (각 단계식으로 Δa* 를 풂)</small><b>{vEv ? kinds.map(k => `${EVENT_INFO[k].short} ${vEv[k] == null ? '—' : vEv[k]!.toFixed(3)}`).join(' · ') : '— (단계식 없음)'}</b>
          <span>앞바퀴 구간 v = {fmt(vFront, 3, 'm/s')}(출력 {pSlow?.toFixed(1) ?? '—'}) → 앞바퀴가 다 내려오면 뒷바퀴 구간 v = {fmt(vRear, 3, 'm/s')}(출력 {pRear == null ? '—' : Math.min(condition.output_value, Math.max(minOut, pRear)).toFixed(1)}){vFront != null && haveEvents ? ` · 예측: ${kinds.map(k => `${EVENT_INFO[k].short} +${eventPredict(em[k]!, k === 'FU' || k === 'FD' ? vFront : (vRear ?? vFront), mKg).toFixed(2)}g`).join(', ')}` : ''}</span></div>}
      </div>}
      <HelpBox id="slow-margin" title="'기준 바로 밑(0.9999…)'으로 맞추면 안 되는 이유" defaultOpen={false}>
        <p>식은 평균을 예측합니다. 같은 속도로 달려도 회차마다 충격이 흔들리므로(이번 실험에서 표준편차 0.5~0.9 g), 예측값을 기준과 똑같이(또는 0.9999배로) 맞추면 <b>절반쯤은 기준을 넘습니다</b>. 그래서 '얼마나 확실하게 넘지 않을지'를 정해야 합니다.</p>
        <ul><li><b>예측 오차 방식(추천)</b>: 목표 = 기준 − 2 × 예측 오차. 오차가 정규분포라면 약 95%(한쪽 97.5%)는 기준 이내. 반복을 늘려 식이 정확해질수록 목표가 기준에 가까워져 더 빨리 지날 수 있습니다.</li>
          <li><b>여유율 방식</b>: 속도를 v_max의 90%처럼 정해진 비율로 낮춤. 간단하지만 얼마나 안전한지 숫자로 말하기 어렵습니다.</li>
          <li><b>단계별</b>: 앞바퀴 오름·내림, 뒷바퀴 오름·내림의 충격이 서로 다르므로 각각의 식으로 '그 단계에서 넘지 않는 속도'를 구합니다. 앞바퀴 두 단계 중 더 엄격한 속도로 들어가고, 앞바퀴가 다 내려오면(기울기가 수평으로 돌아옴) 뒷바퀴 단계 속도로 바꾼 뒤, 다 넘으면 다시 가속합니다. 다만 모터 속도가 바뀌는 데 0.2~0.4초가 걸려, 앞바퀴→뒷바퀴 사이(이 방지턱에서 약 0.1~0.2초) 안에 속도를 크게 바꾸기는 어렵습니다. 실제 결과는 각 회차의 '실제 전환' 기록과 단계별 충격으로 확인하세요.</li></ul>
      </HelpBox>
      <div className="exp-inverse">
        <b>시작 출력과 관계식</b>
        <div className="experiment-fields">
          <label>시작 출력 ({condition.output_mode.toUpperCase()}, 방지턱 전·후 속도)<NumInput min={1} max={100} value={condition.output_value} onChange={e => ctx.setCondition({ output_value: Math.max(1, Math.min(100, Number(e.target.value) || condition.output_value)) })} /></label>
          <label>주행 시간 (s, 출발부터 멈출 때까지)<NumInput min={0.5} max={12} step={0.5} value={runS} onChange={e => setRunS(Math.max(0.5, Math.min(12, Number(e.target.value) || 4)))} /></label>
          <label>쓸 관계식<select value={settings.model_id ?? 'auto'} onChange={e => void ctx.patchSettings({ model_id: e.target.value })}>
            <option value="auto">자동 (교차검증 오차 최소{cmp?.best ? ` → ${cmp.models.find(m => m.id === cmp.best)?.name ?? ''}` : ''})</option>
            {(cmp?.models ?? []).map(m => <option key={m.id} value={m.id}>{m.name}: {m.formula}</option>)}</select></label>
        </div>
        <div className="button-row"><button className="secondary" disabled={!ctx.serverAvailable} onClick={() => void reportPreset()}>보고서 방식으로 맞추기 (거듭제곱 식 · 기준 그대로)</button>
          <span className="exp-hint">지금 식: <b>{chosen ? chosen.formula : '—'}</b>{margin === 'sigma' ? ' · 목표를 기준보다 2×오차만큼 낮게 잡는 중' : ` · 여유율 ${safety}`}</span></div>
        <p className="exp-hint">출력을 정한 값으로만 달리고 싶으면(감속 없음) 측정·분석 탭에서 출력만 바꿔 측정하세요. 이 버튼은 '시작 출력으로 달리다가 방지턱 앞에서 계산된 출력으로 줄이는' 시험입니다. 방지턱 앞에서는 출력만 줄이고 멈추지 않습니다. '다 넘으면 다시 가속'을 켜 두면 방지턱을 넘은 뒤 시작 출력으로 돌아가고, 위에 정한 주행 시간이 끝나야 멈춥니다. 방지턱을 넘고도 충분히 갈 만큼 시간을 넉넉히 주세요(측정 탭의 주행 시간과 따로).</p>
      </div>
      <div className="experiment-fields">
        <label>방지턱을 미리 알아채는 방법<select value={trigger} onChange={e => setTrigger(e.target.value as Trig)}>
          <option value="time">출발 후 시간</option><option value="distance_auto">거리 자동 (초음파, 지난 회차에서 방지턱 위치를 배움)</option><option value="distance">벽까지 거리 직접 입력 (초음파)</option>
          <option value="camera">카메라 (바닥과 색이 다른 물체)</option><option value="camera_distance">카메라 + 초음파 (둘 중 먼저 알아챈 쪽)</option><option value="tape">감속 표시 테이프 (바닥 센서)</option></select></label>
        {trigger === 'time' && <label>출발 후 (s)<NumInput min={0} max={6} step={0.1} value={atS} onChange={e => setAtS(Math.max(0, Number(e.target.value) || 0))} /></label>}
        {(trigger === 'distance' || (trigger === 'camera_distance' && autoCm == null)) && <label>벽까지 (cm) 가 되면<NumInput min={5} max={300} step={5} value={atCm} onChange={e => setAtCm(Math.max(5, Number(e.target.value) || 60))} /></label>}
        {(trigger === 'distance_auto' || trigger === 'camera_distance') && <label>방지턱보다 먼저 감속할 거리 (cm){autoCm != null ? ` → 벽까지 ${autoCm.toFixed(0)} cm에서` : ''}<NumInput min={5} max={150} step={5} value={leadCm} onChange={e => setLeadCm(Math.max(5, Math.min(150, Number(e.target.value) || 30)))} /></label>}
        <label>목표 정하는 방식<select value={margin} onChange={e => setMargin(e.target.value as 'ratio' | 'sigma')}><option value="sigma">예측 오차만큼 기준 아래로 (추천)</option><option value="ratio">v_max에 여유율 곱하기</option></select></label>
        {margin === 'ratio' && <label>여유율 (v_max에 곱함)<NumInput min={0.3} max={1} step={0.05} value={safety} onChange={e => setSafety(Math.max(0.3, Math.min(1, Number(e.target.value) || 0.9)))} /></label>}
        <label className="exp-check"><input type="checkbox" checked={staged} onChange={e => setStaged(e.target.checked)} /> 단계별 (앞바퀴 구간·뒷바퀴 구간을 각각의 식으로)</label>
        <label className="exp-check"><input type="checkbox" checked={resume} onChange={e => setResume(e.target.checked)} /> 다 넘으면 원래 출력으로 다시 가속</label>
        <label>최소 출력 (멈춤 방지)<NumInput min={1} max={100} value={minOut} onChange={e => setMinOut(Math.max(1, Number(e.target.value) || 10))} /></label>
        <label>허용 기준 (g, 빈칸 = 분석 탭 기준)<NumInput min={1.05} max={8} step={0.05} value={targetG} placeholder={data?.criteria?.[bump]?.a_limit_g?.toFixed(2) ?? ''} onChange={e => setTargetG(e.target.value === '' ? '' : Number(e.target.value))} /></label>
        <label>반복<NumInput min={1} max={20} value={repeats} onChange={e => setRepeats(Math.max(1, Math.min(20, Math.round(Number(e.target.value) || 1))))} /></label>
      </div>
      <p className="exp-hint">{trigger === 'time' ? '출발 후 정해진 시간에 줄입니다. 방지턱까지 걸리는 시간보다 0.3~0.5초 일찍 줄여야 속도가 미리 떨어집니다(모터 속도가 바뀌는 데 0.2~0.4초 걸림).'
        : trigger === 'distance' ? '방지턱 뒤쪽에 벽(상자 등, 높이 15 cm 이상)을 두고, 초음파로 벽까지 거리가 이 값이 되면 줄입니다. 측정·분석 탭에서 한 번 달려 보면 "앞바퀴 충격 때 벽까지 거리"가 나오니, 그 값보다 20~40 cm 큰 값을 넣으세요(감속할 거리).'
        : trigger === 'distance_auto' ? '초음파를 켜고 측정한 회차들의 "앞바퀴 충격 때 벽까지 거리"(중앙값)를 방지턱 위치로 보고, 그보다 정한 거리만큼 먼저 줄입니다. 방지턱 뒤쪽에 벽(상자)이 있어야 합니다.'
        : trigger === 'camera_distance' ? '초음파(벽까지 거리)와 카메라(바닥과 색이 다른 방지턱)를 함께 보고, 둘 중 먼저 알아챈 순간에 줄입니다. 초음파 기록이 있는 회차가 있으면 방지턱 위치를 배워 쓰고, 없으면 입력한 거리를 씁니다. 카메라는 아래에서 바닥 보정을 먼저 하세요. 회차 기록의 \'감속 시작\'에 어느 쪽이 먼저였는지 남습니다.'
        : trigger === 'camera' ? '카메라 화면 아래쪽 양옆(유도선 제외)에 바닥과 색이 다른 물체(방지턱)가 일정 비율 이상 나타나면 줄입니다. 아래에서 방지턱이 보이지 않는 자리(출발선)에서 바닥 보정을 하고, 방지턱 앞 20~30 cm에 차를 두고 시험해 점수가 기준을 넘는지 확인하세요. 카메라 각도를 바꾸면 다시 보정해야 합니다.'
        : '방지턱 20~40 cm 앞에 가로로 검은 테이프를 붙이면, 차 앞쪽 바닥 센서가 테이프를 보는 순간 줄입니다(출발선 테이프와 같은 감지 방식).'} 실제로 얼마나 줄었는지는 각 회차의 '방지턱 통과 속도'로 확인합니다.</p>
      {(trigger === 'camera' || trigger === 'camera_distance') && <div className="exp-inverse">
        <b>카메라 방지턱 감지 설정</b>
        <div className="experiment-fields">
          <label>관심 영역 위 (화면 높이 비율)<NumInput min={0} max={0.95} step={0.05} value={settings.vision?.roi_top ?? 0.55} onChange={e => void visionPatch({ roi_top: Number(e.target.value) })} /></label>
          <label>관심 영역 아래<NumInput min={0.05} max={1} step={0.05} value={settings.vision?.roi_bottom ?? 0.9} onChange={e => void visionPatch({ roi_bottom: Number(e.target.value) })} /></label>
          <label>가운데 뺄 폭 (유도선)<NumInput min={0} max={0.8} step={0.05} value={settings.vision?.center_gap ?? 0.2} onChange={e => void visionPatch({ center_gap: Number(e.target.value) })} /></label>
          <label>감지 기준 (바닥 아닌 비율)<NumInput min={0.01} max={0.9} step={0.01} value={settings.vision?.threshold ?? 0.12} onChange={e => void visionPatch({ threshold: Number(e.target.value) })} /></label>
        </div>
        <div className="button-row"><button className="secondary" disabled={!ctx.serverAvailable || ctx.busy} onClick={() => void visionCal()}>① 방지턱 없는 곳에서 바닥 보정</button><button className="secondary" disabled={!ctx.serverAvailable || ctx.busy} onClick={() => void visionTest()}>② 지금 점수 보기</button>
          <span className="exp-hint">{visionOk ? `보정됨 (바닥 색 ${settings.vision?.floor_mean?.map(x => Math.round(x)).join(', ')})` : '보정 필요'}{vt2?.score ? ` · 왼쪽 ${(vt2.score[0] * 100).toFixed(0)}% / 오른쪽 ${(vt2.score[1] * 100).toFixed(0)}% (기준 ${(vt2.threshold * 100).toFixed(0)}%) → ${vt2.detected ? '방지턱 있음' : '없음'}` : ''}</span></div>
      </div>}
      {problem && <p className="diagnostic-error">{problem}</p>}
      <div className="button-row"><button className="primary" disabled={!!problem || !ctx.canAutoRun} onClick={() => void runSlow()}><Play size={15} /> 감속 제어 측정 ({repeats}회)</button>
        <button className="secondary" disabled={!ctx.serverAvailable} onClick={() => void ctx.stopNow()}><Square size={14} /> 정지</button>
        {ctx.auto && ctx.busy && <span className="exp-hint">{ctx.auto.message}</span>}</div>
      {slowRuns.length > 0 && <>
        <p className="exp-hint">감속 제어 {slowRuns.length}회 중 기준 이내 <b>{passN}/{judged}</b>회. 표에서 회차를 누르면 그 회차 그래프로 바뀝니다.</p>
        {showRun && (detail && detail.id === showRun.id ? <RunTraceChart key={showRun.id} series={detail.series} analysis={showRun.analysis} aLimit={aLimit}
          thresholdDelta={Number(showRun.analysis.threshold_used_g ?? showRun.analysis.params?.threshold_delta_g ?? settings.threshold_delta_g)} jumpG={Number(showRun.analysis.params?.jump_g ?? settings.jump_g)}
          zoom="all"
          extraMarks={[...(showRun.slowdown?.slowed_at_s != null ? [{ t: showRun.slowdown.slowed_at_s, label: `감속 시작 (출력 ${showRun.slowdown.output})`, color: '#a04fa0' }] : []),
            ...(showRun.bump_passed_s != null ? [{ t: showRun.bump_passed_s + (showRun.pre_roll_s ?? 0), label: '다 넘음(기울기)', color: '#3c9a5f' }] : [])]}
          title={`위아래 가속도 – 감속 회차 ${new Date(showRun.created_at).toLocaleTimeString()} · 출력 ${showRun.slowdown?.output_initial} → ${showRun.slowdown?.output ?? '유지'} · 멈춘 이유: ${stopReason(showRun)}`}
          name={`slow_trace_${showRun.id}`} /> : <div className="empty-chart">그래프 불러오는 중…</div>)}
        <XYChart name={`slowdown_${bump}`} title="감속 제어 결과: 실제 통과 속도와 최대 충격" xLabel="실제 통과 속도 (m/s, 바퀴 간격)" yLabel="최대 수직 가속도 (g)"
          series={[{ name: `관계식 예측 (${massG} g)`, color: '#2f6fb0', line: true, dots: false, points: curve },
            { name: '기준 이내', color: '#3c9a5f', points: slowRuns.filter(r => r.slowdown?.pass && r.slowdown.measured_speed_mps).map(r => ({ x: r.slowdown?.measured_speed_mps as number, y: r.slowdown?.measured_peak_g as number })) },
            { name: '기준 초과', color: '#c62828', points: slowRuns.filter(r => r.slowdown?.pass === false && r.slowdown.measured_speed_mps).map(r => ({ x: r.slowdown?.measured_speed_mps as number, y: r.slowdown?.measured_peak_g as number })) }]}
          hLines={aLimit ? [{ y: aLimit, label: `허용 기준 ${aLimit.toFixed(2)} g`, color: '#c33' }] : []}
          vLines={vt != null ? [{ x: vt, label: '목표 속도', color: '#a04fa0' }] : []}
          caption="초록 점이 기준선 아래, 목표 속도 근처에 모이면 '관계식 → 속도 결정 → 감속 → 충격 감소'가 실험으로 확인된 것입니다. 점이 목표 속도보다 오른쪽이면 감속할 거리(시간)가 부족했던 것입니다." />
        <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>시각</th><th>질량</th><th>출력</th><th>시작 조건</th><th>예측 v₀ → 목표</th><th>실제 속도</th><th>최대 충격</th><th>판정</th><th>멈춘 이유</th></tr></thead>
          <tbody>{slowRuns.map(r => { const s = r.slowdown!; return <tr key={r.id} className={(s.pass === false ? 'over ' : '') + (r.id === showId ? 'selected' : '')} style={{ cursor: 'pointer' }} onClick={() => setPickId(r.id)}><td>{new Date(r.created_at).toLocaleTimeString()}</td><td>{r.condition.total_mass_g} g</td><td>{s.output_initial} → {s.staged ? `${s.staged.output_front} / ${s.staged.output_rear}` : s.needed ? s.output : '유지'}</td>
            <td>{s.trigger === 'time' ? `${s.at_s}s` : s.trigger === 'distance' ? `${s.at_cm}cm${s.trigger_requested === 'distance_auto' ? '(자동)' : ''}` : s.trigger === 'camera' ? '카메라' : s.trigger === 'camera_distance' ? `카메라+초음파 ${s.at_cm}cm${s.slowed_by ? ` (${s.slowed_by === 'camera' ? '카메라' : '초음파'} 먼저)` : ''}` : '테이프'}{s.slowed_at_s == null && s.needed && !r.simulated ? ' (작동 안 함)' : ''}</td>
            <td>{s.v0_pred_mps.toFixed(3)} → {s.target_v_mps.toFixed(3)}</td><td>{fmt(s.measured_speed_mps, 3)}</td><td>{fmt(s.measured_peak_g, 2)} / {s.a_limit_g.toFixed(2)} g</td><td>{s.pass == null ? '—' : s.pass ? '이내' : '초과'}</td><td>{stopReason(r)}</td></tr>; })}</tbody></table></div>
      </>}
    </section>;

  return <section className="card experiment-card">
      <div className="card-head"><div><h2><Radar size={17} /> 초음파 거리·속력 분석</h2>
        <p>PiCar-X 앞쪽 초음파 센서로 벽까지 거리를 초당 {settings.ultrasonic_hz ?? 20}번 기록하고, 거리 변화로 속력을 구합니다. 방지턱을 오르며 속력이 잠깐 줄어드는 '골'을 찾아 MPU-6050 충격 시각과 비교하고, 바퀴 간격으로 구한 통과 속도를 따로 검증합니다.</p></div></div>
      <div className="experiment-fields">
        <label className="exp-check"><input type="checkbox" checked={!!settings.ultrasonic_log} onChange={e => void ctx.patchSettings({ ultrasonic_log: e.target.checked })} /> 측정 주행 때 초음파 거리 기록</label>
        <label>초당 읽기 (Hz)<NumInput min={5} max={40} value={settings.ultrasonic_hz ?? 20} onChange={e => void ctx.patchSettings({ ultrasonic_hz: Math.max(5, Math.min(40, Number(e.target.value) || 20)) })} /></label>
        <label>벽 앞 자동 정지 (cm, 0 = 끔)<NumInput min={0} max={200} value={settings.wall_stop_cm ?? 0} onChange={e => void ctx.patchSettings({ wall_stop_cm: Math.max(0, Number(e.target.value) || 0) })} /></label>
        <div className="button-row"><button className="secondary" disabled={!ctx.serverAvailable || testing || ctx.busy} onClick={() => void testUs()}>{testing ? '읽는 중…' : '센서 점검 (1.5초)'}</button></div>
      </div>
      {usTest && <p className={usTest.available ? 'exp-hint' : 'diagnostic-error'}>{usTest.available ? <>평균 <b>{usTest.mean_cm} cm</b> · 흔들림(표준편차) <b>{usTest.sd_cm} cm</b> · 초당 {usTest.rate_hz}번 · 유효 {usTest.valid}/{usTest.samples}. {usTest.sd_cm != null && usTest.sd_cm > 1.5 ? '흔들림이 큽니다: 벽이 평평하고 단단한지, 센서 정면에 있는지, 거리가 2 m 이내인지 확인하세요.' : '정상입니다.'}</> : usTest.note}</p>}
      <details className="exp-tips"><summary>설치와 원리, 할 수 있는 것과 없는 것</summary>
        <ul>
          <li><b>벽 놓기</b>: 방지턱 뒤 0.5~1.5 m에 상자나 책을 세로로 세워 둡니다(높이 15 cm 이상, 폭 30 cm 이상, 단단하고 평평한 면). 출발점에서 벽까지 2 m 이내가 좋습니다(멀수록 흔들림이 커짐).</li>
          <li><b>속력 구하기</b>: 거리는 ±0.5~1 cm 흔들리므로 바로 이웃 두 점으로 나누면 오차가 커집니다(0.05초에 1 cm면 0.2 m/s). 그래서 앞뒤 0.15초 안의 점들에 직선을 맞춘 기울기로 속력을 구하고(약 ±0.03 m/s), 차가 기울어 초음파가 바닥에 맞아 튄 값은 이웃 직선에서 크게 벗어난 점으로 찾아 뺍니다.</li>
          <li><b>방지턱 찾기</b>: 속력이 직전 최고보다 8% 넘게 줄었다가 다시 회복하는 곳을 방지턱으로 봅니다(멈추는 구간은 회복하지 않으므로 제외). 초음파는 초당 20번이라 시각 정밀도는 ±0.05초 정도로, 충격 시각 자체는 MPU(초당 수백 번)가 훨씬 정확합니다. 초음파는 '속력이 얼마나 줄었나'와 '바퀴 간격 속도가 맞나'를 확인하는 데 씁니다.</li>
          <li><b>초음파로 방지턱을 직접 볼 수 있나</b>: 높이 1 cm 방지턱은 초음파 빔(센서 높이 수 cm, 퍼지는 각 ±15°)에 거의 잡히지 않습니다. 그래서 '앞바퀴 충격 때 벽까지 거리'를 한 번 배워 두고, 그 거리보다 조금 먼저 감속하는 '거리 자동' 방식을 씁니다(감속 제어에서 선택).</li>
          <li><b>카메라·라이다</b>: PiCar-X에는 라이다가 없습니다. 카메라는 거리를 바로 재지는 못하지만, 바닥과 색이 다른 방지턱이 화면 아래쪽에 나타나는 순간은 알아챌 수 있어 '감속 제어 → 카메라'에서 씁니다(학습 없이 색 차이로 판단).</li>
          <li><b>주의</b>: 초음파를 읽는 동안 라즈베리파이가 바빠져 MPU 기록 속도가 조금 줄 수 있습니다. 각 회차의 표본 속도(Hz)를 확인하세요. 벽 앞 자동 정지를 켜 두면 벽에 부딪히지 않습니다.</li>
        </ul>
      </details>
      {usRuns.length > 0 ? <>
        <p className="exp-hint">초음파를 기록한 {usRuns.length}회 중 속력 골을 MPU 충격 ±0.3초 안에서 찾은 회차 <b>{found}</b>회 ({(found / usRuns.length * 100).toFixed(0)}%){errs.length ? <> · 바퀴 간격 속도와 초음파 속력 차이 평균 <b>{(errs.reduce((a, b) => a + b, 0) / errs.length).toFixed(1)}%</b></> : ''}.</p>
        <div className="exp-theory-grid">
          {cmpPts.length > 0 && <XYChart name={`us_vs_axle_${bump}`} title="두 방법으로 구한 통과 속도" xLabel="바퀴 간격으로 구한 속도 (m/s)" yLabel="초음파로 구한 속력 (m/s)" identity band={0.1}
            series={[{ name: '회차', color: '#1f9e9e', points: cmpPts }]} caption={`대각선 위에 있으면 두 방법이 같은 속도를 냅니다. 바퀴 간격 속도(Δt)를 검증하는 독립적인 방법입니다.${usRuns.length - cmpPts.length > 0 ? ` 초음파 값이 비정상(음수·0 근처·2 m/s 초과)인 ${usRuns.length - cmpPts.length}회는 뺐습니다 — 앞에 벽이 없거나 초음파가 흔들린 회차입니다.` : ''}`} />}
          {dropPts.length > 0 && <XYChart name={`us_drop_${bump}`} title="진입 속력과 방지턱에서 느려진 정도" xLabel="방지턱 직전 속력 (m/s)" yLabel="감소율 (%)" yZero
            series={[{ name: '회차', color: '#a04fa0', points: dropPts }]} caption="바퀴가 턱 모서리에 부딪히며 앞으로 가는 운동량 일부를 잃고, 차체를 들어 올리는 일도 해야 해서 잠깐 느려집니다. 모터가 계속 밀어 주므로 턱을 넘으면 다시 빨라집니다. 감소율이 클수록 바퀴가 턱에 세게 부딪힌 것입니다." />}
        </div>
      </> : <p className="exp-hint">아직 초음파를 기록한 회차가 없습니다. 위에서 '측정 주행 때 초음파 거리 기록'을 켜고 측정하세요. 각 회차는 한 회씩 보기 아래에 거리·속력 그래프가 나옵니다.</p>}
    </section>;
}
