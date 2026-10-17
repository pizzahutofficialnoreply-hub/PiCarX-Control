// 목표 2(관계식으로 정한 속도로 방지턱 앞에서 감속)와 초음파 거리·속력 분석.
import { useState } from 'react';
import { Gauge, Play, Radar } from 'lucide-react';
import { fmt, G, outputFromSpeed, outputLabel, speedFromOutput } from '../services/experiment';
import { XYChart } from '../components/ExperimentCharts';
import NumInput from '../components/NumInput';
import { usePersistentState } from '../services/persist';
import type { ExperimentContext } from './context';
import { solveFor } from './formula';
import { builtinPredictor, type Predictor } from './theory';

type UsTest = { samples: number; valid: number; mean_cm: number | null; sd_cm: number | null; rate_hz: number; values: (number | null)[]; available: boolean; note: string | null };

export default function ControlLab({ ctx, bump }: { ctx: ExperimentContext; bump: string; pred: Predictor | null }) {
  const { data, settings, condition } = ctx;
  const [trigger, setTrigger] = usePersistentState<'time' | 'tape' | 'distance'>('slow-trigger', 'time');
  const [atS, setAtS] = usePersistentState('slow-at-s', 0.6);
  const [atCm, setAtCm] = usePersistentState('slow-at-cm', 60);
  const [safety, setSafety] = usePersistentState('slow-safety', 0.9);
  const [minOut, setMinOut] = usePersistentState('slow-min-out', 10);
  const [targetG, setTargetG] = usePersistentState<number | ''>('slow-target-g', '');
  const [repeats, setRepeats] = usePersistentState('slow-repeats', 3);
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
  const vmax = pred && aLimit && aLimit > 1 ? solveFor(v => pred.delta(v, mKg) ?? NaN, aLimit - 1) : null;
  const vt = vmax != null ? vmax * safety : null;
  const need = v0 != null && vt != null && v0 > vt;
  const pRaw = vt != null ? outputFromSpeed(data?.speed_models, bump, condition.output_mode, vt, massG) : null;
  const pSlow = need && pRaw != null ? Math.min(condition.output_value, Math.max(minOut, pRaw)) : null;
  const dT = pred && vt ? pred.delta(vt, mKg) : null;
  const tapeOk = !!(settings.line?.floor && settings.line?.line);
  const problem = condition.bump_id !== bump ? `실험 조건의 방지턱(${condition.bump_id})이 이 탭에서 보는 방지턱(${bump})과 다릅니다. 측정·분석 탭에서 조건을 맞추세요.`
    : !pred ? '관계식이 아직 없습니다.' : v0 == null ? '출력 → 속도 식이 없습니다(출력 2단계 이상 측정).' : !aLimit ? '허용 기준이 없습니다.' : vmax == null ? '이 기준에 맞는 속도를 식으로 풀 수 없습니다.'
    : trigger === 'tape' && !tapeOk ? '감속 테이프를 쓰려면 라인 추종 탭에서 바닥·테이프 보정을 먼저 하세요.' : '';

  const slowRuns = (data?.runs ?? []).filter(r => r.slowdown && r.condition.bump_id === bump).slice(0, 40);
  const passN = slowRuns.filter(r => r.slowdown?.pass).length;
  const judged = slowRuns.filter(r => r.slowdown?.pass != null).length;
  const curve = pred ? Array.from({ length: 40 }, (_, i) => 0.05 + i * ((Math.max(v0 ?? 1, ...slowRuns.map(r => r.slowdown?.measured_speed_mps ?? 0)) * 1.2) - 0.05) / 39).map(v => ({ x: v, y: 1 + (pred.delta(v, mKg) ?? NaN) })).filter(p => Number.isFinite(p.y)) : [];

  async function runSlow() {
    await ctx.startAutoRun(undefined, {
      repeats, ultrasonic: trigger === 'distance' ? true : undefined,
      slowdown: { trigger, at_s: atS, at_cm: atCm, safety, min_output: minOut, target_g: targetG === '' ? null : targetG },
    });
    ctx.setMessage('감속 제어 측정을 시작했습니다. 진행 상황은 측정·분석 탭에서도 볼 수 있습니다.');
  }
  async function testUs() {
    setTesting(true);
    try { setUsTest(await ctx.request('/api/experiment/ultrasonic/test?seconds=1.5', { timeoutMs: 8000 }) as UsTest); } catch (e) { ctx.fail(e); } finally { setTesting(false); }
  }

  const usRuns = (data?.runs ?? []).filter(r => r.distance_summary && r.condition.bump_id === bump && !r.excluded);
  const found = usRuns.filter(r => r.distance_summary?.us_vs_imu_s != null && Math.abs(r.distance_summary.us_vs_imu_s) <= 0.3).length;
  const cmpPts = usRuns.filter(r => r.analysis.speed_mps && r.distance_summary?.bump_speed_mps).map(r => ({ x: r.analysis.speed_mps as number, y: r.distance_summary?.bump_speed_mps as number, label: `${r.condition.output_value}` }));
  const dropPts = usRuns.filter(r => r.distance_summary?.approach_speed_mps && r.distance_summary?.drop_pct != null && !r.slowdown).map(r => ({ x: r.distance_summary?.approach_speed_mps as number, y: r.distance_summary?.drop_pct as number }));
  const errs = cmpPts.map(p => Math.abs(p.y / p.x - 1) * 100);

  return <>
    <section className="card experiment-card">
      <div className="card-head"><div><h2><Gauge size={17} /> 목표 2 · 관계식으로 정한 속도로 방지턱 앞에서 감속</h2>
        <p>관계식을 거꾸로 풀어 '허용 기준을 넘지 않는 가장 빠른 속도 v_max'를 구하고, 방지턱 앞에서 그 속도가 나오는 출력으로 줄여 실제로 기준 안에 드는지 확인합니다. 결과는 '식 검증' 시행으로 저장돼 관계식 계산에는 섞이지 않습니다.</p></div></div>
      <div className="exp-formula-box">
        <div><small>원리</small><b>v* = v_max(m) × 여유율, 출력* = 속도식⁻¹(v*, m)</b><span>{chosen?.kind === 'power' ? `지금 식(거듭제곱)이면 v*/v₀ = (Δa_기준 / Δa(v₀))^(1/n), n = ${chosen.coef.n.toFixed(2)}: 충격을 절반으로 하려면 속도는 ${(0.5 ** (1 / chosen.coef.n) * 100).toFixed(0)}%로` : '관계식 Δa(v, m) = a_기준 − 1 을 v에 대해 풂'}</span></div>
        <div><small>이번 조건 ({condition.bump_id} · {massG} g · {outputLabel(condition.output_mode, condition.output_value)})</small><b>v₀ ≈ {fmt(v0, 3, 'm/s')} → 예측 {d0 == null ? '—' : (1 + d0).toFixed(2) + ' g'}</b><span>허용 기준 {aLimit?.toFixed(2) ?? '—'} g · v_max {fmt(vmax, 3, 'm/s')} · 목표 {fmt(vt, 3, 'm/s')}</span></div>
        <div className={need ? 'warn' : ''}><small>계획</small><b>{problem ? '—' : need ? `출력 ${condition.output_value} → ${pSlow?.toFixed(1)} 로 감속` : '감속 불필요 (이미 기준 이내)'}</b><span>{need && dT != null ? `감속 후 예측 ${(1 + dT).toFixed(2)} g${pRaw != null && pRaw < minOut ? ` · 계산값 ${pRaw.toFixed(1)}이 최소 출력보다 낮아 ${minOut}로 제한(멈춤 방지) → 기준을 넘을 수 있음` : ''}` : ''}</span></div>
      </div>
      <div className="experiment-fields">
        <label>감속 시작 조건<select value={trigger} onChange={e => setTrigger(e.target.value as 'time' | 'tape' | 'distance')}><option value="time">출발 후 시간</option><option value="distance">벽까지 거리 (초음파)</option><option value="tape">감속 표시 테이프 (바닥 센서)</option></select></label>
        {trigger === 'time' && <label>출발 후 (s)<NumInput min={0} max={6} step={0.1} value={atS} onChange={e => setAtS(Math.max(0, Number(e.target.value) || 0))} /></label>}
        {trigger === 'distance' && <label>벽까지 (cm) 가 되면<NumInput min={5} max={300} step={5} value={atCm} onChange={e => setAtCm(Math.max(5, Number(e.target.value) || 60))} /></label>}
        <label>여유율 (v_max에 곱함)<NumInput min={0.3} max={1} step={0.05} value={safety} onChange={e => setSafety(Math.max(0.3, Math.min(1, Number(e.target.value) || 0.9)))} /></label>
        <label>최소 출력 (멈춤 방지)<NumInput min={1} max={100} value={minOut} onChange={e => setMinOut(Math.max(1, Number(e.target.value) || 10))} /></label>
        <label>허용 기준 (g, 빈칸 = 분석 탭 기준)<NumInput min={1.05} max={8} step={0.05} value={targetG} placeholder={data?.criteria?.[bump]?.a_limit_g?.toFixed(2) ?? ''} onChange={e => setTargetG(e.target.value === '' ? '' : Number(e.target.value))} /></label>
        <label>반복<NumInput min={1} max={20} value={repeats} onChange={e => setRepeats(Math.max(1, Math.min(20, Math.round(Number(e.target.value) || 1))))} /></label>
      </div>
      <p className="exp-hint">{trigger === 'time' ? '출발 후 정해진 시간에 줄입니다. 방지턱까지 걸리는 시간보다 0.3~0.5초 일찍 줄여야 속도가 미리 떨어집니다(모터 속도가 바뀌는 데 0.2~0.4초 걸림).'
        : trigger === 'distance' ? '방지턱 뒤쪽에 벽(상자 등, 높이 15 cm 이상)을 두고, 초음파로 벽까지 거리가 이 값이 되면 줄입니다. 측정·분석 탭에서 한 번 달려 보면 "앞바퀴 충격 때 벽까지 거리"가 나오니, 그 값보다 20~40 cm 큰 값을 넣으세요(감속할 거리).'
        : '방지턱 20~40 cm 앞에 가로로 검은 테이프를 붙이면, 차 앞쪽 바닥 센서가 테이프를 보는 순간 줄입니다(출발선 테이프와 같은 감지 방식).'} 실제로 얼마나 줄었는지는 각 회차의 '방지턱 통과 속도'로 확인합니다.</p>
      {problem && <p className="diagnostic-error">{problem}</p>}
      <div className="button-row"><button className="primary" disabled={!!problem || !ctx.canAutoRun} onClick={() => void runSlow()}><Play size={15} /> 감속 제어 측정 ({repeats}회)</button>
        {ctx.auto && ctx.busy && <span className="exp-hint">{ctx.auto.message}</span>}</div>
      {slowRuns.length > 0 && <>
        <p className="exp-hint">감속 제어 {slowRuns.length}회 중 기준 이내 <b>{passN}/{judged}</b>회.</p>
        <XYChart name={`slowdown_${bump}`} title="감속 제어 결과: 실제 통과 속도와 최대 충격" xLabel="실제 통과 속도 (m/s, 바퀴 간격)" yLabel="최대 수직 가속도 (g)"
          series={[{ name: `관계식 예측 (${massG} g)`, color: '#2f6fb0', line: true, dots: false, points: curve },
            { name: '기준 이내', color: '#3c9a5f', points: slowRuns.filter(r => r.slowdown?.pass && r.slowdown.measured_speed_mps).map(r => ({ x: r.slowdown?.measured_speed_mps as number, y: r.slowdown?.measured_peak_g as number })) },
            { name: '기준 초과', color: '#c62828', points: slowRuns.filter(r => r.slowdown?.pass === false && r.slowdown.measured_speed_mps).map(r => ({ x: r.slowdown?.measured_speed_mps as number, y: r.slowdown?.measured_peak_g as number })) }]}
          hLines={aLimit ? [{ y: aLimit, label: `허용 기준 ${aLimit.toFixed(2)} g`, color: '#c33' }] : []}
          vLines={vt != null ? [{ x: vt, label: '목표 속도', color: '#a04fa0' }] : []}
          caption="초록 점이 기준선 아래, 목표 속도 근처에 모이면 '관계식 → 속도 결정 → 감속 → 충격 감소'가 실험으로 확인된 것입니다. 점이 목표 속도보다 오른쪽이면 감속할 거리(시간)가 부족했던 것입니다." />
        <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>시각</th><th>질량</th><th>출력</th><th>시작 조건</th><th>예측 v₀ → 목표</th><th>실제 속도</th><th>최대 충격</th><th>판정</th></tr></thead>
          <tbody>{slowRuns.map(r => { const s = r.slowdown!; return <tr key={r.id} className={s.pass === false ? 'over' : ''}><td>{new Date(r.created_at).toLocaleTimeString()}</td><td>{r.condition.total_mass_g} g</td><td>{s.output_initial} → {s.needed ? s.output : '유지'}</td>
            <td>{s.trigger === 'time' ? `${s.at_s}s` : s.trigger === 'distance' ? `${s.at_cm}cm` : '테이프'}{s.slowed_at_s == null && s.needed && !r.simulated ? ' (작동 안 함)' : ''}</td>
            <td>{s.v0_pred_mps.toFixed(3)} → {s.target_v_mps.toFixed(3)}</td><td>{fmt(s.measured_speed_mps, 3)}</td><td>{fmt(s.measured_peak_g, 2)} / {s.a_limit_g.toFixed(2)} g</td><td>{s.pass == null ? '—' : s.pass ? '이내' : '초과'}</td></tr>; })}</tbody></table></div>
      </>}
    </section>

    <section className="card experiment-card">
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
          <li><b>카메라·라이다</b>: PiCar-X에는 라이다가 없고 앞쪽 센서는 초음파입니다. 카메라 한 대로는 거리를 바로 알 수 없어(크기를 아는 표적과 보정이 필요) 이 실험에서는 쓰지 않습니다. 대신 휴대폰 슬로모션 영상으로 속도를 재서 '영상 실측 속도' 칸에 넣어 교차 검증할 수 있습니다.</li>
          <li><b>주의</b>: 초음파를 읽는 동안 라즈베리파이가 바빠져 MPU 기록 속도가 조금 줄 수 있습니다. 각 회차의 표본 속도(Hz)를 확인하세요. 벽 앞 자동 정지를 켜 두면 벽에 부딪히지 않습니다.</li>
        </ul>
      </details>
      {usRuns.length > 0 ? <>
        <p className="exp-hint">초음파를 기록한 {usRuns.length}회 중 속력 골을 MPU 충격 ±0.3초 안에서 찾은 회차 <b>{found}</b>회 ({(found / usRuns.length * 100).toFixed(0)}%){errs.length ? <> · 바퀴 간격 속도와 초음파 속력 차이 평균 <b>{(errs.reduce((a, b) => a + b, 0) / errs.length).toFixed(1)}%</b></> : ''}.</p>
        <div className="exp-theory-grid">
          {cmpPts.length > 0 && <XYChart name={`us_vs_axle_${bump}`} title="두 방법으로 구한 통과 속도" xLabel="바퀴 간격으로 구한 속도 (m/s)" yLabel="초음파로 구한 속력 (m/s)" identity band={0.1}
            series={[{ name: '회차', color: '#1f9e9e', points: cmpPts }]} caption="대각선 위에 있으면 두 방법이 같은 속도를 냅니다. 바퀴 간격 속도(Δt)를 검증하는 독립적인 방법입니다." />}
          {dropPts.length > 0 && <XYChart name={`us_drop_${bump}`} title="진입 속력과 방지턱에서 느려진 정도" xLabel="방지턱 직전 속력 (m/s)" yLabel="감소율 (%)" yZero
            series={[{ name: '회차', color: '#a04fa0', points: dropPts }]} caption="바퀴가 턱 모서리에 부딪히며 앞으로 가는 운동량 일부를 잃고, 차체를 들어 올리는 일도 해야 해서 잠깐 느려집니다. 모터가 계속 밀어 주므로 턱을 넘으면 다시 빨라집니다. 감소율이 클수록 바퀴가 턱에 세게 부딪힌 것입니다." />}
        </div>
      </> : <p className="exp-hint">아직 초음파를 기록한 회차가 없습니다. 위에서 '측정 주행 때 초음파 거리 기록'을 켜고 측정하세요. 각 회차는 한 회씩 보기 아래에 거리·속력 그래프가 나옵니다.</p>}
    </section>
  </>;
}
