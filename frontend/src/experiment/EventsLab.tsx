// 단계별 충격: 앞바퀴 오름(FU)·내림(FD), 뒷바퀴 오름(RU)·내림(RD) 네 순간의 충격을 조건별로 모으고,
// 단계마다 속도와의 관계식(거듭제곱)과 '그 단계에서 허용 기준을 넘지 않는 속도'를 구합니다.
import { Footprints } from 'lucide-react';
import { EVENT_INFO, eventPredict, eventSolve, fmt, type EventKind } from '../services/experiment';
import { XYChart } from '../components/ExperimentCharts';
import NumInput from '../components/NumInput';
import HelpBox from '../components/HelpBox';
import { usePersistentState } from '../services/persist';
import type { ExperimentContext } from './context';
import { bumpConst } from './theory';

const KINDS: EventKind[] = ['FU', 'FD', 'RU', 'RD'];

export default function EventsLab({ ctx, bump }: { ctx: ExperimentContext; bump: string }) {
  const { data, settings, condition } = ctx;
  const [massG, setMassG] = usePersistentState('events-mass', condition.base_mass_g + condition.added_mass_g || 600);
  const groups = (data?.groups ?? []).filter(g => g.bump_id === bump && g.mean_speed_mps);
  const em = data?.event_models?.[bump] ?? {};
  const aLimit = data?.criteria?.[bump]?.a_limit_g ?? null;
  const c = bumpConst(settings, bump);
  const runs = (data?.runs ?? []).filter(r => r.condition.bump_id === bump && !r.excluded && (r.analysis.events ?? []).length === 4);
  const vs = groups.map(g => g.mean_speed_mps as number);
  const vLo = vs.length ? Math.min(...vs) * 0.8 : 0.1, vHi = vs.length ? Math.max(...vs) * 1.2 : 1;
  const mKg = massG / 1000;
  const vmax = Object.fromEntries(KINDS.map(k => [k, em[k] && aLimit ? eventSolve(em[k]!, aLimit - 1, mKg) : null])) as Record<EventKind, number | null>;
  const strict = KINDS.filter(k => vmax[k] != null).sort((a, b) => (vmax[a] as number) - (vmax[b] as number))[0];
  const crossMm = c.R != null && c.h != null && c.r != null ? 2 * Math.sqrt((c.R) ** 2 - (c.R - c.h) ** 2) * 1000 : null;
  const vRef = vs.length ? vs.reduce((a, b) => a + b, 0) / vs.length : null;

  return <section className="card experiment-card">
    <div className="card-head"><div><h2><Footprints size={17} /> 단계별 충격 (앞·뒤 바퀴의 오름·내림)</h2>
      <p>한 번 넘을 때 바퀴마다 '오를 때'와 '내려올 때' 두 번, 모두 네 번 충격을 받습니다. 차의 앞뒤 기울기(자이로)로 네 순간을 찾아 각각의 충격을 따로 모읍니다.</p></div></div>
    <HelpBox id="events" title="네 순간은 어떻게 찾고, 왜 따로 보나요?" defaultOpen={false}>
      <ul>
        <li><b>앞바퀴 오름</b>: 앞바퀴가 턱에 올라 차 앞이 들립니다(기울기 +). <b>앞바퀴 내림</b>: 앞바퀴가 턱을 다 지나 내려오면 수평으로 돌아옵니다. <b>뒷바퀴 오름</b>: 뒷바퀴가 오르면 차 뒤가 들립니다(기울기 −). <b>뒷바퀴 내림</b>: 다시 수평.</li>
        <li>기울기가 그 구간 최대치의 20%를 넘는/내려가는 시각을 각 순간으로 잡아, 오름→오름과 내림→내림 간격의 평균으로 속도를 구합니다(충격 피크 위치보다 정확). 숫자는 각 순간 근처의 가장 큰 위아래 가속도입니다.</li>
        <li>오를 때는 바퀴가 턱 모서리에 부딪히며 위로 꺾이고(이론 ②), 내려올 때는 떨어져 바닥에 닿습니다. 원리가 달라 속도에 따라 커지는 정도(지수 n)도 다를 수 있습니다. 그래서 단계마다 식을 따로 만들고, 가장 엄격한 단계가 전체 속도 한계를 정합니다.</li>
        {crossMm != null && vRef != null && <li>이 방지턱에서 바퀴 하나가 턱 위에 있는 거리는 약 {crossMm.toFixed(0)} mm, 축간거리 {settings.wheelbase_mm ?? '—'} mm라 평균 속도 {vRef.toFixed(2)} m/s에서 앞바퀴 오름→내림 {((crossMm / 1000) / vRef * 1000).toFixed(0)} ms, 앞바퀴 내림→뒷바퀴 오름 {(((settings.wheelbase_mm ?? 95) - crossMm) / 1000 / vRef * 1000).toFixed(0)} ms입니다.</li>}
      </ul>
    </HelpBox>
    {groups.length === 0 ? <div className="empty-chart">조건 평균이 아직 없습니다.</div> : <>
      <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>질량</th><th>출력</th><th>속도 (m/s)</th>{KINDS.map(k => <th key={k} style={{ color: EVENT_INFO[k].color }}>{EVENT_INFO[k].label} Δa (g)</th>)}<th>가장 큰 단계</th></tr></thead>
        <tbody>{groups.map(g => { const ev = g.event_delta_g ?? {}; const top = KINDS.filter(k => ev[k] != null).sort((a, b) => (ev[b] as number) - (ev[a] as number))[0];
          return <tr key={`${g.total_mass_g}-${g.output_value}`}><td>{g.total_mass_g} g</td><td>{g.output_value}</td><td>{fmt(g.mean_speed_mps, 3)}</td>
            {KINDS.map(k => <td key={k} className={k === top ? 'exp-strong' : ''}>{ev[k] == null ? '—' : `+${(ev[k] as number).toFixed(2)}`}{g.event_n?.[k] ? <small className="exp-sub">{g.event_n[k]}회</small> : null}</td>)}
            <td>{top ? EVENT_INFO[top].label : '—'}</td></tr>; })}</tbody></table></div>
      <p className="exp-hint">네 순간을 모두 찾은 회차 {runs.length}개로 계산했습니다(기울기가 뚜렷하지 않은 회차는 빠짐). 비어 있으면 측정·분석 탭 그래프에서 4순간 표시가 나오는지 확인하세요.</p>
      <div className="exp-theory-grid">
        <XYChart name={`events_${bump}`} title="단계별 충격과 통과 속도" xLabel="통과 속도 v (m/s)" yLabel="평소보다 더 받은 가속도 Δa (g)" yZero legendMax={8}
          series={KINDS.flatMap(k => {
            const pts = groups.filter(g => g.event_delta_g?.[k] != null).map(g => ({ x: g.mean_speed_mps as number, y: g.event_delta_g?.[k] as number }));
            const m = em[k];
            const curve = m ? Array.from({ length: 30 }, (_, i) => vLo + (vHi - vLo) * i / 29).map(v => ({ x: v, y: eventPredict(m, v, mKg) })) : [];
            return [{ name: EVENT_INFO[k].label, color: EVENT_INFO[k].color, points: pts }, ...(curve.length ? [{ name: `${EVENT_INFO[k].short} 식`, color: EVENT_INFO[k].color, line: true, dots: false, dash: '5 3', points: curve }] : [])];
          })}
          hLines={aLimit ? [{ y: aLimit - 1, label: `허용 기준 +${(aLimit - 1).toFixed(2)} g`, color: '#c33' }] : []}
          caption="점 = 조건 평균, 점선 = 단계별 식 Δa = k·vⁿ. 위에 있는 단계일수록 충격이 큰 단계입니다." />
        <div>
          <div className="exp-inline"><label>총질량 (g)<NumInput min={1} value={massG} onChange={e => setMassG(Math.max(1, Number(e.target.value) || 600))} /></label></div>
          <div className="experiment-table-wrap"><table className="experiment-table"><thead><tr><th>단계</th><th>식</th><th>R²</th><th>기준 이내 최대 속도</th></tr></thead>
            <tbody>{KINDS.map(k => { const m = em[k]; return <tr key={k} className={k === strict ? 'selected' : ''}><td style={{ color: EVENT_INFO[k].color }}><b>{EVENT_INFO[k].label}</b></td>
              <td>{m ? `Δa = ${m.k.toPrecision(3)}·v^${m.n.toFixed(2)}${m.uses_mass ? `·m^${m.p.toFixed(2)}` : ''}` : '—'}</td><td>{m ? m.r2.toFixed(3) : '—'}</td>
              <td>{vmax[k] == null ? '—' : `${(vmax[k] as number).toFixed(3)} m/s`}{k === strict ? ' ← 가장 엄격' : ''}</td></tr>; })}</tbody></table></div>
          <p className="exp-hint">{strict ? <>허용 기준 {aLimit?.toFixed(2)} g에서 가장 먼저 기준에 닿는 단계는 <b>{EVENT_INFO[strict].label}</b>입니다. 전체를 한 속도로 지난다면 그 단계의 속도({(vmax[strict] as number).toFixed(3)} m/s)가 한계이고, '감속 제어'의 단계별 옵션은 앞바퀴 구간과 뒷바퀴 구간에 각각 다른 속도를 씁니다.</> : '출력(속도)을 2단계 이상 측정하면 단계별 식이 생깁니다.'}</p>
        </div>
      </div>
    </>}
  </section>;
}
